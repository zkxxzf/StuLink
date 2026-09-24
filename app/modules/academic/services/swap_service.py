# StuLink v1.18.2.0 2026-09-24
# 调课服务（基于 timetable.db 新模型）：申请 / 批量 / 审核 / 执行 / 撤销 / 统计 / 实时生效
# Copyright (c) 2026 zkxxzf. Apache License 2.0
#
# 自包含原则：仅依赖 app.models.timetable 中的模型，不导入 schedule_service
# （并行开发，集成任务会统一去重）。冲突检测等辅助逻辑在本文件内以私有函数实现。
import json
from datetime import datetime, date, timedelta

from sqlalchemy import func

from app.extensions import db
from app.models.timetable import (
    TermSchedule, PeriodDef, ScheduleEntry, ScheduleSwap, ScheduleVersion,
    SWAP_STATUS, SWAP_TYPES, WEEKDAY_NAMES, MAX_PERIOD,
)


def _notify_swap_result(sw, action, review_note=''):
    """审核完成后通知申请人（静默失败，不影响主流程）。"""
    try:
        from app.modules.notifications.services.notification_service import notify_users
        applicant_uid = sw.applicant_uid
        if not applicant_uid:
            return
        if action == 'approve':
            title = '【调课审核】已通过'
            content = (f'您提交的调课申请（#{sw.id}）已被审核通过。'
                       f'请及时执行调课操作。')
        else:
            title = '【调课审核】已拒绝'
            content = (f'您提交的调课申请（#{sw.id}）已被拒绝。'
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
    except Exception:
        pass  # 通知失败不影响主流程


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
)

# get_periods 由 schedule_common 提供（委托包装）
from app.modules.academic.services.schedule_common import get_periods as _gp_common  # noqa: E402


def _get_periods(schedule_id):
    """节次列表（按 sort_order 升序，委托 schedule_common）。"""
    return _gp_common(schedule_id)


def get_periods(schedule_id):
    """公共包装：供路由层获取节次定义。"""
    return _get_periods(schedule_id)


def _period_map(schedule_id):
    """{period_number: PeriodDef} 映射。"""
    return {p.period_number: p for p in _get_periods(schedule_id)}


def _period_label(schedule_id, period_number):
    """节次显示文案，如 '第3节 10:00-10:45'。"""
    if not period_number:
        return None
    pd = PeriodDef.query.filter_by(term_schedule_id=schedule_id,
                                   period_number=period_number).first()
    if not pd:
        return f'第{period_number}节'
    if pd.start_time and pd.end_time:
        return f'{pd.period_name} {pd.start_time}-{pd.end_time}'
    return pd.period_name


def _check_class_conflict(schedule_id, grade, class_name, weekday,
                          period_number, exclude_entry_id=None,
                          week_range=None, week=None):
    """同班同时段冲突：返回冲突的 ScheduleEntry 或 None（过滤软删除）。

    week_range / week：按周次判断——无周次交集的条目不算冲突（单双周交替课）。
    """
    q = ScheduleEntry.query.filter(
        ScheduleEntry.term_schedule_id == schedule_id,
        ScheduleEntry.grade == grade,
        ScheduleEntry.class_name == class_name,
        ScheduleEntry.weekday == weekday,
        ScheduleEntry.period_number == period_number,
        ScheduleEntry.is_deleted.is_(False),
    )
    if exclude_entry_id:
        q = q.filter(ScheduleEntry.id != exclude_entry_id)
    return _pick_conflict(q.all(), week_range=week_range, week=week)


def _check_teacher_conflict(schedule_id, teacher_uid, weekday, period_number,
                            exclude_entry_id=None, week_range=None, week=None):
    """同教师同时段冲突：返回冲突的 ScheduleEntry 或 None（过滤软删除）。"""
    if not teacher_uid:
        return None
    q = ScheduleEntry.query.filter(
        ScheduleEntry.term_schedule_id == schedule_id,
        ScheduleEntry.teacher_uid == teacher_uid,
        ScheduleEntry.weekday == weekday,
        ScheduleEntry.period_number == period_number,
        ScheduleEntry.is_deleted.is_(False),
    )
    if exclude_entry_id:
        q = q.filter(ScheduleEntry.id != exclude_entry_id)
    return _pick_conflict(q.all(), week_range=week_range, week=week)


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
    period_numbers = sorted(pmap.keys()) if pmap else list(range(1, MAX_PERIOD + 1))
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

    result = []
    for w in weekdays:
        for p in period_numbers:
            ce = class_busy.get((w, p))
            te = teacher_busy.get((w, p))
            class_conflict = ce is not None
            teacher_conflict = te is not None
            available = not class_conflict and not teacher_conflict
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
            }
            if include_all or available:
                result.append(slot)
    return result


def get_class_options(schedule_id=None):
    """从现有课表条目派生年级/班级选项，供申请表单级联下拉。

    返回 {'grades': [...], 'grade_classes': {grade: [class_name, ...]}}。
    """
    if schedule_id is None:
        sched = _get_active_schedule()
        schedule_id = sched.id if sched else None
    q = ScheduleEntry.query.filter(ScheduleEntry.is_deleted.is_(False))
    if schedule_id:
        q = q.filter(ScheduleEntry.term_schedule_id == schedule_id)
    rows = (q.with_entities(ScheduleEntry.grade, ScheduleEntry.class_name)
            .distinct().order_by(ScheduleEntry.grade, ScheduleEntry.class_name).all())
    grades, grade_classes = [], {}
    for g, c in rows:
        if g not in grade_classes:
            grade_classes[g] = []
            grades.append(g)
        if c and c not in grade_classes[g]:
            grade_classes[g].append(c)
    return {'grades': grades, 'grade_classes': grade_classes}


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
               reason='', swap_type='personal'):
    """创建个人调课申请（status='pending'）。

    返回 (success: bool, message: str, swap: ScheduleSwap|None)。
    """
    entry = db.session.get(ScheduleEntry, original_entry_id) if original_entry_id else None
    if not entry or entry.is_deleted:
        return False, '原课表条目不存在或已删除', None
    if not new_weekday or not new_period:
        return False, '请选择目标星期和节次', None
    if not (1 <= int(new_weekday) <= 7):
        return False, '目标星期无效（1-7）', None
    if not (1 <= int(new_period) <= MAX_PERIOD):
        return False, f'目标节次无效（1-{MAX_PERIOD}）', None
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
        is_permanent=bool(is_permanent),
        reason=(reason or '').strip() or None,
        status='pending',
    )
    try:
        db.session.add(swap)
        db.session.commit()
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        return False, f'提交失败：{e}', None
    return True, '调课申请已提交，待审核', swap


def bulk_apply_swap(applicant_uid, applicant_name, entry_ids, new_weekday,
                    new_period, new_room=None, swap_date=None, is_permanent=False,
                    reason=''):
    """统一调课：一次把多个条目调到同一新时段。

    逐条校验班级/教师冲突，并检测批量内部（同班/同教师调到同一时段）互相冲突。
    全部无冲突才批量创建；否则返回冲突明细且不写库。
    返回 (success, message, {'created': n, 'conflicts': [...]}）。
    """
    result = {'created': 0, 'conflicts': []}
    if not entry_ids:
        return False, '请至少选择一条课程', result
    if not new_weekday or not new_period:
        return False, '请选择目标星期和节次', result
    if not (1 <= int(new_weekday) <= 7) or not (1 <= int(new_period) <= MAX_PERIOD):
        return False, '目标星期或节次无效', result
    new_weekday, new_period = int(new_weekday), int(new_period)

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
        wr, wk = _week_ctx(schedule_id, entry, is_permanent, swap_date)
        cc = _check_class_conflict(schedule_id, entry.grade, entry.class_name,
                                   new_weekday, new_period, exclude_entry_id=entry.id,
                                   week_range=wr, week=wk)
        tc = _check_teacher_conflict(schedule_id, entry.teacher_uid,
                                     new_weekday, new_period, exclude_entry_id=entry.id,
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

    # 批量内部互相冲突：同班 / 同教师 调到同一目标时段
    seen_class, seen_teacher, final_valid = {}, {}, []
    for entry in valid_entries:
        ckey = (entry.grade, entry.class_name)
        tkey = entry.teacher_uid
        if ckey in seen_class:
            conflicts.append({'entry_id': entry.id, 'subject': entry.subject,
                              'class': f'{entry.grade}{entry.class_name}',
                              'desc': f'与所选条目 #{seen_class[ckey]} 目标时段相同（同班冲突）'})
            continue
        if tkey and tkey in seen_teacher:
            conflicts.append({'entry_id': entry.id, 'subject': entry.subject,
                              'class': f'{entry.grade}{entry.class_name}',
                              'desc': f'与所选条目 #{seen_teacher[tkey]} 目标时段相同（同教师冲突）'})
            continue
        seen_class[ckey] = entry.id
        if tkey:
            seen_teacher[tkey] = entry.id
        final_valid.append(entry)

    if conflicts:
        result['conflicts'] = conflicts
        return False, f'存在 {len(conflicts)} 条冲突，未创建任何调课', result

    created = 0
    try:
        for entry in final_valid:
            swap = ScheduleSwap(
                term_schedule_id=entry.term_schedule_id,
                swap_type='bulk',
                original_entry_id=entry.id,
                applicant_uid=applicant_uid,
                applicant_name=applicant_name,
                new_weekday=new_weekday,
                new_period=new_period,
                new_room=(new_room or '').strip() or None,
                swap_date=swap_date,
                is_permanent=bool(is_permanent),
                reason=(reason or '').strip() or None,
                status='pending',
            )
            db.session.add(swap)
            created += 1
        db.session.commit()
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        return False, f'创建失败：{e}', {'created': 0, 'conflicts': conflicts}
    return True, f'已创建 {created} 条统一调课申请', {'created': created, 'conflicts': []}


# ══════════════════════════════════════════════════════════════════════
# 列表 / 详情
# ══════════════════════════════════════════════════════════════════════

def _decorate_swap(sw):
    """把 ScheduleSwap 转为 dict 并附带原条目信息与目标时段文案。"""
    d = sw.to_dict()
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
    d['target_slot'] = (f'{WEEKDAY_NAMES.get(sw.new_weekday, "")}第{sw.new_period}节'
                        if sw.new_weekday and sw.new_period else None)
    d['target_period_label'] = _period_label(sw.term_schedule_id, sw.new_period)
    d['is_temp'] = not sw.is_permanent
    return d


def get_swap_list(status=None, applicant_uid=None, schedule_id=None, page=1,
                  per_page=20, is_reviewer=False, swap_type=None):
    """分页调课列表。普通教师只看自己的，审核者可看全部。

    返回 (items: list[dict], pagination)。
    """
    q = ScheduleSwap.query
    if schedule_id:
        q = q.filter(ScheduleSwap.term_schedule_id == schedule_id)
    if status and status in SWAP_STATUS:
        q = q.filter(ScheduleSwap.status == status)
    if swap_type and swap_type in SWAP_TYPES:
        q = q.filter(ScheduleSwap.swap_type == swap_type)
    if not is_reviewer and applicant_uid:
        q = q.filter(ScheduleSwap.applicant_uid == applicant_uid)
    q = q.order_by(ScheduleSwap.created_at.desc(), ScheduleSwap.id.desc())
    pagination = q.paginate(page=page, per_page=per_page, error_out=False)
    items = [_decorate_swap(sw) for sw in pagination.items]
    return items, pagination


def count_pending(schedule_id=None, applicant_uid=None):
    """待审核数量（用于列表页徽章）。"""
    q = ScheduleSwap.query.filter(ScheduleSwap.status == 'pending')
    if schedule_id:
        q = q.filter(ScheduleSwap.term_schedule_id == schedule_id)
    if applicant_uid:
        q = q.filter(ScheduleSwap.applicant_uid == applicant_uid)
    return q.count()


def get_swap_detail(swap_id):
    """调课详情：含原条目 to_dict、目标节次名称/时间、审核信息、版本记录。"""
    sw = db.session.get(ScheduleSwap, swap_id)
    if not sw:
        return None
    d = _decorate_swap(sw)
    schedule_id = sw.term_schedule_id
    orig = db.session.get(ScheduleEntry, sw.original_entry_id) if sw.original_entry_id else None
    d['original_entry'] = orig.to_dict() if orig else None
    tgt = db.session.get(ScheduleEntry, sw.target_entry_id) if sw.target_entry_id else None
    d['target_entry'] = tgt.to_dict() if tgt else None
    # 原/目标节次定义
    opd = (PeriodDef.query.filter_by(term_schedule_id=schedule_id,
                                     period_number=orig.period_number).first()
           if orig else None)
    d['original_period'] = opd.to_dict() if opd else None
    npd = (PeriodDef.query.filter_by(term_schedule_id=schedule_id,
                                     period_number=sw.new_period).first()
           if sw.new_period else None)
    d['target_period'] = npd.to_dict() if npd else None
    # 版本记录（原条目 + 目标条目）
    entry_ids = [x for x in (sw.original_entry_id, sw.target_entry_id) if x]
    versions = []
    if entry_ids:
        vq = (ScheduleVersion.query
              .filter(ScheduleVersion.entry_id.in_(entry_ids))
              .order_by(ScheduleVersion.operated_at.desc(), ScheduleVersion.id.desc()))
        versions = [v.to_dict() for v in vq.all()]
    d['versions'] = versions
    return d


def get_my_swaps(applicant_uid, status=None):
    """我的调课申请（列表 dict）。"""
    q = ScheduleSwap.query.filter(ScheduleSwap.applicant_uid == applicant_uid)
    if status and status in SWAP_STATUS:
        q = q.filter(ScheduleSwap.status == status)
    swaps = q.order_by(ScheduleSwap.created_at.desc(), ScheduleSwap.id.desc()).all()
    return [_decorate_swap(sw) for sw in swaps]


# ══════════════════════════════════════════════════════════════════════
# 审核 / 执行 / 撤销
# ══════════════════════════════════════════════════════════════════════

def review_swap(swap_id, reviewer_id, reviewer_name, action, review_note=''):
    """审核调课：action='approve'/'reject'。仅 pending 可审。

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
        sw.status = 'rejected'
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
        return True, '已驳回该调课申请', sw

    # approve
    entry = db.session.get(ScheduleEntry, sw.original_entry_id) if sw.original_entry_id else None
    if not entry or entry.is_deleted:
        return False, '原课表条目不存在或已删除，无法通过', None
    if sw.new_weekday and sw.new_period:
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
    sw.status = 'approved'
    sw.reviewed_by = reviewer_id
    sw.review_note = (review_note or '').strip()[:200] or None
    sw.reviewed_at = datetime.now()
    try:
        db.session.commit()
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        return False, f'操作失败：{e}', None
    # 通知申请人：已通过
    _notify_swap_result(sw, 'approve')
    return True, '已通过审核，可执行调课', sw


def _snapshot_str(entry):
    """条目快照（委托 schedule_common）。"""
    from app.modules.academic.services.schedule_common import snapshot_entry
    return snapshot_entry(entry)


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
    """永久调课执行（在 execute_swap 的事务内调用）。"""
    cc = _check_class_conflict(sw.term_schedule_id, entry.grade, entry.class_name,
                               sw.new_weekday, sw.new_period, exclude_entry_id=entry.id,
                               week_range=entry.week_range)
    if cc:
        db.session.rollback()
        return False, f'目标时段本班已有课程（{cc.subject}），无法执行', None
    tc = _check_teacher_conflict(sw.term_schedule_id, entry.teacher_uid,
                                 sw.new_weekday, sw.new_period, exclude_entry_id=entry.id,
                                 week_range=entry.week_range)
    if tc:
        db.session.rollback()
        return False, (f'目标时段教师已有课程（{tc.grade}{tc.class_name} '
                       f'{tc.subject}），无法执行'), None

    # 1) 原条目旧数据快照 → version(action='swap')
    remark = (f'调课#{sw.id}：{sw.reason or "无原因"}'
              f'（{WEEKDAY_NAMES.get(entry.weekday, "")}第{entry.period_number}节 → '
              f'{WEEKDAY_NAMES.get(sw.new_weekday, "")}第{sw.new_period}节）')[:200]
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
        class_name=entry.class_name, weekday=sw.new_weekday,
        period_number=sw.new_period, week_range=entry.week_range,
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
    """临时调课执行（在 execute_swap 的事务内调用）。"""
    swap_date = sw.swap_date
    if not swap_date:
        db.session.rollback()
        return False, '临时调课缺少具体日期，无法执行', None
    target_weekday = swap_date.isoweekday()
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
    """申请人撤销自己的 pending 申请（标记 rejected + review_note='申请人撤销'）。"""
    sw = db.session.get(ScheduleSwap, swap_id)
    if not sw:
        return False, '调课记录不存在', None
    if sw.applicant_uid != applicant_uid:
        return False, '只能撤销自己的调课申请', None
    if sw.status != 'pending':
        return False, (f'当前状态为「{SWAP_STATUS.get(sw.status, sw.status)}」，'
                       f'仅待审核的申请可撤销'), None
    sw.status = 'rejected'
    sw.review_note = '申请人撤销'
    sw.reviewed_at = datetime.now()
    try:
        db.session.commit()
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        return False, f'撤销失败：{e}', None
    return True, '已撤销该调课申请', sw


# ══════════════════════════════════════════════════════════════════════
# 临时调课叠加 / 实时生效条目（供查课与今日课表）
# ══════════════════════════════════════════════════════════════════════

def get_temp_swaps_by_date(schedule_id, target_date):
    """某天已执行的临时调课列表（含原/目标条目 dict），供课表视图叠加显示。"""
    swaps = ScheduleSwap.query.filter(
        ScheduleSwap.term_schedule_id == schedule_id,
        ScheduleSwap.swap_date == target_date,
        ScheduleSwap.is_permanent.is_(False),
        ScheduleSwap.status == 'executed',
    ).all()
    result = []
    for sw in swaps:
        d = sw.to_dict()
        orig = db.session.get(ScheduleEntry, sw.original_entry_id) if sw.original_entry_id else None
        tgt = db.session.get(ScheduleEntry, sw.target_entry_id) if sw.target_entry_id else None
        d['original_entry'] = orig.to_dict() if orig else None
        d['target_entry'] = tgt.to_dict() if tgt else None
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
    for sw in temp_swaps:
        if not sw.target_entry_id:
            continue
        te = db.session.get(ScheduleEntry, sw.target_entry_id)
        if (te and not te.is_deleted and te.grade == grade
                and te.class_name == class_name and te.weekday == weekday
                and te.period_number == period_number):
            return te.to_dict()
    # 2) 本时段常规课被临时调走？
    if base_entry:
        for sw in temp_swaps:
            if sw.original_entry_id == base_entry.id:
                return None
    # 3) 常规生效
    return base_entry.to_dict() if base_entry else None


def _temp_target_ids(schedule_id):
    """所有临时调课产生的目标条目 id 集合（这些条目不进常规周课表）。

    实现已上收到 schedule_common，与排课模块共用同一份口径。
    """
    return _temp_target_ids_common(schedule_id)


def build_live_schedule(schedule_id, target_date, period_number, grade_filter=None):
    """构建某天某节次全校（或指定年级）实时课表，按年级分区。

    返回 {grade: [ {class_name, grade, subject, teacher_name, room,
                    entry_type, is_temp_swap, period_number} ]}。
    叠加当天临时调课：调入标记 is_temp_swap=True，被调走的常规课显示为无课。
    """
    weekday = target_date.isoweekday()
    # 全部班级（未删除条目派生）
    cq = ScheduleEntry.query.filter(
        ScheduleEntry.term_schedule_id == schedule_id,
        ScheduleEntry.is_deleted.is_(False),
    )
    if grade_filter:
        cq = cq.filter(ScheduleEntry.grade == grade_filter)
    classes = (cq.with_entities(ScheduleEntry.grade, ScheduleEntry.class_name)
               .distinct().order_by(ScheduleEntry.grade, ScheduleEntry.class_name).all())

    # 本时段常规条目（排除临时调课目标条目）
    temp_ids = _temp_target_ids(schedule_id)
    bq = ScheduleEntry.query.filter(
        ScheduleEntry.term_schedule_id == schedule_id,
        ScheduleEntry.weekday == weekday,
        ScheduleEntry.period_number == period_number,
        ScheduleEntry.is_deleted.is_(False),
    )
    if grade_filter:
        bq = bq.filter(ScheduleEntry.grade == grade_filter)
    base_map = {}
    for e in bq.all():
        if e.id not in temp_ids:
            base_map[(e.grade, e.class_name)] = e

    # 当天临时调课：调入映射 + 被调走原条目 id
    temp_swaps = get_temp_swaps_by_date(schedule_id, target_date)
    moved_away, temp_in = set(), {}
    for ts in temp_swaps:
        oe = ts.get('original_entry')
        if oe:
            moved_away.add(oe['id'])
        te = ts.get('target_entry')
        if te and te['weekday'] == weekday and te['period_number'] == period_number:
            if not grade_filter or te['grade'] == grade_filter:
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

def get_swap_stats(schedule_id=None, days=30):
    """调课统计：状态计数 / 类型分布 / 学科 Top / 近 N 天趋势（可 JSON 序列化）。"""
    base = ScheduleSwap.query
    if schedule_id:
        base = base.filter(ScheduleSwap.term_schedule_id == schedule_id)

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
