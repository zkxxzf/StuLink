# StuLink v1.18.2.0 2026-09-24
# 教务 · 课表：查看 / 导入 / 编辑 / 删除
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import io
import time
import uuid

from flask import (render_template, request, redirect, url_for,
                   flash, send_file, abort, jsonify)
from flask_login import login_required, current_user

from app.extensions import db
from app.models.academic import Timetable, TimetableEntry, Teacher
from app.modules.academic import bp
from app.modules.academic.services import timetable_import_service
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation

import threading   # L-4：草稿并发保护

_WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

# 导入草稿（内存级，服务重启后自动失效，与教师导入同模式）
# L-4：进程内 dict 在多线程（waitress 多线程）下并发读写会互相覆盖/丢失，这里加锁。
_DRAFT = {}
_DRAFT_TTL = 1800
_DRAFT_LOCK = threading.RLock()

# L-4：草稿条目数量上限，避免被反复上传刷爆内存
_DRAFT_MAX = 200


def _purge_expired_drafts():
    now = time.time()
    with _DRAFT_LOCK:
        for key in [k for k, v in _DRAFT.items() if now - v.get('_ts', 0) > _DRAFT_TTL]:
            _DRAFT.pop(key, None)
        if len(_DRAFT) > _DRAFT_MAX:
            # 按时间淘汰最旧的，保留最近 _DRAFT_MAX 个
            for key in sorted(_DRAFT, key=lambda k: _DRAFT[k].get('_ts', 0))[:
                              len(_DRAFT) - _DRAFT_MAX]:
                _DRAFT.pop(key, None)


@bp.route('/timetable')
@login_required
@perm_required('academic.view')
def timetable_page():
    """课表查看（按学期切换；按用户数据范围过滤年级）"""
    from app.modules.grades.services.scope import user_grade_scope
    ug = user_grade_scope(current_user)
    tid = request.args.get('id', type=int)
    timetables = Timetable.query.order_by(Timetable.id.desc()).all()
    if ug is not None:
        timetables = [t for t in timetables if not t.grade or t.grade in ug]
    current = None
    if tid:
        current = db.session.get(Timetable, tid)
        if current and current not in timetables:
            current = None
    elif timetables:
        current = timetables[0]

    entries = []
    if current:
        entries = (TimetableEntry.query.filter_by(timetable_id=current.id)
                   .order_by(TimetableEntry.weekday, TimetableEntry.period).all())

    # 按 星期 × 节次 组织成网格，便于展示
    grid = {}
    periods = set()
    for e in entries:
        grid.setdefault((e.weekday or 0, e.period or 0), []).append(e)
        periods.add(e.period or 0)
    return render_template('academic/timetable.html', timetables=timetables,
                           current=current, entries=entries,
                           weekday_names=_WEEKDAYS,
                           periods=sorted(periods), grid=grid)


@bp.route('/timetable/import')
@login_required
@perm_required('academic.view')
def timetable_import_page():
    """课表导入页面"""
    return render_template('academic/timetable_import.html')


@bp.route('/timetable/template.xlsx')
@login_required
@perm_required('academic.view')
def timetable_template():
    """下载课表导入模板"""
    buf = timetable_import_service.build_template()
    return send_file(buf, as_attachment=True,
                     download_name='课表导入模板.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument'
                              '.spreadsheetml.sheet')


@bp.route('/timetable/import/upload', methods=['POST'])
@login_required
@perm_required('academic.edit')
def timetable_import_upload():
    """上传课表 Excel → 解析预览"""
    file = request.files.get('file')
    name = (request.form.get('name') or '').strip()
    grade = (request.form.get('grade') or '').strip()

    if not file or not file.filename:
        flash('请选择课表 Excel 文件', 'danger')
        return redirect(url_for('academic.timetable_import_page'))
    if not name:
        flash('请填写课表名称（如 2026-2027学年第一学期）', 'danger')
        return redirect(url_for('academic.timetable_import_page'))

    try:
        entries = timetable_import_service.parse_timetable_excel(io.BytesIO(file.read()))
    except ValueError as e:
        flash(str(e), 'danger')
        return redirect(url_for('academic.timetable_import_page'))
    except Exception:
        flash('文件解析失败：请使用下载的标准模板填写后重试', 'danger')
        return redirect(url_for('academic.timetable_import_page'))

    # 校验教师是否存在 + 冲突检测
    timetable_import_service.validate_and_enrich(entries)
    timetable_import_service.detect_conflicts(entries)

    plan = timetable_import_service.build_import_plan(name, grade, entries)
    token = uuid.uuid4().hex
    _purge_expired_drafts()
    with _DRAFT_LOCK:   # L-4
        _DRAFT[token] = {'plan': plan, 'entries': entries, 'fname': file.filename,
                         '_ts': time.time()}
    return render_template('academic/timetable_import.html',
                           token=token, plan=plan, fname=file.filename,
                           preview=True)


@bp.route('/timetable/import/confirm', methods=['POST'])
@login_required
@perm_required('academic.edit')
def timetable_import_confirm():
    """确认导入：写入数据库"""
    token = (request.form.get('token') or '').strip()
    _purge_expired_drafts()
    with _DRAFT_LOCK:   # L-4
        draft = _DRAFT.get(token)
    if not draft:
        flash('导入批次已失效，请重新上传文件', 'danger')
        return redirect(url_for('academic.timetable_import_page'))

    entries = draft['entries']
    plan = draft['plan']
    try:
        tt = timetable_import_service.confirm_import(
            name=plan['name'], grade=plan['grade'],
            entries=entries, created_by=current_user.id)
    except Exception as e:
        db.session.rollback()
        flash(f'导入失败：{e}', 'danger')
        return redirect(url_for('academic.timetable_import_page'))

    valid_count = len([e for e in entries if not e['errors']])
    log_operation(current_user, '导入', '课表', tt.id,
                  f'{tt.name}：写入 {valid_count} 节课', module='academic')
    flash(f'课表「{tt.name}」导入成功，共 {valid_count} 节课', 'success')
    with _DRAFT_LOCK:   # L-4
        _DRAFT.pop(token, None)
    return redirect(url_for('academic.timetable_page', id=tt.id))


@bp.route('/timetable/<int:tid>/edit', methods=['POST'])
@login_required
@perm_required('academic.edit')
def timetable_edit(tid):
    """编辑课表名称/年级"""
    tt = db.session.get(Timetable, tid)
    if not tt:
        abort(404)

    name = (request.form.get('name') or '').strip()
    grade = (request.form.get('grade') or '').strip()

    if not name:
        flash('课表名称不能为空', 'danger')
        return redirect(url_for('academic.timetable_page', id=tid))

    changes = []
    if name != tt.name:
        changes.append(f'名称 {tt.name}→{name}')
        tt.name = name
    if (grade or None) != (tt.grade or None):
        changes.append(f'年级 {(tt.grade or "空")}→{(grade or "空")}')
        tt.grade = grade or None

    if changes:
        db.session.commit()
        log_operation(current_user, '更新', '课表', tid,
                      '；'.join(changes), module='academic')
        flash(f'课表已更新', 'success')
    else:
        flash('无字段变化', 'info')
    return redirect(url_for('academic.timetable_page', id=tid))


@bp.route('/timetable/<int:tid>/delete', methods=['POST'])
@login_required
@perm_required('academic.edit')
def timetable_delete(tid):
    """删除课表（级联删除 entries）"""
    tt = db.session.get(Timetable, tid)
    if not tt:
        abort(404)

    entry_count = TimetableEntry.query.filter_by(timetable_id=tid).count()
    TimetableEntry.query.filter_by(timetable_id=tid).delete()
    db.session.delete(tt)
    db.session.commit()

    log_operation(current_user, '删除', '课表', tid,
                  f'{tt.name}：删除 {entry_count} 节课', module='academic')
    flash(f'课表「{tt.name}」已删除', 'success')
    return redirect(url_for('academic.timetable_page'))


# ====== 课表单条增删改 ======

@bp.route('/timetable/<int:tid>/entry/add', methods=['POST'])
@login_required
@perm_required('academic.edit')
def timetable_entry_add(tid):
    """添加单条课表条目"""
    tt = db.session.get(Timetable, tid)
    if not tt:
        abort(404)

    teacher_uid = (request.form.get('teacher_uid') or '').strip()
    subject = (request.form.get('subject') or '').strip()
    class_name = (request.form.get('class_name') or '').strip()
    weekday = request.form.get('weekday', type=int)
    period = request.form.get('period', type=int)
    week_range = (request.form.get('week_range') or '').strip()
    room = (request.form.get('room') or '').strip()

    if not all([teacher_uid, subject, weekday, period]):
        flash('教师编号、学科、星期、节次为必填项', 'danger')
        return redirect(url_for('academic.timetable_page', id=tid))

    t = Teacher.query.filter_by(teacher_uid=teacher_uid).first()
    teacher_name = t.name if t else ''

    entry = TimetableEntry(
        timetable_id=tid,
        teacher_uid=teacher_uid,
        teacher_name=teacher_name,
        subject=subject,
        class_name=class_name or None,
        weekday=weekday,
        period=period,
        week_range=week_range or None,
        room=room or None,
    )
    db.session.add(entry)
    db.session.commit()
    wd_names = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
    wd_label = wd_names[weekday - 1] if weekday and 1 <= weekday <= 7 else '?'
    log_operation(current_user, '新增', '课表条目', entry.id,
                  f'{tt.name}：{wd_label} 第{period}节 {subject}', module='academic')
    flash('课表条目已添加', 'success')
    return redirect(url_for('academic.timetable_page', id=tid))


@bp.route('/timetable/<int:tid>/entry/<int:eid>/edit', methods=['POST'])
@login_required
@perm_required('academic.edit')
def timetable_entry_edit(tid, eid):
    """编辑单条课表条目"""
    tt = db.session.get(Timetable, tid)
    entry = db.session.get(TimetableEntry, eid)
    if not tt or not entry or entry.timetable_id != tid:
        abort(404)

    teacher_uid = (request.form.get('teacher_uid') or '').strip()
    subject = (request.form.get('subject') or '').strip()
    class_name = (request.form.get('class_name') or '').strip()
    weekday = request.form.get('weekday', type=int)
    period = request.form.get('period', type=int)
    week_range = (request.form.get('week_range') or '').strip()
    room = (request.form.get('room') or '').strip()

    if teacher_uid:
        t = Teacher.query.filter_by(teacher_uid=teacher_uid).first()
        entry.teacher_uid = teacher_uid
        entry.teacher_name = t.name if t else ''
    if subject:
        entry.subject = subject
    entry.class_name = class_name or None
    if weekday:
        entry.weekday = weekday
    if period:
        entry.period = period
    entry.week_range = week_range or None
    entry.room = room or None

    db.session.commit()
    log_operation(current_user, '更新', '课表条目', eid,
                  f'{tt.name}', module='academic')
    flash('课表条目已更新', 'success')
    return redirect(url_for('academic.timetable_page', id=tid))


@bp.route('/timetable/<int:tid>/entry/<int:eid>/delete', methods=['POST'])
@login_required
@perm_required('academic.edit')
def timetable_entry_delete(tid, eid):
    """删除单条课表条目"""
    tt = db.session.get(Timetable, tid)
    entry = db.session.get(TimetableEntry, eid)
    if not tt or not entry or entry.timetable_id != tid:
        abort(404)

    db.session.delete(entry)
    db.session.commit()
    log_operation(current_user, '删除', '课表条目', eid,
                  f'{tt.name}：{entry.subject} {entry.class_name or ""}',
                  module='academic')
    flash('课表条目已删除', 'success')
    return redirect(url_for('academic.timetable_page', id=tid))
