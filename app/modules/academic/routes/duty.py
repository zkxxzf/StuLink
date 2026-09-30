# StuLink v1.18.6.0 2026-09-30
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""任课安排 / 备课组长（2026-09-25）。

- `/academic/duty`：任课安排表（按班级 / 按教师 / 按学科 三种视角，支持周次与年级过滤）
- `/academic/leaders`：备课组长名单（按学科分组维护，含职责与备课组范围）

任课安排由课表实时聚合、不落库；备课组长落库在 academic.db.subject_leaders。
"""
import io
from datetime import datetime

from flask import (render_template, request, redirect, url_for, flash,
                   send_file, jsonify, abort)
from flask_login import login_required, current_user

from app.extensions import db
from app.models.academic import DUTY_TEMPLATES, SUBJECT_ORDER
from app.models.timetable import TermSchedule, WEEKDAY_NAMES
from app.modules.academic import bp
from app.modules.academic.services import duty_service as svc
from app.modules.academic.services import night_duty_service as nd
from app.modules.academic.services import schedule_service as sch_svc
from app.modules.academic.services.schedule_common import get_active_schedule
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation

_XLSX_MIME = ('application/vnd.openxmlformats-officedocument'
              '.spreadsheetml.sheet')

DUTY_VIEWS = ('class', 'teacher', 'subject')


def _pick_schedule():
    """选定学期：?sid= 优先，其次当前启用中的学期。"""
    sid = request.args.get('sid', type=int) or request.form.get('sid', type=int)
    if sid:
        ts = db.session.get(TermSchedule, sid)
        if ts:
            return ts
    return get_active_schedule()


def _filters():
    week = request.args.get('week', type=int) or None
    grade = (request.args.get('grade') or '').strip() or None
    class_type = (request.args.get('class_type') or '').strip() or None
    direction = (request.args.get('direction') or '').strip() or None
    view = (request.args.get('view') or 'class').strip()
    if view not in DUTY_VIEWS:
        view = 'class'
    return week, grade, view, class_type, direction


def _warn_hours():
    """周课时预警阈值（默认 16 节/周，可用 ?warn= 覆盖）"""
    return request.args.get('warn', type=int) or 16


# ── 任课安排 ───────────────────────────────────────────────────────────────

@bp.route('/duty')
@login_required
@perm_required('academic.view')
def duty_table():
    """任课安排表：按年级分块列出各班的学科、任课教师与周课时数。"""
    ts = _pick_schedule()
    week, grade, view, class_type, direction = _filters()
    warn_hours = _warn_hours()
    data = None
    if ts:
        if view == 'teacher':
            data = {'teachers': svc.build_teacher_duty(ts.id, week=week, grade=grade,
                                                       warn_hours=warn_hours)}
        elif view == 'subject':
            data = {'subjects': svc.build_subject_duty(ts.id, week=week, grade=grade)}
        else:
            data = svc.build_class_duty(ts.id, week=week, grade=grade,
                                        class_type=class_type, direction=direction)
    return render_template('academic/duty_table.html',
                           ts=ts, data=data, week=week, grade=grade, view=view,
                           class_type=class_type, direction=direction,
                           warn_hours=warn_hours,
                           class_types=svc.class_type_options(),
                           schedules=sch_svc.list_schedules())


@bp.route('/duty/export')
@login_required
@perm_required('academic.view')
def duty_export():
    """导出任课安排 Excel（与页面三种视角口径一致）"""
    ts = _pick_schedule()
    if not ts:
        flash('尚未建立学期课表，无法导出任课安排', 'warning')
        return redirect(url_for('academic.schedule_manage'))
    week, grade, view, class_type, direction = _filters()
    buf = svc.export_duty_workbook(ts.id, week=week, grade=grade, view=view,
                                   class_type=class_type, direction=direction)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    suffix = {'class': '按班级', 'teacher': '按教师', 'subject': '按学科'}[view]
    return send_file(buf, as_attachment=True,
                     download_name=f'{ts.name}_任课安排_{suffix}_{stamp}.xlsx',
                     mimetype=_XLSX_MIME)


@bp.route('/api/duty')
@login_required
@perm_required('academic.view')
def api_duty():
    """任课安排 JSON（供其它页面/大屏复用）"""
    ts = _pick_schedule()
    if not ts:
        return jsonify({'success': False, 'message': '尚未建立学期课表', 'data': None}), 400
    week, grade, view, class_type, direction = _filters()
    if view == 'teacher':
        data = {'teachers': svc.build_teacher_duty(ts.id, week=week, grade=grade,
                                                   warn_hours=_warn_hours())}
    elif view == 'subject':
        data = {'subjects': svc.build_subject_duty(ts.id, week=week, grade=grade)}
    else:
        data = svc.build_class_duty(ts.id, week=week, grade=grade)
    return jsonify({'success': True, 'message': 'ok', 'data': data})


# ── 备课组长名单 ───────────────────────────────────────────────────────────

def _leader_filters():
    return {
        'school_year': (request.args.get('school_year') or '').strip(),
        'term': (request.args.get('term') or '').strip(),
        'subject': (request.args.get('subject') or '').strip(),
        'grade': (request.args.get('grade') or '').strip(),
        'keyword': (request.args.get('q') or '').strip(),
    }


@bp.route('/leaders')
@login_required
@perm_required('academic.view')
def subject_leaders():
    """备课组长名单：按学科分组展示，可维护（需 academic.edit）。"""
    f = _leader_filters()
    years = svc.leader_years()
    school_year = f['school_year'] or (years[0] if years else '')
    leaders = svc.list_leaders(school_year=school_year or None,
                               term=f['term'] or None,
                               subject=f['subject'] or None,
                               grade=f['grade'] or None,
                               keyword=f['keyword'] or None)
    return render_template('academic/subject_leaders.html',
                           leaders=leaders, groups=svc.group_leaders(leaders),
                           years=years, school_year=school_year,
                           subjects=SUBJECT_ORDER, duty_templates=DUTY_TEMPLATES,
                           grades=_grade_options(),
                           f=f, can_edit=current_user.has_perm('academic.edit'))


def _grade_options():
    """年级下拉候选：班级档案里的年级 + 高一/高二/高三兜底。"""
    grades = []
    try:
        from app.models import ClassProfile
        grades = sorted({cp.grade for cp in ClassProfile.query.all() if cp.grade})
    except Exception:  # noqa: BLE001
        pass
    for g in ('高一', '高二', '高三'):
        if g not in grades:
            grades.append(g)
    return grades


@bp.route('/leaders/save', methods=['POST'])
@login_required
@perm_required('academic.edit')
def subject_leader_save():
    """新增/更新备课组长（同学年×学期×年级×学科重复保存即更新）"""
    data = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    data = data or {}
    try:
        leader, created = svc.save_leader(data, operator_id=current_user.id)
    except ValueError as e:
        flash(str(e), 'danger')
        return redirect(url_for('academic.subject_leaders',
                                school_year=(data.get('school_year') or '').strip()))
    log_operation(current_user, '创建' if created else '更新', '备课组长', leader.id,
                  f'{leader.school_year} {leader.grade or "全校"}{leader.subject}'
                  f' {leader.leader_name}', module='academic')
    flash(f'备课组长「{leader.leader_name}」（{leader.grade or "全校"}{leader.subject}）'
          f'已{"新增" if created else "更新"}', 'success')
    return redirect(url_for('academic.subject_leaders',
                            school_year=leader.school_year,
                            subject=leader.subject or None))


@bp.route('/leaders/delete', methods=['POST'])
@login_required
@perm_required('academic.edit')
def subject_leader_delete():
    """删除备课组长记录"""
    lid = request.form.get('id', type=int)
    if not lid and request.is_json:
        lid = (request.get_json(silent=True) or {}).get('id')
    info = svc.delete_leader(lid)
    if not info:
        flash('记录不存在或已删除', 'warning')
    else:
        log_operation(current_user, '删除', '备课组长', lid, info, module='academic')
        flash(f'已删除：{info}', 'success')
    return redirect(url_for('academic.subject_leaders',
                            school_year=request.form.get('school_year') or None))


@bp.route('/leaders/export')
@login_required
@perm_required('academic.view')
def subject_leader_export():
    """导出备课组长名单 Excel"""
    f = _leader_filters()
    years = svc.leader_years()
    school_year = f['school_year'] or (years[0] if years else '')
    leaders = svc.list_leaders(school_year=school_year or None,
                               term=f['term'] or None,
                               subject=f['subject'] or None,
                               grade=f['grade'] or None,
                               keyword=f['keyword'] or None)
    buf = svc.export_leaders_workbook(leaders, school_year=school_year)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    return send_file(buf, as_attachment=True,
                     download_name=f'备课组长名单_{school_year or ""}_{stamp}.xlsx',
                     mimetype=_XLSX_MIME)


# ══════════════════════════════════════════════════════════════════════════════
# 晚自习值班表（2026-09-26，高中刚需）
# 高中晚自习不排学科课，而是排教师值班看班；值班只落 night_duties 表，
# 不写课表条目 —— 值班不是课，不该出现在班级课表网格里。
# ══════════════════════════════════════════════════════════════════════════════
def _night_editable(ts):
    """归档学期只读；编辑还需 academic.timetable 权限（由装饰器保证）。"""
    from app.modules.academic.routes.schedule import _editable
    return bool(ts) and _editable(ts)


@bp.route('/night-duty')
@login_required
@perm_required('academic.view')
def night_duty_index():
    """晚自习值班入口（侧栏导航用）"""
    ts = get_active_schedule()
    if not ts:
        flash('尚未建立学期课表，请先创建学期', 'warning')
        return redirect(url_for('academic.schedule_manage'))
    return redirect(url_for('academic.night_duty', sid=ts.id))


@bp.route('/schedule/<int:sid>/night-duty')
@login_required
@perm_required('academic.view')
def night_duty(sid):
    """晚自习值班表：年级 × 星期 × 晚自习节次"""
    ts = db.session.get(TermSchedule, sid)
    if not ts:
        abort(404)
    grade = (request.args.get('grade') or '').strip() or None
    data = nd.get_roster(sid, grades=[grade] if grade else None)
    return render_template('academic/night_duty.html',
                           ts=ts, data=data, grade=grade or '',
                           conflicts=nd.check_conflicts(sid),
                           today=nd.today_duties(sid),
                           stats=nd.teacher_stats(sid),
                           teachers=nd.teacher_pool(),
                           can_edit=_night_editable(ts),
                           schedules=sch_svc.list_schedules(),
                           weekdays=nd.WEEKDAYS,
                           weekday_names=WEEKDAY_NAMES)


@bp.route('/schedule/<int:sid>/night-duty/auto', methods=['POST'])
@login_required
@perm_required('academic.timetable')
def night_duty_auto(sid):
    """一键均衡排班"""
    ts = db.session.get(TermSchedule, sid)
    if not ts:
        abort(404)
    if not _night_editable(ts):
        flash('该学期已归档，不能修改值班表', 'warning')
        return redirect(url_for('academic.night_duty', sid=sid))
    max_per_week = request.form.get('max_per_week', type=int) or 2
    grade = (request.form.get('grade') or '').strip() or None
    ok, msg = nd.auto_assign(sid, max_per_week=max_per_week,
                             grades=[grade] if grade else None,
                             operator=current_user)
    flash(msg, 'success' if ok else 'warning')
    return redirect(url_for('academic.night_duty', sid=sid, grade=grade or ''))


@bp.route('/schedule/<int:sid>/night-duty/set', methods=['POST'])
@login_required
@perm_required('academic.timetable')
def night_duty_set(sid):
    """手工指定 / 清空某个班次（AJAX JSON；teacher_uid 为空＝清空）"""
    ts = db.session.get(TermSchedule, sid)
    if not ts:
        return jsonify({'success': False, 'message': '学期不存在'}), 404
    if not _night_editable(ts):
        return jsonify({'success': False, 'message': '该学期已归档，不能修改值班表'}), 403
    d = request.get_json(silent=True) or request.form
    grade = (d.get('grade') or '').strip()
    try:
        weekday = int(d.get('weekday') or 0)
        period = int(d.get('period_number') or 0)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': '参数格式错误'}), 400
    uid = (d.get('teacher_uid') or '').strip()
    if not grade or not (1 <= weekday <= 7) or period <= 0:
        return jsonify({'success': False, 'message': '参数不完整'}), 400
    name = ''
    if uid:
        row = next((t for t in nd.teacher_pool() if t['uid'] == uid), None)
        if not row:
            return jsonify({'success': False, 'message': '教师不存在'}), 404
        name = row['name']
    ok, msg = nd.set_duty(sid, grade, weekday, period, uid, name,
                          note=(d.get('note') or '').strip() or None,
                          operator=current_user)
    return jsonify({'success': ok, 'message': msg, 'teacher_name': name})


@bp.route('/schedule/<int:sid>/night-duty/export')
@login_required
@perm_required('academic.view')
def night_duty_export(sid):
    """导出晚自习值班表 Excel（贴墙/发年级组）"""
    ts = db.session.get(TermSchedule, sid)
    if not ts:
        abort(404)
    grade = (request.args.get('grade') or '').strip() or None
    wb = nd.export_workbook(sid, grades=[grade] if grade else None)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_operation(current_user, '导出', '晚自习值班', sid,
                  f'{ts.name}{grade or ""}', module='academic')
    return send_file(buf, as_attachment=True,
                     download_name=f'晚自习值班表_{ts.name}_{stamp}.xlsx',
                     mimetype=_XLSX_MIME)
