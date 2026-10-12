# StuLink v1.19.47.20 2026-10-12
# 考务环节④⑤⑥⑦：核对任课教师 / 考试时间表 / 监考编排 / 监考表导出
#
# 用户口径（2026-10-12 定案）：
#   · 环节④ 仅涉及「本次考试」：把该年级任课安排核对/临时调整后落在本批次，
#     不回写全局 TeacherSubjectLink（应对临时换老师 1-2 月）。
#   · 环节⑤ 考试时间表承载「时间段 → 考试科目」，是环节⑥ 回避教师的依据。
#   · 环节⑥ 教师池 = 环节④ 任课教师（去重）+ 其他监考员（可为无账号的校外老师，
#     仅本次考试使用，不开系统账号）；每考场监考人数 1/2/3（默认 1，存 affair.invig_slots）；
#     回避规则：考化学 → 任教化学的老师不排该场次；监考表一人一格。
#   · 环节⑦ 导出监考表（Excel + PDF）。
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import io
import re
from datetime import date, datetime

import openpyxl
from flask import jsonify, request, send_file
from flask_login import login_required, current_user

from app.extensions import db
from app.models import User
from app.models.grades import (AffairTeacher, AffairSchedule, AffairInvigilator,
                               AffairInvigilation, AffairRoom, AffairStudent,
                               TeacherSubjectLink, SUBJECTS)
from app.modules.grades import bp
from app.modules.grades.utils import numeric_classes
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation
from app.utils.export_helpers import xl_row
from app.utils.text_guard import sanitize_label, sanitize_prefix, safe_download_name
from app.utils.upload_guard import validate_upload

_MAX_SLOTS = 3
# 2026-10-12 安全审计 M-1：考试科目白名单。时间表科目是环节⑥「同科回避」的匹配键
# （_auto_arrange 按 {sch.subject} 回避任教该科老师），此前任意文本可入库 →
# 手写「化学(」之类的值会让回避规则静默失配，排出来就是化学老师监考化学。
_SUBJ_SET = set(SUBJECTS)


# ==================== 公共小工具 ====================

def _affair(aid):
    """取批次并校验年级可见（复用考务模块的越权校验）"""
    from app.modules.grades.routes.exam_affairs import _get_affair_checked
    return _get_affair_checked(aid)


def _class_list(affair):
    """本批次班级列表：优先取**年级**数字教学班（与任课映射页同口径）；
    学籍无数据时回落本批次名单里的班级。"""
    from app.modules.grades.routes.teachers import _grade_classes
    cls = _grade_classes(affair.grade)
    if cls:
        return cls
    names = [s.class_name for s in
             AffairStudent.query.filter_by(affair_id=affair.id).all() if s.class_name]
    return numeric_classes(sorted(set(names))) or sorted(set(names))


def _teacher_candidates(grade):
    """下拉候选教师（复用成绩模块的筛选：启用 + 教师类角色，拼音排序）"""
    from app.modules.grades.routes.teachers import _matrix_users
    return _matrix_users(grade)


def _sync_teachers_from_links(affair):
    """用当前 TeacherSubjectLink（同年级 active）覆盖写入本批次任课核对表。"""
    AffairTeacher.query.filter_by(affair_id=affair.id).delete()
    links = TeacherSubjectLink.query.filter_by(grade=affair.grade, active=True).all()
    uids = {l.user_id for l in links if l.user_id}
    names = ({u.id: u.real_name for u in User.query.filter(User.id.in_(uids)).all()}
             if uids else {})
    n = 0
    for l in links:
        db.session.add(AffairTeacher(
            affair_id=affair.id, grade=affair.grade, class_name=l.class_name,
            subject=l.subject, user_id=l.user_id,
            teacher_name=names.get(l.user_id), source='sync'))
        n += 1
    return n


def ensure_teachers_synced(affair):
    """首次进入环节④ 时自动带入当前任课安排（幂等：已有数据不再覆盖）。"""
    if AffairTeacher.query.filter_by(affair_id=affair.id).count() == 0:
        _sync_teachers_from_links(affair)
        db.session.commit()


def _split_subjects(text):
    """学科文本 → 集合（支持顿号/逗号/斜杠/空格分隔，如「语文、数学」）"""
    return {s for s in re.split(r'[、，,/\s]+', (text or '').strip()) if s}


def invig_pool(affair):
    """监考教师池：环节④ 任课教师（按姓名去重，学科取并集）+ 其他监考员。

    返回 [{name, user_id, subjects(set), source}]（保持「先任课教师、后其他」的稳定顺序）。
    """
    pool = {}

    def _entry(name, user_id, source):
        e = pool.get(name)
        if e is None:
            e = {'name': name, 'user_id': user_id, 'subjects': set(), 'source': source}
            pool[name] = e
        elif user_id and not e['user_id']:
            e['user_id'] = user_id
        return e

    for t in AffairTeacher.query.filter_by(affair_id=affair.id).all():
        nm = (t.teacher_name or '').strip()
        if not nm:
            continue
        e = _entry(nm, t.user_id, 'teacher')
        if t.subject:
            e['subjects'].add(t.subject)
    for o in AffairInvigilator.query.filter_by(affair_id=affair.id).all():
        nm = (o.name or '').strip()
        if not nm:
            continue
        e = _entry(nm, None, 'other')
        e['subjects'] |= _split_subjects(o.subject)
    return list(pool.values())


def _schedule_key(s):
    return (s.sort_order or 0, s.exam_date or date.min, s.start_time or '', s.id)


# ==================== 环节④ 核对任课教师 ====================

@bp.route('/affairs/<int:aid>/teachers')
@login_required
@perm_required('grades.edit')
def affair_teachers_get(aid):
    affair = _affair(aid)
    ensure_teachers_synced(affair)
    cells = {}
    for t in AffairTeacher.query.filter_by(affair_id=affair.id).all():
        cells[f'{t.class_name}|{t.subject}'] = {
            'user_id': t.user_id, 'name': t.teacher_name, 'source': t.source}
    return jsonify(success=True, data={
        'grade': affair.grade,
        'classes': _class_list(affair),
        'subjects': SUBJECTS,
        'cells': cells,
        'users': _teacher_candidates(affair.grade),
    })


@bp.route('/affairs/<int:aid>/teachers/sync', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_teachers_sync(aid):
    """从当前任课安排表重新带入（覆盖本批次已核对的调整）"""
    affair = _affair(aid)
    n = _sync_teachers_from_links(affair)
    db.session.commit()
    log_operation(current_user, '同步', '考务任课核对', aid,
                  f'{affair.grade} 带入 {n} 条', module='grades')
    return jsonify({'ok': True, 'synced': n})


@bp.route('/affairs/<int:aid>/teachers/save', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_teachers_save(aid):
    """保存本批次任课核对矩阵（仅本场，不回写全局映射）

    body JSON: {cells: {'01班|语文': user_id 或 '' , ...}}
    """
    affair = _affair(aid)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raw = (request.get_data(as_text=True) or '').strip()
        try:
            data = __import__('json').loads(raw) if raw.startswith('{') else {}
        except (ValueError, TypeError):
            data = {}
    cells = data.get('cells') or {}
    classes = set(_class_list(affair))
    # 校验 user_id
    uids = {int(v) for v in cells.values() if str(v).strip()}
    name_of = {}
    if uids:
        rows = User.query.filter(User.id.in_(uids)).all()
        name_of = {u.id: u.real_name for u in rows}
        if uids - set(name_of):
            return jsonify({'ok': False, 'msg': '存在无效的教师账号'})
    existing = {(t.class_name, t.subject): t
                for t in AffairTeacher.query.filter_by(affair_id=affair.id).all()}
    for key, val in cells.items():
        if '|' not in key:
            continue
        cls, subj = key.split('|', 1)
        if cls not in classes or subj not in SUBJECTS:
            continue
        uid = int(val) if str(val).strip() else None
        row = existing.get((cls, subj))
        if uid is None and row is None:
            continue
        if row is None:
            row = AffairTeacher(affair_id=affair.id, grade=affair.grade,
                                class_name=cls, subject=subj)
            db.session.add(row)
            existing[(cls, subj)] = row
        row.user_id = uid
        row.teacher_name = name_of.get(uid) if uid else None
        row.source = 'manual'
    db.session.commit()
    log_operation(current_user, '维护', '考务任课核对', aid,
                  f'{affair.grade} 保存 {len(cells)} 格', module='grades')
    return jsonify({'ok': True})


# ==================== 环节⑤ 考试时间表 ====================

_SCHED_HEADERS = ['考试日期', '日期', '开始时间', '结束时间', '考试科目', '科目',
                  '选科', '备注']


def _parse_time(v):
    """时间单元格 → 'HH:MM'（兼容 datetime.time / 'HH:MM' / 'HHMM'）"""
    if v is None:
        return ''
    if isinstance(v, datetime):
        return v.strftime('%H:%M')
    if hasattr(v, 'strftime'):
        try:
            return v.strftime('%H:%M')
        except (ValueError, AttributeError):
            pass
    s = str(v).strip()
    m = re.match(r'^(\d{1,2})[:：](\d{1,2})', s)
    if m:
        return '%02d:%02d' % (int(m.group(1)), int(m.group(2)))
    if re.match(r'^\d{3,4}$', s):
        return '%02d:%02d' % (int(s[:-2]), int(s[-2:]))
    return s


def _parse_date(v):
    if v is None or str(v).strip() == '':
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip().replace('/', '-').replace('.', '-')
    for fmt in ('%Y-%m-%d', '%m-%d', '%Y%m%d', '%m月%d日'):
        try:
            d = datetime.strptime(s, fmt)
            return d.date() if fmt.startswith('%Y') else d.replace(year=date.today().year).date()
        except ValueError:
            continue
    return None


@bp.route('/affairs/<int:aid>/schedules/template')
@login_required
@perm_required('grades.edit')
def affair_schedules_template(aid):
    affair = _affair(aid)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '考试时间表'
    ws.append(['考试日期', '开始时间', '结束时间', '考试科目', '选科', '备注'])
    ws.append(['2026-10-08', '09:00', '10:30', '语文', '', ''])
    ws.append(['2026-10-08', '14:00', '15:30', '数学', '', ''])
    for ci in range(1, 7):
        c = ws.cell(row=1, column=ci)
        c.font = openpyxl.styles.Font(bold=True, color='FFFFFF')
        c.fill = openpyxl.styles.PatternFill(start_color='4472C4', end_color='4472C4',
                                             fill_type='solid')
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return send_file(out, as_attachment=True,
                     download_name='考试时间表模板.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@bp.route('/affairs/<int:aid>/schedules/import', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_schedules_import(aid):
    """导入考试时间表（表头容错：考试日期/日期、开始时间、结束时间、考试科目/科目、选科、备注）"""
    affair = _affair(aid)
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'ok': False, 'msg': '请选择 Excel 文件'})
    ok, err = validate_upload(f.filename, allowed_exts=['xlsx', 'xls'], stream=f.stream)
    if not ok:
        return jsonify({'ok': False, 'msg': err})
    try:
        wb = openpyxl.load_workbook(f.stream, data_only=True)
    except Exception as e:  # noqa: BLE001
        return jsonify({'ok': False, 'msg': f'无法读取 Excel：{e}'})
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return jsonify({'ok': False, 'msg': '文件为空'})
    head = [str(x or '').strip() for x in rows[0]]

    def _col(*names):
        for nm in names:
            if nm in head:
                return head.index(nm)
        return -1

    ci_date = _col('考试日期', '日期')
    ci_start = _col('开始时间', '开始')
    ci_end = _col('结束时间', '结束')
    ci_subj = _col('考试科目', '科目')
    ci_sel = _col('选科', '选科组合')
    ci_note = _col('备注')
    if ci_subj < 0:
        return jsonify({'ok': False, 'msg': '未找到「考试科目」列'})
    base_order = (AffairSchedule.query.filter_by(affair_id=affair.id).count())
    added, skipped = 0, 0
    for r in rows[1:]:
        def cell(i):
            return r[i] if (0 <= i < len(r)) else None
        # 2026-10-12 安全审计 M-1：Excel 导入同样做服务端白名单 + 科目白名单校验
        # 2026-10-13：科目支持多选（「、」分隔，与手填口径一致）
        names = [s for s in re.split(r'[、，,/\\s]+', str(cell(ci_subj) or '').strip()) if s]
        if not names or any(s not in _SUBJ_SET for s in names):
            skipped += 1
            continue
        subj = '、'.join(names)
        db.session.add(AffairSchedule(
            affair_id=affair.id,
            exam_date=_parse_date(cell(ci_date)) if ci_date >= 0 else None,
            start_time=_parse_time(cell(ci_start)) if ci_start >= 0 else '',
            end_time=_parse_time(cell(ci_end)) if ci_end >= 0 else '',
            subject=subj,
            selection=sanitize_label(cell(ci_sel) if ci_sel >= 0 else '', 30),
            note=sanitize_label(cell(ci_note) if ci_note >= 0 else '', 100),
            sort_order=base_order + added))
        added += 1
    db.session.commit()
    log_operation(current_user, '导入', '考试时间表', aid, f'新增 {added} 场', module='grades')
    return jsonify({'ok': True, 'added': added, 'skipped': skipped})


@bp.route('/affairs/<int:aid>/schedules/save', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_schedules_save(aid):
    """新增/修改一场（表单：id 为空则新增）

    2026-10-13：科目改为**多选**（如"四选二"一场同时考两科）——
    前端勾选后以「、」连接提交；每个科目都必须在字典内；
    存储沿用「、」分隔，与环节⑥「同科回避」的 _split_subjects 口径一致。
    """
    affair = _affair(aid)
    sid = request.form.get('id', type=int)
    names = [s for s in re.split(r'[、，,/\\s]+', (request.form.get('subject') or '').strip())
             if s]
    if not names:
        return jsonify({'ok': False, 'msg': '请选择考试科目'})
    bad = [s for s in names if s not in _SUBJ_SET]
    if bad:
        return jsonify({'ok': False, 'msg': f'科目不在科目字典内：{"、".join(bad)}'})
    subj = '、'.join(names)
    row = AffairSchedule.query.get(sid) if sid else None
    if row is not None and row.affair_id != affair.id:
        return jsonify({'ok': False, 'msg': '场次不属于本批次'})
    if row is None:
        row = AffairSchedule(affair_id=affair.id,
                             sort_order=AffairSchedule.query.filter_by(
                                 affair_id=affair.id).count())
        db.session.add(row)
    row.exam_date = _parse_date(request.form.get('exam_date'))
    row.start_time = _parse_time(request.form.get('start_time'))
    row.end_time = _parse_time(request.form.get('end_time'))
    row.subject = subj
    row.selection = sanitize_label(request.form.get('selection'), 30)
    row.note = sanitize_label(request.form.get('note'), 100)
    db.session.commit()
    return jsonify({'ok': True, 'id': row.id})


@bp.route('/affairs/<int:aid>/schedules/<int:sid>/delete', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_schedule_delete(aid, sid):
    affair = _affair(aid)
    row = AffairSchedule.query.get_or_404(sid)
    if row.affair_id != affair.id:
        return jsonify({'ok': False, 'msg': '场次不属于本批次'})
    n_inv = AffairInvigilation.query.filter_by(
        affair_id=affair.id, schedule_id=sid).delete()
    _desc = f'{row.exam_date or ""} {row.subject}'
    db.session.delete(row)
    db.session.commit()
    # 2026-10-12 数据可信度审计（§3.8）：删场次会连带删该场次全部监考安排，
    # 此前无业务日志（兜底网只记 endpoint 名）。
    log_operation(current_user, '删除', '考试时间表', aid,
                  f'删除场次「{_desc}」并清理其 {n_inv} 格监考安排', module='grades')
    return jsonify({'ok': True})


# ==================== 环节⑥ 监考编排 ====================

@bp.route('/affairs/<int:aid>/invig')
@login_required
@perm_required('grades.edit')
def affair_invig_get(aid):
    affair = _affair(aid)
    ensure_teachers_synced(affair)
    pool = invig_pool(affair)
    schedules = sorted(AffairSchedule.query.filter_by(affair_id=affair.id).all(),
                       key=_schedule_key)
    rooms = AffairRoom.query.filter_by(affair_id=affair.id).order_by(AffairRoom.room_no).all()
    invigs = AffairInvigilation.query.filter_by(affair_id=affair.id).all()
    by_key = {}
    for iv in invigs:
        by_key.setdefault((iv.schedule_id, iv.room_no), {})[iv.slot] = {
            'name': iv.name or '', 'user_id': iv.user_id}
    excl_all, excl_sch = _excludes(affair)
    # 每人格数（已排的监考位）—— 供前端展示"谁排得多/谁还没排到"
    load = {}
    for iv in invigs:
        if iv.name:
            load[iv.name] = load.get(iv.name, 0) + 1
    return jsonify(success=True, data={
        'slots': max(1, min(_MAX_SLOTS, affair.invig_slots or 1)),
        'pool': [{'name': p['name'], 'user_id': p['user_id'],
                  'subjects': sorted(p['subjects']), 'source': p['source'],
                  'excluded': p['name'] in excl_all,
                  'excl_schedules': sorted(sid for sid, names in excl_sch.items()
                                           if p['name'] in names)}
                 for p in pool],
        'load': load,
        'excludes': [{'id': e.id, 'name': e.name, 'schedule_id': e.schedule_id,
                      'note': e.note or ''}
                     for e in _excl_rows(affair)],
        'others': [{'id': o.id, 'name': o.name, 'subject': o.subject or '', 'note': o.note or ''}
                   for o in AffairInvigilator.query.filter_by(affair_id=affair.id).all()],
        'schedules': [{'id': s.id, 'date': s.exam_date.strftime('%Y-%m-%d') if s.exam_date else '',
                       'start': s.start_time or '', 'end': s.end_time or '',
                       'subject': s.subject, 'selection': s.selection or ''} for s in schedules],
        'rooms': [{'room_no': r.room_no, 'location': r.location or ''} for r in rooms],
        'invig': {f'{k[0]}|{k[1]}': v for k, v in by_key.items()},
    })


@bp.route('/affairs/<int:aid>/invig/settings', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_invig_settings(aid):
    """设置每考场监考人数（1/2/3，默认 1）"""
    affair = _affair(aid)
    n = request.form.get('slots', type=int) or 1
    old = affair.invig_slots or 1
    affair.invig_slots = max(1, min(_MAX_SLOTS, n))
    db.session.commit()
    # 2026-10-12 数据可信度审计（§3.8）：该设置决定监考表列数（1/2/3 人），
    # 改动会影响后续所有编排与导出，原先无业务日志。
    log_operation(current_user, '设置', '监考人数', aid,
                  f'每考场监考人数 {old} → {affair.invig_slots}', module='grades')
    return jsonify({'ok': True, 'slots': affair.invig_slots})


@bp.route('/affairs/<int:aid>/invig/others/add', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_invig_others_add(aid):
    """新增其他监考员（可无系统账号：校外老师，仅本次考试使用）"""
    affair = _affair(aid)
    # 2026-10-12 安全审计 M-1：姓名/学科/备注走 sanitize_label 服务端白名单
    # （与 exam_affairs 的考场位置/备注同防线）。这些字段会进监考表 Excel/PDF，
    # 且前端有 innerHTML 拼接路径，只 .strip() 不构成纵深防御。
    name = sanitize_label(request.form.get('name'), 30)
    if not name:
        return jsonify({'ok': False, 'msg': '请填写姓名'})
    exist = AffairInvigilator.query.filter_by(affair_id=affair.id, name=name).first()
    if exist is not None:
        return jsonify({'ok': False, 'msg': '该监考员已存在'})
    db.session.add(AffairInvigilator(
        affair_id=affair.id, name=name,
        subject=sanitize_label(request.form.get('subject'), 20),
        note=sanitize_label(request.form.get('note'), 100)))
    db.session.commit()
    # 2026-10-12 数据可信度审计（§3.8）：其他监考员（含校外无账号老师）增删原先无业务日志
    log_operation(current_user, '新增', '其他监考员', aid, f'{name}', module='grades')
    return jsonify({'ok': True})


@bp.route('/affairs/<int:aid>/invig/others/<int:oid>/delete', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_invig_other_delete(aid, oid):
    affair = _affair(aid)
    row = AffairInvigilator.query.get_or_404(oid)
    if row.affair_id != affair.id:
        return jsonify({'ok': False, 'msg': '不属于本批次'})
    _nm = row.name
    db.session.delete(row)
    db.session.commit()
    log_operation(current_user, '删除', '其他监考员', aid, f'{_nm}', module='grades')
    return jsonify({'ok': True})


@bp.route('/affairs/<int:aid>/invig/others/import', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_invig_others_import(aid):
    """导入其他监考员名单（Excel：姓名、任教学科、备注）"""
    affair = _affair(aid)
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'ok': False, 'msg': '请选择 Excel 文件'})
    ok, err = validate_upload(f.filename, allowed_exts=['xlsx', 'xls'], stream=f.stream)
    if not ok:
        return jsonify({'ok': False, 'msg': err})
    try:
        wb = openpyxl.load_workbook(f.stream, data_only=True)
    except Exception as e:  # noqa: BLE001
        return jsonify({'ok': False, 'msg': f'无法读取 Excel：{e}'})
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return jsonify({'ok': False, 'msg': '文件为空'})
    head = [str(x or '').strip() for x in rows[0]]

    def _col(*names):
        for nm in names:
            if nm in head:
                return head.index(nm)
        return -1

    ci_name = _col('姓名', '监考员', '监考老师')
    ci_subj = _col('任教学科', '学科', '科目')
    ci_note = _col('备注')
    if ci_name < 0:
        return jsonify({'ok': False, 'msg': '未找到「姓名」列'})
    exist = {o.name for o in AffairInvigilator.query.filter_by(affair_id=affair.id).all()}
    teacher_names = {t.teacher_name for t in
                     AffairTeacher.query.filter_by(affair_id=affair.id).all()
                     if t.teacher_name}
    added, dup = 0, 0
    for r in rows[1:]:
        def cell(i):
            return r[i] if (0 <= i < len(r)) else None
        # 2026-10-12 安全审计 M-1：Excel 导入同样走服务端白名单（导入是写入面最宽的口子）
        nm = sanitize_label(cell(ci_name), 30)
        if not nm:
            continue
        if nm in exist or nm in teacher_names:
            dup += 1
            continue
        db.session.add(AffairInvigilator(
            affair_id=affair.id, name=nm,
            subject=sanitize_label(cell(ci_subj) if ci_subj >= 0 else '', 20),
            note=sanitize_label(cell(ci_note) if ci_note >= 0 else '', 100)))
        exist.add(nm)
        added += 1
    db.session.commit()
    log_operation(current_user, '导入', '其他监考员', aid, f'新增 {added} 人', module='grades')
    return jsonify({'ok': True, 'added': added, 'dup': dup})


@bp.route('/affairs/<int:aid>/invig/exclude', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_invig_exclude_add(aid):
    """加入排除名单：form name（必填）、schedule_id（空=整批次不监考）、note"""
    from app.models.grades import AffairInvigExclude

    affair = _affair(aid)
    name = (request.form.get('name') or '').strip()
    sid = request.form.get('schedule_id', type=int)
    if not name:
        return jsonify({'ok': False, 'msg': '请选择老师'})
    pool = {p['name']: p for p in invig_pool(affair)}
    p = pool.get(name)
    if p is None:
        return jsonify({'ok': False, 'msg': '该老师不在监考池中'})
    if sid:
        sch = AffairSchedule.query.get(sid)
        if sch is None or sch.affair_id != affair.id:
            return jsonify({'ok': False, 'msg': '场次无效'})
    q = AffairInvigExclude.query.filter_by(affair_id=affair.id, name=name,
                                           schedule_id=sid)
    if q.first() is not None:
        return jsonify({'ok': False, 'msg': '该排除项已存在'})
    db.session.add(AffairInvigExclude(
        affair_id=affair.id, name=name, user_id=p['user_id'], schedule_id=sid,
        note=(request.form.get('note') or '').strip()[:100] or None))
    db.session.commit()
    scope = '整批次不监考' if not sid else '不监考该场次'
    log_operation(current_user, '修改', '监考排除名单', aid, f'{name} {scope}',
                  module='grades')
    return jsonify({'ok': True, 'msg': f'{name}：{scope}'})


@bp.route('/affairs/<int:aid>/invig/exclude/delete', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_invig_exclude_del(aid):
    """恢复某人监考（删除排除项）：form id"""
    from app.models.grades import AffairInvigExclude

    affair = _affair(aid)
    eid = request.form.get('id', type=int)
    row = db.session.get(AffairInvigExclude, eid) if eid else None
    if row is None or row.affair_id != affair.id:
        return jsonify({'ok': False, 'msg': '排除项不存在'})
    _n = row.name
    db.session.delete(row)
    db.session.commit()
    log_operation(current_user, '修改', '监考排除名单', aid, f'恢复 {_n} 监考',
                  module='grades')
    return jsonify({'ok': True, 'msg': f'已恢复 {_n}'})


@bp.route('/affairs/<int:aid>/invig/auto', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_invig_auto(aid):
    """自动编排监考：场次 × 考场 × 监考位，回避本场科目教师，尽量均衡轮转。"""
    affair = _affair(aid)
    # 2026-10-12（§3.5）：容量不足等前置条件不满足时给 400 + 明确原因，
    # 不要让 ValueError 冒成 500（原实现未捕获，前端只看到"请求失败"）。
    try:
        rows, info = _auto_arrange(affair)
    except ValueError as e:
        return jsonify({'ok': False, 'msg': str(e)}), 400
    db.session.commit()
    gaps = info.get('gap_count') or 0
    log_operation(current_user, '编排', '监考表', aid,
                  f'{info["schedules"]}场×{info["rooms"]}考场×{info["slots"]}人 → '
                  f'{rows}/{info.get("total", rows)}格'
                  + (f'，缺口 {gaps}' if gaps else ''),
                  module='grades')
    return jsonify({'ok': True, 'rows': rows, **info})


def _excl_rows(affair):
    """排除名单明细（整批在前）；表尚未迁移时返回空列表。"""
    from app.models.grades import AffairInvigExclude

    try:
        return (AffairInvigExclude.query.filter_by(affair_id=affair.id)
                .order_by(AffairInvigExclude.schedule_id.is_(None).desc(),
                          AffairInvigExclude.name).all())
    except Exception:                       # noqa: BLE001
        db.session.rollback()
        return []


def _excludes(affair):
    """监考排除名单 → (整批不监考 set(name), {场次id: set(name)} 不监考该场次)"""
    excl_all, excl_sch = set(), {}
    rows = _excl_rows(affair)
    for e in rows:
        nm = (e.name or '').strip()
        if not nm:
            continue
        if e.schedule_id:
            excl_sch.setdefault(e.schedule_id, set()).add(nm)
        else:
            excl_all.add(nm)
    return excl_all, excl_sch


def _time_overlap(s1, s2):
    """两个场次时间是否重叠（同一人被排进重叠场次 = 分身乏术）。

    只有**同一天且两端时间齐全**才判定；时间缺失时不误判（保守放行）。
    """
    if not s1.exam_date or s1.exam_date != s2.exam_date:
        return False
    a1, b1 = (s1.start_time or ''), (s1.end_time or '')
    a2, b2 = (s2.start_time or ''), (s2.end_time or '')
    if not (a1 and b1 and a2 and b2):
        return False
    return a1 < b2 and a2 < b1


def _auto_arrange(affair):
    """自动编排监考（2026-10-13 增强）：

    约束（硬）：
      H1 同科回避：任教该场科目的老师不排（一场多科时每科都回避）
      H2 一人一格：同一场次内一人只监一个考场
      H3 排除名单：整批不监考 / 不监考指定场次
      H4 时间冲突：同一人被排的两个场次时间不能重叠

    算法：**MRV（候选最少的场次先排）+ 最小负载均衡** ——
      1. 先算每场次的静态候选（排除 + 回避后）；
      2. 按候选数升序处理场次（最受约束的先排，能显著减少"后面排不下"的缺口）；
      3. 每格在候选里取「当前已排格数最少」的人（天然做到尽量均匀、人人有份）；
      4. 排不下的格子**记录缺口与原因**并继续，不再无声留空。

    返回 (rows, stat)；stat 含 filled/total/gaps/load（每人格数）。
    """
    pool = invig_pool(affair)
    excl_all, excl_sch = _excludes(affair)
    schedules = sorted(AffairSchedule.query.filter_by(affair_id=affair.id).all(),
                       key=_schedule_key)
    rooms = AffairRoom.query.filter_by(affair_id=affair.id).order_by(AffairRoom.room_no).all()
    slots = max(1, min(_MAX_SLOTS, affair.invig_slots or 1))
    if not pool:
        raise ValueError('监考教师池为空（请先在环节④ 核对任课教师）')
    if not schedules:
        raise ValueError('尚未导入考试时间表（环节⑤）')
    if not rooms:
        raise ValueError('尚未设置考场（环节②）')
    # 2026-10-12 数据可信度审计（§3.5）：原先池内人手不够时 `if not cands: break`
    # 直接留空，该考场剩余监考位**无声无息**——20 考场×2 人=40 格、池里只有 25 人时，
    # 15 个空格既不报错也不计数，导出的监考表就是空的。
    # 这里先做容量预检：总格数 vs 池内可用教师数（按场次剔除同科回避后取最小值）。
    def _static_cands(sch):
        return [p for p in pool
                if p['name'] not in excl_all
                and p['name'] not in excl_sch.get(sch.id, set())
                and not (p['subjects'] & _split_subjects(sch.subject))]

    total_cells = len(schedules) * len(rooms) * slots
    # 2026-10-13：容量预检改为**各场次可排上限之和**（原先用"单场最小可用人数 × 场次数"，
    # 但各场次可用人数本就不同 —— 回避/排除往往只影响个别场次，那样算会误判"人不够"）。
    per_sch = {s.id: len(_static_cands(s)) for s in schedules}
    capacity = sum(min(n, len(rooms) * slots) for n in per_sch.values())
    avail = min(per_sch.values()) if per_sch else 0
    if capacity < total_cells:
        raise ValueError(
            '监考教师不足：共需 %d 格（%d 场 × %d 考场 × %d 人），'
            '而各场次可排教师合计最多 %d 格（已剔除同科回避与排除名单，'
            '最少的一场只有 %d 人可用）→ 缺 %d 格。'
            '请增加监考员（环节⑥「其他监考员」）、取消部分排除，或减少每考场监考人数。'
            % (total_cells, len(schedules), len(rooms), slots,
               capacity, avail, total_cells - capacity))
    AffairInvigilation.query.filter_by(affair_id=affair.id).delete()
    load = {p['name']: 0 for p in pool}
    busy = {p['name']: [] for p in pool}        # name -> 已排入的场次（判时间冲突）
    rows = 0
    gaps = []
    # MRV：候选最少的场次先排（最受约束的先排，避免"前面随便排、后面排不下"）
    order = sorted(schedules, key=lambda s: (len(_static_cands(s)), _schedule_key(s)))
    for sch in order:
        avoid = _split_subjects(sch.subject)
        used_this = set()          # 同一场次内，一人只监一个考场
        for room in rooms:
            for slot in range(1, slots + 1):
                cands = [p for p in pool
                         if p['name'] not in used_this
                         and p['name'] not in excl_all
                         and p['name'] not in excl_sch.get(sch.id, set())
                         and not (p['subjects'] & avoid)
                         and not any(_time_overlap(sch, s2) for s2 in busy[p['name']])]
                if not cands:
                    gaps.append({'schedule': f'{sch.exam_date or ""} {sch.subject}',
                                 'room': room.room_no, 'slot': slot,
                                 'reason': '无可用教师（同科回避 / 排除 / 时间冲突）'})
                    continue       # 不再 break —— 原来会跳过该考场剩余监考位
                cands.sort(key=lambda p: (load[p['name']], p['name']))
                p = cands[0]
                used_this.add(p['name'])
                load[p['name']] += 1
                busy[p['name']].append(sch)
                db.session.add(AffairInvigilation(
                    affair_id=affair.id, schedule_id=sch.id, room_no=room.room_no,
                    slot=slot, name=p['name'], user_id=p['user_id'],
                    subject='、'.join(sorted(p['subjects']))))
                rows += 1
    return rows, {'slots': slots, 'schedules': len(schedules), 'rooms': len(rooms),
                  'total': total_cells, 'filled': rows, 'gap_count': len(gaps),
                  'gaps': gaps[:8],
                  'load': {k: v for k, v in sorted(load.items(), key=lambda x: -x[1])}}


@bp.route('/affairs/<int:aid>/invig/set', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_invig_set(aid):
    """手动指定某一格监考老师（一人一格）。按姓名设置，姓名不在池中则拒绝。

    form: schedule_id, room_no, slot, name
    """
    affair = _affair(aid)
    sid = request.form.get('schedule_id', type=int)
    room_no = (request.form.get('room_no') or '').strip()
    slot = request.form.get('slot', type=int) or 1
    name = (request.form.get('name') or '').strip()
    sch = AffairSchedule.query.get(sid)
    if sch is None or sch.affair_id != affair.id:
        return jsonify({'ok': False, 'msg': '场次无效'})
    if not AffairRoom.query.filter_by(affair_id=affair.id, room_no=room_no).first():
        return jsonify({'ok': False, 'msg': '考场无效'})
    row = AffairInvigilation.query.filter_by(
        affair_id=affair.id, schedule_id=sid, room_no=room_no, slot=slot).first()
    if not name:
        if row is not None:
            db.session.delete(row)
            db.session.commit()
        return jsonify({'ok': True, 'name': ''})
    pool = {p['name']: p for p in invig_pool(affair)}
    p = pool.get(name)
    if p is None:
        return jsonify({'ok': False, 'msg': '该老师不在监考池中'})
    if sch.subject and (p['subjects'] & _split_subjects(sch.subject)):
        return jsonify({'ok': False, 'msg': f'{name} 任教{sch.subject}，本场需回避'})
    # 2026-10-13：手动指派与自动编排同一套硬约束（原先手动可绕过）
    excl_all, excl_sch = _excludes(affair)
    if name in excl_all:
        return jsonify({'ok': False, 'msg': f'{name} 已设为本次考试不监考'})
    if name in excl_sch.get(sid, set()):
        return jsonify({'ok': False, 'msg': f'{name} 已设为不监考这一场'})
    dup = AffairInvigilation.query.filter_by(
        affair_id=affair.id, schedule_id=sid, name=name).filter(
        db.or_(AffairInvigilation.room_no != room_no,
               AffairInvigilation.slot != slot)).first()
    if dup is not None:
        return jsonify({'ok': False,
                        'msg': f'{name} 本场已排在考场 {dup.room_no}，不能同时监考两个考场'})
    for other in AffairInvigilation.query.filter_by(
            affair_id=affair.id, name=name).all():
        if other.schedule_id == sid:
            continue
        s2 = AffairSchedule.query.get(other.schedule_id)
        if s2 is not None and _time_overlap(sch, s2):
            return jsonify({'ok': False,
                            'msg': f'{name} 在 {s2.exam_date} {s2.start_time}-{s2.end_time} '
                                   f'已排监考，与本场时间重叠'})
    if row is None:
        row = AffairInvigilation(affair_id=affair.id, schedule_id=sid,
                                 room_no=room_no, slot=slot)
        db.session.add(row)
    row.name = name
    row.user_id = p['user_id']
    row.subject = '、'.join(sorted(p['subjects']))
    db.session.commit()
    # 2026-10-12 数据可信度审计（§3.8）：手动改监考表原先无业务日志，
    # 事后无法回答"这一格是谁改的、从谁改成谁"。
    log_operation(current_user, '修改', '监考表', aid,
                  f'{sch.exam_date or ""} {sch.subject}｜考场 {room_no} 第 {slot} 位 → {name}',
                  module='grades')
    return jsonify({'ok': True, 'name': name})


# ==================== 环节⑦ 导出监考表 ====================

def _invig_rows(affair):
    """监考表数据行：按「场次 → 考场」排序，返回 (headers, rows, slots)。"""
    schedules = {s.id: s for s in
                 AffairSchedule.query.filter_by(affair_id=affair.id).all()}
    rooms = {r.room_no: r for r in
             AffairRoom.query.filter_by(affair_id=affair.id).all()}
    invigs = AffairInvigilation.query.filter_by(affair_id=affair.id).all()
    slots = max(1, min(_MAX_SLOTS, affair.invig_slots or 1))
    for iv in invigs:
        if iv.slot and iv.slot > slots:
            slots = min(_MAX_SLOTS, iv.slot)
    by_key = {}
    for iv in invigs:
        by_key.setdefault((iv.schedule_id, iv.room_no), {})[iv.slot] = iv.name or ''
    headers = ['考试时间', '考试科目', '考场号', '考场位置']
    headers += [f'监考老师{n}' for n in range(1, slots + 1)]
    order = sorted(schedules.values(), key=_schedule_key)
    rows = []
    for s in order:
        for rn in sorted(rooms):
            key = (s.id, rn)
            when = ' '.join(x for x in [
                s.exam_date.strftime('%Y-%m-%d') if s.exam_date else '',
                (f'{s.start_time}-{s.end_time}' if (s.start_time or s.end_time) else '')] if x)
            sel = f'（{s.selection}）' if s.selection else ''
            names = by_key.get(key, {})
            row = [when, (s.subject or '') + sel, rn, rooms[rn].location or '']
            row += [names.get(n, '') for n in range(1, slots + 1)]
            rows.append(row)
    return headers, rows, slots


@bp.route('/affairs/<int:aid>/export/invig')
@login_required
@perm_required('grades.edit')
def affair_export_invig(aid):
    affair = _affair(aid)
    headers, rows, _slots = _invig_rows(affair)
    if not rows:
        return jsonify({'ok': False, 'msg': '尚未编排监考表'}), 400
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '监考表'
    from app.modules.grades.routes.exam_affairs import _write_sheet
    _write_sheet(ws, headers, rows)
    for i, w in enumerate([22, 14, 10, 20] + [14] * (_len_after(headers)), start=1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    log_operation(current_user, '导出', '监考表', aid, f'{len(rows)} 行', module='grades')
    return send_file(out, as_attachment=True,
                     download_name=safe_download_name(f'监考表_{affair.name}') + '.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


def _len_after(headers):
    return max(0, len(headers) - 4)


@bp.route('/affairs/<int:aid>/export/invig/pdf')
@login_required
@perm_required('grades.edit')
def affair_export_invig_pdf(aid):
    affair = _affair(aid)
    headers, rows, _slots = _invig_rows(affair)
    if not rows:
        return jsonify({'ok': False, 'msg': '尚未编排监考表'}), 400
    from app.utils.affair_pdf import seating_list_sections
    widths = [26, 16, 12, 26] + [20] * (len(headers) - 4)
    buf = seating_list_sections(
        f'{affair.name}　监考表（{len(rows)} 行）', headers,
        [(f'{affair.grade} 监考安排', rows)], widths_mm=widths)
    return send_file(buf, as_attachment=True,
                     download_name=safe_download_name(f'监考表_{affair.name}') + '.pdf',
                     mimetype='application/pdf')
