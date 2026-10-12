# StuLink v1.19.43.2 2026-10-12
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
from app.utils.upload_guard import validate_upload

_MAX_SLOTS = 3


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
        subj = str(cell(ci_subj) or '').strip()
        if not subj:
            skipped += 1
            continue
        db.session.add(AffairSchedule(
            affair_id=affair.id,
            exam_date=_parse_date(cell(ci_date)) if ci_date >= 0 else None,
            start_time=_parse_time(cell(ci_start)) if ci_start >= 0 else '',
            end_time=_parse_time(cell(ci_end)) if ci_end >= 0 else '',
            subject=subj,
            selection=str(cell(ci_sel) or '').strip() if ci_sel >= 0 else '',
            note=str(cell(ci_note) or '').strip() if ci_note >= 0 else '',
            sort_order=base_order + added))
        added += 1
    db.session.commit()
    log_operation(current_user, '导入', '考试时间表', aid, f'新增 {added} 场', module='grades')
    return jsonify({'ok': True, 'added': added, 'skipped': skipped})


@bp.route('/affairs/<int:aid>/schedules/save', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_schedules_save(aid):
    """新增/修改一场（表单：id 为空则新增）"""
    affair = _affair(aid)
    sid = request.form.get('id', type=int)
    subj = (request.form.get('subject') or '').strip()
    if not subj:
        return jsonify({'ok': False, 'msg': '请填写考试科目'})
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
    row.selection = (request.form.get('selection') or '').strip()
    row.note = (request.form.get('note') or '').strip()
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
    AffairInvigilation.query.filter_by(affair_id=affair.id, schedule_id=sid).delete()
    db.session.delete(row)
    db.session.commit()
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
    return jsonify(success=True, data={
        'slots': max(1, min(_MAX_SLOTS, affair.invig_slots or 1)),
        'pool': [{'name': p['name'], 'user_id': p['user_id'],
                  'subjects': sorted(p['subjects']), 'source': p['source']}
                 for p in pool],
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
    affair.invig_slots = max(1, min(_MAX_SLOTS, n))
    db.session.commit()
    return jsonify({'ok': True, 'slots': affair.invig_slots})


@bp.route('/affairs/<int:aid>/invig/others/add', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_invig_others_add(aid):
    """新增其他监考员（可无系统账号：校外老师，仅本次考试使用）"""
    affair = _affair(aid)
    name = (request.form.get('name') or '').strip()
    if not name:
        return jsonify({'ok': False, 'msg': '请填写姓名'})
    exist = AffairInvigilator.query.filter_by(affair_id=affair.id, name=name).first()
    if exist is not None:
        return jsonify({'ok': False, 'msg': '该监考员已存在'})
    db.session.add(AffairInvigilator(
        affair_id=affair.id, name=name,
        subject=(request.form.get('subject') or '').strip(),
        note=(request.form.get('note') or '').strip()))
    db.session.commit()
    return jsonify({'ok': True})


@bp.route('/affairs/<int:aid>/invig/others/<int:oid>/delete', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_invig_other_delete(aid, oid):
    affair = _affair(aid)
    row = AffairInvigilator.query.get_or_404(oid)
    if row.affair_id != affair.id:
        return jsonify({'ok': False, 'msg': '不属于本批次'})
    db.session.delete(row)
    db.session.commit()
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
        nm = str(cell(ci_name) or '').strip()
        if not nm:
            continue
        if nm in exist or nm in teacher_names:
            dup += 1
            continue
        db.session.add(AffairInvigilator(
            affair_id=affair.id, name=nm,
            subject=str(cell(ci_subj) or '').strip() if ci_subj >= 0 else '',
            note=str(cell(ci_note) or '').strip() if ci_note >= 0 else ''))
        exist.add(nm)
        added += 1
    db.session.commit()
    log_operation(current_user, '导入', '其他监考员', aid, f'新增 {added} 人', module='grades')
    return jsonify({'ok': True, 'added': added, 'dup': dup})


@bp.route('/affairs/<int:aid>/invig/auto', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_invig_auto(aid):
    """自动编排监考：场次 × 考场 × 监考位，回避本场科目教师，尽量均衡轮转。"""
    affair = _affair(aid)
    rows, info = _auto_arrange(affair)
    db.session.commit()
    log_operation(current_user, '编排', '监考表', aid,
                  f'{info["schedules"]}场×{info["rooms"]}考场×{info["slots"]}人 → {rows}格',
                  module='grades')
    return jsonify({'ok': True, 'rows': rows, **info})


def _auto_arrange(affair):
    pool = invig_pool(affair)
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
    AffairInvigilation.query.filter_by(affair_id=affair.id).delete()
    load = {p['name']: 0 for p in pool}
    rows = 0
    for sch in schedules:
        avoid = {sch.subject} if sch.subject else set()
        used_this = set()          # 同一场次内，一人只监一个考场
        for room in rooms:
            for slot in range(1, slots + 1):
                cands = [p for p in pool
                         if p['name'] not in used_this
                         and not (p['subjects'] & avoid)]
                if not cands:
                    break
                cands.sort(key=lambda p: (load[p['name']], p['name']))
                p = cands[0]
                used_this.add(p['name'])
                load[p['name']] += 1
                db.session.add(AffairInvigilation(
                    affair_id=affair.id, schedule_id=sch.id, room_no=room.room_no,
                    slot=slot, name=p['name'], user_id=p['user_id'],
                    subject='、'.join(sorted(p['subjects']))))
                rows += 1
    return rows, {'slots': slots, 'schedules': len(schedules), 'rooms': len(rooms)}


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
    if sch.subject and (p['subjects'] & {sch.subject}):
        return jsonify({'ok': False, 'msg': f'{name} 任教{sch.subject}，本场需回避'})
    if row is None:
        row = AffairInvigilation(affair_id=affair.id, schedule_id=sid,
                                 room_no=room_no, slot=slot)
        db.session.add(row)
    row.name = name
    row.user_id = p['user_id']
    row.subject = '、'.join(sorted(p['subjects']))
    db.session.commit()
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
                     download_name=f'监考表_{affair.name}.xlsx',
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
    return send_file(buf, as_attachment=True, download_name=f'监考表_{affair.name}.pdf',
                     mimetype='application/pdf')
