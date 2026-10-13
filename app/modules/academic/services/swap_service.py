# StuLink v1.19.47.27 2026-10-10
# 调课服务（基于 timetable.db 新模型）：申请 / 批量 / 审核 / 执行 / 撤销 / 统计 / 实时生效
# Copyright (c) 2026 zkxxzf. Apache License 2.0
#
# 自包含原则：仅依赖 app.models.timetable 中的模型，不导入 schedule_service
# （并行开发，集成任务会统一去重）。冲突检测等辅助逻辑在本文件内以私有函数实现。
import json
import uuid
from datetime import datetime, date, timedelta

from sqlalchemy import func, select

from app.extensions import db
from app.models.timetable import (
    TermSchedule, PeriodDef, ScheduleEntry, ScheduleSwap, ScheduleVersion,
    SWAP_STATUS, SWAP_TYPES, WEEKDAY_NAMES, PERIOD_NUMBER_CEILING,
    EXCHANGE_KINDS, PARTNER_STATUS,
    exchange_content_text,
    get_default_periods,
)


def _notify_swap_result(sw, action, review_note='', count=1):
    """审核完成后通知申请人（静默失败，不影响主流程）。

    count > 1 表示批量调课（个人多节 / 统一调课批次）：整批只发**一条**通知
    （不然一次审批 260 门课会给申请人刷 260 条一样的消息）。
    """
    try:
        from app.modules.notifications.services.notification_service import notify_users
        applicant_uid = sw.applicant_uid
        if not applicant_uid:
            return
        subject = (f'调课批次（共 {count} 门课）' if count > 1
                   else f'调课申请（#{sw.id}）')
        if action == 'approve':
            title = '【调课审核】已通过'
            content = f'您提交的{subject}已被审核通过。请及时执行调课操作。'
        else:
            title = '【调课审核】已拒绝'
            content = (f'您提交的{subject}已被拒绝。'
                       f'理由：{review_note or "无"}')
        notify_users(
            uids=[applicant_uid],
            title=title,
            content=content,
            category='academic',
            biz_type='swap_result',
            biz_id=sw.id,
            link_url='/academic/swap',
        )
    except Exception as _e:  # noqa: BLE001
        # 2026-10-11 审计：通知失败不阻断主流程，但需留痕以便排查（原为静默 pass）
        logger.warning('调课结果通知发送失败（不阻断主流程）: %s', _e)


# ══════════════════════════════════════════════════════════════════════
# 学期与基础数据
# ══════════════════════════════════════════════════════════════════════

# get_active_schedule 由 schedule_common 提供（统一入口）
from app.modules.academic.services.schedule_common import get_active_schedule  # noqa: E402

# 保留 _get_active_schedule 别名供内部兼容
_get_active_schedule = get_active_schedule


def _week_of_date(schedule_id, target_date):
    """返回日期所属教学周（未配置学期起止日期时返回 None，此时不做周次过滤）。"""
    ts = db.session.get(TermSchedule, schedule_id) if schedule_id else None
    if not ts or not target_date:
        return None
    try:
        return ts.get_week_number(target_date)
    except Exception:  # noqa: BLE001
        return None


def _week_ctx(schedule_id, entry, is_permanent, swap_date=None):
    """冲突判定用的周次上下文 → (week_range, week)。

    - 永久调课：沿用原条目的周次范围（两者同范围，比对交集）；
    - 临时调课：只关心"调课日期所属教学周"，避免与别周的课误判冲突。
    """
    if is_permanent:
        return (entry.week_range, None)
    return (None, _week_of_date(schedule_id, swap_date))


# 周次交集判定 / 冲突挑选统一由 schedule_common 提供（与排课模块同一套口径）
from app.modules.academic.services.schedule_common import (  # noqa: E402
    pick_conflict as _pick_conflict,
    temp_target_ids as _temp_target_ids_common,
    week_ranges_overlap as _week_overlap,
)

# get_periods 由 schedule_common 提供（委托包装）
from app.modules.academic.services.schedule_common import get_periods as _gp_common  # noqa: E402

# 2026-10-10 架构审计修复：本文件原有静默 `except Exception: pass`，失败不可观测。
# 统一加 logger 记录 warning（仅留痕，不改变任何降级行为与返回值）。
import logging

logger = logging.getLogger(__name__)


def _get_periods(schedule_id):
    """节次列表（按 sort_order 升序，委托 schedule_common）。"""
    return _gp_common(schedule_id)


def get_periods(schedule_id):
    """公共包装：供路由层获取节次定义。"""
    return _get_periods(schedule_id)


def _period_map(schedule_id):
    """{period_number: PeriodDef} 映射。"""
    return {p.period_number: p for p in _get_periods(schedule_id)}


def _period_label(schedule_id, period_number, period_map=None):
    """节次显示文案，如 '第3节 10:00-10:45'。

    period_map（{period_number: PeriodDef}）可选：批量场景由调用方一次取好，
    避免逐行查 PeriodDef（2026-10-10 N+1 优化，行为不变）。
    """
    if not period_number:
        return None
    if period_map is not None:
        pd = period_map.get(period_number)
    else:
        pd = PeriodDef.query.filter_by(term_schedule_id=schedule_id,
                                       period_number=period_number).first()
    if not pd:
        return f'第{period_number}节'
    if pd.start_time and pd.end_time:
        return f'{pd.period_name} {pd.start_time}-{pd.end_time}'
    return pd.period_name


def _apply_exclude(q, exclude_entry_id=None, extra_exclude_ids=None):
    """把"不算冲突"的条目 id 从查询里排掉（自身条目 + 同批让位条目）。

    2026-10-12：个人调课支持一次提交多节（`apply_swap_multi`），整批一起生效。
    永久调课整批执行后，同批各条目的**原位置都会腾出来**，所以"自己的目标格正好
    是同一批另一条的原格"（整体后移一节、两节互换）不算冲突——提交 / 审批 / 执行
    三处必须用同一口径，否则提交能过、审批或执行又被拦下。
    """
    ids = [exclude_entry_id] if exclude_entry_id else []
    ids += [x for x in (extra_exclude_ids or []) if x]
    if ids:
        q = q.filter(ScheduleEntry.id.notin_(set(ids)))
    return q


def _check_class_conflict(schedule_id, grade, class_name, weekday,
                          period_number, exclude_entry_id=None,
                          week_range=None, week=None, extra_exclude_ids=None):
    """同班同时段冲突：返回冲突的 ScheduleEntry 或 None（过滤软删除）。

    week_range / week：按周次判断——无周次交集的条目不算冲突（单双周交替课）。
    extra_exclude_ids：同批"会让位"的原条目 id（见 _apply_exclude）。
    """
    q = ScheduleEntry.query.filter(
        ScheduleEntry.term_schedule_id == schedule_id,
        ScheduleEntry.grade == grade,
        ScheduleEntry.class_name == class_name,
        ScheduleEntry.weekday == weekday,
        ScheduleEntry.period_number == period_number,
        ScheduleEntry.is_deleted.is_(False),
    )
    q = _apply_exclude(q, exclude_entry_id, extra_exclude_ids)
    return _pick_conflict(q.all(), week_range=week_range, week=week)


def _check_teacher_conflict(schedule_id, teacher_uid, weekday, period_number,
                            exclude_entry_id=None, week_range=None, week=None,
                            extra_exclude_ids=None):
    """同教师同时段冲突：返回冲突的 ScheduleEntry 或 None（过滤软删除）。

    extra_exclude_ids：同批"会让位"的原条目 id（见 _apply_exclude）。
    """
    if not teacher_uid:
        return None
    q = ScheduleEntry.query.filter(
        ScheduleEntry.term_schedule_id == schedule_id,
        ScheduleEntry.teacher_uid == teacher_uid,
        ScheduleEntry.weekday == weekday,
        ScheduleEntry.period_number == period_number,
        ScheduleEntry.is_deleted.is_(False),
    )
    q = _apply_exclude(q, exclude_entry_id, extra_exclude_ids)
    return _pick_conflict(q.all(), week_range=week_range, week=week)


def _batch_yield_ids(sw_list):
    """同批"会让位"的原条目 id：永久调课整批执行后这些位置会腾出来。

    临时调课不动常规课表（原条目保持），因此不参与让位。
    """
    return [s.original_entry_id for s in (sw_list or [])
            if getattr(s, 'is_permanent', False) and s.original_entry_id]


def get_available_slots(schedule_id, grade, class_name, teacher_uid,
                        weekday=None, exclude_entry_id=None, include_all=False):
    """返回该班 + 该教师都空闲的目标时段列表（调课选择目标时段的核心体验）。

    - include_all=False（默认）：仅返回无冲突的可用时段（供选择/校验）。
    - include_all=True：返回全部 7×N 时段，每个带 class_conflict/teacher_conflict/
      available 标记与冲突描述（供前端网格 UI 禁用并标红冲突格）。
    每项为 dict：weekday / weekday_text / period_number / period_name /
      start_time / end_time / period_type / class_conflict / teacher_conflict /
      available / conflict_desc。
    """
    pmap = _period_map(schedule_id)
    # 该学期还没定义节次时，按**默认模板的节数**占位（不是上限：一天几节由学校自定义）
    period_numbers = (sorted(pmap.keys()) if pmap
                      else list(range(1, len(get_default_periods()) + 1)))
    weekdays = [weekday] if weekday else list(range(1, 8))

    # 本班占用（排除自身条目）
    cq = ScheduleEntry.query.filter(
        ScheduleEntry.term_schedule_id == schedule_id,
        ScheduleEntry.grade == grade,
        ScheduleEntry.class_name == class_name,
        ScheduleEntry.is_deleted.is_(False),
    )
    if exclude_entry_id:
        cq = cq.filter(ScheduleEntry.id != exclude_entry_id)
    class_busy = {(e.weekday, e.period_number): e for e in cq.all()}

    # 该教师占用（排除自身条目）
    teacher_busy = {}
    if teacher_uid:
        tq = ScheduleEntry.query.filter(
            ScheduleEntry.term_schedule_id == schedule_id,
            ScheduleEntry.teacher_uid == teacher_uid,
            ScheduleEntry.is_deleted.is_(False),
        )
        if exclude_entry_id:
            tq = tq.filter(ScheduleEntry.id != exclude_entry_id)
        teacher_busy = {(e.weekday, e.period_number): e for e in tq.all()}

    # 2026-10-13（v1.19.47.27）：临时调课产生的条目不能作为对调对象
    temp_ids = _temp_target_ids(schedule_id)
    result = []
    for w in weekdays:
        for p in period_numbers:
            ce = class_busy.get((w, p))
            te = teacher_busy.get((w, p))
            class_conflict = ce is not None
            # 本班这一格就是**我自己的另一节课**时，不算"教师冲突"（那正是自交换的对象）
            teacher_conflict = te is not None and (ce is None or te.id != ce.id)
            available = not class_conflict and not teacher_conflict
            # 本班这一格有课 → 不是"不能用"，而是"可以和它换"：
            #   他人任教 = 与同事对调（黄格，需对方确认）；本人任教 = 我自己的两节互换（紫格）
            swap_entry = None
            if ce and ce.teacher_uid and ce.id not in temp_ids:
                swap_entry = ce
            pd = pmap.get(p)
            conflict_desc = None
            if ce:
                conflict_desc = f'本班：{ce.subject}'
            elif te:
                conflict_desc = f'教师：{te.subject}（{te.grade}{te.class_name}）'
            slot = {
                'weekday': w,
                'weekday_text': WEEKDAY_NAMES.get(w, ''),
                'period_number': p,
                'period_name': pd.period_name if pd else f'第{p}节',
                'start_time': pd.start_time if pd else None,
                'end_time': pd.end_time if pd else None,
                'period_type': pd.period_type if pd else None,
                'class_conflict': class_conflict,
                'teacher_conflict': teacher_conflict,
                'available': available,
                'conflict_desc': conflict_desc,
                # 对调对象（绿=空档直接调；黄=与同事对调需确认；紫=我自己的两节互换）
                'swappable': swap_entry is not None,
                'swap_self': bool(swap_entry and swap_entry.teacher_uid == teacher_uid),
                'swap_entry_id': swap_entry.id if swap_entry else None,
                'swap_teacher_uid': swap_entry.teacher_uid if swap_entry else None,
                'swap_teacher_name': swap_entry.teacher_name if swap_entry else None,
                'swap_subject': swap_entry.subject if swap_entry else None,
                'swap_grade': swap_entry.grade if swap_entry else None,
                'swap_class_name': swap_entry.class_name if swap_entry else None,
            }
            if include_all or available:
                result.append(slot)
    return result


def get_class_options(schedule_id=None):
    """年级/班级选项，供申请表单与查课筛选级联下拉。

    2026-10-09：原来只从课表条目派生（课表没排到的班在下拉里就消失了）。现在统一走
    teaching_scope_service —— 以班级档案的"在用班级"为准，课表里出现过的班兜底，
    与学生管理 / 成绩管理共用同一套班级基础数据。

    返回 {'grades': [...], 'grade_classes': {grade: [class_name, ...]}}。
    """
    from app.modules.academic.services import teaching_scope_service
    if schedule_id is None:
        sched = _get_active_schedule()
        schedule_id = sched.id if sched else None
    return teaching_scope_service.class_candidates(schedule_id)


def query_entries(schedule_id=None, grade=None, class_name=None, weekday=None,
                  teacher_uid=None, limit=500):
    """按条件查询未删除的课表条目（供申请表单级联选择原课程）。"""
    if schedule_id is None:
        sched = _get_active_schedule()
        schedule_id = sched.id if sched else None
    q = ScheduleEntry.query.filter(ScheduleEntry.is_deleted.is_(False))
    if schedule_id:
        q = q.filter(ScheduleEntry.term_schedule_id == schedule_id)
    if grade:
        q = q.filter(ScheduleEntry.grade == grade)
    if class_name:
        q = q.filter(ScheduleEntry.class_name == class_name)
    if weekday:
        q = q.filter(ScheduleEntry.weekday == weekday)
    if teacher_uid:
        q = q.filter(ScheduleEntry.teacher_uid == teacher_uid)
    entries = (q.order_by(ScheduleEntry.weekday, ScheduleEntry.period_number,
                          ScheduleEntry.class_name).limit(limit).all())
    return [e.to_dict() for e in entries]


def get_entry_detail(entry_id):
    """单个课表条目详情（含节次文案），供申请表单展示原课程卡片。"""
    entry = db.session.get(ScheduleEntry, entry_id)
    if not entry or entry.is_deleted:
        return None
    d = entry.to_dict()
    d['period_label'] = _period_label(entry.term_schedule_id, entry.period_number)
    return d


# ══════════════════════════════════════════════════════════════════════
# 调课申请
# ══════════════════════════════════════════════════════════════════════

def apply_swap(applicant_uid, applicant_name, original_entry_id, new_weekday,
               new_period, new_room=None, swap_date=None, is_permanent=False,
               reason='', swap_type='personal', source_date=None,
               source_weekday=None):
    """创建个人调课申请（status='pending'）。

    swap_date   = 目标日期（调到哪天上）；
    new_weekday = 目标那天按"周几的课表"上课（调休时≠日期的星期，如周六上周三的课 → 3）；
    source_date / source_weekday = 原课所在日期与那天按周几上课（跨天/调休场景留痕）。
    返回 (success: bool, message: str, swap: ScheduleSwap|None)。
    """
    entry = db.session.get(ScheduleEntry, original_entry_id) if original_entry_id else None
    if not entry or entry.is_deleted:
        return False, '原课表条目不存在或已删除', None
    if not new_weekday or not new_period:
        return False, '请选择目标星期和节次', None
    if not (1 <= int(new_weekday) <= 7):
        return False, '目标星期无效（1-7）', None
    # 2026-10-10：原为 1..13 上限；现在只挡非法编号（该学期定义了几节就用到第几节）
    if not (1 <= int(new_period) <= PERIOD_NUMBER_CEILING):
        return False, '目标节次无效', None
    new_weekday, new_period = int(new_weekday), int(new_period)
    if is_permanent and new_weekday == entry.weekday and new_period == entry.period_number:
        return False, '目标时段与原时段相同，无需调课', None
    if not is_permanent and not swap_date:
        return False, '临时调课请选择具体日期', None

    schedule_id = entry.term_schedule_id
    week_range, week = _week_ctx(schedule_id, entry, is_permanent, swap_date)
    cc = _check_class_conflict(schedule_id, entry.grade, entry.class_name,
                               new_weekday, new_period, exclude_entry_id=entry.id,
                               week_range=week_range, week=week)
    if cc:
        return False, (f'目标时段本班已有课程（{WEEKDAY_NAMES.get(new_weekday, "")}'
                       f'第{new_period}节 {cc.subject}），存在冲突'), None
    tc = _check_teacher_conflict(schedule_id, entry.teacher_uid,
                                 new_weekday, new_period, exclude_entry_id=entry.id,
                                 week_range=week_range, week=week)
    if tc:
        return False, (f'目标时段授课教师已有课程（{tc.grade}{tc.class_name} '
                       f'{tc.subject}），存在冲突'), None

    swap = ScheduleSwap(
        term_schedule_id=schedule_id,
        swap_type=swap_type if swap_type in SWAP_TYPES else 'personal',
        original_entry_id=entry.id,
        applicant_uid=applicant_uid,
        applicant_name=applicant_name,
        new_weekday=new_weekday,
        new_period=new_period,
        new_room=(new_room or '').strip() or None,
        swap_date=swap_date,
        source_date=source_date,
        source_weekday=(int(source_weekday) if source_weekday else None),
        is_permanent=bool(is_permanent),
        reason=(reason or '').strip() or None,
        status='pending',
        approval_step=0,
    )
    try:
        db.session.add(swap)
        db.session.commit()
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        return False, f'提交失败：{e}', None
    return True, '调课申请已提交，待审核', swap


def _overlap_of(entries, week_range):
    """在已占位条目里找一条**周次有交集**的（单双周交替同格不算冲突）。"""
    for e in (entries or []):
        if _week_overlap(e.week_range, week_range):
            return e
    return None


def _dup_desc(prev, cur, twd, tpd):
    """同班同时段重复的说明。

    若两条课在**原课表里本来就占同一格**（同星期、同节次），说明这是历史脏数据
    （一个班同一时段排了两门课且周次重叠）；调课后依旧重复，这时直接点名并给出可操作
    建议，比"与所选条目 #NNN 目标时段相同"有用得多。
    """
    if prev.weekday == cur.weekday and prev.period_number == cur.period_number:
        return (f'原课表 {WEEKDAY_NAMES.get(cur.weekday, "")}第{cur.period_number}节'
                f' 本就有 #{prev.id} {prev.subject} 与 #{cur.id} {cur.subject} 两门课'
                f'（周次重叠），一起调到同一时段仍是重复排课；'
                f'请先在课表里删掉多余的那一条')
    return f'与所选条目 #{prev.id} 目标时段相同（同班冲突）'


def bulk_apply_swap(applicant_uid, applicant_name, entry_ids, new_weekday,
                    new_period, new_room=None, swap_date=None, is_permanent=False,
                    reason='', period_offset=None):
    """统一调课：一次把多个条目调到同一新时段，**或整体平移 N 节**。

    两种目标模式（v1.19.47.27 新增平移，专治"一整天的课一起挪"）：
      - 统一目标：全部落到同一个 (new_weekday, new_period)（不同班之间才可能）；
      - 整体平移：每条目落到「自己原节次 + period_offset」（可 +1 后移 / -1 前移），
        星期沿用各自原值或统一指定 new_weekday。

    逐条校验班级/教师冲突，并检测批量内部（同班/同教师调到同一时段）互相冲突。
    全部无冲突才批量创建；否则返回冲突明细且不写库。
    返回 (success, message, {'created': n, 'conflicts': [...]}）。
    """
    result = {'created': 0, 'conflicts': []}
    if not entry_ids:
        return False, '请至少选择一条课程', result
    # 2026-10-10：目标星期/节次改为选填——留空即沿用各自原值（只换教室、或只调日期）。
    # 但三者不能全空，否则这次调课没有任何实际变化。
    new_room = (new_room or '').strip() or None
    period_offset = int(period_offset) if period_offset not in (None, '') else None
    if period_offset is not None and not (-20 <= period_offset <= 20):
        return False, '节次平移值无效（只允许 ±20 节内）', result
    # 2026-10-13：平移也是一种"有实际变化"的调整方式，不能算"全空"
    if new_weekday is None and new_period is None and not new_room and not period_offset:
        return False, '请至少指定目标星期、目标节次、新教室或平移节数中的一项', result
    if new_weekday is not None and not (1 <= int(new_weekday) <= 7):
        return False, '目标星期无效', result
    if new_period is not None and not (1 <= int(new_period) <= PERIOD_NUMBER_CEILING):
        return False, '目标节次无效', result
    new_weekday = int(new_weekday) if new_weekday else None
    new_period = int(new_period) if new_period else None

    def _tgt_period(entry):
        """冲突检测用：该条目实际会落到第几节（平移按各自原节次 + offset）"""
        if period_offset:
            return (entry.period_number or 0) + period_offset
        return new_period or entry.period_number

    def _saved_period(entry):
        """落库用：留空仍写 NULL（执行时沿用原值），只有平移模式才写计算值"""
        return (entry.period_number + period_offset) if period_offset else new_period

    conflicts = []
    valid_entries = []
    schedule_id = None
    for eid in entry_ids:
        entry = db.session.get(ScheduleEntry, eid)
        if not entry or entry.is_deleted:
            conflicts.append({'entry_id': eid, 'desc': '条目不存在或已删除'})
            continue
        if schedule_id is None:
            schedule_id = entry.term_schedule_id
        elif entry.term_schedule_id != schedule_id:
            conflicts.append({'entry_id': eid, 'subject': entry.subject,
                              'class': f'{entry.grade}{entry.class_name}',
                              'desc': '跨学期条目不允许一起调课'})
            continue
        # 留空的维度沿用该条目原值，冲突检测也按「实际会落到哪一格」来判
        tgt_weekday = new_weekday or entry.weekday
        tgt_period = _tgt_period(entry)
        if not (1 <= tgt_period <= PERIOD_NUMBER_CEILING):
            conflicts.append({'entry_id': eid, 'subject': entry.subject,
                              'class': f'{entry.grade}{entry.class_name}',
                              'desc': '平移后节次超出范围，请调整平移值'})
            continue
        moved = (tgt_weekday != entry.weekday or tgt_period != entry.period_number)
        if is_permanent and not moved:
            # 永久调课且时段原地不动（只换教室）→ 无需时段冲突检测
            cc = tc = None
        else:
            wr, wk = _week_ctx(schedule_id, entry, is_permanent, swap_date)
            cc = _check_class_conflict(schedule_id, entry.grade, entry.class_name,
                                       tgt_weekday, tgt_period, exclude_entry_id=entry.id,
                                       week_range=wr, week=wk)
            tc = _check_teacher_conflict(schedule_id, entry.teacher_uid,
                                         tgt_weekday, tgt_period, exclude_entry_id=entry.id,
                                         week_range=wr, week=wk)
        if cc:
            conflicts.append({'entry_id': eid, 'subject': entry.subject,
                              'class': f'{entry.grade}{entry.class_name}',
                              'desc': f'目标时段本班已有 {cc.subject}'})
        elif tc:
            conflicts.append({'entry_id': eid, 'subject': entry.subject,
                              'class': f'{entry.grade}{entry.class_name}',
                              'desc': f'目标时段教师已有 {tc.grade}{tc.class_name} {tc.subject}'})
        else:
            valid_entries.append(entry)

    # 批量内部互相冲突：同一目标时段出现同班 / 同教师
    # （留空的维度按各自原值算，所以 key 必须带上目标星期与节次）
    # 2026-10-10：**按周次交集判定** —— 单周课与双周课落在同一格并不冲突（常见排课方式），
    # 老实现只看 (星期, 节次, 班级) 会把它们误判为冲突，导致"整天的课一起调到周六"直接失败。
    seen_class, seen_teacher, final_valid = {}, {}, []
    for entry in valid_entries:
        twd = new_weekday or entry.weekday
        tpd = _tgt_period(entry)
        ckey = (twd, tpd, entry.grade, entry.class_name)
        tkey = (twd, tpd, entry.teacher_uid) if entry.teacher_uid else None
        hit = _overlap_of(seen_class.get(ckey), entry.week_range)
        if hit:
            conflicts.append({'entry_id': entry.id, 'subject': entry.subject,
                              'class': f'{entry.grade}{entry.class_name}',
                              'desc': _dup_desc(hit, entry, twd, tpd)})
            continue
        hit_t = _overlap_of(seen_teacher.get(tkey), entry.week_range) if tkey else None
        if hit_t:
            conflicts.append({'entry_id': entry.id, 'subject': entry.subject,
                              'class': f'{entry.grade}{entry.class_name}',
                              'desc': f'与所选条目 #{hit_t.id} 目标时段相同（同教师冲突）'})
            continue
        seen_class.setdefault(ckey, []).append(entry)
        if tkey:
            seen_teacher.setdefault(tkey, []).append(entry)
        final_valid.append(entry)

    if conflicts:
        result['conflicts'] = conflicts
        return False, f'存在 {len(conflicts)} 条冲突，未创建任何调课', result

    created = 0
    # 2026-10-10：整批共享一个 batch_id —— 列表按批次展示、整批一次审批/执行
    batch_id = uuid.uuid4().hex
    try:
        for entry in final_valid:
            swap = ScheduleSwap(
                term_schedule_id=entry.term_schedule_id,
                swap_type='bulk',
                original_entry_id=entry.id,
                applicant_uid=applicant_uid,
                applicant_name=applicant_name,
                new_weekday=new_weekday,
                new_period=_saved_period(entry),
                new_room=new_room,
                swap_date=swap_date,
                is_permanent=bool(is_permanent),
                reason=(reason or '').strip() or None,
                status='pending',
                batch_id=batch_id,
            )
            db.session.add(swap)
            created += 1
        db.session.commit()
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        return False, f'创建失败：{e}', {'created': 0, 'conflicts': conflicts}
    return True, (f'已提交统一调课：{created} 门课合成 1 条记录，'
                  f'审批一次整批生效'), {
        'created': created, 'conflicts': [], 'batch_id': batch_id}


# ══════════════════════════════════════════════════════════════════════
# 个人调课·多节一起申请（2026-10-12 用户要求"多节课一块申请"）
# ══════════════════════════════════════════════════════════════════════

# 一次最多几节：个人一天也就几节，纯粹防手滑/滥用（不是业务上限）
MAX_SWAP_ITEMS = 30


def _batch_overlap(cands, week_range, week, is_permanent):
    """批内互斥用：在候选里找一条周次可比的条目。

    - 永久调课：按周次范围交集判（单双周交替同格不算冲突，与 bulk 同口径）；
    - 临时调课：按目标日期所属教学周编号判（学期未配起止日期时保守判冲突）。
    """
    for cand in (cands or []):
        entry, _twd, _tpd, _sdate, wr, wk = cand
        if is_permanent:
            if _week_overlap(wr, week_range):
                return entry
        elif week is None or wk is None or wk == week:
            return entry
    return None


def apply_swap_multi(applicant_uid, applicant_name, items, is_permanent=False,
                     reason='', source_date=None, source_weekday=None,
                     new_room=None, swap_type='personal'):
    """个人调课：一次提交多节，每节各自指定目标时段（共享 batch_id）。

    语义与单条 `apply_swap` **逐条一致**（永久=改常规课表；临时=只改指定日期），
    差别只在"一次提交多节"：复用统一调课的批次链路，列表里折叠成一条、
    审批/执行/撤销一次点完整批。

    items: [{'entry_id': int, 'new_weekday': int, 'new_period': int,
             'swap_date': date|None}, ...]（永久调课时 swap_date 忽略）
    **全成全败**：任一条冲突则整批不创建（与 bulk_apply_swap 同口径）。
    返回 (success, message, {'created': n, 'batch_id': str|None, 'conflicts': [...]})。
    """
    result = {'created': 0, 'batch_id': None, 'conflicts': []}
    items = list(items or [])
    if not items:
        return False, '请至少选择一节要调走的课', result
    if len(items) > MAX_SWAP_ITEMS:
        return False, f'一次最多提交 {MAX_SWAP_ITEMS} 节，请分批提交', result
    if not (reason or '').strip():
        return False, '请填写调课理由', result
    is_permanent = bool(is_permanent)

    def _rej(idx, entry, desc):
        result['conflicts'].append({
            'index': idx,
            'entry_id': entry.id if entry else None,
            'subject': entry.subject if entry else None,
            'class': f'{entry.grade}{entry.class_name}' if entry else None,
            'desc': desc,
        })

    # ── ① 逐条字段校验（学期必须一致：调课只在同一张课表内） ──
    seen_ids, prepared = set(), []
    schedule_id = None
    for idx, it in enumerate(items, 1):
        eid = it.get('entry_id')
        if not eid:
            _rej(idx, None, '缺少原课程条目')
            continue
        eid = int(eid)
        if eid in seen_ids:
            _rej(idx, None, '同一节课重复选择')
            continue
        seen_ids.add(eid)
        entry = db.session.get(ScheduleEntry, eid)
        if not entry or entry.is_deleted:
            _rej(idx, None, '原课表条目不存在或已删除')
            continue
        twd, tpd = it.get('new_weekday'), it.get('new_period')
        if not twd or not tpd:
            _rej(idx, entry, '未指定目标时段')
            continue
        twd, tpd = int(twd), int(tpd)
        if not (1 <= twd <= 7):
            _rej(idx, entry, '目标星期无效（1-7）')
            continue
        if not (1 <= tpd <= PERIOD_NUMBER_CEILING):
            _rej(idx, entry, '目标节次无效')
            continue
        if is_permanent and twd == entry.weekday and tpd == entry.period_number:
            _rej(idx, entry, '目标时段与原时段相同，无需调课')
            continue
        sdate = None if is_permanent else it.get('swap_date')
        if not is_permanent and not sdate:
            _rej(idx, entry, '临时调课请选择具体日期')
            continue
        if schedule_id is None:
            schedule_id = entry.term_schedule_id
        elif entry.term_schedule_id != schedule_id:
            _rej(idx, entry, '跨学期条目不允许一起调课')
            continue
        prepared.append((idx, entry, twd, tpd, sdate))

    if result['conflicts']:
        result['conflicts'].sort(key=lambda c: c.get('index') or 0)
        return False, f'{len(result["conflicts"])} 节信息不完整，整批未创建', result

    # ── ② 目标时段冲突（同批永久条目的原位置整批执行后会腾出来 → 不算冲突）──
    yield_ids = [e.id for (_i, e, _w, _p, _d) in prepared] if is_permanent else []
    stage = []
    for idx, entry, twd, tpd, sdate in prepared:
        wr, wk = _week_ctx(schedule_id, entry, is_permanent, sdate)
        wd_text = WEEKDAY_NAMES.get(twd, '')
        cc = _check_class_conflict(schedule_id, entry.grade, entry.class_name, twd, tpd,
                                   exclude_entry_id=entry.id, week_range=wr, week=wk,
                                   extra_exclude_ids=yield_ids)
        if cc:
            _rej(idx, entry, f'目标 {wd_text}第{tpd}节 本班已有课程（{cc.subject}）')
            continue
        tc = _check_teacher_conflict(schedule_id, entry.teacher_uid, twd, tpd,
                                     exclude_entry_id=entry.id, week_range=wr, week=wk,
                                     extra_exclude_ids=yield_ids)
        if tc:
            _rej(idx, entry, (f'目标 {wd_text}第{tpd}节 授课教师已有课程'
                              f'（{tc.grade}{tc.class_name} {tc.subject}）'))
            continue
        stage.append((entry, twd, tpd, sdate, wr, wk))

    # ── ③ 批内互斥：两节不能落到同一格（同班 / 同教师，按周次交集判）──
    seen_class, seen_teacher, final = {}, {}, []
    for cand in stage:
        entry, twd, tpd, _sdate, wr, wk = cand
        wd_text = WEEKDAY_NAMES.get(twd, '')
        hit = _batch_overlap(seen_class.get((twd, tpd, entry.grade, entry.class_name)),
                             wr, wk, is_permanent)
        if hit:
            _rej(0, entry, (f'与「{hit.subject} {hit.grade}{hit.class_name}」的目标时段重复'
                            f'（{wd_text}第{tpd}节）'))
            continue
        if entry.teacher_uid:
            hit_t = _batch_overlap(seen_teacher.get((twd, tpd, entry.teacher_uid)),
                                   wr, wk, is_permanent)
            if hit_t:
                _rej(0, entry, (f'与「{hit_t.subject} {hit_t.grade}{hit_t.class_name}」'
                                f'的目标时段同教师相撞（{wd_text}第{tpd}节）'))
                continue
        seen_class.setdefault((twd, tpd, entry.grade, entry.class_name), []).append(cand)
        if entry.teacher_uid:
            seen_teacher.setdefault((twd, tpd, entry.teacher_uid), []).append(cand)
        final.append(cand)

    if result['conflicts']:
        n = len(result['conflicts'])
        return False, f'{n} 节存在冲突，整批未创建（{result["conflicts"][0]["desc"]}）', result

    # ── ④ 创建：多条共享一个 batch_id（单条时不打批次号，保持旧行为） ──
    batch_id = uuid.uuid4().hex if len(final) > 1 else None
    created, first_swap = 0, None
    try:
        for entry, twd, tpd, sdate, _wr, _wk in final:
            sw = ScheduleSwap(
                term_schedule_id=entry.term_schedule_id,
                swap_type=swap_type if swap_type in SWAP_TYPES else 'personal',
                original_entry_id=entry.id,
                applicant_uid=applicant_uid,
                applicant_name=applicant_name,
                new_weekday=twd,
                new_period=tpd,
                new_room=(new_room or '').strip() or None,
                swap_date=sdate,
                source_date=source_date,
                source_weekday=(int(source_weekday) if source_weekday else None),
                is_permanent=is_permanent,
                reason=(reason or '').strip() or None,
                status='pending',
                approval_step=0,
                batch_id=batch_id,
            )
            db.session.add(sw)
            if first_swap is None:
                first_swap = sw
            created += 1
        db.session.commit()
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        return False, f'提交失败：{e}', {'created': 0, 'batch_id': None, 'conflicts': []}
    result['created'] = created
    result['batch_id'] = batch_id
    result['rep_id'] = first_swap.id if first_swap else None
    if created > 1:
        return True, f'已提交 {created} 节调课申请（整批一次审批 / 执行）', result
    return True, '调课申请已提交，待审核', result


def apply_swap_batch(applicant_uid, applicant_name, items, is_permanent=False,
                     reason='', new_room=None, source_date=None,
                     source_weekday=None):
    """调课 / 与人对调的**混合批量**提交（v1.19.47.27，用户口径 2026-10-13）。

    用户口径：普通调课与两人换课是**同一个动作**，差别只在目标格现在有没有人 ——
      - 目标格**空着** → 我把课挪过去（普通调课）；
      - 目标格**有人** → 与那位老师对调（多一步"对方确认"，各自确认自己的那条）。

    items: [{'entry_id': int, 'new_weekday': int, 'new_period': int,
             'swap_date': date|None,
             'partner_entry_id': int(可选；有值 = 与那节课对调),
             'swap_class': bool(可选；对调时是否连班级一起换，默认只换时间),
             'partner_swap_date': date(可选；对方那天，默认同 swap_date)}]

    一次提交可以混着来（几节调空档 + 几节和不同老师换）：共享 batch_id 整批审批 /
    执行 / 撤销，对调的那些行各自等自己的 partner 确认。**全成全败**。
    返回 (success, message, {'created', 'batch_id', 'rep_id', 'conflicts'})
    """
    result = {'created': 0, 'batch_id': None, 'rep_id': None, 'conflicts': []}
    items = list(items or [])
    if not items:
        return False, '请至少选择一节要调的课', result
    if len(items) > MAX_SWAP_ITEMS:
        return False, f'一次最多提交 {MAX_SWAP_ITEMS} 节，请分批提交', result
    if not (reason or '').strip():
        return False, '请填写调课理由', result
    is_permanent = bool(is_permanent)

    def _rej(idx, entry, desc):
        result['conflicts'].append({
            'index': idx,
            'entry_id': entry.id if entry else None,
            'subject': entry.subject if entry else None,
            'class': f'{entry.grade}{entry.class_name}' if entry else None,
            'desc': desc,
        })

    # ── ① 字段校验 + 分流（有没有 partner_entry_id 决定这条是"调"还是"换"）──
    seen_ids, prepared, schedule_id = set(), [], None
    for idx, it in enumerate(items, 1):
        eid = it.get('entry_id')
        entry = db.session.get(ScheduleEntry, int(eid)) if eid else None
        if not entry or entry.is_deleted:
            _rej(idx, None, '原课表条目不存在或已删除')
            continue
        if eid in seen_ids:
            _rej(idx, entry, '同一节课重复选择')
            continue
        seen_ids.add(eid)
        # 普通调课的权限（本人任教的课 / 自己负责班级的课）由路由层
        # swap_entry_authorizer 把关，这里不再重复收紧；
        # 只有「与人对调」才必须是授课教师本人（换课必须本人谈定）。
        if it.get('partner_entry_id') and (not entry.teacher_uid
                                           or entry.teacher_uid != applicant_uid):
            _rej(idx, entry, '与人对调只能由这节课的授课教师本人发起')
            continue
        twd, tpd = it.get('new_weekday'), it.get('new_period')
        if not twd or not tpd:
            _rej(idx, entry, '未指定目标时段')
            continue
        twd, tpd = int(twd), int(tpd)
        if not (1 <= twd <= 7) or not (1 <= tpd <= PERIOD_NUMBER_CEILING):
            _rej(idx, entry, '目标星期/节次无效')
            continue
        if is_permanent and twd == entry.weekday and tpd == entry.period_number:
            _rej(idx, entry, '目标时段与原时段相同，无需调课')
            continue
        sdate = None if is_permanent else it.get('swap_date')
        if not is_permanent and not sdate:
            _rej(idx, entry, '临时调课请选择具体日期')
            continue
        e2 = None
        if it.get('partner_entry_id'):
            e2 = db.session.get(ScheduleEntry, int(it['partner_entry_id']))
            if not e2 or e2.is_deleted:
                _rej(idx, entry, '对方的课程不存在或已删除')
                continue
            if e2.id == entry.id:
                _rej(idx, entry, '不能与同一节课对调')
                continue
            # 注：目标格若是**我自己的另一节课** → 允许，视为"我自己的两节互换"
            # （不需要任何人确认，执行时两条一起换位置）
            if e2.id in _temp_target_ids(e2.term_schedule_id):
                _rej(idx, entry, '对方这节课是临时调课产生的条目，不能作为对调对象')
                continue
        if schedule_id is None:
            schedule_id = entry.term_schedule_id
        elif entry.term_schedule_id != schedule_id:
            _rej(idx, entry, '跨学期条目不允许一起调课')
            continue
        dup = ScheduleSwap.query.filter(
            ScheduleSwap.original_entry_id == entry.id,
            ScheduleSwap.status == 'pending').first()
        if dup:
            _rej(idx, entry, f'这节课已有待处理申请（#{dup.id}）')
            continue
        prepared.append({
            'idx': idx, 'entry': entry, 'twd': twd, 'tpd': tpd, 'sdate': sdate,
            'e2': e2, 'swap_class': bool(it.get('swap_class')),
            # 「无需对方同意确认」默认勾选 → 前端不传即为 False（不需要确认）
            'need_confirm': bool(it.get('need_partner_confirm')),
            'partner_swap_date': (None if is_permanent
                                  else (it.get('partner_swap_date') or sdate)),
        })
    if result['conflicts']:
        result['conflicts'].sort(key=lambda c: c.get('index') or 0)
        return False, f'{len(result["conflicts"])} 节信息不完整，整批未创建', result

    # ── ② 冲突校验：本批参与条目整体腾出（换课行还要过换课专用校验）──
    all_yield = []
    for r in prepared:
        all_yield.append(r['entry'].id)
        if r['e2'] is not None and r['e2'].id not in all_yield:
            all_yield.append(r['e2'].id)
    for r in prepared:
        entry, twd, tpd, sdate = r['entry'], r['twd'], r['tpd'], r['sdate']
        wd_text = WEEKDAY_NAMES.get(twd, '')
        if r['e2'] is not None:                       # 与人对调
            errs = check_exchange_conflicts(
                schedule_id, entry, r['e2'], 'exchange', is_permanent, sdate,
                swap_time=True, swap_class=r['swap_class'],
                partner_swap_date=r['partner_swap_date'],
                extra_yield_ids=all_yield)
            if errs:
                _rej(r['idx'], entry, '；'.join(errs))
            continue
        wr, wk = _week_ctx(schedule_id, entry, is_permanent, sdate)
        cc = _check_class_conflict(schedule_id, entry.grade, entry.class_name,
                                   twd, tpd, exclude_entry_id=entry.id,
                                   week_range=wr, week=wk,
                                   extra_exclude_ids=all_yield)
        if cc:
            _rej(r['idx'], entry,
                 f'目标 {wd_text}第{tpd}节 本班已有课程（{cc.subject}）')
            continue
        tc = _check_teacher_conflict(schedule_id, entry.teacher_uid, twd, tpd,
                                     exclude_entry_id=entry.id, week_range=wr,
                                     week=wk, extra_exclude_ids=all_yield)
        if tc:
            _rej(r['idx'], entry,
                 f'目标 {wd_text}第{tpd}节 授课教师已有课程'
                 f'（{tc.grade}{tc.class_name} {tc.subject}）')
    if result['conflicts']:
        n = len(result['conflicts'])
        return False, (f'{n} 节存在冲突，整批未创建'
                       f'（{result["conflicts"][0]["desc"]}）'), result

    # ── ③ 创建（共享 batch_id；对调行各自等自己的 partner 确认）──
    batch_id = uuid.uuid4().hex if len(prepared) > 1 else None
    made, exch_rows = [], []
    try:
        for r in prepared:
            entry, e2 = r['entry'], r['e2']
            p_name = ''
            # 自交换：目标格是我自己的另一节课 → 不需要对方确认（也就没有"对方"）
            self_swap = bool(e2 is not None and e2.teacher_uid == applicant_uid)
            if e2 is not None:
                p_name, _u = _teacher_brief(e2.teacher_uid)
                p_name = p_name or (e2.teacher_name or '')
            sw = ScheduleSwap(
                term_schedule_id=entry.term_schedule_id,
                swap_type='exchange' if e2 is not None else 'personal',
                original_entry_id=entry.id,
                applicant_uid=applicant_uid,
                applicant_name=applicant_name or entry.teacher_name,
                new_weekday=r['twd'], new_period=r['tpd'],
                new_room=(new_room or '').strip() or None,
                swap_date=r['sdate'],
                source_date=source_date,
                source_weekday=(int(source_weekday) if source_weekday else None),
                is_permanent=is_permanent,
                reason=(reason or '').strip() or None,
                status='pending', approval_step=0, batch_id=batch_id,
                exchange_kind=('exchange' if e2 is not None else None),
                swap_time=(True if e2 is not None else None),
                swap_class=(r['swap_class'] if e2 is not None else None),
                partner_uid=(e2.teacher_uid if e2 is not None else None),
                partner_name=(p_name or None),
                partner_entry_id=(e2.id if e2 is not None else None),
                partner_swap_date=r['partner_swap_date'],
                need_partner_confirm=(False if (e2 is None or self_swap)
                                      else r['need_confirm']),
                partner_status=(None if (e2 is None or self_swap)
                                else ('pending' if r['need_confirm']
                                      else 'agreed')),
                partner_responded_at=(
                    datetime.now() if (e2 is not None and not self_swap
                                       and not r['need_confirm']) else None),
                partner_note=(
                    '（申请人选择无需对方确认）' if (e2 is not None
                                             and not self_swap
                                             and not r['need_confirm']) else None),
            )
            db.session.add(sw)
            made.append(sw)
            if e2 is not None:
                exch_rows.append(sw)
        db.session.commit()
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        return False, f'提交失败：{e}', {'created': 0, 'batch_id': None,
                                         'rep_id': None, 'conflicts': []}

    for sw in exch_rows:                    # 每条各自通知自己的 partner（自交换不用通知）
        if sw.partner_status == 'pending':
            _notify_exchange(sw, 'partner')              # 请求确认
        elif sw.partner_status == 'agreed':
            _notify_exchange(sw, 'partner', event='notify')   # 免确认：仅知会
    result['created'] = len(made)
    result['batch_id'] = batch_id
    result['rep_id'] = made[0].id if made else None
    waiting = [s for s in exch_rows if s.partner_status == 'pending']
    if len(made) > 1:
        tail = f'，其中 {len(waiting)} 节需对方确认' if waiting else ''
        return True, f'已提交 {len(made)} 节调课申请（整批一次审批 / 执行）{tail}', result
    if exch_rows:
        if made[0].partner_status == 'pending':
            return True, (f'调课申请已提交，等待 {made[0].partner_name or "对方"} 确认'
                          f'后进入教务审批'), result
        return True, '调课申请已提交（你自己的两节课互换），待审批', result
    return True, '调课申请已提交，待审核', result


# ══════════════════════════════════════════════════════════════════════
# 列表 / 详情
# ══════════════════════════════════════════════════════════════════════

def _build_entry_map(entry_ids):
    """一次取回一批 ScheduleEntry，返回 {id: 对象}（2026-10-10 N+1 优化）。"""
    ids = {i for i in entry_ids if i}
    if not ids:
        return {}
    rows = ScheduleEntry.query.filter(ScheduleEntry.id.in_(ids)).all()
    return {e.id: e for e in rows}


def _build_period_maps(swaps):
    """按学期一次性取节次定义，返回 {schedule_id: {period_number: PeriodDef}}。

    2026-10-10 N+1 优化：同一批 swap 不再逐行查 PeriodDef；同学期只取一次。
    """
    out = {}
    for sw in swaps:
        sid = sw.term_schedule_id
        if sid and sid not in out:
            out[sid] = _period_map(sid)
    return out


def _decorate_swap(sw, entry_map=None, period_maps=None):
    """把 ScheduleSwap 转为 dict 并附带原条目信息与目标时段文案。

    entry_map / period_maps 可选：批量场景由调用方一次取好（2026-10-10 N+1 优化）；
    不传时退回逐行查询，返回结构与字段完全一致。
    """
    d = sw.to_dict()
    if entry_map is not None:
        orig = entry_map.get(sw.original_entry_id) if sw.original_entry_id else None
    else:
        orig = db.session.get(ScheduleEntry, sw.original_entry_id) if sw.original_entry_id else None
    if orig:
        d['original_subject'] = orig.subject
        d['original_class'] = f'{orig.grade}{orig.class_name}'
        d['original_slot'] = f'{WEEKDAY_NAMES.get(orig.weekday, "")}第{orig.period_number}节'
        d['original_teacher'] = orig.teacher_name
        d['original_room'] = orig.room
    else:
        d.update({'original_subject': None, 'original_class': None,
                  'original_slot': None, 'original_teacher': None, 'original_room': None})
    # 2026-10-10：目标星期/节次可留空（沿用原值），展示时按「实际会落到哪一格」并标注沿用项
    target_wd = sw.new_weekday or (orig.weekday if orig else None)
    target_pd = sw.new_period or (orig.period_number if orig else None)
    d['target_slot'] = (f'{WEEKDAY_NAMES.get(target_wd, "")}第{target_pd}节'
                        if target_wd and target_pd else None)
    _keep = []
    if sw.new_weekday is None:
        _keep.append('星期')
    if sw.new_period is None:
        _keep.append('节次')
    d['target_keep_text'] = ('沿用原' + '与'.join(_keep)) if _keep else ''
    _pmap = period_maps.get(sw.term_schedule_id) if period_maps is not None else None
    d['target_period_label'] = _period_label(sw.term_schedule_id, target_pd, _pmap)
    d['is_temp'] = not sw.is_permanent
    return d


def batch_rep_filter():
    """列表折叠口径：**一次统一调课在列表里就是一条记录**。

    统一调课一次可能落下几十上百条 ScheduleSwap（共享 batch_id），它们的状态与
    审批级总是整批一起推进（review_swap_batch / 整批执行），所以列表、计数、徽章
    都按"代表行"来算：每个 (batch_id, status, approval_step) 只留最小 id 一条；
    没有 batch_id 的记录（个人调课 / 历史数据）照旧各占一行。

    供调课列表 / 工作台「我的调课」等只读场景复用，避免同一批重复刷屏。
    """
    reps = (db.session.query(func.min(ScheduleSwap.id))
            .filter(ScheduleSwap.batch_id.isnot(None))
            .group_by(ScheduleSwap.batch_id, ScheduleSwap.status,
                      ScheduleSwap.approval_step))
    return db.or_(ScheduleSwap.batch_id.is_(None), ScheduleSwap.id.in_(reps))


def get_swap_list(status=None, applicant_uid=None, schedule_id=None, page=1,
                  per_page=20, is_reviewer=False, swap_type=None,
                  review_step=None, visible_grades=None):
    """分页调课列表。普通教师只看自己的，审核者可看全部。

    review_step（分级审批）：一级审批人（如年级长）看不到全校全部，只看到
    "轮到自己这一级"的待审 + 自己提交的记录。
    2026-10-10：统一调课按批次折叠——同一次统一调课只出现一条（整批一次审批/执行）。
    visible_grades（数据范围）：一级审批人的可见年级集合，None 表示不限
    （管理员 / 课表终审）。非 None 时，待审队列只保留本年级的调课，
    避免年级长看到外年级申请。
    返回 (items: list[dict], pagination)。
    """
    q = ScheduleSwap.query.filter(batch_rep_filter())
    if schedule_id:
        q = q.filter(ScheduleSwap.term_schedule_id == schedule_id)
    if status and status in SWAP_STATUS:
        q = q.filter(ScheduleSwap.status == status)
    if swap_type and swap_type in SWAP_TYPES:
        q = q.filter(ScheduleSwap.swap_type == swap_type)
    if not is_reviewer and applicant_uid:
        if review_step is not None:
            my_step = db.and_(ScheduleSwap.status == 'pending',
                              ScheduleSwap.approval_step == review_step)
            if visible_grades is not None:
                # 一级审批人只看本年级：按原课表条目的年级收敛（同库子查询）
                my_step = db.and_(
                    my_step,
                    ScheduleSwap.original_entry_id.in_(
                        select(ScheduleEntry.id).where(
                            ScheduleEntry.grade.in_(list(visible_grades)))))
            q = q.filter(db.or_(ScheduleSwap.applicant_uid == applicant_uid,
                                my_step))
        else:
            q = q.filter(ScheduleSwap.applicant_uid == applicant_uid)
    q = q.order_by(ScheduleSwap.created_at.desc(), ScheduleSwap.id.desc())
    pagination = q.paginate(page=page, per_page=per_page, error_out=False)
    # 2026-10-10 N+1 优化：原条目一次 in_ 取回、节次按学期一次取好，再无逐行查询
    swaps = pagination.items
    entry_map = _build_entry_map([s.original_entry_id for s in swaps])
    period_maps = _build_period_maps(swaps)
    items = [_decorate_swap(sw, entry_map=entry_map, period_maps=period_maps)
             for sw in swaps]
    # 2026-10-10：统一调课批次规模（同 batch_id 的全部记录数，不分状态），
    # 折叠后的那一条据此显示「统一调课 · 共 N 门」
    batch_ids = [s.batch_id for s in swaps if s.batch_id]
    if batch_ids:
        counts = dict(db.session.query(ScheduleSwap.batch_id,
                                       db.func.count(ScheduleSwap.id))
                      .filter(ScheduleSwap.batch_id.in_(batch_ids))
                      .group_by(ScheduleSwap.batch_id).all())
    else:
        counts = {}
    for sw, item in zip(swaps, items):
        item['batch_count'] = counts.get(sw.batch_id, 1) if sw.batch_id else 1
    return items, pagination


def count_pending(schedule_id=None, applicant_uid=None):
    """待审核数量（用于列表页徽章）。

    2026-10-10：与列表同一口径——统一调课整批算 1 条（点一次就审完整批，
    不能按记录数吓唬审批人）。
    """
    q = ScheduleSwap.query.filter(ScheduleSwap.status == 'pending',
                                 batch_rep_filter())
    # v1.19.47.27：换课「待对方确认」还没到教务这一步，不计入待审数 / 侧栏徽章
    q = q.filter(db.or_(ScheduleSwap.partner_status.is_(None),
                        ScheduleSwap.partner_status == 'agreed'))
    if schedule_id:
        q = q.filter(ScheduleSwap.term_schedule_id == schedule_id)
    if applicant_uid:
        q = q.filter(ScheduleSwap.applicant_uid == applicant_uid)
    return q.count()



def get_partner_pending(partner_uid, limit=20):
    """待**我**（作为 partner）确认的换课 / 代课记录（v1.19.47.27，教师工作台用）。

    批量换课时每条记录各有自己的 partner → 这里**不做批次折叠**，逐条列出，
    让教师在工作台里就能逐条同意 / 拒绝（换课每条各自确认，互不影响）。
    """
    if not partner_uid:
        return []
    rows = (ScheduleSwap.query
            .filter(ScheduleSwap.partner_uid == partner_uid,
                    ScheduleSwap.partner_status == 'pending',
                    ScheduleSwap.status == 'pending')
            .order_by(ScheduleSwap.created_at.desc()).limit(limit).all())
    if not rows:
        return []

    def _txt(e):
        if not e:
            return ''
        return (f'{WEEKDAY_NAMES.get(e.weekday, "")}第{e.period_number}节 '
                f'{e.grade}{e.class_name} {e.subject}')

    ids = []
    for sw in rows:
        ids += [sw.original_entry_id, sw.partner_entry_id]
    entry_map = _build_entry_map([i for i in ids if i])
    out = []
    for sw in rows:
        d = sw.to_dict()
        d['original_text'] = _txt(entry_map.get(sw.original_entry_id)) or '（原条目已删除）'
        d['partner_entry_text'] = _txt(
            entry_map.get(sw.partner_entry_id)) if sw.partner_entry_id else ''
        out.append(d)
    return out

def get_swap_detail(swap_id):
    """调课详情：含原条目 to_dict、目标节次名称/时间、审核信息、版本记录。"""
    sw = db.session.get(ScheduleSwap, swap_id)
    if not sw:
        return None
    schedule_id = sw.term_schedule_id
    # 2026-10-10 N+1 优化：原/目标条目一次 in_ 取回；节次定义（原 + 目标）一次取好，
    # 不在 _decorate_swap 之后重复查 PeriodDef（原实现同表同学期共查 3 次）。
    entry_ids = [x for x in (sw.original_entry_id, sw.target_entry_id) if x]
    entry_map = _build_entry_map(entry_ids)
    period_map = _period_map(schedule_id) if schedule_id else {}
    d = _decorate_swap(sw, entry_map=entry_map,
                       period_maps={schedule_id: period_map} if schedule_id else {})
    orig = entry_map.get(sw.original_entry_id) if sw.original_entry_id else None
    d['original_entry'] = orig.to_dict() if orig else None
    tgt = entry_map.get(sw.target_entry_id) if sw.target_entry_id else None
    d['target_entry'] = tgt.to_dict() if tgt else None
    # 原/目标节次定义
    opd = period_map.get(orig.period_number) if orig else None
    d['original_period'] = opd.to_dict() if opd else None
    _tgt_pd = sw.new_period or (orig.period_number if orig else None)
    npd = period_map.get(_tgt_pd) if _tgt_pd else None
    d['target_period'] = npd.to_dict() if npd else None
    # 版本记录（原条目 + 目标条目）
    versions = []
    if entry_ids:
        vq = (ScheduleVersion.query
              .filter(ScheduleVersion.entry_id.in_(entry_ids))
              .order_by(ScheduleVersion.operated_at.desc(), ScheduleVersion.id.desc()))
        versions = [v.to_dict() for v in vq.all()]
    d['versions'] = versions
    # 2026-10-10：统一调课批次 —— 详情页要把"这批一共调了哪几门课"列清楚
    # （列表里整批只占一条，具体条目在这里看）
    d['is_batch'] = bool(getattr(sw, 'batch_id', None))
    batch_members = []
    if d['is_batch']:
        sibs = (ScheduleSwap.query
                .filter(ScheduleSwap.batch_id == sw.batch_id)
                .order_by(ScheduleSwap.id).all())
        smap = _build_entry_map([s.original_entry_id for s in sibs])
        for s in sibs:
            o = smap.get(s.original_entry_id) if s.original_entry_id else None
            twd = s.new_weekday or (o.weekday if o else None)
            tpd = s.new_period or (o.period_number if o else None)
            keep = []
            if s.new_weekday is None:
                keep.append('星期')
            if s.new_period is None:
                keep.append('节次')
            batch_members.append({
                'id': s.id,
                'subject': o.subject if o else None,
                'class': f'{o.grade}{o.class_name}' if o else None,
                'teacher': o.teacher_name if o else None,
                'original_slot': (f'{WEEKDAY_NAMES.get(o.weekday, "")}第{o.period_number}节'
                                  if o else None),
                'target_slot': (f'{WEEKDAY_NAMES.get(twd, "")}第{tpd}节'
                                if twd and tpd else None),
                'keep_text': ('沿用原' + '与'.join(keep)) if keep else '',
                'status': s.status,
                'status_text': SWAP_STATUS.get(s.status, s.status),
                'is_current': s.id == sw.id,
                'target_entry_id': s.target_entry_id,
            })
        batch_members.sort(key=lambda m: ((m['class'] or ''), (m['original_slot'] or '')))
    d['batch_members'] = batch_members
    d['batch_count'] = len(batch_members) or 1
    return d


def get_my_swaps(applicant_uid, status=None):
    """我的调课申请（列表 dict）。"""
    q = ScheduleSwap.query.filter(ScheduleSwap.applicant_uid == applicant_uid)
    if status and status in SWAP_STATUS:
        q = q.filter(ScheduleSwap.status == status)
    swaps = q.order_by(ScheduleSwap.created_at.desc(), ScheduleSwap.id.desc()).all()
    # 2026-10-10 N+1 优化：原条目一次 in_ 取回、节次按学期一次取好
    entry_map = _build_entry_map([s.original_entry_id for s in swaps])
    period_maps = _build_period_maps(swaps)
    return [_decorate_swap(sw, entry_map=entry_map, period_maps=period_maps)
            for sw in swaps]


# ══════════════════════════════════════════════════════════════════════
# 审核 / 执行 / 撤销
# ══════════════════════════════════════════════════════════════════════

# ── 分级审批链路（2026-10-09）──────────────────────────────────────────
# 默认两级：① 调课审批（academic.swap_approve，年级/教务干事）→
#          ② 课表管理（academic.timetable，终审 + 执行，教务主任）
# 想改成一级审批：在 system_setting 里加 swap_approval_mode = one（只走课表管理）。
APPROVAL_STEPS = [
    {'key': 'swap_approve', 'name': '调课审批', 'perm': 'academic.swap_approve'},
    {'key': 'timetable', 'name': '课表管理（终审 / 执行）', 'perm': 'academic.timetable'},
]


def get_approval_mode():
    """调课审批级数：'two'（默认两级）或 'one'（仅课表管理一级）。

    2026-10-10 N+1 优化：同一请求内结果缓存到 flask.g，列表页逐行判权限与模板
    多次调用不再重复查 system_setting；无请求上下文（CLI/测试）时退回直查，行为不变。
    """
    from flask import g, has_request_context
    ctx = has_request_context()
    if ctx:
        cached = getattr(g, '_swap_approval_mode', None)
        if cached is not None:
            return cached
    mode = 'two'
    try:
        from app.models.system_setting import SystemSetting
        row = SystemSetting.query.filter_by(key='swap_approval_mode').first()
        v = (row.value or '').strip().lower() if row and row.value else ''
        if v in ('one', 'two'):
            mode = v
    except Exception as _e:  # noqa: BLE001  设置表不可用时退回默认，不影响审批
        logger.warning('swap_service.py::get_approval_mode 异常（已降级处理）: %s', _e)
    if ctx:
        g._swap_approval_mode = mode
    return mode


def approval_chain():
    """当前生效的审批链路（step 从 0 起）。"""
    steps = list(APPROVAL_STEPS)
    if get_approval_mode() == 'one':
        steps = [s for s in steps if s['key'] == 'timetable'] or steps
    return steps


def review_step_of(sw):
    """该申请当前停在哪一级（仅 pending 有意义）。"""
    steps = approval_chain()
    cur = sw.approval_step or 0
    return steps[cur] if cur < len(steps) else None


def swap_grade(sw):
    """调课记录对应课表条目的年级（数据范围判定用；取不到返回 None）。"""
    if not sw or not sw.original_entry_id:
        return None
    entry = db.session.get(ScheduleEntry, sw.original_entry_id)
    return entry.grade if entry else None


def review_visible_grades(user):
    """审批人可见的年级集合；None 表示不限（管理员 / 课表终审为全校口径）。

    分级审批下，持「调课审批」的年级长只应审**本年级**（与 permission_map 的
    说明一致），不能跨年级查看/审批；管理员与课表终审仍为全校范围。
    """
    if not user or getattr(user, 'role', None) == 'admin':
        return None
    if user.has_perm('academic.timetable'):
        return None
    from app.modules.academic.services.access_scope import visible_academic_grades
    return visible_academic_grades(user)


def can_review(sw, user):
    """当前用户能否审批这条申请的"当前这一级"（管理员可审任何一级）。"""
    if not sw or sw.status != 'pending':
        return False
    # v1.19.47.27 换课：对方确认前不进教务审批队列（避免"教务批了、对方还没同意"）
    if getattr(sw, 'partner_status', None) == 'pending':
        return False
    step = review_step_of(sw)
    if not step:
        return False
    if not (getattr(user, 'role', None) == 'admin' or user.has_perm(step['perm'])):
        return False
    # 数据范围：非全校口径的审批人（如年级长）只能审本年级的调课
    grades = review_visible_grades(user)
    if grades is None:
        return True
    grade = swap_grade(sw)
    return bool(grade) and grade in grades


def review_swap(swap_id, reviewer_id, reviewer_name, action, review_note='',
                user=None):
    """审核调课：action='approve'/'reject'。仅 pending 可审，按审批级逐级推进。

    - approve：当前级通过 → 还有下一级就停在下一级（仍 pending）；最后一级通过 → status='approved'。
    - reject：任意一级驳回 → status='rejected'。
    每步都写进 approvals_json（谁、哪一级、什么时间、什么意见），详情页直接展示。
    approve 时再次校验目标时段冲突（申请后课表可能已变）。
    返回 (success, message, swap)。
    """
    sw = db.session.get(ScheduleSwap, swap_id)
    if not sw:
        return False, '调课记录不存在', None
    if sw.status != 'pending':
        return False, f'当前状态为「{SWAP_STATUS.get(sw.status, sw.status)}」，仅待审核记录可审核', None
    if action not in ('approve', 'reject'):
        return False, '无效的审核操作', None

    if action == 'reject':
        if not (review_note or '').strip():
            return False, '驳回必须填写理由', None
        step = review_step_of(sw) or APPROVAL_STEPS[-1]
        sw.status = 'rejected'
        sw.add_approval(sw.approval_step or 0, step['key'], step['name'],
                        reviewer_id, reviewer_name, 'reject', '驳回',
                        review_note.strip()[:200])
        sw.reviewed_by = reviewer_id
        sw.review_note = review_note.strip()[:200]
        sw.reviewed_at = datetime.now()
        try:
            db.session.commit()
        except Exception as e:  # noqa: BLE001
            db.session.rollback()
            return False, f'操作失败：{e}', None
        # 通知申请人：已驳回
        _notify_swap_result(sw, 'reject', review_note=sw.review_note)
        return True, f'已在「{step["name"]}」环节驳回该调课申请', sw

    # approve
    entry = db.session.get(ScheduleEntry, sw.original_entry_id) if sw.original_entry_id else None
    if not entry or entry.is_deleted:
        return False, '原课表条目不存在或已删除，无法通过', None
    # v1.19.47.27 换课：复检按"对调后"口径（普通调课仍按新时段单点复检）
    if getattr(sw, 'exchange_kind', None):
        e2 = db.session.get(ScheduleEntry, sw.partner_entry_id) if sw.partner_entry_id else None
        if sw.exchange_kind != 'cover' and (not e2 or e2.is_deleted):
            return False, '对方的课程条目已不存在，无法通过', None
        errs = check_exchange_conflicts(
            sw.term_schedule_id, entry, e2, sw.exchange_kind,
            sw.is_permanent, sw.swap_date, cover_uid=sw.partner_uid,
            swap_time=getattr(sw, 'swap_time', True),
            swap_class=getattr(sw, 'swap_class', True),
            partner_swap_date=getattr(sw, 'partner_swap_date', None))
        if errs:
            return False, '换课复检未通过（课表已变化）：' + '；'.join(errs), None
    elif sw.new_weekday and sw.new_period:
        wr, wk = _week_ctx(sw.term_schedule_id, entry, sw.is_permanent, sw.swap_date)
        cc = _check_class_conflict(sw.term_schedule_id, entry.grade, entry.class_name,
                                   sw.new_weekday, sw.new_period, exclude_entry_id=entry.id,
                                   week_range=wr, week=wk)
        if cc:
            return False, f'目标时段本班已有课程（{cc.subject}），课表已变化，无法通过', None
        tc = _check_teacher_conflict(sw.term_schedule_id, entry.teacher_uid,
                                     sw.new_weekday, sw.new_period, exclude_entry_id=entry.id,
                                     week_range=wr, week=wk)
        if tc:
            return False, (f'目标时段教师已有课程（{tc.grade}{tc.class_name} '
                           f'{tc.subject}），无法通过'), None
    steps = approval_chain()
    cur = sw.approval_step or 0
    step = steps[cur] if cur < len(steps) else steps[-1]
    sw.add_approval(cur, step['key'], step['name'], reviewer_id, reviewer_name,
                    'approve', '通过', (review_note or '').strip()[:200])
    sw.reviewed_by = reviewer_id
    sw.review_note = (review_note or '').strip()[:200] or None
    sw.reviewed_at = datetime.now()
    if cur + 1 < len(steps):
        # 还有下一级：保持 pending，推进到下一级
        sw.approval_step = cur + 1
        nxt = steps[cur + 1]['name']
        try:
            db.session.commit()
        except Exception as e:  # noqa: BLE001
            db.session.rollback()
            return False, f'操作失败：{e}', None
        _notify_swap_result(sw, 'approve')
        return True, f'「{step["name"]}」已通过，待「{nxt}」审批', sw
    sw.status = 'approved'
    try:
        db.session.commit()
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        return False, f'操作失败：{e}', None
    # 通知申请人：已通过
    _notify_swap_result(sw, 'approve')
    return True, '审批流程已完成（终审通过），可执行调课', sw


def _snapshot_str(entry):
    """条目快照（委托 schedule_common）。"""
    from app.modules.academic.services.schedule_common import snapshot_entry
    return snapshot_entry(entry)


# ── 统一调课批次（2026-10-10）────────────────────────────────────────
def batch_pending_siblings(sw, status='pending'):
    """同一 batch_id 下指定状态的记录（含 sw 自己）。无 batch_id 时返回 [sw]。

    统一调课一次生成多条记录并共享 batch_id，审批时整批一起推进，
    避免几十条一个个点。
    """
    if not getattr(sw, 'batch_id', None):
        return [sw]
    return (ScheduleSwap.query
            .filter(ScheduleSwap.batch_id == sw.batch_id,
                    ScheduleSwap.status == status)
            .order_by(ScheduleSwap.id).all())


def review_swap_batch(swap_id, reviewer_id, reviewer_name, action, review_note=''):
    """调课批次审批（个人多节 / 统一调课共用）：同批次内所有 pending 记录执行同一动作。

    单事务、全成全败；返回 (success, message, 代表记录)。
    """
    sw = db.session.get(ScheduleSwap, swap_id)
    if not sw:
        return False, '调课记录不存在', None
    if sw.status != 'pending':
        return False, f'当前状态为「{SWAP_STATUS.get(sw.status, sw.status)}」，仅待审核记录可审核', None
    if action not in ('approve', 'reject'):
        return False, '无效的审核操作', None
    if action == 'reject' and not (review_note or '').strip():
        return False, '驳回必须填写理由', None

    sibs = batch_pending_siblings(sw)
    if not sibs:
        return False, '没有待审核的记录', None
    # 2026-10-12（v1.19.47.27）批量换课 / 代课：批内每条都有自己的 partner，
    # 通过前必须**全部答复过** —— 避免教务单方面把别人的课批掉。
    if action == 'approve':
        waiting = [s for s in sibs
                   if s.exchange_kind and s.partner_status == 'pending']
        if waiting:
            names = sorted({(s.partner_name or '对方老师') for s in waiting})
            return False, (f'还有 {len(waiting)} 条记录等待对方确认（'
                           f'{"、".join(names)}），请等他们答复后再整批审批'), None
    steps = approval_chain()
    # 2026-10-12：整批永久调课一起生效 —— 同批各原位置都会腾出，复核冲突时必须
    # 与提交/执行同一口径（否则"整体后移一节 / 两节互换"会在审批这一步被误拦）。
    yield_ids = _batch_yield_ids(sibs)
    done = []
    for s in sibs:
        if action == 'reject':
            step = review_step_of(s) or APPROVAL_STEPS[-1]
            s.status = 'rejected'
            s.add_approval(s.approval_step or 0, step['key'], step['name'], reviewer_id,
                           reviewer_name, 'reject', '驳回', review_note.strip()[:200])
            s.reviewed_by = reviewer_id
            s.review_note = review_note.strip()[:200]
            s.reviewed_at = datetime.now()
            done.append(s)
            continue
        # approve：逐条再次校验目标时段（课表可能已变）
        entry = db.session.get(ScheduleEntry, s.original_entry_id) if s.original_entry_id else None
        if not entry or entry.is_deleted:
            db.session.rollback()
            return False, f'#{s.id} 的原课表条目不存在或已删除，整批未通过', None
        if s.new_weekday and s.new_period:
            wr, wk = _week_ctx(s.term_schedule_id, entry, s.is_permanent, s.swap_date)
            cc = _check_class_conflict(s.term_schedule_id, entry.grade, entry.class_name,
                                       s.new_weekday, s.new_period, exclude_entry_id=entry.id,
                                       week_range=wr, week=wk, extra_exclude_ids=yield_ids)
            if cc:
                db.session.rollback()
                return False, f'#{s.id} 目标时段本班已有课程（{cc.subject}），课表已变化，整批未通过', None
            tc = _check_teacher_conflict(s.term_schedule_id, entry.teacher_uid,
                                         s.new_weekday, s.new_period, exclude_entry_id=entry.id,
                                         week_range=wr, week=wk, extra_exclude_ids=yield_ids)
            if tc:
                db.session.rollback()
                return False, (f'#{s.id} 目标时段教师已有课程（{tc.grade}{tc.class_name} '
                               f'{tc.subject}），整批未通过'), None
        cur = s.approval_step or 0
        step = steps[cur] if cur < len(steps) else steps[-1]
        s.add_approval(cur, step['key'], step['name'], reviewer_id, reviewer_name,
                       'approve', '通过', (review_note or '').strip()[:200])
        s.reviewed_by = reviewer_id
        s.review_note = (review_note or '').strip()[:200] or None
        s.reviewed_at = datetime.now()
        if cur + 1 < len(steps):
            s.approval_step = cur + 1
        else:
            s.status = 'approved'
        done.append(s)
    try:
        db.session.commit()
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        return False, f'操作失败：{e}', None
    # 整批只通知一条（260 门课发 260 条通知 = 另一种"审批死了"）
    if done:
        _notify_swap_result(done[0], action, review_note=review_note,
                            count=len(done))
    n = len(done)
    if action == 'reject':
        return True, f'已驳回该调课批次（{n} 门课）', done[0]
    return True, f'该调课批次已通过（{n} 门课）', done[0]


def execute_swap(swap_id, operator_id, operator_name):
    """执行调课——真正修改课表的地方。仅 approved 可执行；重复执行有幂等保护。

    - 永久调课：快照原条目→软删除原条目→新建 swap 条目→写两条版本→target_entry_id 指向新条目。
    - 临时调课：常规课表原条目保持不变；新建带日期的 swap 条目记录当天实际安排。
    全过程单事务，异常 rollback。返回 (success, message, swap)。
    """
    sw = db.session.get(ScheduleSwap, swap_id)
    if not sw:
        return False, '调课记录不存在', None
    if sw.status == 'executed':
        return False, '该调课已执行，请勿重复操作', None
    if sw.status != 'approved':
        return False, (f'当前状态为「{SWAP_STATUS.get(sw.status, sw.status)}」，'
                       f'仅已通过的调课可执行'), None
    entry = db.session.get(ScheduleEntry, sw.original_entry_id) if sw.original_entry_id else None
    if not entry:
        return False, '原课表条目不存在', None
    if entry.is_deleted:
        return False, '原课表条目已被删除，无法执行', None

    try:
        if sw.is_permanent:
            return _execute_permanent(sw, entry, operator_id, operator_name)
        return _execute_temp(sw, entry, operator_id, operator_name)
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        return False, f'执行失败：{e}', None


def _execute_permanent(sw, entry, operator_id, operator_name):
    """永久调课执行（在 execute_swap 的事务内调用）。

    2026-10-10：目标星期/节次可留空（只换教室/只调日期），留空的维度沿用原条目值。
    2026-10-12（v1.19.47.27）：换课（exchange_kind）走专用执行分支。
    """
    if getattr(sw, 'exchange_kind', None):
        return _execute_exchange_permanent(sw, entry, operator_id, operator_name)
    target_weekday = sw.new_weekday or entry.weekday
    target_period = sw.new_period or entry.period_number
    # 2026-10-12：批次内"整体后移 / 两节互换" —— 同批其它 approved 永久条目的原位置
    # 本次整批执行后都会腾出，逐条执行时不能再当占用（临时调课不动常规课表，故不排除）。
    yield_ids = _batch_yield_ids(
        [s for s in batch_pending_siblings(sw, status='approved') if s.id != sw.id]
        if getattr(sw, 'batch_id', None) else [])
    cc = _check_class_conflict(sw.term_schedule_id, entry.grade, entry.class_name,
                               target_weekday, target_period, exclude_entry_id=entry.id,
                               week_range=entry.week_range, extra_exclude_ids=yield_ids)
    if cc:
        db.session.rollback()
        return False, f'目标时段本班已有课程（{cc.subject}），无法执行', None
    tc = _check_teacher_conflict(sw.term_schedule_id, entry.teacher_uid,
                                 target_weekday, target_period, exclude_entry_id=entry.id,
                                 week_range=entry.week_range, extra_exclude_ids=yield_ids)
    if tc:
        db.session.rollback()
        return False, (f'目标时段教师已有课程（{tc.grade}{tc.class_name} '
                       f'{tc.subject}），无法执行'), None

    # 1) 原条目旧数据快照 → version(action='swap')
    remark = (f'调课#{sw.id}：{sw.reason or "无原因"}'
              f'（{WEEKDAY_NAMES.get(entry.weekday, "")}第{entry.period_number}节 → '
              f'{WEEKDAY_NAMES.get(target_weekday, "")}第{target_period}节）')[:200]
    db.session.add(ScheduleVersion(
        term_schedule_id=sw.term_schedule_id, entry_id=entry.id, action='swap',
        snapshot_json=_snapshot_str(entry), operator_id=operator_id,
        operator_name=operator_name, remark=remark, operated_at=datetime.now(),
    ))
    # 2) 软删除原条目（保留追溯）
    entry.is_deleted = True
    entry.updated_at = datetime.now()
    # 3) 新建 swap 条目
    new_entry = ScheduleEntry(
        term_schedule_id=entry.term_schedule_id, grade=entry.grade,
        class_name=entry.class_name, weekday=target_weekday,
        period_number=target_period, week_range=entry.week_range,
        subject=entry.subject, teacher_uid=entry.teacher_uid,
        teacher_name=entry.teacher_name, room=(sw.new_room or entry.room),
        entry_type='swap', original_entry_id=entry.id,
        note=f'由调课#{sw.id}产生', is_deleted=False,
    )
    db.session.add(new_entry)
    db.session.flush()  # 取得新条目 id
    # 4) 新条目 version(action='create')
    db.session.add(ScheduleVersion(
        term_schedule_id=sw.term_schedule_id, entry_id=new_entry.id, action='create',
        snapshot_json=_snapshot_str(new_entry), operator_id=operator_id,
        operator_name=operator_name,
        remark=f'调课#{sw.id}生成的新课表条目（原条目#{entry.id}）'[:200],
        operated_at=datetime.now(),
    ))
    # 5) 更新 swap
    sw.target_entry_id = new_entry.id
    sw.status = 'executed'
    db.session.commit()
    return True, '调课已执行，课表已更新', sw


def _execute_temp(sw, entry, operator_id, operator_name):
    """临时调课执行（在 execute_swap 的事务内调用）。

    2026-10-12（v1.19.47.27）：换课（exchange_kind）走专用执行分支。
    """
    if getattr(sw, 'exchange_kind', None):
        return _execute_exchange_temp(sw, entry, operator_id, operator_name)
    swap_date = sw.swap_date
    if not swap_date:
        db.session.rollback()
        return False, '临时调课缺少具体日期，无法执行', None
    # 2026-10-13 审计修复：目标星期统一取 sw.new_weekday（与提交 apply_swap /
    # 终审 review_swap 的冲突校验同口径），仅在未填时回退到日期实际星期。
    # 原来这里写死 swap_date.isoweekday()，导致"调休时按周三课表上课"这类
    # new_weekday≠日期星期 的场景，审批校验与实际落位不是同一格。
    target_weekday = sw.new_weekday or swap_date.isoweekday()
    target_period = sw.new_period or entry.period_number
    target_week = _week_of_date(sw.term_schedule_id, swap_date)  # 只校验调课当周
    cc = _check_class_conflict(sw.term_schedule_id, entry.grade, entry.class_name,
                               target_weekday, target_period, exclude_entry_id=entry.id,
                               week=target_week)
    if cc:
        db.session.rollback()
        return False, f'{swap_date} 目标时段本班已有课程（{cc.subject}），无法执行', None
    tc = _check_teacher_conflict(sw.term_schedule_id, entry.teacher_uid,
                                 target_weekday, target_period, exclude_entry_id=entry.id,
                                 week=target_week)
    if tc:
        db.session.rollback()
        return False, (f'{swap_date} 目标时段教师已有课程（{tc.grade}{tc.class_name} '
                       f'{tc.subject}），无法执行'), None

    # 原条目当天信息写入版本（常规课表不变，仅记录当天调整）
    remark = (f'临时调课#{sw.id} {swap_date}：原第{entry.period_number}节 → '
              f'第{target_period}节（{sw.reason or "无原因"}，常规课表不变）')[:200]
    db.session.add(ScheduleVersion(
        term_schedule_id=sw.term_schedule_id, entry_id=entry.id, action='swap',
        snapshot_json=_snapshot_str(entry), operator_id=operator_id,
        operator_name=operator_name, remark=remark, operated_at=datetime.now(),
    ))
    # 新建带日期的临时条目（原条目保持 is_deleted=False）
    temp_entry = ScheduleEntry(
        term_schedule_id=entry.term_schedule_id, grade=entry.grade,
        class_name=entry.class_name, weekday=target_weekday,
        period_number=target_period, week_range=entry.week_range,
        subject=entry.subject, teacher_uid=entry.teacher_uid,
        teacher_name=entry.teacher_name, room=(sw.new_room or entry.room),
        entry_type='swap', original_entry_id=entry.id,
        note=f'临时调课 {swap_date.strftime("%Y-%m-%d")}', is_deleted=False,
    )
    db.session.add(temp_entry)
    db.session.flush()
    db.session.add(ScheduleVersion(
        term_schedule_id=sw.term_schedule_id, entry_id=temp_entry.id, action='create',
        snapshot_json=_snapshot_str(temp_entry), operator_id=operator_id,
        operator_name=operator_name,
        remark=f'临时调课#{sw.id}生成（{swap_date}）'[:200], operated_at=datetime.now(),
    ))
    sw.target_entry_id = temp_entry.id
    sw.status = 'executed'
    db.session.commit()
    return True, f'临时调课已执行（{swap_date}），常规课表保持不变', sw


def cancel_swap(swap_id, applicant_uid):
    """申请人撤销自己的 pending 申请（标记 rejected + review_note='申请人撤销'）。

    2026-10-10：统一调课按"一条记录"处理 —— 带 batch_id 时整批 pending 一起撤销。
    """
    sw = db.session.get(ScheduleSwap, swap_id)
    if not sw:
        return False, '调课记录不存在', None
    if sw.applicant_uid != applicant_uid:
        return False, '只能撤销自己的调课申请', None
    if sw.status != 'pending':
        return False, (f'当前状态为「{SWAP_STATUS.get(sw.status, sw.status)}」，'
                       f'仅待审核的申请可撤销'), None
    targets = batch_pending_siblings(sw) if getattr(sw, 'batch_id', None) else [sw]
    for s in targets:
        s.status = 'rejected'
        s.review_note = '申请人撤销'
        s.reviewed_at = datetime.now()
    try:
        db.session.commit()
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        return False, f'撤销失败：{e}', None
    if len(targets) > 1:
        return True, f'已撤销该调课批次（{len(targets)} 门课）', sw
    return True, '已撤销该调课申请', sw


def _revoke_one(sw, operator_id, operator_name):
    """回退单条已执行调课（在 revoke_executed_swap 的事务内调用）。

    - 撤销本次执行生成的条目（target_entry_id 指向的 swap 条目）→ 软删除；
    - 永久调课：恢复被软删除的原条目；临时调课：原条目本就未动，无需恢复；
    - 每条写一条 action='revoke' 的版本留痕；
    - 清空 target_entry_id，状态回到「已通过」以便重新执行。
    """
    if sw.target_entry_id:
        new_entry = db.session.get(ScheduleEntry, sw.target_entry_id)
        if new_entry and not new_entry.is_deleted:
            new_entry.is_deleted = True
            new_entry.updated_at = datetime.now()
            db.session.add(ScheduleVersion(
                term_schedule_id=sw.term_schedule_id, entry_id=new_entry.id,
                action='revoke', snapshot_json=_snapshot_str(new_entry),
                operator_id=operator_id, operator_name=operator_name,
                remark=f'撤销调课#{sw.id}执行：移除该次生成的课表条目'[:200],
                operated_at=datetime.now(),
            ))
    if sw.is_permanent and sw.original_entry_id:
        entry = db.session.get(ScheduleEntry, sw.original_entry_id)
        if entry and entry.is_deleted:
            entry.is_deleted = False
            entry.updated_at = datetime.now()
            db.session.add(ScheduleVersion(
                term_schedule_id=sw.term_schedule_id, entry_id=entry.id,
                action='revoke', snapshot_json=_snapshot_str(entry),
                operator_id=operator_id, operator_name=operator_name,
                remark=f'撤销调课#{sw.id}执行：恢复原课表条目'[:200],
                operated_at=datetime.now(),
            ))
    # 2026-10-12（v1.19.47.27）换课：对方侧条目同样回退（删对方当天条目 / 恢复对方原条目）
    if getattr(sw, 'partner_target_entry_id', None):
        p_new = db.session.get(ScheduleEntry, sw.partner_target_entry_id)
        if p_new and not p_new.is_deleted:
            p_new.is_deleted = True
            p_new.updated_at = datetime.now()
            db.session.add(ScheduleVersion(
                term_schedule_id=sw.term_schedule_id, entry_id=p_new.id,
                action='revoke', snapshot_json=_snapshot_str(p_new),
                operator_id=operator_id, operator_name=operator_name,
                remark=f'撤销换课#{sw.id}执行：移除对方侧条目'[:200],
                operated_at=datetime.now(),
            ))
    if sw.is_permanent and getattr(sw, 'partner_entry_id', None):
        p_entry = db.session.get(ScheduleEntry, sw.partner_entry_id)
        if p_entry and p_entry.is_deleted:
            p_entry.is_deleted = False
            p_entry.updated_at = datetime.now()
            db.session.add(ScheduleVersion(
                term_schedule_id=sw.term_schedule_id, entry_id=p_entry.id,
                action='revoke', snapshot_json=_snapshot_str(p_entry),
                operator_id=operator_id, operator_name=operator_name,
                remark=f'撤销换课#{sw.id}执行：恢复对方原课表条目'[:200],
                operated_at=datetime.now(),
            ))
    sw.partner_target_entry_id = None
    sw.target_entry_id = None
    sw.status = 'approved'
    sw.review_note = '已撤销执行（回退课表）'
    sw.reviewed_at = datetime.now()


def revoke_executed_swap(swap_id, operator_id, operator_name):
    """撤销已执行的调课（回退课表）。仅 executed 可撤销；统一调课批次整批回退。

    2026-10-11 新增：执行后若发现调错，可回退——永久调课恢复原课程、临时调课删除
    当天安排；记录回到「已通过」状态，可重新执行（或重新审批）。全过程单事务。
    """
    sw = db.session.get(ScheduleSwap, swap_id)
    if not sw:
        return False, '调课记录不存在', None
    if sw.status != 'executed':
        return False, (f'当前状态为「{SWAP_STATUS.get(sw.status, sw.status)}」，'
                       f'仅已执行的调课可撤销执行'), None
    targets = (batch_pending_siblings(sw, status='executed')
               if getattr(sw, 'batch_id', None) else [sw])
    for s in targets:
        _revoke_one(s, operator_id, operator_name)
    try:
        db.session.commit()
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        return False, f'撤销执行失败：{e}', None
    if len(targets) > 1:
        return True, f'已撤销该调课批次的执行（{len(targets)} 门课，课表已回退）', sw
    return True, '已撤销执行，课表已回退', sw


# ══════════════════════════════════════════════════════════════════════
# 两人换课（v1.19.47.27）：申请 → 对方确认 → 教务审批 → 执行
#   类型：time 换时段 / class 换班上课 / cover 请人代课
#   范围：is_permanent=False 只影响某一天；True 改常规课表
# ══════════════════════════════════════════════════════════════════════

def _teacher_brief(teacher_uid):
    """按教师编号取 (姓名, users.id)；查不到返回 ('', None)。跨库逻辑键，失败不抛。"""
    if not teacher_uid:
        return '', None
    try:
        from app.models.academic import Teacher
        t = Teacher.query.filter_by(teacher_uid=teacher_uid).first()
        if not t:
            return '', None
        return (t.name or ''), t.user_id
    except Exception:  # noqa: BLE001
        return '', None


def exchange_plan(e1, e2, kind, swap_time=True, swap_class=True,
                  cover_uid=None, cover_name=''):
    """换课「落位计划」：返回 (n1, n2)，n2=None 表示无该侧（代课只有我方）。

    各 plan 键：weekday / period_number / grade / class_name / subject / room /
                teacher_uid / teacher_name

    v1.19.47.27：原先三种类型（time / class / cover）里 time 与 class 被用户指出重复，
    合并为一个 `exchange` + 两个勾选 —— **勾什么就换什么**（教师始终各归各位）：

        swap_time  ✔ → 星期 / 节次互换；否则各自保持
        swap_class ✔ → 年级 / 班级 / 学科 / 教室互换；否则各自保持

        ✔时间 ✔班级 = 整条互换（我去上他那节、他来上我这节）
        ✔时间 ✘班级 = 只换时间（各上自己的班）
        ✘时间 ✔班级 = 只换班级（时间不动，去上对方的班）

    `cover` 请人代课：只把我方条目的授课教师换成对方（时间、班级都不动）。
    """
    if kind == 'cover':
        n1 = {'weekday': e1.weekday, 'period_number': e1.period_number,
              'grade': e1.grade, 'class_name': e1.class_name, 'subject': e1.subject,
              'room': e1.room, 'teacher_uid': cover_uid or e1.teacher_uid,
              'teacher_name': cover_name or (e1.teacher_name or '')}
        return n1, None

    if swap_time:
        wd1, pd1 = e2.weekday, e2.period_number
        wd2, pd2 = e1.weekday, e1.period_number
    else:
        wd1, pd1 = e1.weekday, e1.period_number
        wd2, pd2 = e2.weekday, e2.period_number
    if swap_class:
        cls1, cls2 = e2, e1       # 年级 / 班 / 学科 / 教室跟着换
    else:
        cls1, cls2 = e1, e2
    n1 = {'weekday': wd1, 'period_number': pd1,
          'grade': cls1.grade, 'class_name': cls1.class_name,
          'subject': cls1.subject, 'room': cls1.room,
          'teacher_uid': e1.teacher_uid, 'teacher_name': e1.teacher_name}
    n2 = {'weekday': wd2, 'period_number': pd2,
          'grade': cls2.grade, 'class_name': cls2.class_name,
          'subject': cls2.subject, 'room': cls2.room,
          'teacher_uid': e2.teacher_uid, 'teacher_name': e2.teacher_name}
    return n1, n2


def check_exchange_conflicts(schedule_id, e1, e2, kind, is_permanent, swap_date,
                             cover_uid=None, swap_time=True, swap_class=True,
                             partner_swap_date=None, extra_yield_ids=None):
    """换课落位后的冲突校验；返回错误描述列表（空 = 通过）。

    口径：对调后两条目都离开原位（两条自身互不构成冲突，故整批排除），
    但与**第三方**不得在同一格撞车；代课只动我方教师 → 对方在该时段不能另有课
    （代课不排除 e2）。

    v1.19.47.27：换课有**两个日期**（我方那天 / 对方那天），两侧按各自日期算单双周，
    不再用同一个 swap_date 兜底。
    """
    errs = []
    if kind == 'exchange':
        if not e2:
            return ['对方课程不存在']
        if e1.term_schedule_id != e2.term_schedule_id:
            return ['两节课不在同一张课表（学期）内，无法对调']
        if (e1.week_range or '') != (e2.week_range or ''):
            return ['两节课的单双周设置不同（%s / %s），暂不支持对调'
                    % (e1.week_range or '全周', e2.week_range or '全周')]
    yield_ids = [i for i in ([e1.id] if kind == 'cover' else [e1.id, getattr(e2, 'id', None)])
                 if i]
    if extra_yield_ids:            # 批量提交：本批内其它 involvement 也一起騰出，避免同批互撞
        for i in extra_yield_ids:
            if i and i not in yield_ids:
                yield_ids.append(i)
    plan1, plan2 = exchange_plan(e1, e2, kind, swap_time=swap_time,
                                 swap_class=swap_class, cover_uid=cover_uid)
    # 每侧按自己的日期算周次（我方 DATE / 对方 DATE，可不同）
    sides = (('我方课程', plan1, swap_date),
             ('对方课程', plan2, partner_swap_date or swap_date))
    for label, plan, side_date in sides:
        if not plan:
            continue
        # 2026-10-13 审计修复 #9：临时换课（有具体日期）时，落位用的是
        # **该日期的实际星期**（见 _execute_exchange_temp 的 side_wd），
        # 而这里原先一直用 plan['weekday']（课表条目自身星期）做冲突校验。
        # 遇到"调休按周三课表上课"（目标星期≠日期星期）时，校验的格子与实际
        # 落位的格子不是同一格 → 可能审批通过却落在从未校验过的时段。
        # 永久换课不涉及具体日期，仍按 plan['weekday']（= 课表星期）校验。
        if is_permanent or not side_date:
            w_args = {'week_range': e1.week_range}
            check_weekday = plan['weekday']
        else:
            w_args = {'week': _week_of_date(schedule_id, side_date)}
            check_weekday = side_date.isoweekday()
        wd_text = WEEKDAY_NAMES.get(check_weekday, '')
        pd = plan['period_number']
        cc = _check_class_conflict(schedule_id, plan['grade'], plan['class_name'],
                                   check_weekday, pd,
                                   extra_exclude_ids=yield_ids, **w_args)
        if cc:
            errs.append('%s落到 %s第%s节 时该班已有课程（%s）'
                        % (label, wd_text, pd, cc.subject))
        tc = _check_teacher_conflict(schedule_id, plan['teacher_uid'],
                                     check_weekday, pd,
                                     extra_exclude_ids=yield_ids, **w_args)
        if tc:
            errs.append('%s落到 %s第%s节 时教师已有课程（%s%s %s）'
                        % (label, wd_text, pd, tc.grade, tc.class_name, tc.subject))
    return errs


def _notify_exchange(sw, audience, event='', note=''):
    """换课通知（静默失败）：audience='partner' 发给对方 / 'applicant' 发给申请人。"""
    try:
        from app.modules.notifications.services.notification_service import notify_users
        kind_text = EXCHANGE_KINDS.get(sw.exchange_kind, '换课')
        if sw.exchange_kind == 'exchange':
            kind_text = '换课（%s）' % exchange_content_text(
                getattr(sw, 'swap_time', False), getattr(sw, 'swap_class', False))
        p_date = getattr(sw, 'partner_swap_date', None)
        if sw.is_permanent:
            scope = '整学期'
        elif sw.exchange_kind == 'exchange' and p_date and p_date != sw.swap_date:
            scope = f'{sw.swap_date}（我这天）/ {p_date}（对方那天）'
        else:
            scope = str(sw.swap_date) if sw.swap_date else ''
        if audience == 'partner':
            uid = sw.partner_uid
            if event == 'notify':               # 申请人勾了「无需对方同意确认」
                title = '【换课知会】%s已发起与你的%s' % (
                    sw.applicant_name or '对方老师', kind_text)
                content = ('范围：%s；理由：%s。该申请无需你确认，已直接提交教务审批。'
                           % (scope, sw.reason or '无'))
            else:
                title = '【换课待确认】%s请你%s' % (sw.applicant_name or '对方老师', kind_text)
                content = ('范围：%s；理由：%s。请到「教务管理 → 调课管理」确认是否同意，'
                           '你确认后才会进入教务审批。' % (scope, sw.reason or '无'))
            biz_type = 'swap_partner'
        else:
            uid = sw.applicant_uid
            if event == 'agree':
                title = '【换课】对方已同意，等待教务审批'
                content = '%s 已同意你的「%s」申请（%s），申请已进入教务审批。' % (
                    sw.partner_name or '对方老师', kind_text, scope)
            else:
                title = '【换课】对方已拒绝'
                content = '%s 拒绝了你发起的「%s」（%s）。理由：%s' % (
                    sw.partner_name or '对方老师', kind_text, scope, note or '无')
            biz_type = 'swap_result'
        if not uid:
            return
        notify_users(uids=[uid], title=title, content=content, category='academic',
                     biz_type=biz_type, biz_id=sw.id, link_url='/academic/swap')
    except Exception as _e:  # noqa: BLE001
        logger.warning('换课通知发送失败（不阻断主流程）: %s', _e)


def apply_exchange_batch(applicant_uid, applicant_name, items, kind='exchange',
                         is_permanent=False, reason=''):
    """批量发起「两人换课 / 请人代课」申请（v1.19.47.27）。

    items：[{'entry_id': int, 'partner_uid': str, 'partner_entry_id': int(换课时必填),
             'swap_time': bool, 'swap_class': bool,
             'swap_date': date, 'partner_swap_date': date(对方那天)}]

    - **支持一次和多人换/代多节**：全部记录挂在同一个 `batch_id` 下，教务整批审批；
      每条记录有自己的 partner_uid → **各自确认**（互不影响）。
    - 全成全败：任一行校验不通过，整批不入库（不会出现"换了一半"）。
    - 临时换课有**两个日期**（我这天 / 对方那天），永久换课不需要日期。

    返回 (success, message, {'created': n, 'batch_id': str|None, 'rep_id': int|None})
    """
    result = {'created': 0, 'batch_id': None, 'rep_id': None}
    kind = (kind or '').strip()
    if kind not in EXCHANGE_KINDS:
        return False, '换课类型无效', result
    if not items:
        return False, '请至少选择一节课', result
    if len(items) > MAX_SWAP_ITEMS:
        return False, f'一次最多提交 {MAX_SWAP_ITEMS} 节课，请分批提交', result
    if not (reason or '').strip():
        return False, '请填写申请理由', result
    is_permanent = bool(is_permanent)
    obj_word = '对方教师' if kind == 'exchange' else '代课教师'

    prepared = []
    seen_entry = set()
    for idx, raw in enumerate(items, 1):
        entry_id = (raw or {}).get('entry_id')
        entry = db.session.get(ScheduleEntry, int(entry_id)) if entry_id else None
        if not entry or entry.is_deleted:
            return False, f'第 {idx} 行：课表条目不存在或已删除', result
        if not entry.teacher_uid or entry.teacher_uid != applicant_uid:
            return False, f'第 {idx} 行：只能为自己的课程发起申请', result
        if entry.id in seen_entry:
            return False, f'第 {idx} 行：同一节课重复选择了', result
        seen_entry.add(entry.id)
        partner_uid = ((raw or {}).get('partner_uid') or '').strip()
        if not partner_uid:
            return False, f'第 {idx} 行：请选择{obj_word}', result
        if partner_uid == applicant_uid:
            return False, f'第 {idx} 行：不能选择自己作为{obj_word}', result
        e2 = None
        if kind == 'exchange':
            p_entry_id = (raw or {}).get('partner_entry_id')
            e2 = (db.session.get(ScheduleEntry, int(p_entry_id))
                  if p_entry_id else None)
            if not e2 or e2.is_deleted:
                return False, f'第 {idx} 行：对方的课程不存在或已删除', result
            if e2.teacher_uid != partner_uid:
                return False, f'第 {idx} 行：对方课程与所选教师不一致，请重新选择', result
            if e2.id == entry.id:
                return False, f'第 {idx} 行：不能与同一节课对调', result
            if e2.id in _temp_target_ids(e2.term_schedule_id):
                return False, (f'第 {idx} 行：对方这节课是临时调课产生的条目，'
                               f'不能作为对调对象'), result
        swap_time = bool((raw or {}).get('swap_time', True))
        swap_class = bool((raw or {}).get('swap_class', True))
        if kind == 'exchange' and not (swap_time or swap_class):
            return False, f'第 {idx} 行：请至少勾选一种对调内容（上课时间 / 上课班级）', result
        swap_date = (raw or {}).get('swap_date')
        partner_swap_date = ((raw or {}).get('partner_swap_date') or swap_date)
        if is_permanent:
            swap_date = None
            partner_swap_date = None
        elif not swap_date:
            return False, f'第 {idx} 行：临时申请请选择日期', result
        dup = ScheduleSwap.query.filter(
            ScheduleSwap.original_entry_id == entry.id,
            ScheduleSwap.status == 'pending',
        ).first()
        if dup:
            return False, (f'第 {idx} 行：这节课已有待处理的申请（#{dup.id}），'
                           f'请先撤销或等它处理完'), result
        prepared.append((entry, e2, partner_uid, swap_time, swap_class,
                         swap_date, partner_swap_date))

    # 冲突校验：本批所有参与条目一起排除，换完后不与第三方撞车
    all_yield = []
    for entry, e2, *_rest in prepared:
        if entry.id not in all_yield:
            all_yield.append(entry.id)
        if e2 is not None and e2.id not in all_yield:
            all_yield.append(e2.id)
    for idx, (entry, e2, partner_uid, swap_time, swap_class,
              swap_date, partner_swap_date) in enumerate(prepared, 1):
        errs = check_exchange_conflicts(
            entry.term_schedule_id, entry, e2, kind, is_permanent, swap_date,
            cover_uid=partner_uid, swap_time=swap_time, swap_class=swap_class,
            partner_swap_date=partner_swap_date, extra_yield_ids=all_yield)
        if errs:
            return False, f'第 {idx} 行：' + '；'.join(errs), result

    batch_id = uuid.uuid4().hex if len(prepared) > 1 else None
    made = []
    try:
        for (entry, e2, partner_uid, swap_time, swap_class,
             swap_date, partner_swap_date) in prepared:
            p_name, _p_uid = _teacher_brief(partner_uid)
            if not p_name and e2 is not None:
                p_name = e2.teacher_name or ''
            sw = ScheduleSwap(
                term_schedule_id=entry.term_schedule_id,
                swap_type='exchange',
                original_entry_id=entry.id,
                applicant_uid=applicant_uid,
                applicant_name=applicant_name or entry.teacher_name,
                new_weekday=((e2.weekday if swap_time else entry.weekday)
                             if e2 else None),
                new_period=((e2.period_number if swap_time else entry.period_number)
                            if e2 else None),
                swap_date=swap_date,
                partner_swap_date=partner_swap_date,
                is_permanent=is_permanent,
                reason=reason,
                status='pending',
                approval_step=0,
                batch_id=batch_id,
                exchange_kind=kind,
                swap_time=bool(swap_time),
                swap_class=bool(swap_class),
                partner_uid=partner_uid,
                partner_name=p_name,
                partner_entry_id=(e2.id if e2 else None),
                partner_status='pending',
            )
            db.session.add(sw)
            made.append(sw)
        db.session.commit()
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        return False, f'创建换课申请失败：{e}', result

    for sw in made:                      # 每条各自通知自己的 partner
        _notify_exchange(sw, 'partner')
    result['created'] = len(made)
    result['batch_id'] = batch_id
    result['rep_id'] = made[0].id if made else None
    verb = '换课' if kind == 'exchange' else '代课'
    tail = f'，整批 {len(made)} 门课' if len(made) > 1 else ''
    return True, f'{verb}申请已提交{tail}，等待对方确认后进入教务审批', result


def respond_exchange(swap_id, action, user_id, user_name, note=''):
    """对方对换课申请表态：action='agree' / 'refuse'（路由层已校验操作者是 partner 本人）。

    - 仅 partner_status='pending' 且记录仍 pending 时可操作；
    - agree：进入教务审批队列；refuse：记录直接置为已驳回（附理由）。
    """
    sw = db.session.get(ScheduleSwap, swap_id)
    if not sw or not sw.exchange_kind:
        return False, '该申请不是换课申请', None
    if sw.partner_status != 'pending':
        return False, ('该申请对方已处理（%s），不能重复操作'
                       % PARTNER_STATUS.get(sw.partner_status or '', '已处理')), None
    if sw.status != 'pending':
        return False, ('该申请当前状态为「%s」，无法再确认'
                       % SWAP_STATUS.get(sw.status, sw.status)), None
    if action not in ('agree', 'refuse'):
        return False, '操作无效', None
    if action == 'refuse' and not (note or '').strip():
        return False, '请填写拒绝的原因（让对方知道怎么调整）', None
    sw.partner_status = 'agreed' if action == 'agree' else 'refused'
    sw.partner_note = (note or '')[:200]
    sw.partner_responded_at = datetime.now()
    sw.add_approval(0, 'partner', '对方确认', user_id, user_name,
                    sw.partner_status,
                    PARTNER_STATUS.get(sw.partner_status, ''), sw.partner_note)
    if action == 'refuse':
        sw.status = 'rejected'
        sw.reviewed_at = datetime.now()
        sw.review_note = f'对方不同意：{sw.partner_note or "无"}'
    try:
        db.session.commit()
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        return False, f'提交失败：{e}', None
    _notify_exchange(sw, 'applicant', event=action, note=sw.partner_note)
    if action == 'agree':
        return True, '已同意换课，等待教务审批', sw
    return True, '已拒绝该换课申请', sw


def _mk_exchange_entry(sw, src, plan, note_prefix):
    """按落位计划构造换课条目（src 为原条目，仅用于继承周次/来源）。"""
    return ScheduleEntry(
        term_schedule_id=src.term_schedule_id, grade=plan['grade'],
        class_name=plan['class_name'], weekday=plan['weekday'],
        period_number=plan['period_number'], week_range=src.week_range,
        subject=plan['subject'], teacher_uid=plan['teacher_uid'],
        teacher_name=plan['teacher_name'], room=plan['room'],
        entry_type='swap', original_entry_id=src.id,
        note=f'{note_prefix}#{sw.id}产生', is_deleted=False,
    )


def _execute_exchange_permanent(sw, e1, operator_id, operator_name):
    """永久换课执行：两侧原条目软删（各写快照），按落位计划各建一条新条目。"""
    kind = sw.exchange_kind
    e2 = db.session.get(ScheduleEntry, sw.partner_entry_id) if sw.partner_entry_id else None
    if kind != 'cover' and (not e2 or e2.is_deleted):
        db.session.rollback()
        return False, '对方的课程条目已不存在，无法执行换课', None
    errs = check_exchange_conflicts(sw.term_schedule_id, e1, e2, kind, True, None,
                                    cover_uid=sw.partner_uid,
                                    swap_time=getattr(sw, 'swap_time', True),
                                    swap_class=getattr(sw, 'swap_class', True))
    if errs:
        db.session.rollback()
        return False, '换课执行前复检未通过：' + '；'.join(errs), None
    plan1, plan2 = exchange_plan(e1, e2, kind, cover_uid=sw.partner_uid,
                                 cover_name=sw.partner_name,
                                 swap_time=getattr(sw, 'swap_time', True),
                                 swap_class=getattr(sw, 'swap_class', True))
    kind_text = EXCHANGE_KINDS.get(kind, kind)
    srcs = [(e1, plan1)]
    if kind != 'cover':
        srcs.append((e2, plan2))
    for src, plan in srcs:
        db.session.add(ScheduleVersion(
            term_schedule_id=sw.term_schedule_id, entry_id=src.id, action='swap',
            snapshot_json=_snapshot_str(src), operator_id=operator_id,
            operator_name=operator_name,
            remark=(f'换课#{sw.id}（{kind_text}）：{WEEKDAY_NAMES.get(src.weekday, "")}'
                    f'第{src.period_number}节 → {WEEKDAY_NAMES.get(plan["weekday"], "")}'
                    f'第{plan["period_number"]}节')[:200],
            operated_at=datetime.now(),
        ))
        src.is_deleted = True
        src.updated_at = datetime.now()
    new1 = _mk_exchange_entry(sw, e1, plan1, '由换课')
    db.session.add(new1)
    db.session.flush()
    db.session.add(ScheduleVersion(
        term_schedule_id=sw.term_schedule_id, entry_id=new1.id, action='create',
        snapshot_json=_snapshot_str(new1), operator_id=operator_id,
        operator_name=operator_name,
        remark=f'换课#{sw.id}生成的新课表条目（原条目#{e1.id}）'[:200],
        operated_at=datetime.now(),
    ))
    sw.target_entry_id = new1.id
    if len(srcs) > 1:
        new2 = _mk_exchange_entry(sw, e2, plan2, '由换课')
        db.session.add(new2)
        db.session.flush()
        db.session.add(ScheduleVersion(
            term_schedule_id=sw.term_schedule_id, entry_id=new2.id, action='create',
            snapshot_json=_snapshot_str(new2), operator_id=operator_id,
            operator_name=operator_name,
            remark=f'换课#{sw.id}生成的新课表条目（原条目#{e2.id}）'[:200],
            operated_at=datetime.now(),
        ))
        sw.partner_target_entry_id = new2.id
    sw.status = 'executed'
    db.session.commit()
    return True, f'换课已执行（{kind_text}），课表已更新', sw


def _execute_exchange_temp(sw, e1, operator_id, operator_name):
    """临时换课执行：常规课表不动，为「那一天」各建一条替换条目（我方 / 对方）。"""
    swap_date = sw.swap_date
    if not swap_date:
        db.session.rollback()
        return False, '临时换课缺少具体日期，无法执行', None
    kind = sw.exchange_kind
    e2 = db.session.get(ScheduleEntry, sw.partner_entry_id) if sw.partner_entry_id else None
    if kind != 'cover' and (not e2 or e2.is_deleted):
        db.session.rollback()
        return False, '对方的课程条目已不存在，无法执行换课', None
    partner_date = getattr(sw, 'partner_swap_date', None) or swap_date
    errs = check_exchange_conflicts(sw.term_schedule_id, e1, e2, kind, False, swap_date,
                                    cover_uid=sw.partner_uid,
                                    swap_time=getattr(sw, 'swap_time', True),
                                    swap_class=getattr(sw, 'swap_class', True),
                                    partner_swap_date=partner_date)
    if errs:
        db.session.rollback()
        return False, '换课执行前复检未通过：' + '；'.join(errs), None
    plan1, plan2 = exchange_plan(e1, e2, kind, cover_uid=sw.partner_uid,
                                 cover_name=sw.partner_name,
                                 swap_time=getattr(sw, 'swap_time', True),
                                 swap_class=getattr(sw, 'swap_class', True))
    kind_text = EXCHANGE_KINDS.get(kind, kind)
    # 2026-10-13 审计修复 #9：临时换课的落位一律取**该日期的实际星期**，
    # 与 check_exchange_conflicts 中临时分支的校验口径一致（该校验已同步改为
    # side_date.isoweekday()）。永久换课走 _execute_exchange_permanent，
    # 那里用 plan['weekday']（课表星期），两条路径互不干扰。
    wd_1 = swap_date.isoweekday()
    wd_2 = partner_date.isoweekday()    # 对方那天（可能与我这天不同）
    made = []
    for src, plan, side_date, side_wd in ((e1, plan1, swap_date, wd_1),
                                          (e2, plan2, partner_date, wd_2)):
        if not plan:
            continue
        d = dict(plan)
        d['weekday'] = side_wd          # 临时条目：星期取各自那天的实际星期
        t = _mk_exchange_entry(sw, src, d, '临时换课')
        t.note = f'临时换课 {side_date.strftime("%Y-%m-%d")}'
        db.session.add(t)
        db.session.flush()
        db.session.add(ScheduleVersion(
            term_schedule_id=sw.term_schedule_id, entry_id=t.id, action='create',
            snapshot_json=_snapshot_str(t), operator_id=operator_id,
            operator_name=operator_name,
            remark=f'临时换课#{sw.id}生成（{swap_date}，{kind_text}）'[:200],
            operated_at=datetime.now(),
        ))
        made.append(t)
    db.session.add(ScheduleVersion(
        term_schedule_id=sw.term_schedule_id, entry_id=e1.id, action='swap',
        snapshot_json=_snapshot_str(e1), operator_id=operator_id,
        operator_name=operator_name,
        remark=f'临时换课#{sw.id} {swap_date}（{kind_text}，常规课表不变）'[:200],
        operated_at=datetime.now(),
    ))
    sw.target_entry_id = made[0].id if made else None
    sw.partner_target_entry_id = made[1].id if len(made) > 1 else None
    sw.status = 'executed'
    db.session.commit()
    return True, f'临时换课已执行（{swap_date}），常规课表保持不变', sw


# ══════════════════════════════════════════════════════════════════════
# 临时调课叠加 / 实时生效条目（供查课与今日课表）
# ══════════════════════════════════════════════════════════════════════

def get_temp_swaps_by_date(schedule_id, target_date):
    """某天已执行的临时调课列表（含原/目标条目 dict），供课表视图叠加显示。

    两类都算"这天要处理的调课"：
    - swap_date == 这天：调过来的课（target_entry 在这天生效）；
    - source_date == 这天：调走的课（原课这天不上，target 在另一天）——跨天调课时
      原课在该日应显示为"无课"，因此只回 original_entry、不回 target_entry。
    """
    swaps = ScheduleSwap.query.filter(
        ScheduleSwap.term_schedule_id == schedule_id,
        ScheduleSwap.is_permanent.is_(False),
        ScheduleSwap.status == 'executed',
        db.or_(ScheduleSwap.swap_date == target_date,
               ScheduleSwap.source_date == target_date),
    ).all()
    result = []
    # 2026-10-10 N+1 优化：当天全部原/目标条目一次 in_ 取回，替代循环内逐条 get
    # 2026-10-12（v1.19.47.27）换课：对方侧的「原条目 / 当天条目」一并取回
    entry_map = _build_entry_map(
        [i for sw in swaps
         for i in (sw.original_entry_id, sw.target_entry_id,
                   getattr(sw, 'partner_entry_id', None),
                   getattr(sw, 'partner_target_entry_id', None)) if i])
    for sw in swaps:
        d = sw.to_dict()
        is_source_day = (sw.source_date == target_date and sw.swap_date != target_date)
        orig = entry_map.get(sw.original_entry_id) if sw.original_entry_id else None
        tgt = None if is_source_day else (
            entry_map.get(sw.target_entry_id) if sw.target_entry_id else None)
        d['original_entry'] = orig.to_dict() if orig else None
        d['target_entry'] = tgt.to_dict() if tgt else None
        p_orig = (entry_map.get(sw.partner_entry_id)
                  if getattr(sw, 'partner_entry_id', None) else None)
        p_tgt = None if is_source_day else (
            entry_map.get(sw.partner_target_entry_id)
            if getattr(sw, 'partner_target_entry_id', None) else None)
        d['partner_original_entry'] = p_orig.to_dict() if p_orig else None
        d['partner_target_entry'] = p_tgt.to_dict() if p_tgt else None
        result.append(d)
    return result


def resolve_effective_entry(schedule_id, grade, class_name, weekday, period_number, target_date):
    """给定具体日期，返回该班该节次'实际生效'的课表条目 dict（无课返回 None）。

    若当天有临时调课调入本时段 → 以临时条目为准；
    若本时段常规课当天被临时调走 → 返回 None；
    否则返回常规（含永久调课后）的条目。
    """
    # 本时段的常规条目（排除临时调课产生的条目）
    temp_target_ids = _temp_target_ids(schedule_id)
    base_q = ScheduleEntry.query.filter(
        ScheduleEntry.term_schedule_id == schedule_id,
        ScheduleEntry.grade == grade,
        ScheduleEntry.class_name == class_name,
        ScheduleEntry.weekday == weekday,
        ScheduleEntry.period_number == period_number,
        ScheduleEntry.is_deleted.is_(False),
    )
    base_entry = next((e for e in base_q.all() if e.id not in temp_target_ids), None)

    # 当天已执行的临时调课
    temp_swaps = ScheduleSwap.query.filter(
        ScheduleSwap.term_schedule_id == schedule_id,
        ScheduleSwap.swap_date == target_date,
        ScheduleSwap.is_permanent.is_(False),
        ScheduleSwap.status == 'executed',
    ).all()

    # 1) 临时调入本时段？
    # 2026-10-10 N+1 优化：当天临时调课的目标条目一次 in_ 取回，替代循环内逐条 get
    tgt_map = _build_entry_map(
        [i for sw in temp_swaps
         for i in (sw.target_entry_id, getattr(sw, 'partner_target_entry_id', None))
         if i])
    for sw in temp_swaps:
        # v1.19.47.27 换课：我方 / 对方两条当天条目都要参与"调入本时段"判定
        for eid in (sw.target_entry_id, getattr(sw, 'partner_target_entry_id', None)):
            te = tgt_map.get(eid) if eid else None
            if (te and not te.is_deleted and te.grade == grade
                    and te.class_name == class_name and te.weekday == weekday
                    and te.period_number == period_number):
                return te.to_dict()
    # 2) 本时段常规课被临时调走？（换课时，对方条目也算被调走）
    if base_entry:
        for sw in temp_swaps:
            if base_entry.id in (sw.original_entry_id,
                                 getattr(sw, 'partner_entry_id', None)):
                return None
    # 3) 常规生效
    return base_entry.to_dict() if base_entry else None


def _temp_target_ids(schedule_id):
    """所有临时调课产生的目标条目 id 集合（这些条目不进常规周课表）。

    实现已上收到 schedule_common，与排课模块共用同一份口径。
    """
    return _temp_target_ids_common(schedule_id)


def build_live_schedule(schedule_id, target_date, period_number, grade_filter=None,
                        allowed_classes=None):
    """构建某天某节次全校（或指定年级）实时课表，按年级分区。

    返回 {grade: [ {class_name, grade, subject, teacher_name, room,
                    entry_type, is_temp_swap, period_number} ]}。
    叠加当天临时调课：调入标记 is_temp_swap=True，被调走的常规课显示为无课。
    """
    weekday = target_date.isoweekday()
    if grade_filter is None:
        grade_filters = None
    elif isinstance(grade_filter, str):
        grade_filters = {grade_filter} if grade_filter else set()
    else:
        grade_filters = {grade for grade in grade_filter if grade}

    def class_visible(grade, class_name):
        if allowed_classes is None:
            return True
        classes = allowed_classes.get(grade, set())
        return classes is None or (class_name or '') in classes

    # 全部班级（未删除条目派生）
    cq = ScheduleEntry.query.filter(
        ScheduleEntry.term_schedule_id == schedule_id,
        ScheduleEntry.is_deleted.is_(False),
    )
    if grade_filters is not None:
        cq = cq.filter(ScheduleEntry.grade.in_(grade_filters))
    classes = (cq.with_entities(ScheduleEntry.grade, ScheduleEntry.class_name)
               .distinct().order_by(ScheduleEntry.grade, ScheduleEntry.class_name).all())
    classes = [(grade, class_name) for grade, class_name in classes
               if class_visible(grade, class_name)]

    # 本时段常规条目（排除临时调课目标条目）
    temp_ids = _temp_target_ids(schedule_id)
    bq = ScheduleEntry.query.filter(
        ScheduleEntry.term_schedule_id == schedule_id,
        ScheduleEntry.weekday == weekday,
        ScheduleEntry.period_number == period_number,
        ScheduleEntry.is_deleted.is_(False),
    )
    if grade_filters is not None:
        bq = bq.filter(ScheduleEntry.grade.in_(grade_filters))
    base_map = {}
    for e in bq.all():
        if not class_visible(e.grade, e.class_name):
            continue
        if e.id not in temp_ids:
            base_map[(e.grade, e.class_name)] = e

    # 当天临时调课：调入映射 + 被调走原条目 id
    temp_swaps = get_temp_swaps_by_date(schedule_id, target_date)
    moved_away, temp_in = set(), {}
    for ts in temp_swaps:
        # v1.19.47.27 换课：对方侧条目同样参与"调入 / 调走"叠加
        for oe in (ts.get('original_entry'), ts.get('partner_original_entry')):
            if oe and class_visible(oe.get('grade'), oe.get('class_name')):
                moved_away.add(oe['id'])
        for te in (ts.get('target_entry'), ts.get('partner_target_entry')):
            if not te or te['weekday'] != weekday or te['period_number'] != period_number:
                continue
            if grade_filters is None or te['grade'] in grade_filters:
                if class_visible(te.get('grade'), te.get('class_name')):
                    temp_in[(te['grade'], te['class_name'])] = te

    data = {}
    for grade, class_name in classes:
        row = {'grade': grade, 'class_name': class_name, 'period_number': period_number,
               'subject': None, 'teacher_name': None, 'room': None,
               'entry_type': None, 'is_temp_swap': False}
        key = (grade, class_name)
        if key in temp_in:
            te = temp_in[key]
            row.update({'subject': te['subject'], 'teacher_name': te['teacher_name'],
                        'room': te['room'], 'entry_type': te['entry_type'],
                        'is_temp_swap': True})
        else:
            be = base_map.get(key)
            if be and be.id not in moved_away:
                row.update({'subject': be.subject, 'teacher_name': be.teacher_name,
                            'room': be.room, 'entry_type': be.entry_type,
                            'is_temp_swap': False})
        data.setdefault(grade, []).append(row)
    for g in data:
        data[g].sort(key=lambda r: r['class_name'])
    return data


def current_period_number(periods, now=None):
    """根据当前时间 HH:MM 与节次起止时间判定当前节次号（无匹配返回最近已开始的）。"""
    now = now or datetime.now()
    hm = now.strftime('%H:%M')
    started = None
    for pd in periods:
        if pd.start_time and pd.end_time:
            if pd.start_time <= hm <= pd.end_time:
                return pd.period_number
            if pd.start_time <= hm:
                started = pd.period_number
    return started


# ══════════════════════════════════════════════════════════════════════
# 统计
# ══════════════════════════════════════════════════════════════════════

def get_swap_stats(schedule_id=None, days=30, applicant_uid=None):
    """调课统计：状态计数 / 类型分布 / 学科 Top / 近 N 天趋势（可 JSON 序列化）。"""
    base = ScheduleSwap.query
    if schedule_id:
        base = base.filter(ScheduleSwap.term_schedule_id == schedule_id)
    if applicant_uid:
        base = base.filter(ScheduleSwap.applicant_uid == applicant_uid)

    status_counts = {'pending': 0, 'approved': 0, 'rejected': 0, 'executed': 0}
    for st, cnt in (base.with_entities(ScheduleSwap.status, func.count(ScheduleSwap.id))
                    .group_by(ScheduleSwap.status).all()):
        if st in status_counts:
            status_counts[st] = int(cnt)
    total = sum(status_counts.values())

    type_dist = []
    for st, cnt in (base.with_entities(ScheduleSwap.swap_type, func.count(ScheduleSwap.id))
                    .group_by(ScheduleSwap.swap_type).all()):
        type_dist.append({'key': st, 'name': SWAP_TYPES.get(st, st or '未知'), 'value': int(cnt)})

    # 学科分布 Top（关联原条目 subject）
    subj_q = (db.session.query(ScheduleEntry.subject, func.count(ScheduleSwap.id))
              .join(ScheduleEntry, ScheduleSwap.original_entry_id == ScheduleEntry.id))
    if schedule_id:
        subj_q = subj_q.filter(ScheduleSwap.term_schedule_id == schedule_id)
    if applicant_uid:
        subj_q = subj_q.filter(ScheduleSwap.applicant_uid == applicant_uid)
    subj_rows = (subj_q.group_by(ScheduleEntry.subject)
                 .order_by(func.count(ScheduleSwap.id).desc()).limit(10).all())
    subject_dist = [{'name': (s or '未知'), 'value': int(c)} for s, c in subj_rows]

    # 近 N 天趋势
    days = max(1, int(days or 30))
    today = date.today()
    since_dt = datetime.combine(today - timedelta(days=days - 1), datetime.min.time())
    trend_q = base.filter(ScheduleSwap.created_at >= since_dt)
    trend_rows = (trend_q
                  .with_entities(func.date(ScheduleSwap.created_at), func.count(ScheduleSwap.id))
                  .group_by(func.date(ScheduleSwap.created_at)).all())
    trend_map = {str(d): int(c) for d, c in trend_rows}
    trend = []
    for i in range(days):
        day = (today - timedelta(days=days - 1 - i)).isoformat()
        trend.append({'date': day, 'count': trend_map.get(day, 0)})

    return {
        'status_counts': status_counts,
        'status_labels': {k: SWAP_STATUS.get(k, k) for k in status_counts},
        'total': total,
        'type_dist': type_dist,
        'subject_dist': subject_dist,
        'trend': trend,
        'days': days,
    }
