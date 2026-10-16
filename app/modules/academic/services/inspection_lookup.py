# -*- coding: utf-8 -*-
"""查课录入「按教师反查课程」（2026-10-14 用户口径）。

需求（用户）：录入查课时，选中教师后**自动追踪**出班级与科目，仍可手动修改。

口径与查课核对页（swap_service.build_live_schedule / resolve_effective_entry）同源：
  ① 常规周课表：按 weekday 命中，且 week_range 覆盖该日期所属教学周
     （支持单双周 / 跳周），排除「临时调课目标条目」；
  ② 叠加当天已执行的临时调课：被调走（含两人换课的对方侧原条目）的课不计入；
     当天调入的条目（含「请人代课」生成的当天条目）按条目上的实际任课教师计入。

返回按节次升序的列表（同一节次可能有多条：合班 / 走班场景，前端取首条并提示）：
  [{period, grade, class_name, subject, room, teacher_uid, teacher_name,
    entry_id, is_temp_swap}]
"""
from app.extensions import db
from app.models.timetable import ScheduleEntry, TermSchedule
from app.modules.academic.services.schedule_common import (
    temp_target_ids, week_range_covers,
)


def _to_row(entry_dict, is_temp_swap):
    """课表条目 dict → 查课录入所需的最小字段。"""
    return {
        'period': entry_dict.get('period_number'),
        'grade': entry_dict.get('grade') or '',
        'class_name': entry_dict.get('class_name') or '',
        'subject': entry_dict.get('subject') or '',
        'room': entry_dict.get('room') or '',
        'teacher_uid': entry_dict.get('teacher_uid') or '',
        'teacher_name': entry_dict.get('teacher_name') or '',
        'entry_id': entry_dict.get('id'),
        'is_temp_swap': bool(is_temp_swap),
    }


def teacher_courses_on(schedule_id, target_date, teacher_uid):
    """该教师在某天『实际生效』的课列表（常规周课表 + 当天临时调课 / 代课叠加）。

    无课 / 学期课表不存在 / 参数不全 → 返回 []（调用方无需区分，前端按空处理）。
    """
    uid = (teacher_uid or '').strip()
    if not schedule_id or not target_date or not uid:
        return []
    weekday = target_date.isoweekday()
    sched = db.session.get(TermSchedule, schedule_id)
    # 未配置学期起止日期时 get_week_number 返回 None，
    # week_range_covers(None 周) 保守视为覆盖（宁可多带出，也不漏课）
    week = sched.get_week_number(target_date) if sched else None
    temp_ids = temp_target_ids(schedule_id)

    items = {}                      # entry_id → row（dict）
    rows = (ScheduleEntry.query
            .filter(ScheduleEntry.term_schedule_id == schedule_id,
                    ScheduleEntry.teacher_uid == uid,
                    ScheduleEntry.weekday == weekday,
                    ScheduleEntry.is_deleted.is_(False))
            .order_by(ScheduleEntry.period_number, ScheduleEntry.id).all())
    for e in rows:
        # 临时调课产生的条目只在调课当天生效，不进常规周课表口径（见 schedule_common）
        if e.id in temp_ids:
            continue
        if not week_range_covers(e.week_range, week):
            continue
        items[e.id] = _to_row(e.to_dict(), False)

    # 函数内导入：swap_service 体量大，且避免模块级导入环的风险
    from app.modules.academic.services.swap_service import get_temp_swaps_by_date
    for ts in get_temp_swaps_by_date(schedule_id, target_date):
        # ① 当天被调走（含两人换课的对方侧）的课：原条目不再计入
        for key in ('original_entry', 'partner_original_entry'):
            oe = ts.get(key)
            if oe and oe.get('id'):
                items.pop(oe['id'], None)
        # ② 当天调入（含请人代课）的课：按条目上的实际任课教师计入
        for key in ('target_entry', 'partner_target_entry'):
            te = ts.get(key)
            if not te or te.get('weekday') != weekday:
                continue
            if (te.get('teacher_uid') or '').strip() != uid:
                continue
            items[te['id']] = _to_row(te, True)

    return sorted(items.values(),
                  key=lambda r: (r['period'] or 0, r['entry_id'] or 0))
