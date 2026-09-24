# StuLink v1.18.1.0 2026-09-24
# 教务 · 调课管理（重构版，基于 timetable.db）：
#   个人调课 / 统一调课 / 审核 / 执行 / 撤销 / 详情 / 统计 / 联动 API
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from datetime import date

from flask import (render_template, request, redirect, url_for, flash,
                   abort, jsonify)
from flask_login import login_required, current_user

from app.extensions import db
from app.models.timetable import (ScheduleEntry, SWAP_STATUS, SWAP_TYPES,
                                  WEEKDAY_NAMES, MAX_PERIOD)
from app.modules.academic import bp
from app.modules.academic.services import swap_service
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation


# ── 内部工具 ──────────────────────────────────────────────────────────

def _parse_date(value):
    try:
        return date.fromisoformat((value or '').strip())
    except (ValueError, AttributeError):
        return None


def _applicant():
    """当前登录用户对应的申请人 (uid, name)。无教师名单记录时回退账号名。"""
    from app.modules.academic.services import teacher_service
    t = teacher_service.teacher_of_user(current_user)
    if t:
        return t.teacher_uid, t.name
    uid = (getattr(current_user, 'username', None) or str(current_user.id))[:16]
    return uid, current_user.real_name


def _is_reviewer():
    """审核/执行权限：管理员或持有教务·课表管理权限者。"""
    return current_user.role == 'admin' or current_user.has_perm('academic.timetable')


def _bool_flag(value):
    return str(value).strip().lower() in ('1', 'on', 'true', 'yes')


# ══════════════════════════════════════════════════════════════════════
# 页面路由
# ══════════════════════════════════════════════════════════════════════

@bp.route('/swap')
@login_required
@perm_required('academic.swap')
def swap_page():
    """调课管理列表：审核者看全部（含待审徽章），教师看自己的。支持状态/类型筛选与分页。"""
    sched = swap_service.get_active_schedule()
    schedule_id = sched.id if sched else None
    status = (request.args.get('status') or '').strip()
    swap_type = (request.args.get('swap_type') or '').strip()
    page = request.args.get('page', 1, type=int)
    is_reviewer = _is_reviewer()
    uid, _ = _applicant()
    applicant_uid = None if is_reviewer else uid

    items, pagination = swap_service.get_swap_list(
        status=status or None, swap_type=swap_type or None,
        schedule_id=schedule_id, applicant_uid=applicant_uid,
        page=page, per_page=20, is_reviewer=is_reviewer)
    pending = swap_service.count_pending(schedule_id, applicant_uid)

    return render_template('academic/swap_list.html',
                           items=items, pagination=pagination, schedule=sched,
                           status=status, swap_type=swap_type, pending=pending,
                           is_reviewer=is_reviewer, current_uid=uid,
                           status_map=SWAP_STATUS, type_map=SWAP_TYPES,
                           weekday_names=WEEKDAY_NAMES)


@bp.route('/swap/apply', methods=['GET', 'POST'])
@login_required
@perm_required('academic.swap')
def swap_apply():
    """个人调课申请：选原课程 → 选目标时段 → 填理由/日期/是否永久。"""
    sched = swap_service.get_active_schedule()
    if not sched:
        flash('尚未建立学期课表，无法申请调课', 'warning')
        return redirect(url_for('academic.swap_page'))

    if request.method == 'POST':
        uid, name = _applicant()
        entry_id = request.form.get('entry_id', type=int)
        new_weekday = request.form.get('new_weekday', type=int)
        new_period = request.form.get('new_period', type=int)
        new_room = (request.form.get('new_room') or '').strip()
        reason = (request.form.get('reason') or '').strip()
        is_permanent = _bool_flag(request.form.get('is_permanent'))
        swap_date = _parse_date(request.form.get('swap_date'))

        if not entry_id:
            flash('请选择原课程', 'danger')
            return redirect(url_for('academic.swap_apply'))
        if not reason:
            flash('请填写调课理由', 'danger')
            return redirect(url_for('academic.swap_apply'))

        ok, msg, swap = swap_service.apply_swap(
            applicant_uid=uid, applicant_name=name, original_entry_id=entry_id,
            new_weekday=new_weekday, new_period=new_period, new_room=new_room or None,
            swap_date=swap_date, is_permanent=is_permanent, reason=reason,
            swap_type='personal')
        if not ok:
            db.session.rollback()
            flash(msg, 'danger')
            return redirect(url_for('academic.swap_apply'))
        log_operation(current_user, '新增', '调课申请', swap.id,
                      f'{name} 个人调课 #{swap.id}', module='academic')
        flash(msg, 'success')
        return redirect(url_for('academic.swap_detail', swap_id=swap.id))

    periods = swap_service.get_periods(sched.id)
    class_opts = swap_service.get_class_options(sched.id)
    uid, name = _applicant()
    return render_template('academic/swap_apply.html',
                           schedule=sched, periods=periods, class_opts=class_opts,
                           weekdays=WEEKDAY_NAMES, max_period=MAX_PERIOD,
                           applicant_uid=uid, applicant_name=name)


@bp.route('/swap/bulk', methods=['GET', 'POST'])
@login_required
@perm_required('academic.swap')
def swap_bulk():
    """统一调课：选年级/班级/星期 → 勾选多个课程 → 批量指定新时段。"""
    sched = swap_service.get_active_schedule()
    if not sched:
        flash('尚未建立学期课表，无法统一调课', 'warning')
        return redirect(url_for('academic.swap_page'))

    class_opts = swap_service.get_class_options(sched.id)
    periods = swap_service.get_periods(sched.id)

    if request.method == 'POST':
        uid, name = _applicant()
        raw_ids = request.form.getlist('entry_ids')
        entry_ids = [int(x) for x in raw_ids if str(x).strip().isdigit()]
        new_weekday = request.form.get('new_weekday', type=int)
        new_period = request.form.get('new_period', type=int)
        new_room = (request.form.get('new_room') or '').strip()
        reason = (request.form.get('reason') or '').strip()
        is_permanent = _bool_flag(request.form.get('is_permanent'))
        swap_date = _parse_date(request.form.get('swap_date'))

        ok, msg, result = swap_service.bulk_apply_swap(
            applicant_uid=uid, applicant_name=name, entry_ids=entry_ids,
            new_weekday=new_weekday, new_period=new_period, new_room=new_room or None,
            swap_date=swap_date, is_permanent=is_permanent, reason=reason)
        if not ok:
            db.session.rollback()
            return render_template('academic/swap_bulk.html',
                                   schedule=sched, class_opts=class_opts, periods=periods,
                                   weekdays=WEEKDAY_NAMES, max_period=MAX_PERIOD,
                                   bulk_error=msg, bulk_conflicts=result.get('conflicts', []),
                                   f_grade=request.form.get('grade', ''),
                                   f_class=request.form.get('class_name', ''),
                                   f_weekday=request.form.get('weekday', ''))
        log_operation(current_user, '新增', '统一调课', 0,
                      f'{name} 批量 {result.get("created", 0)} 条', module='academic')
        flash(msg, 'success')
        return redirect(url_for('academic.swap_page'))

    return render_template('academic/swap_bulk.html',
                           schedule=sched, class_opts=class_opts, periods=periods,
                           weekdays=WEEKDAY_NAMES, max_period=MAX_PERIOD,
                           bulk_error=None, bulk_conflicts=[])


@bp.route('/swap/stats')
@login_required
@perm_required('academic.swap')
def swap_stats():
    """调课统计页（ECharts 图表，本地文件懒加载）。"""
    sched = swap_service.get_active_schedule()
    stats = swap_service.get_swap_stats(schedule_id=sched.id if sched else None, days=30)
    return render_template('academic/swap_stats.html',
                           schedule=sched, stats=stats, status_map=SWAP_STATUS)


@bp.route('/swap/<int:swap_id>')
@login_required
@perm_required('academic.swap')
def swap_detail(swap_id):
    """调课详情：原课程 / 目标时段 / 审核记录 / 执行状态 / 时间线。"""
    d = swap_service.get_swap_detail(swap_id)
    if not d:
        abort(404)
    is_reviewer = _is_reviewer()
    uid, _ = _applicant()
    if not is_reviewer and d.get('applicant_uid') != uid:
        abort(403)
    # 审核人姓名（跨库解析，放在路由层避免服务层依赖 User）
    d['reviewer_name'] = None
    if d.get('reviewed_by'):
        from app.models import User
        u = db.session.get(User, d['reviewed_by'])
        d['reviewer_name'] = u.real_name if u else f'用户#{d["reviewed_by"]}'
    return render_template('academic/swap_detail.html',
                           d=d, is_reviewer=is_reviewer,
                           is_owner=(d.get('applicant_uid') == uid),
                           status_map=SWAP_STATUS, weekday_names=WEEKDAY_NAMES)


# ══════════════════════════════════════════════════════════════════════
# 审核 / 执行 / 撤销（JSON，前端 AJAX 调用；执行前检查状态防重复）
# ══════════════════════════════════════════════════════════════════════

@bp.route('/swap/<int:swap_id>/approve', methods=['POST'])
@login_required
@perm_required('academic.timetable')
def swap_approve(swap_id):
    """审核通过（再次校验目标时段冲突）。"""
    review_note = (request.form.get('review_note') or '').strip()
    ok, msg, sw = swap_service.review_swap(
        swap_id, current_user.id, current_user.real_name, 'approve', review_note)
    if not ok:
        db.session.rollback()
        return jsonify({'success': False, 'message': msg}), 400
    log_operation(current_user, '审核', '调课记录', swap_id,
                  f'通过 #{swap_id}', module='academic')
    return jsonify({'success': True, 'message': msg,
                    'data': sw.to_dict() if sw else None})


@bp.route('/swap/<int:swap_id>/reject', methods=['POST'])
@login_required
@perm_required('academic.timetable')
def swap_reject(swap_id):
    """驳回（必填理由）。"""
    review_note = (request.form.get('review_note') or '').strip()
    if not review_note:
        return jsonify({'success': False, 'message': '驳回必须填写理由'}), 400
    ok, msg, sw = swap_service.review_swap(
        swap_id, current_user.id, current_user.real_name, 'reject', review_note)
    if not ok:
        db.session.rollback()
        return jsonify({'success': False, 'message': msg}), 400
    log_operation(current_user, '审核', '调课记录', swap_id,
                  f'驳回 #{swap_id}', module='academic')
    return jsonify({'success': True, 'message': msg,
                    'data': sw.to_dict() if sw else None})


@bp.route('/swap/<int:swap_id>/execute', methods=['POST'])
@login_required
@perm_required('academic.timetable')
def swap_execute(swap_id):
    """执行调课——真正修改课表（仅 approved 可执行，含幂等保护）。"""
    ok, msg, sw = swap_service.execute_swap(
        swap_id, current_user.id, current_user.real_name)
    if not ok:
        db.session.rollback()
        return jsonify({'success': False, 'message': msg}), 400
    log_operation(current_user, '执行', '调课记录', swap_id,
                  f'执行调课 #{swap_id}（{"永久" if sw.is_permanent else "临时"}）',
                  module='academic')
    return jsonify({'success': True, 'message': msg,
                    'data': sw.to_dict() if sw else None})


@bp.route('/swap/<int:swap_id>/cancel', methods=['POST'])
@login_required
@perm_required('academic.swap')
def swap_cancel(swap_id):
    """申请人撤销自己的 pending 申请。"""
    uid, _ = _applicant()
    ok, msg, sw = swap_service.cancel_swap(swap_id, uid)
    if not ok:
        db.session.rollback()
        return jsonify({'success': False, 'message': msg}), 400
    log_operation(current_user, '撤销', '调课记录', swap_id,
                  f'撤销 #{swap_id}', module='academic')
    return jsonify({'success': True, 'message': msg,
                    'data': sw.to_dict() if sw else None})


# ══════════════════════════════════════════════════════════════════════
# 联动 API（JSON：{success, message, data}）
# ══════════════════════════════════════════════════════════════════════

@bp.route('/api/swap/entries')
@login_required
@perm_required('academic.swap')
def api_swap_entries():
    """按 grade/class_name/weekday/teacher_uid 返回可选原课程条目（申请表单级联）。"""
    grade = (request.args.get('grade') or '').strip()
    class_name = (request.args.get('class_name') or '').strip()
    weekday = request.args.get('weekday', type=int)
    teacher_uid = (request.args.get('teacher_uid') or '').strip()
    sched = swap_service.get_active_schedule()
    entries = swap_service.query_entries(
        schedule_id=sched.id if sched else None, grade=grade or None,
        class_name=class_name or None, weekday=weekday or None,
        teacher_uid=teacher_uid or None)
    return jsonify({'success': True, 'message': 'ok', 'data': entries})


@bp.route('/api/swap/available-slots')
@login_required
@perm_required('academic.swap')
def api_swap_available_slots():
    """按 entry_id 返回该课程可用的目标时段（含节次名称/时间/冲突标记）。"""
    entry_id = request.args.get('entry_id', type=int)
    if not entry_id:
        return jsonify({'success': False, 'message': '缺少 entry_id 参数'}), 400
    entry = db.session.get(ScheduleEntry, entry_id)
    if not entry or entry.is_deleted:
        return jsonify({'success': False, 'message': '原课表条目不存在或已删除'}), 404
    slots = swap_service.get_available_slots(
        entry.term_schedule_id, entry.grade, entry.class_name, entry.teacher_uid,
        exclude_entry_id=entry.id, include_all=True)
    return jsonify({'success': True, 'message': 'ok', 'data': {
        'entry': entry.to_dict(), 'slots': slots,
    }})


@bp.route('/api/swap/list')
@login_required
@perm_required('academic.swap')
def api_swap_list():
    """调课列表 JSON（前端表格异步刷新用）。"""
    sched = swap_service.get_active_schedule()
    status = (request.args.get('status') or '').strip() or None
    swap_type = (request.args.get('swap_type') or '').strip() or None
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    is_reviewer = _is_reviewer()
    uid, _ = _applicant()
    items, pagination = swap_service.get_swap_list(
        status=status, swap_type=swap_type, schedule_id=sched.id if sched else None,
        applicant_uid=None if is_reviewer else uid, page=page,
        per_page=per_page, is_reviewer=is_reviewer)
    return jsonify({'success': True, 'message': 'ok', 'data': {
        'items': items, 'page': pagination.page, 'pages': pagination.pages,
        'total': pagination.total, 'per_page': pagination.per_page,
    }})


@bp.route('/api/swap/stats')
@login_required
@perm_required('academic.swap')
def api_swap_stats():
    """调课统计数据 JSON（供 ECharts 图表）。"""
    sched = swap_service.get_active_schedule()
    days = request.args.get('days', 30, type=int)
    data = swap_service.get_swap_stats(schedule_id=sched.id if sched else None,
                                       days=days)
    return jsonify({'success': True, 'message': 'ok', 'data': data})
