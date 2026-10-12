# -*- coding: utf-8 -*-
"""调课工具（2026-10-11 新增）：申请 / 统一调课 / 审批 / 执行 / 撤销。

权限口径与网页端完全一致，写操作尤其严格：
  · 提交申请      academic.swap       + swap_entry_scope（本人任教或负责班级）
  · 统一调课      academic.swap_bulk  + swap_entry_scope（逐条校验）
  · 一级审批      academic.swap_approve + swap_review_scope（轮到本级 + 本年级）
  · 终审/执行     academic.timetable  + swap_review_scope
  · 撤销本人申请  academic.swap       + 申请人本人
  · 撤销执行      academic.timetable

统一调课（academic.swap_bulk）是独立权限点，普通教师不具备——MCP 侧不得
用 academic.swap 放行批量调课，否则会绕过网页端的权限拆分。

关键安全约定：
  1. **申请/撤销只产生"待审核"记录，不直接改课表**；真正改课表的是"执行"，
     且执行前必须走完审批链（终审 approved）。AI 不能一步到位改课表。
  2. 审批/执行前复用网页端 can_review，防止跨年级、跨级审批。
  3. 服务层返回的 (ok, msg, obj) 一律转成 ValueError（中文提示）或正常结果，
     不把内部状态直接抛给模型。
"""
from datetime import date

from ..core import guards, respond, toolkit

_SWAP_FIELDS = ('id', 'swap_type', 'status', 'approval_step', 'applicant_uid',
                'applicant_name', 'new_weekday', 'new_period', 'new_room',
                'swap_date', 'is_permanent', 'reason', 'review_note',
                'created_at', 'reviewed_at', 'batch_id')
_ENTRY_FIELDS = ('id', 'grade', 'class_name', 'weekday', 'period_number',
                 'subject', 'teacher_uid', 'teacher_name', 'room')


def _parse_date(value):
    """YYYY-MM-DD → date；空串返回 None（临时调课才需要日期）。"""
    text = (value or '').strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except (TypeError, ValueError):
        raise ValueError(f'日期格式应为 YYYY-MM-DD，收到：{text}')


def _entry_or_raise(entry_id):
    from app.models.timetable import ScheduleEntry
    entry = ScheduleEntry.query.get(int(entry_id)) if entry_id else None
    if entry is None or entry.is_deleted:
        raise ValueError(f'课表条目不存在或已删除：{entry_id}（可用 list_swap_entries 查询）')
    return entry


def _swap_or_raise(swap_id):
    from app.models.timetable import ScheduleSwap
    swap = ScheduleSwap.query.get(int(swap_id)) if swap_id else None
    if swap is None:
        raise ValueError(f'调课记录不存在：{swap_id}')
    return swap


def _applicant_of(user):
    """登录账号 → 申请人 (uid, name)，与网页端 _applicant() 同口径。"""
    from app.modules.academic.services import teacher_service
    teacher = teacher_service.teacher_of_user(user)
    if teacher:
        return teacher.teacher_uid, teacher.name
    return (getattr(user, 'username', None) or str(user.id))[:16], user.real_name


def _ok_or_raise(result):
    """服务层 (ok, msg, obj) → obj；失败转 ValueError（中文提示）。"""
    ok, msg, obj = result
    if not ok:
        raise ValueError(msg)
    return obj


# ══════════════════════════════════════════════════════════════════════
# 读工具
# ══════════════════════════════════════════════════════════════════════

def _my_swaps(user, status):
    from app.modules.academic.services import swap_service
    uid, _ = _applicant_of(user)
    rows = swap_service.get_my_swaps(uid, status=status or None)
    return {'count': len(rows), 'swaps': respond.limit_rows(
        [respond.pick(s, _SWAP_FIELDS) for s in rows])}


def _pending(user, page, per_page):
    """待我审批的队列：复用网页端同样的两个收窄条件（审批级 + 年级范围）。"""
    from app.modules.academic.services import swap_service
    guards.swap_reviewable(user)   # 无审批权（含"只持有 academic.swap"）直接拒绝，
    #                                否则 get_swap_list 会以本人身份返回自己的申请
    is_reviewer = user.role == 'admin' or user.has_perm('academic.timetable')
    step = None
    if not is_reviewer:
        chain = swap_service.approval_chain()
        for i, st in enumerate(chain):
            if user.role == 'admin' or user.has_perm(st['perm']):
                step = i
                break
    visible = None if is_reviewer else swap_service.review_visible_grades(user)
    items, pagination = swap_service.get_swap_list(
        status='pending', page=max(1, int(page or 1)),
        per_page=max(1, min(int(per_page or 20), 100)),
        is_reviewer=is_reviewer, review_step=step, visible_grades=visible)
    return {'total': pagination.total, 'page': pagination.page,
            'pages': pagination.pages,
            'swaps': [respond.pick(s, _SWAP_FIELDS) for s in items]}


def _entries(user, grade, class_name, weekday, teacher_uid, limit):
    from app.modules.academic.services import swap_service
    from app.modules.academic.services.access_scope import swap_entry_authorizer
    sched = swap_service.get_active_schedule()
    if not sched:
        raise ValueError('尚未建立学期课表，无法查询可调课程')
    rows = swap_service.query_entries(
        schedule_id=sched.id, grade=grade or None, class_name=class_name or None,
        weekday=weekday or None, teacher_uid=teacher_uid or None,
        limit=max(1, min(int(limit or 200), 500)))
    # 只返回"我能申请调课"的条目，避免模型拿着无权条目去调 submit_swap 撞墙
    from app.models.timetable import ScheduleEntry
    allowed = swap_entry_authorizer(user)
    out = []
    for row in rows:
        entry = ScheduleEntry.query.get(row['id'])
        if entry and allowed(entry):
            out.append({k: row.get(k) for k in _ENTRY_FIELDS})
    return {'schedule_id': sched.id, 'schedule_name': sched.name,
            'count': len(out), 'entries': out}


def _slots(user, entry_id, weekday):
    from app.modules.academic.services import swap_service
    entry = _entry_or_raise(entry_id)
    guards.swap_entry_scope(user, entry)
    slots = swap_service.get_available_slots(
        entry.term_schedule_id, entry.grade, entry.class_name, entry.teacher_uid,
        weekday=weekday or None, exclude_entry_id=entry.id)
    return {'entry': respond.pick(entry, _ENTRY_FIELDS),
            'count': len(slots), 'slots': slots}


def _detail(user, swap_id):
    from app.modules.academic.services import swap_service
    swap = _swap_or_raise(swap_id)
    uid, _ = _applicant_of(user)
    is_reviewer = user.role == 'admin' or user.has_perm('academic.timetable')
    if not is_reviewer and swap.applicant_uid != uid:
        # 非审批人只能看自己的：复用网页端 _require_swap_access 的口径
        raise PermissionError('只能查看本人提交的调课申请')
    data = swap_service.get_swap_detail(swap_id)
    if not data:
        raise ValueError(f'调课记录不存在：{swap_id}')
    data['versions'] = respond.limit_rows(data.get('versions') or [])
    return data


# ══════════════════════════════════════════════════════════════════════
# 写工具
# ══════════════════════════════════════════════════════════════════════

def _apply(user, entry_id, new_weekday, new_period, reason, new_room,
           swap_date, is_permanent):
    """提交个人调课申请（只产生 pending 记录，不直接改课表）。"""
    from app.modules.academic.services import swap_service
    from app.utils.helpers import log_operation
    sched = swap_service.get_active_schedule()
    if not sched:
        raise ValueError('尚未建立学期课表，无法申请调课')
    entry = _entry_or_raise(entry_id)
    if entry.term_schedule_id != sched.id:
        raise ValueError('只能对当前学期课表中的课程申请调课')
    guards.swap_entry_scope(user, entry)          # 本人任教 / 负责班级
    if not (reason or '').strip():
        raise ValueError('请填写调课理由')
    uid, name = _applicant_of(user)
    target = _parse_date(swap_date)
    if not is_permanent and not target:
        raise ValueError('临时调课必须提供 swap_date（调到哪一天，YYYY-MM-DD）')
    swap = _ok_or_raise(swap_service.apply_swap(
        applicant_uid=uid, applicant_name=name, original_entry_id=entry.id,
        new_weekday=new_weekday, new_period=new_period,
        new_room=(new_room or '').strip() or None, swap_date=target,
        is_permanent=bool(is_permanent), reason=(reason or '').strip(),
        swap_type='personal', source_date=target))
    log_operation(user, '新增', '调课申请', swap.id,
                  f'{name} 个人调课 #{swap.id}（MCP）', module='academic')
    return {'created': True, 'swap': respond.pick(swap, _SWAP_FIELDS),
            'message': '调课申请已提交，状态为「待审核」，审批通过后还需执行才会更新课表'}


def _apply_bulk(user, entry_ids, new_weekday, new_period, reason, new_room,
                swap_date, is_permanent):
    """统一调课（整批）：逐条校验授权，任一条越权则整批拒绝。"""
    from app.models.timetable import ScheduleEntry
    from app.modules.academic.services import swap_service
    from app.modules.academic.services.access_scope import swap_entry_authorizer
    from app.utils.helpers import log_operation

    if not entry_ids:
        raise ValueError('entry_ids 不能为空')
    if len(entry_ids) > 100:
        raise ValueError('单次最多对 100 条课程做统一调课，请分批提交')
    if not (reason or '').strip():
        raise ValueError('请填写调课理由')

    # 先逐条校验"我能不能对这节课申请"，越权直接拒绝整批（避免部分写入）
    allowed = swap_entry_authorizer(user)
    entries = []
    for eid in entry_ids:
        entry = (ScheduleEntry.query.get(int(eid)) if str(eid).strip().isdigit()
                 else None)
        if entry is None or entry.is_deleted:
            raise ValueError(f'课表条目不存在或已删除：{eid}')
        if not allowed(entry):
            raise PermissionError(
                f'条目 #{eid}（{entry.grade}{entry.class_name} {entry.subject}）'
                f'不在你可申请的范围内，整批已拒绝')
        entries.append(entry)

    uid, name = _applicant_of(user)
    target = _parse_date(swap_date)
    if not is_permanent and not target:
        raise ValueError('临时调课必须提供 swap_date（YYYY-MM-DD）')
    ok, msg, result = swap_service.bulk_apply_swap(
        applicant_uid=uid, applicant_name=name,
        entry_ids=[e.id for e in entries], new_weekday=new_weekday,
        new_period=new_period, new_room=(new_room or '').strip() or None,
        swap_date=target, is_permanent=bool(is_permanent),
        reason=(reason or '').strip())
    if not ok:
        conflicts = (result or {}).get('conflicts') or []
        detail = '；'.join(f"#{c.get('entry_id')} {c.get('desc', '')}"
                           for c in conflicts[:5])
        raise ValueError(msg + (f'（冲突明细：{detail}）' if detail else ''))
    created = (result or {}).get('created') or 0
    log_operation(user, '新增', '调课申请', 0,
                  f'{name} 统一调课 {created} 门（MCP）', module='academic')
    return {'created': created, 'count': len(entries), 'message': msg,
            'next': '批次已提交，状态为「待审核」，审批通过后还需执行才会更新课表'}


def _cancel(user, swap_id):
    """撤销本人待审核的申请（带 batch_id 时整批撤销）。"""
    from app.models.timetable import ScheduleSwap
    from app.modules.academic.services import swap_service
    from app.utils.helpers import log_operation
    swap = _swap_or_raise(swap_id)
    uid, _ = _applicant_of(user)
    if swap.applicant_uid != uid:
        raise PermissionError('只能撤销本人提交的调课申请')
    result = swap_service.cancel_swap(swap_id, uid)
    updated = _ok_or_raise(result)
    updated = ScheduleSwap.query.get(int(swap_id)) or updated
    log_operation(user, '撤销', '调课记录', swap_id,
                  f'撤销 #{swap_id}（MCP）', module='academic')
    return {'cancelled': True, 'swap': respond.pick(updated, _SWAP_FIELDS),
            'message': '已撤销（整批申请会一起撤销），状态置为「已拒绝」'}


def _review(user, swap_id, action, review_note):
    """审批调课（approve / reject）：分级推进，整批一次处理。"""
    from app.modules.academic.services import swap_service
    from app.utils.helpers import log_operation
    if action not in ('approve', 'reject'):
        raise ValueError('action 只能是 approve 或 reject')
    # 准入闸：不具备任何一级审批资格直接拒绝（随后 can_review 再判"轮不轮到我"）
    guards.swap_reviewable(user)
    swap = _swap_or_raise(swap_id)
    guards.swap_review_scope(user, swap)          # 轮到本级 + 年级范围
    if action == 'reject' and not (review_note or '').strip():
        raise ValueError('驳回必须填写理由（review_note）')
    note = (review_note or '').strip()[:200]
    # 统一调课批次：一次审批整批推进，与网页端一致
    if getattr(swap, 'batch_id', None):
        result = swap_service.review_swap_batch(
            swap_id, user.id, user.real_name, action, note)
    else:
        result = swap_service.review_swap(
            swap_id, user.id, user.real_name, action, note)
    updated = _ok_or_raise(result)
    log_operation(user, '审批', '调课记录', swap_id,
                  f'{"通过" if action == "approve" else "驳回"} #{swap_id}（MCP）',
                  module='academic')
    return {'reviewed': True, 'action': action,
            'swap': respond.pick(updated, _SWAP_FIELDS),
            'message': '已处理；若还有下一审批级则状态仍为「待审核」，'
                       '终审通过后状态变为「已通过」，需执行才更新课表'}


def _execute(user, swap_id):
    """执行已通过的调课（真正改课表）。仅 approved 可执行。"""
    from app.modules.academic.services import swap_service
    from app.utils.helpers import log_operation
    swap = _swap_or_raise(swap_id)
    if getattr(swap, 'batch_id', None):
        # 整批执行：与网页端一致，逐条执行并汇总失败项
        siblings = swap_service.batch_pending_siblings(swap, status='approved')
        if not siblings:
            raise ValueError('该统一调课批次没有可执行的记录（需先通过终审）')
        done, fails = 0, []
        for s in siblings:
            ok, msg, _ = swap_service.execute_swap(
                s.id, user.id, user.real_name)
            if ok:
                done += 1
            else:
                fails.append(f'#{s.id}：{msg}')
        if not done:
            raise ValueError('；'.join(fails[:3]) or '执行失败')
        log_operation(user, '执行', '调课记录', swap_id,
                      f'执行统一调课批次 {done} 门（MCP）', module='academic')
        return {'executed': done, 'failed': len(fails),
                'failures': fails[:5],
                'message': f'已执行该批次 {done} 门课，课表已更新'}
    updated = _ok_or_raise(swap_service.execute_swap(
        swap_id, user.id, user.real_name))
    log_operation(user, '执行', '调课记录', swap_id,
                  f'执行调课 #{swap_id}（MCP）', module='academic')
    return {'executed': True, 'swap': respond.pick(updated, _SWAP_FIELDS),
            'message': '已执行，课表已更新；如调错了可调用 revoke_swap_execution 回退'}


def _revoke(user, swap_id):
    """撤销已执行的调课（回退课表）。仅 executed 可撤销。"""
    from app.modules.academic.services import swap_service
    from app.utils.helpers import log_operation
    _swap_or_raise(swap_id)
    updated = _ok_or_raise(swap_service.revoke_executed_swap(
        swap_id, user.id, user.real_name))
    log_operation(user, '撤销执行', '调课记录', swap_id,
                  f'撤销执行 #{swap_id}（MCP）', module='academic')
    return {'revoked': True, 'swap': respond.pick(updated, _SWAP_FIELDS),
            'message': '已撤销执行，课表已回退，记录回到「已通过」可重新执行'}


def register(server):
    """把本模块的工具注册到 MCP 服务。"""

    # ── 读 ────────────────────────────────────────────────────────────
    @server.tool(name='list_my_swaps',
                 description='查询我提交的调课申请；status 可填 pending/approved/'
                             'rejected/executed（留空为全部）')
    async def list_my_swaps(status: str = '') -> str:
        return await toolkit.db_call(lambda u: _my_swaps(u, status),
                                     tool='list_my_swaps',
                                     params={'status': status},
                                     perm='academic.swap')

    @server.tool(name='list_pending_swaps',
                 description='查询待我审批的调课队列（按审批级与年级范围自动收窄，'
                             '与网页端「调课审批」列表同口径）；需调课审批或课表管理权限')
    async def list_pending_swaps(page: int = 1, per_page: int = 20) -> str:
        return await toolkit.db_call(
            lambda u: _pending(u, page, per_page), tool='list_pending_swaps',
            params={'page': page, 'per_page': per_page},
            perm=('academic.swap_approve', 'academic.timetable'))

    @server.tool(name='list_swap_entries',
                 description='列出我可申请调课的课表条目（已按"本人任教/负责班级"过滤）；'
                             '可按 grade/class_name/weekday(1-7)/teacher_uid 筛选')
    async def list_swap_entries(grade: str = '', class_name: str = '',
                                weekday: int = 0, teacher_uid: str = '',
                                limit: int = 200) -> str:
        params = {'grade': grade, 'class_name': class_name, 'weekday': weekday}
        return await toolkit.db_call(
            lambda u: _entries(u, grade, class_name, weekday, teacher_uid, limit),
            tool='list_swap_entries', params=params, perm='academic.swap')

    @server.tool(name='get_swap_available_slots',
                 description='查询某节课可调到的空闲时段（已排除本班与授课教师的冲突）；'
                             'weekday 留空返回整周')
    async def get_swap_available_slots(entry_id: int, weekday: int = 0) -> str:
        return await toolkit.db_call(
            lambda u: _slots(u, entry_id, weekday),
            tool='get_swap_available_slots',
            params={'entry_id': entry_id, 'weekday': weekday},
            perm='academic.swap')

    @server.tool(name='get_swap_detail',
                 description='查询调课申请详情（含原/目标条目、审批记录、版本留痕）')
    async def get_swap_detail(swap_id: int) -> str:
        return await toolkit.db_call(lambda u: _detail(u, swap_id),
                                     tool='get_swap_detail',
                                     params={'swap_id': swap_id},
                                     perm='academic.swap')

    # ── 写 ────────────────────────────────────────────────────────────
    @server.tool(name='submit_swap_request',
                 description='【写操作】提交个人调课申请（仅生成「待审核」记录，'
                             '不直接改课表）。entry_id 从 list_swap_entries 取，'
                             'new_weekday 1-7、new_period 为节次；'
                             'is_permanent=false 时必须给 swap_date（YYYY-MM-DD）')
    async def submit_swap_request(entry_id: int, new_weekday: int,
                                  new_period: int, reason: str,
                                  new_room: str = '', swap_date: str = '',
                                  is_permanent: bool = False) -> str:
        params = {'entry_id': entry_id, 'new_weekday': new_weekday,
                  'new_period': new_period, 'new_room': new_room,
                  'swap_date': swap_date, 'is_permanent': is_permanent}
        return await toolkit.db_write(
            lambda u: _apply(u, entry_id, new_weekday, new_period, reason,
                             new_room, swap_date, is_permanent),
            tool='submit_swap_request', params=params, perm='academic.swap')

    @server.tool(name='submit_bulk_swap',
                 description='【写操作】统一调课（整批调到同一时段，需 academic.swap_bulk '
                             '权限，普通教师不具备）。entry_ids 为课表条目 id 数组，'
                             'new_weekday/new_period/new_room 至少给一项（留空沿用原值）；'
                             '任一条越权或冲突则整批拒绝')
    async def submit_bulk_swap(entry_ids: list, reason: str,
                               new_weekday: int = 0, new_period: int = 0,
                               new_room: str = '', swap_date: str = '',
                               is_permanent: bool = False) -> str:
        ids = [int(x) for x in (entry_ids or []) if str(x).strip().isdigit()]
        params = {'entry_ids': ids, 'new_weekday': new_weekday,
                  'new_period': new_period, 'new_room': new_room,
                  'swap_date': swap_date, 'is_permanent': is_permanent}
        return await toolkit.db_write(
            lambda u: _apply_bulk(u, ids, new_weekday or None, new_period or None,
                                  reason, new_room, swap_date, is_permanent),
            tool='submit_bulk_swap', params=params, perm='academic.swap_bulk')

    @server.tool(name='cancel_swap_request',
                 description='【写操作】撤销本人提交的待审核调课申请'
                             '（统一调课批次会整批撤销）')
    async def cancel_swap_request(swap_id: int) -> str:
        return await toolkit.db_write(lambda u: _cancel(u, swap_id),
                                      tool='cancel_swap_request',
                                      params={'swap_id': swap_id},
                                      perm='academic.swap')

    @server.tool(name='review_swap',
                 description='【写操作】审批调课：action=approve/reject，驳回必须填 '
                             'review_note。只能审"当前轮到自己这一级"且在本年级范围内的'
                             '申请（一级=调课审批、终审=课表管理）；统一调课批次一次审批整批推进')
    async def review_swap(swap_id: int, action: str, review_note: str = '') -> str:
        params = {'swap_id': swap_id, 'action': action}
        return await toolkit.db_write(
            lambda u: _review(u, swap_id, action, review_note),
            tool='review_swap', params=params,
            perm=('academic.swap_approve', 'academic.timetable'))

    @server.tool(name='execute_swap',
                 description='【写操作】执行已通过终审的调课（真正修改课表）。'
                             '统一调课批次会整批执行；调错了可用 revoke_swap_execution 回退')
    async def execute_swap(swap_id: int) -> str:
        return await toolkit.db_write(lambda u: _execute(u, swap_id),
                                      tool='execute_swap',
                                      params={'swap_id': swap_id},
                                      perm='academic.timetable')

    @server.tool(name='revoke_swap_execution',
                 description='【写操作】撤销已执行的调课并回退课表（恢复原课程、'
                             '移除临时安排）；统一调课批次整批回退，记录回到「已通过」')
    async def revoke_swap_execution(swap_id: int) -> str:
        return await toolkit.db_write(lambda u: _revoke(u, swap_id),
                                      tool='revoke_swap_execution',
                                      params={'swap_id': swap_id},
                                      perm='academic.timetable')
