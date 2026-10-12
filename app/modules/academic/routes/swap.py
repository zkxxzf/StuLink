# StuLink v1.19.47.24 2026-10-10
# 教务 · 调课管理（重构版，基于 timetable.db）：
#   个人调课 / 统一调课 / 审核 / 执行 / 撤销 / 详情 / 统计 / 联动 API
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import json
from datetime import date

from flask import (render_template, request, redirect, url_for, flash,
                   abort, jsonify)
from flask_login import login_required, current_user

from app.extensions import db
from app.models.timetable import (ScheduleEntry, ScheduleSwap, SWAP_STATUS,
                                  SWAP_TYPES, WEEKDAY_NAMES)
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


def _int_or_none(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _parse_swap_items(form):
    """解析申请表单的"多节明细"（items_json 优先，回退旧的单条字段）。

    返回 [{'entry_id', 'new_weekday', 'new_period', 'swap_date'}, ...]；
    缺项/非法项原样带过，由服务层统一给"第 N 条信息不完整"的提示。
    """
    raw = (form.get('items_json') or '').strip()
    if raw:
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return []
        rows = data.get('items') if isinstance(data, dict) else None
        items = []
        for it in (rows or []):
            if not isinstance(it, dict):
                continue
            items.append({
                'entry_id': _int_or_none(it.get('entry_id')),
                'new_weekday': _int_or_none(it.get('new_weekday')),
                'new_period': _int_or_none(it.get('new_period')),
                'swap_date': _parse_date(it.get('swap_date')),
            })
        return items
    # 兼容旧单条字段（旧书签表单 / 外部调用）
    eid = form.get('entry_id', type=int)
    if not eid:
        return []
    return [{
        'entry_id': eid,
        'new_weekday': form.get('new_weekday', type=int),
        'new_period': form.get('new_period', type=int),
        'swap_date': _parse_date(form.get('swap_date')),
    }]


def _flash_swap_conflicts(msg, result):
    """整批冲突明细回显（最多 3 条，别刷屏）。"""
    conflicts = (result or {}).get('conflicts') or []
    if not conflicts:
        flash(msg, 'danger')
        return
    detail = '；'.join(f'第{c.get("index") or "?"}条 {c.get("desc") or ""}'.strip()
                      for c in conflicts[:3])
    more = f'（共 {len(conflicts)} 条）' if len(conflicts) > 3 else ''
    flash(f'{msg}：{detail}{more}', 'danger')


# ══════════════════════════════════════════════════════════════════════
# 页面路由
# ══════════════════════════════════════════════════════════════════════

def _my_entries_of(sched, uid):
    """我的本学期课表条目 dict 列表（排除临时调课产生的条目）—— 三个申请页共用。"""
    temp_ids = swap_service._temp_target_ids(sched.id)
    rows = (ScheduleEntry.query
            .filter(ScheduleEntry.term_schedule_id == sched.id,
                    ScheduleEntry.teacher_uid == uid,
                    ScheduleEntry.is_deleted.is_(False))
            .order_by(ScheduleEntry.weekday, ScheduleEntry.period_number).all())
    return [e.to_dict() for e in rows if e.id not in temp_ids]


def _render_swap_page(template, active):
    """渲染三个申请页（普通调课 / 两人换课 / 请人代课）—— 同一套模板变量与 Tab。"""
    sched = swap_service.get_active_schedule()
    uid, name = _applicant()
    return render_template(
        template, sched=sched, schedule=sched, active=active,
        periods=swap_service.get_periods(sched.id) if sched else [],
        my_uid=uid, my_name=name, applicant_uid=uid, applicant_name=name,
        today=date.today().isoformat(),
        my_entries=_my_entries_of(sched, uid) if sched else [],
        chain=swap_service.approval_chain())


def _parse_json_items(form, required=()):
    """解析 `items_json`（{"items":[{...}]}）：校验必填键并把日期串转成 date。

    三页统一的多行提交格式；换课还要把 partner_entry_id / 日期规范成强类型。
    """
    raw = (form.get('items_json') or '').strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception:  # noqa: BLE001
        return []
    rows = (data.get('items') if isinstance(data, dict)
            else (data if isinstance(data, list) else []))
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if any(not row.get(k) for k in required):
            continue
        d = dict(row)
        for k in ('swap_date', 'partner_swap_date'):
            if k in d:
                d[k] = _parse_date(d[k])
        if d.get('partner_entry_id') not in (None, ''):
            d['partner_entry_id'] = int(d['partner_entry_id'])
        out.append(d)
    return out


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
                            can_bulk=current_user.has_perm('academic.swap_bulk'),
                           status_map=SWAP_STATUS, type_map=SWAP_TYPES,
                           weekday_names=WEEKDAY_NAMES,
                           chain=swap_service.approval_chain(),
                           my_review_step=review_step)


@bp.route('/swap/apply', methods=['GET', 'POST'])
@login_required
@perm_required('academic.swap')
def swap_apply():
    """个人调课申请：选原课程（可多节，各自指定目标时段）→ 填理由/日期/是否永久。

    2026-10-12：一次可提交多节 —— 明细走 `items_json`（服务层 apply_swap_multi
    共享 batch_id，整批一次审批/执行/撤销），同时兼容旧的单条字段。
    """
    sched = swap_service.get_active_schedule()
    if not sched:
        flash('尚未建立学期课表，无法申请调课', 'warning')
        return redirect(url_for('academic.swap_page'))

    if request.method == 'POST':
        uid, name = _applicant()
        reason = (request.form.get('reason') or '').strip()
        is_permanent = _bool_flag(request.form.get('is_permanent'))
        new_room = (request.form.get('new_room') or '').strip()
        # 2026-10-13（v1.19.47.24）：普通调课与「与人对调」合并 —— items 可带
        # partner_entry_id（有 = 那格有人，和对调；没有 = 空档，直接调）。
        items = _parse_json_items(request.form, required=('entry_id',))
        if not items:
            items = _parse_swap_items(request.form)   # 兼容旧的单条字段提交

        if not items:
            flash('请至少选择一节要调走的课', 'danger')
            return redirect(url_for('academic.swap_apply'))
        if not reason:
            flash('请填写调课理由', 'danger')
            return redirect(url_for('academic.swap_apply'))

        # 逐条：条目存在 / 未删 / 本学期 / 有权申请（与单条时代同一口径）
        allowed = swap_entry_authorizer(current_user)
        first_entry = None
        for it in items:
            entry = db.session.get(ScheduleEntry, it['entry_id']) if it.get('entry_id') else None
            if (not entry or entry.is_deleted or entry.term_schedule_id != sched.id
                    or not allowed(entry)):
                flash('你只能为本人任教课程或负责班级的课程提交申请', 'danger')
                return redirect(url_for('academic.swap_apply'))
            if first_entry is None:
                first_entry = entry

        # 调休/跨天：原课日期 + 那天上的是周几的课（默认=日期星期，可手改）
        source_date = _parse_date(request.form.get('source_date'))
        source_weekday = request.form.get('source_weekday', type=int)
        if not source_date:
            source_date = _parse_date(request.form.get('swap_date'))  # 兼容旧单条入参
        if source_date and not source_weekday:
            source_weekday = first_entry.weekday

        ok, msg, result = swap_service.apply_swap_batch(
            applicant_uid=uid, applicant_name=name, items=items,
            is_permanent=is_permanent, reason=reason, new_room=new_room or None,
            source_date=source_date, source_weekday=source_weekday)
        if not ok:
            db.session.rollback()
            _flash_swap_conflicts(msg, result)
            return redirect(url_for('academic.swap_apply'))
        log_operation(current_user, '新增', '调课申请', result.get('rep_id') or 0,
                      f'{name} 个人调课 {result.get("created", 1)} 节'
                      + (f'（批次 {str(result.get("batch_id"))[:8]}）'
                         if result.get('batch_id') else ''),
                      module='academic')
        flash(msg, 'success')
        return redirect(url_for('academic.swap_detail', swap_id=result['rep_id']))

    uid, name = _applicant()
    periods = swap_service.get_periods(sched.id)
    class_opts = _swap_class_options(sched.id)
    # 2026-10-12 换课：我的课程清单（供换课表单选「我的课」；排除临时调课条目）
    temp_ids = swap_service._temp_target_ids(sched.id)
    my_entries = (ScheduleEntry.query
                  .filter(ScheduleEntry.term_schedule_id == sched.id,
                          ScheduleEntry.teacher_uid == uid,
                          ScheduleEntry.is_deleted.is_(False))
                  .order_by(ScheduleEntry.weekday, ScheduleEntry.period_number).all())
    my_entries = [e.to_dict() for e in my_entries if e.id not in temp_ids]
    return render_template('academic/swap_apply.html',
                           schedule=sched, periods=periods, class_opts=class_opts,
                           weekdays=WEEKDAY_NAMES,
                           applicant_uid=uid, applicant_name=name,
                           today=date.today().isoformat(),
                           my_entries=my_entries,
                           chain=swap_service.approval_chain())


def _commit_exchange_batch(items, uid, name, reason, is_permanent,
                           kind, kind_name, back):
    """换课 / 代课批量提交的公共落库 + 日志 + 跳转（失败整批回滚并回 *back* 页）。"""
    ok, msg, result = swap_service.apply_exchange_batch(
        applicant_uid=uid, applicant_name=name, items=items,
        kind=kind, is_permanent=is_permanent, reason=reason)
    if not ok:
        db.session.rollback()
        flash(msg, 'danger')
        return redirect(url_for(back))
    rep_id = result.get('rep_id')
    log_operation(current_user, '新增', f'{kind_name}申请', rep_id or 0,
                  f'{name} {kind_name} {result.get("created", 1)} 节'
                  + (f'（批次 {str(result.get("batch_id"))[:8]}）'
                     if result.get('batch_id') else ''),
                  module='academic')
    flash(msg, 'success')
    return redirect(url_for('academic.swap_detail', swap_id=rep_id))


@bp.route('/swap/cover', methods=['GET', 'POST'])
@login_required
@perm_required('academic.swap')
def swap_cover():
    """请人代课（v1.19.47.24）：我的课请别的老师来上，**支持批量**（多人代多节）。

    时间 / 班级都不变，只换授课人；每条记录各自等代课老师确认。
    """
    sched = swap_service.get_active_schedule()
    if not sched:
        flash('尚未建立学期课表，无法申请代课', 'warning')
        return redirect(url_for('academic.swap_page'))
    if request.method == 'POST':
        uid, name = _applicant()
        reason = (request.form.get('reason') or '').strip()
        is_permanent = _bool_flag(request.form.get('is_permanent'))
        items = _parse_json_items(request.form, required=('entry_id', 'partner_uid'))
        if not items:
            flash('请至少完整填写一行代课（我的课 + 代课教师）', 'danger')
            return redirect(url_for('academic.swap_cover'))
        if not reason:
            flash('请填写代课理由', 'danger')
            return redirect(url_for('academic.swap_cover'))
        return _commit_exchange_batch(items, uid, name, reason, is_permanent,
                                      kind='cover', kind_name='代课',
                                      back='academic.swap_cover')
    return _render_swap_page('academic/swap_cover.html', 'cover')


@bp.route('/swap/<int:swap_id>/partner-respond', methods=['POST'])
@login_required
@perm_required('academic.swap')
def swap_partner_respond(swap_id):
    """换课「对方确认」：同意 / 拒绝（仅对方本人或管理员可操作）。"""
    sw = db.session.get(ScheduleSwap, swap_id)
    if not sw:
        abort(404)
    uid, _ = _applicant()
    if not (current_user.role == 'admin' or uid == sw.partner_uid):
        flash('只有对方本人可以确认该换课申请', 'danger')
        return redirect(url_for('academic.swap_detail', swap_id=swap_id))
    action = (request.form.get('action') or '').strip()
    note = (request.form.get('note') or '').strip()
    ok, msg, _ = swap_service.respond_exchange(
        swap_id, action, current_user.id,
        current_user.real_name or current_user.username or '', note)
    if ok:
        log_operation(current_user, '审核' if action == 'agree' else '驳回',
                      '换课确认', swap_id, msg, module='academic')
    flash(msg, 'success' if ok else 'danger')
    return redirect(url_for('academic.swap_detail', swap_id=swap_id))


@bp.route('/api/swap/partner-entries')
@login_required
@perm_required('academic.swap')
def api_swap_partner_entries():
    """某位教师本学期课表条目（换课选「对方的课」用；排除临时调课产生的条目）。"""
    uid = (request.args.get('uid') or '').strip()
    if not uid:
        return jsonify({'success': False, 'message': '缺少 uid 参数'}), 400
    sched = swap_service.get_active_schedule()
    if not sched:
        return jsonify({'success': True, 'message': 'ok', 'data': {'entries': []}})
    temp_ids = swap_service._temp_target_ids(sched.id)
    rows = (ScheduleEntry.query
            .filter(ScheduleEntry.term_schedule_id == sched.id,
                    ScheduleEntry.teacher_uid == uid,
                    ScheduleEntry.is_deleted.is_(False))
            .order_by(ScheduleEntry.weekday, ScheduleEntry.period_number).all())
    entries = [e.to_dict() for e in rows if e.id not in temp_ids]
    return jsonify({'success': True, 'message': 'ok',
                    'data': {'entries': entries, 'schedule_id': sched.id}})


@bp.route('/api/swap/partner-teachers')
@login_required
@perm_required('academic.swap')
def api_swap_partner_teachers():
    """本学期有课表条目的教师清单（换课选对象用）：uid / 姓名 / 节次数。"""
    sched = swap_service.get_active_schedule()
    if not sched:
        return jsonify({'success': True, 'message': 'ok', 'data': {'teachers': []}})
    rows = (ScheduleEntry.query
            .filter(ScheduleEntry.term_schedule_id == sched.id,
                    ScheduleEntry.teacher_uid.isnot(None),
                    ScheduleEntry.is_deleted.is_(False))
            .with_entities(ScheduleEntry.teacher_uid, ScheduleEntry.teacher_name)
            .all())
    my_uid, _ = _applicant()
    seen = {}
    for t_uid, t_name in rows:
        if not t_uid or t_uid == my_uid:
            continue
        d = seen.setdefault(t_uid, {'uid': t_uid, 'name': t_name or '', 'count': 0})
        d['count'] += 1
    teachers = sorted(seen.values(), key=lambda x: (x['name'] or '', x['uid']))
    return jsonify({'success': True, 'message': 'ok', 'data': {'teachers': teachers}})


@bp.route('/swap/bulk', methods=['GET', 'POST'])
@login_required
@perm_required('academic.swap_bulk')
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

        # 2026-10-13（v1.19.47.24）：两种目标模式 —— 统一落到同一格 / 整体平移 N 节
        mode = (request.form.get('mode') or 'target').strip()
        period_offset = request.form.get('period_offset', type=int) if mode == 'offset' else None
        ok, msg, result = swap_service.bulk_apply_swap(
            applicant_uid=uid, applicant_name=name, entry_ids=entry_ids,
            new_weekday=new_weekday, new_period=new_period, new_room=new_room or None,
            swap_date=swap_date, is_permanent=is_permanent, reason=reason,
            period_offset=period_offset)
        if not ok:
            db.session.rollback()
            return render_template('academic/swap_bulk.html',
                                   schedule=sched, class_opts=class_opts, periods=periods,
                                   weekdays=WEEKDAY_NAMES,
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
                           weekdays=WEEKDAY_NAMES,
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
    # 2026-10-12 换课：对方（partner）本人也要能打开详情（确认按钮在详情页）
    is_partner = bool(d.get('partner_uid') and d.get('partner_uid') == uid)
    if d.get('partner_entry_id'):
        _pe = db.session.get(ScheduleEntry, d['partner_entry_id'])
        d['partner_entry'] = _pe.to_dict() if _pe else None
    else:
        d['partner_entry'] = None
    # 一级审批人（年级长）也要能打开详情，否则点不进去审批
    if not (is_reviewer or _can_approve() or is_partner) and d.get('applicant_uid') != uid:
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
                           is_partner=is_partner,
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
    # 2026-10-10：统一调课批次 —— 一次审批，整批一起推进（不再逐条点）
    if getattr(sw, 'batch_id', None):
        ok, msg, sw = swap_service.review_swap_batch(
            swap_id, current_user.id, current_user.real_name, 'approve', review_note)
    else:
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
    # 2026-10-10：统一调课批次 —— 一次驳回，整批一起处理
    if getattr(sw, 'batch_id', None):
        ok, msg, sw = swap_service.review_swap_batch(
            swap_id, current_user.id, current_user.real_name, 'reject', review_note)
    else:
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
    """执行调课——真正修改课表（仅 approved 可执行，含幂等保护）。

    2026-10-10：统一调课批次 —— 点一次「执行」把整批 approved 记录一起执行。
    """
    sw0 = db.session.get(ScheduleSwap, swap_id)
    if sw0 and getattr(sw0, 'batch_id', None):
        sibs = swap_service.batch_pending_siblings(sw0, status='approved')
        if not sibs:
            return jsonify({'success': False, 'message': '该批次没有可执行的记录'}), 400
        done, fails = 0, []
        for s in sibs:
            ok, msg, _ = swap_service.execute_swap(
                s.id, current_user.id, current_user.real_name)
            if ok:
                done += 1
            else:
                fails.append(f'#{s.id}：{msg}')
        if not done:
            return jsonify({'success': False,
                            'message': '；'.join(fails[:3]) or '执行失败'}), 400
        log_operation(current_user, '执行', '调课记录', swap_id,
                      f'执行调课批次 {done} 门', module='academic')
        m = f'已执行该批次 {done} 门课'
        if fails:
            m += f'；{len(fails)} 条未执行：' + '；'.join(fails[:3])
        return jsonify({'success': True, 'message': m, 'data': None})
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


@bp.route('/swap/<int:swap_id>/revoke', methods=['POST'])
@login_required
@perm_required('academic.timetable')
def swap_revoke(swap_id):
    """撤销已执行的调课（回退课表改动）。

    2026-10-11 新增：针对"执行错了"的场景 —— 永久调课恢复原条目、临时调课删除当天
    安排；统一调课批次整批回退。撤销后记录回到「已通过」，可重新执行。
    权限与「执行」同级（academic.timetable），由教务/管理员操作。
    """
    ok, msg, sw = swap_service.revoke_executed_swap(
        swap_id, current_user.id, current_user.real_name)
    if not ok:
        db.session.rollback()
        return jsonify({'success': False, 'message': msg}), 400
    log_operation(current_user, '撤销执行', '调课记录', swap_id,
                  f'撤销执行 #{swap_id}', module='academic')
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
