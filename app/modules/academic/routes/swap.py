# StuLink v1.18.9.2 2026-10-10
# 教务 · 调课管理（重构版，基于 timetable.db）：
#   个人调课 / 统一调课 / 审核 / 执行 / 撤销 / 详情 / 统计 / 联动 API
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from datetime import date

from flask import (render_template, request, redirect, url_for, flash,
                   abort, jsonify)
from flask_login import login_required, current_user

from app.extensions import db
from app.models.timetable import (ScheduleEntry, ScheduleSwap, SWAP_STATUS,
                                  SWAP_TYPES, WEEKDAY_NAMES, MAX_PERIOD)
from app.modules.academic.services.access_scope import swap_entry_authorizer
from app.modules.academic import bp
from app.modules.academic.services import swap_service, teaching_scope_service
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
    """终审/执行权限（看全部队列）：管理员或持有教务·课表管理权限者。"""
    return current_user.role == 'admin' or current_user.has_perm('academic.timetable')


def _has_perm(perm):
    return current_user.role == 'admin' or current_user.has_perm(perm)


def _my_review_step():
    """当前用户能审的审批级序号（0 起）；审不了返回 None。

    分级审批（v1.18.8.0）：默认 ① 调课审批（年级长）→ ② 课表管理（教务，终审+执行）。
    """
    steps = swap_service.approval_chain()
    for i, step in enumerate(steps):
        if _has_perm(step['perm']):
            return i
    return None


def _step_of(item):
    """列表项当前停在审批链的第几级（返回 step dict 或 None）。"""
    steps = swap_service.approval_chain()
    cur = item.get('approval_step') or 0
    return steps[cur] if cur < len(steps) else None


def _decorate_review(items):
    """给列表项补 `can_review`（当前这一级是否轮到我审）与 `review_step_name`。"""
    for item in items:
        step = _step_of(item) if item.get('status') == 'pending' else None
        item['can_review'] = bool(step and _has_perm(step['perm']))
        item['review_step_name'] = step['name'] if step else None
    return items


def _can_approve():
    """能否参与审批（任意一级）：管理员 / 课表管理 / 一级调课审批。"""
    return (current_user.role == 'admin'
            or current_user.has_perm('academic.timetable')
            or current_user.has_perm('academic.swap_approve'))


def _require_swap_access():
    """Allow applicants, timetable reviewers and first-level approvers to open the queue."""
    if not (_can_approve() or current_user.has_perm('academic.swap')):
        abort(403)


def _swap_class_options(schedule_id):
    """Class filters for only the courses the current account may request.

    2026-10-09：下拉只保留"在用班级"（teaching_scope_service），历史/批量导入的
    未启用班级不再混进来（此前一个下拉能冒出几百个班）。
    """
    allowed = swap_entry_authorizer(current_user)
    active = set(teaching_scope_service.active_class_pairs())
    entries = ScheduleEntry.query.filter_by(
        term_schedule_id=schedule_id, is_deleted=False).all()
    grade_classes = {}
    for entry in entries:
        if not allowed(entry):
            continue
        if active and (entry.grade, entry.class_name) not in active:
            continue      # 未启用班级（历史 / 批量导入）不进下拉
        grade_classes.setdefault(entry.grade, set()).add(entry.class_name)
    grade_classes = {grade: sorted(classes)
                     for grade, classes in grade_classes.items()}
    return {
        'grades': sorted(grade_classes),
        'grade_classes': grade_classes,
    }


def _bool_flag(value):
    return str(value).strip().lower() in ('1', 'on', 'true', 'yes')


# ══════════════════════════════════════════════════════════════════════
# 页面路由
# ══════════════════════════════════════════════════════════════════════

@bp.route('/swap')
@login_required
def swap_page():
    """调课管理列表：审核者看全部（含待审徽章），教师看自己的。支持状态/类型筛选与分页。"""
    _require_swap_access()
    sched = swap_service.get_active_schedule()
    schedule_id = sched.id if sched else None
    status = (request.args.get('status') or '').strip()
    swap_type = (request.args.get('swap_type') or '').strip()
    page = request.args.get('page', 1, type=int)
    is_reviewer = _is_reviewer()
    uid, _ = _applicant()
    applicant_uid = None if is_reviewer else uid
    # 一级审批人（年级长）：看得到"轮到自己这一级"的待审 + 自己提交的，不是全校全部
    review_step = None if is_reviewer else _my_review_step()
    # 数据范围：一级审批人再按本年级收敛（管理员 / 课表终审为全校口径）
    visible_grades = (None if is_reviewer
                      else swap_service.review_visible_grades(current_user))

    items, pagination = swap_service.get_swap_list(
        status=status or None, swap_type=swap_type or None,
        schedule_id=schedule_id, applicant_uid=applicant_uid,
        page=page, per_page=20, is_reviewer=is_reviewer,
        review_step=review_step, visible_grades=visible_grades)
    _decorate_review(items)
    pending = swap_service.count_pending(schedule_id, applicant_uid)

    return render_template('academic/swap_list.html',
                           items=items, pagination=pagination, schedule=sched,
                           status=status, swap_type=swap_type, pending=pending,
                           is_reviewer=is_reviewer, current_uid=uid,
                            can_apply=current_user.has_perm('academic.swap'),
                           status_map=SWAP_STATUS, type_map=SWAP_TYPES,
                           weekday_names=WEEKDAY_NAMES,
                           chain=swap_service.approval_chain(),
                           my_review_step=review_step)


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
        entry = db.session.get(ScheduleEntry, entry_id)
        if (not entry or entry.is_deleted or entry.term_schedule_id != sched.id or
                not swap_entry_authorizer(current_user)(entry)):
            flash('你只能为本人任教课程或负责班级的课程提交申请', 'danger')
            return redirect(url_for('academic.swap_apply'))
        if not reason:
            flash('请填写调课理由', 'danger')
            return redirect(url_for('academic.swap_apply'))

        # 调休/跨天：原课日期 + 那天上的是周几的课（默认=日期星期，可手改）
        swap_date = _parse_date(request.form.get('swap_date'))
        source_date = _parse_date(request.form.get('source_date'))
        source_weekday = request.form.get('source_weekday', type=int)
        if not source_date:
            source_date = swap_date          # 只传目标日期时按"同一天"处理
        if not swap_date:
            swap_date = source_date
        if source_date and not source_weekday:
            source_weekday = entry.weekday

        ok, msg, swap = swap_service.apply_swap(
            applicant_uid=uid, applicant_name=name, original_entry_id=entry_id,
            new_weekday=new_weekday, new_period=new_period, new_room=new_room or None,
            swap_date=swap_date, is_permanent=is_permanent, reason=reason,
            swap_type='personal', source_date=source_date,
            source_weekday=source_weekday)
        if not ok:
            db.session.rollback()
            flash(msg, 'danger')
            return redirect(url_for('academic.swap_apply'))
        log_operation(current_user, '新增', '调课申请', swap.id,
                      f'{name} 个人调课 #{swap.id}', module='academic')
        flash(msg, 'success')
        return redirect(url_for('academic.swap_detail', swap_id=swap.id))

    uid, name = _applicant()
    periods = swap_service.get_periods(sched.id)
    class_opts = _swap_class_options(sched.id)
    return render_template('academic/swap_apply.html',
                           schedule=sched, periods=periods, class_opts=class_opts,
                           weekdays=WEEKDAY_NAMES, max_period=MAX_PERIOD,
                           applicant_uid=uid, applicant_name=name,
                           today=date.today().isoformat(),
                           chain=swap_service.approval_chain())


@bp.route('/swap/bulk', methods=['GET', 'POST'])
@login_required
@perm_required('academic.swap')
def swap_bulk():
    """统一调课：选年级/班级/星期 → 勾选多个课程 → 批量指定新时段。"""
    sched = swap_service.get_active_schedule()
    if not sched:
        flash('尚未建立学期课表，无法统一调课', 'warning')
        return redirect(url_for('academic.swap_page'))

    _applicant()  # 先完成唯一姓名匹配的教师账号绑定，再据此筛选可申请课程。
    class_opts = _swap_class_options(sched.id)
    periods = swap_service.get_periods(sched.id)

    if request.method == 'POST':
        uid, name = _applicant()
        raw_ids = request.form.getlist('entry_ids')
        entry_ids = [int(x) for x in raw_ids if str(x).strip().isdigit()]
        allowed = swap_entry_authorizer(current_user)
        requested_entries = [db.session.get(ScheduleEntry, eid) for eid in entry_ids]
        if any(not entry or entry.is_deleted or entry.term_schedule_id != sched.id or
               not allowed(entry)
               for entry in requested_entries):
            flash('选中的课程中包含你无权申请调课的课程', 'danger')
            return render_template('academic/swap_bulk.html',
                                   schedule=sched, class_opts=class_opts,
                                   periods=periods, weekdays=WEEKDAY_NAMES,
                                   max_period=MAX_PERIOD,
                                   bulk_error='请只选择本人任教课程或负责班级的课程',
                                   bulk_conflicts=[],
                                   f_grade=request.form.get('grade', ''),
                                   f_class=request.form.get('class_name', ''),
                                   f_weekday=request.form.get('weekday', ''))
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
def swap_stats():
    """调课统计页（ECharts 图表，本地文件懒加载）。"""
    _require_swap_access()
    sched = swap_service.get_active_schedule()
    uid, _ = _applicant()
    stats = swap_service.get_swap_stats(
        schedule_id=sched.id if sched else None, days=30,
        applicant_uid=None if _is_reviewer() else uid)
    return render_template('academic/swap_stats.html',
                           schedule=sched, stats=stats, status_map=SWAP_STATUS)


@bp.route('/swap/<int:swap_id>')
@login_required
def swap_detail(swap_id):
    """调课详情：原课程 / 目标时段 / 审核记录 / 执行状态 / 时间线。"""
    _require_swap_access()
    d = swap_service.get_swap_detail(swap_id)
    if not d:
        abort(404)
    is_reviewer = _is_reviewer()
    uid, _ = _applicant()
    # 一级审批人（年级长）也要能打开详情，否则点不进去审批
    if not (is_reviewer or _can_approve()) and d.get('applicant_uid') != uid:
        abort(403)
    # 数据范围：非全校口径的审批人（如年级长）不得打开外年级的调课详情
    if not is_reviewer and d.get('applicant_uid') != uid:
        grades = swap_service.review_visible_grades(current_user)
        if grades is not None:
            sw = db.session.get(ScheduleSwap, swap_id)
            if swap_service.swap_grade(sw) not in grades:
                abort(403)
    # 审核人姓名（跨库解析，放在路由层避免服务层依赖 User）
    d['reviewer_name'] = None
    if d.get('reviewed_by'):
        from app.models import User
        u = db.session.get(User, d['reviewed_by'])
        d['reviewer_name'] = u.real_name if u else f'用户#{d["reviewed_by"]}'
    # 审批链路：把"经过谁"显式画出来（含每一级谁审的、什么时间、什么意见）
    steps = swap_service.approval_chain()
    cur = d.get('approval_step') or 0
    step_now = steps[cur] if (d.get('status') == 'pending' and cur < len(steps)) else None
    d['can_review'] = bool(step_now and (current_user.role == 'admin'
                                         or current_user.has_perm(step_now['perm'])))
    d['review_step_name'] = step_now['name'] if step_now else None
    return render_template('academic/swap_detail.html',
                           d=d, is_reviewer=is_reviewer,
                           is_owner=(d.get('applicant_uid') == uid),
                           status_map=SWAP_STATUS, weekday_names=WEEKDAY_NAMES,
                           chain=steps, cur_step=cur,
                           approvals=d.get('approvals') or [])


# ══════════════════════════════════════════════════════════════════════
# 审核 / 执行 / 撤销（JSON，前端 AJAX 调用；执行前检查状态防重复）
# ══════════════════════════════════════════════════════════════════════

def _reject_if_not_my_step(swap_id):
    """分级审批：不是当前这一级的审批人 → 直接返回 (None, err_json)。"""
    sw = db.session.get(ScheduleSwap, swap_id)
    if not sw:
        return None, (jsonify({'success': False, 'message': '调课记录不存在'}), 404)
    if not swap_service.can_review(sw, current_user):
        step = swap_service.review_step_of(sw)
        name = step['name'] if step else '（已结束）'
        return None, (jsonify({'success': False,
                               'message': f'当前环节是「{name}」，不轮到你审批'}), 403)
    return sw, None


@bp.route('/swap/<int:swap_id>/approve', methods=['POST'])
@login_required
def swap_approve(swap_id):
    """审批通过：按当前审批级推进（再次校验目标时段冲突）。

    2026-10-09 分级审批：能否审批取决于申请当前停在哪一级
    （一级=调课审批 / 二级=课表管理终审），不再一律要求课表管理权限。
    """
    sw, err = _reject_if_not_my_step(swap_id)
    if err:
        return err
    step = swap_service.review_step_of(sw)
    review_note = (request.form.get('review_note') or '').strip()
    ok, msg, sw = swap_service.review_swap(
        swap_id, current_user.id, current_user.real_name, 'approve', review_note)
    if not ok:
        db.session.rollback()
        return jsonify({'success': False, 'message': msg}), 400
    log_operation(current_user, '审批', '调课记录', swap_id,
                  f'通过（{step["name"] if step else "终审"}） #{swap_id}',
                  module='academic')
    return jsonify({'success': True, 'message': msg,
                    'data': sw.to_dict() if sw else None})


@bp.route('/swap/<int:swap_id>/reject', methods=['POST'])
@login_required
def swap_reject(swap_id):
    """驳回（必填理由，任意一级驳回即终止）。"""
    sw, err = _reject_if_not_my_step(swap_id)
    if err:
        return err
    review_note = (request.form.get('review_note') or '').strip()
    if not review_note:
        return jsonify({'success': False, 'message': '驳回必须填写理由'}), 400
    ok, msg, sw = swap_service.review_swap(
        swap_id, current_user.id, current_user.real_name, 'reject', review_note)
    if not ok:
        db.session.rollback()
        return jsonify({'success': False, 'message': msg}), 400
    log_operation(current_user, '审批', '调课记录', swap_id,
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
    allowed = swap_entry_authorizer(current_user)
    models = ({entry.id: entry for entry in ScheduleEntry.query.filter(
        ScheduleEntry.id.in_([item['id'] for item in entries])).all()}
              if entries else {})
    entries = [item for item in entries
               if item['id'] in models and allowed(models[item['id']])]
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
    if not swap_entry_authorizer(current_user)(entry):
        return jsonify({'success': False, 'message': '无权为该课程提交调课申请'}), 403
    slots = swap_service.get_available_slots(
        entry.term_schedule_id, entry.grade, entry.class_name, entry.teacher_uid,
        exclude_entry_id=entry.id, include_all=True)
    return jsonify({'success': True, 'message': 'ok', 'data': {
        'entry': entry.to_dict(), 'slots': slots,
    }})


@bp.route('/api/swap/list')
@login_required
def api_swap_list():
    """调课列表 JSON（前端表格异步刷新用）。"""
    _require_swap_access()
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
def api_swap_stats():
    """调课统计数据 JSON（供 ECharts 图表）。"""
    _require_swap_access()
    sched = swap_service.get_active_schedule()
    days = request.args.get('days', 30, type=int)
    data = swap_service.get_swap_stats(schedule_id=sched.id if sched else None,
                                       days=days,
                                       applicant_uid=None if _is_reviewer() else _applicant()[0])
    return jsonify({'success': True, 'message': 'ok', 'data': data})
