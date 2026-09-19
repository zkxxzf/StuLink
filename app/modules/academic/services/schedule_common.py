# StuLink v1.16.0 2026-09-18
# 课表公共辅助函数（schedule_service 与 swap_service 共享）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""课表模块公共辅助层。

抽取 schedule_service 与 swap_service 中重复的辅助函数，
两个 service 均从此处导入，避免逻辑分叉。
"""
import json
from datetime import datetime

from app.extensions import db
from app.models.timetable import (
    TermSchedule, PeriodDef, ScheduleEntry, ScheduleVersion,
)


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
