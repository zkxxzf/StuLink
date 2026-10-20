# StuLink v1.19.49.46 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序调课：申请人解析、本人课程、调课单的小程序字段。

只做「取数 + 压缩字段」，审批 / 冲突判定 / 执行全部复用
`app.modules.academic.services.swap_service`，不复制任何业务规则。
"""
from app.models.timetable import ScheduleEntry, WEEKDAY_NAMES
from app.modules.academic.services import swap_service, teacher_service
from app.modules.academic.services.schedule_common import get_active_schedule


def applicant(user):
    """当前账号对应的申请人 (uid, name)；无教师档案时返回 (None, name)。

    2026-10-19 审计：原实现把登录名当 teacher_uid 兜底，手工建号若与某
    教师 teacher_uid 同名即可冒充对方调课。无档案一律 fail-closed：
    调用方按空集处理（我的课程为空、不能确认别人的申请）。
    """
    t = teacher_service.teacher_of_user(user)
    if t:
        return t.teacher_uid, t.name
    return None, user.real_name or ''


def active_schedule():
    """当前在用学期课表；没有则返回 None（页面按空态处理）。"""
    return get_active_schedule()


def my_entries(uid):
    """本学期本人任教的课表条目（排除临时调课生成的条目）。"""
    sched = active_schedule()
    if sched is None or not uid:
        return []
    temp_ids = swap_service._temp_target_ids(sched.id)
    rows = (ScheduleEntry.query
            .filter(ScheduleEntry.term_schedule_id == sched.id,
                    ScheduleEntry.teacher_uid == uid,
                    ScheduleEntry.is_deleted.is_(False))
            .order_by(ScheduleEntry.weekday,
                      ScheduleEntry.period_number).all())
    return [e for e in rows if e.id not in temp_ids]


def entry_item(entry):
    """课表条目 → 小程序卡片字段（课程 / 班级 / 节次）。"""
    return {
        'id': entry.id,
        'weekday': entry.weekday,
        'weekday_text': WEEKDAY_NAMES.get(entry.weekday, ''),
        'period': entry.period_number,
        'subject': entry.subject or '',
        'class_text': '%s%s' % (entry.grade or '', entry.class_name or ''),
        'room': entry.room or '',
        'week_badge': entry.week_badge(),
    }


def swap_item(d, uid):
    """调课 dict → 小程序列表 / 详情字段（含可撤回、可确认标记）。"""
    pending = d.get('status') == 'pending'
    return {
        'id': d.get('id'),
        'status': d.get('status'),
        'status_text': d.get('status_text') or '',
        'swap_type_text': d.get('swap_type_text') or '',
        'exchange_kind_text': d.get('exchange_kind_text') or '',
        'applicant_name': d.get('applicant_name') or '',
        'partner_name': d.get('partner_name') or '',
        'partner_status': d.get('partner_status'),
        'partner_status_text': d.get('partner_status_text') or '',
        'original_subject': d.get('original_subject') or '',
        'original_class': d.get('original_class') or '',
        'original_slot': d.get('original_slot') or '',
        'target_slot': d.get('target_slot') or '',
        'target_keep_text': d.get('target_keep_text') or '',
        'target_period_label': d.get('target_period_label') or '',
        'is_temp': bool(d.get('is_temp')),
        'swap_date': d.get('swap_date') or '',
        'source_date': d.get('source_date') or '',
        'reason': d.get('reason') or '',
        'review_note': d.get('review_note') or '',
        'created_at': d.get('created_at') or '',
        'batch_count': d.get('batch_count') or 1,
        'approvals': d.get('approvals') or [],
        'can_cancel': bool(pending and d.get('applicant_uid') == uid),
        'can_confirm': bool(pending and d.get('partner_uid') == uid
                            and d.get('partner_status') == 'pending'),
    }
