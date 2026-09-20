# StuLink v1.17.0 2026-09-20
# 课表公共辅助函数（schedule_service 与 swap_service 共享）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""课表模块公共辅助层。

抽取 schedule_service 与 swap_service 中重复的辅助函数，
两个 service 均从此处导入，避免逻辑分叉。
"""
import json
import re
from datetime import datetime

from app.extensions import db
from app.models.timetable import (
    TermSchedule, PeriodDef, ScheduleEntry, ScheduleVersion, ScheduleSwap,
)

# 周次探针上限：用于把"周次字符串"展开成周集合做交集判断（覆盖一学期的合理范围）
WEEK_PROBE_MAX = 40


def week_range_covers(week_range_str, week):
    """判断周次范围字符串是否覆盖指定周。

    支持写法："1-18"、"1-9,11-18"、"5"（单周）、"单周"、"双周"、"全周"、
    "1-18(单)"、空值/None（视为全周覆盖）；解析失败时保守地视为覆盖。
    """
    if week is None:
        return True
    try:
        week = int(week)
    except (ValueError, TypeError):
        return True
    s = (week_range_str or '').strip()
    if not s or s in ('全周', '全部', '每周'):
        return True
    # 单/双周标记（兼容 "单周"、"1-18(单)"、"1-18（双）" 等写法）
    parity = None
    if '单' in s:
        parity = 'odd'
    elif '双' in s:
        parity = 'even'
    if parity:
        if s in ('单周', '双周'):
            return (week % 2 == 1) if parity == 'odd' else (week % 2 == 0)
        # 带范围+奇偶标记：先继续解析范围，最后叠加奇偶过滤
    # 去括号内容后按逗号/顿号分段解析范围
    body = re.sub(r'[（(][^）)]*[）)]', '', s)
    covered = False
    parsed_any = False
    for seg in re.split(r'[,，、;；]', body):
        seg = seg.strip()
        if not seg:
            continue
        m = re.match(r'^(\d{1,2})\s*[-–—~～]\s*(\d{1,2})$', seg)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            if lo > hi:
                lo, hi = hi, lo
            parsed_any = True
            if lo <= week <= hi:
                covered = True
            continue
        m = re.match(r'^(\d{1,2})$', seg)
        if m:
            parsed_any = True
            if int(m.group(1)) == week:
                covered = True
            continue
        # 无法解析的片段：保守视为覆盖
        return True
    if not parsed_any:
        return True  # 整串都解析失败，保守视为覆盖
    if parity and covered:
        return (week % 2 == 1) if parity == 'odd' else (week % 2 == 0)
    return covered


def week_set(week_range_str, probe_max=WEEK_PROBE_MAX):
    """把周次字符串展开为覆盖周集合 {1..probe_max}；解析失败视为全周 = 全集。"""
    return {w for w in range(1, probe_max + 1)
            if week_range_covers(week_range_str, w)}


def week_ranges_overlap(range_a, range_b):
    """两个周次范围是否有交集。

    用于冲突判定：单周课与双周课占用同一格子并不冲突（常见排课方式）。
    任一为空/全周/无法解析 → 视为全周覆盖（保守判为有交集）。
    """
    return bool(week_set(range_a) & week_set(range_b))


def get_active_schedule():
    """返回当前学期。

    优先通过 term_service.resolve_schedule_by_date() 按日期定位当前学期
    （命中 is_current 或日期区间时生效）；未配置日期或解析异常时，行为完全
    等同原来（取 status='active' 的学期；无则取最新创建的；再无返回 None）。
    """
    try:
        from app.modules.academic.services import term_service
        sched, _week = term_service.resolve_schedule_by_date()
        if sched is not None:
            return sched
    except Exception:
        pass
    sched = (TermSchedule.query.filter_by(status='active')
             .order_by(TermSchedule.id.desc()).first())
    if sched:
        return sched
    return (TermSchedule.query
            .order_by(TermSchedule.created_at.desc(), TermSchedule.id.desc())
            .first())


def get_periods(schedule_id):
    """按 sort_order 返回该学期全部节次。"""
    return (PeriodDef.query.filter_by(term_schedule_id=schedule_id)
            .order_by(PeriodDef.sort_order, PeriodDef.period_number).all())


def pick_conflict(entries, week_range=None, week=None):
    """从候选条目中挑出**真正冲突**的一条（按周次过滤）。

    - week_range：待写入条目的周次范围；与之无周次交集的候选不算冲突
      （单周课与双周课可共用同一格子）。
    - week：只校验某一周（临时调课按具体日期所属教学周判断）。
    """
    for e in entries:
        if week_range is not None and not week_ranges_overlap(e.week_range, week_range):
            continue
        if week is not None and not week_range_covers(e.week_range, week):
            continue
        return e
    return None


def temp_target_ids(schedule_id):
    """临时调课产生的目标条目 id 集合。

    这些条目只在"调课当天"生效（见 swap_service.resolve_effective_entry /
    build_live_schedule），**不进常规周课表**，因此周课表/导出/统计统一排除它们。
    """
    rows = (ScheduleSwap.query
            .filter(ScheduleSwap.term_schedule_id == schedule_id,
                    ScheduleSwap.is_permanent.is_(False),
                    ScheduleSwap.target_entry_id.isnot(None))
            .with_entities(ScheduleSwap.target_entry_id).all())
    return {r[0] for r in rows if r[0]}


def snapshot_entry(entry):
    """生成条目快照 JSON 字符串（用于版本记录）。"""
    return json.dumps(entry.to_dict(), ensure_ascii=False, default=str)


def record_version(term_schedule_id, entry_id, action, operator_id,
                   operator_name, remark='', snapshot_json=None):
    """写入一条版本快照记录（不 commit，由调用方控制事务）。"""
    v = ScheduleVersion(
        term_schedule_id=term_schedule_id,
        entry_id=entry_id,
        action=action,
        snapshot_json=snapshot_json,
        operator_id=operator_id,
        operator_name=operator_name,
        remark=remark,
        operated_at=datetime.now(),
    )
    db.session.add(v)
    return v
