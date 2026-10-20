# StuLink v1.19.49.39 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序工作台：今日课程 / 今日监考 / 未读通知（首页第一屏）。

课程复用 `schedule_service.get_teacher_view`（已按教学周与 weekday 过滤，
单双周不会误显示）；监考取考务环节⑥的落库结果；通知复用未读数缓存。
任一子项异常都降级为空，不影响首页整体可用。
"""
import logging
from datetime import date, timedelta

from flask import g
from sqlalchemy import and_, or_

from app.extensions import db
from app.models import AffairInvigilation, AffairSchedule, ExamAffair
from app.modules.academic.services import schedule_service as svc
from app.modules.academic.services.schedule_common import get_active_schedule
from app.modules.wxapi import bp
from app.modules.wxapi.core.guards import wx_login_required
from app.modules.wxapi.core.response import wx_ok
from app.modules.wxapi.academic.routes_schedule import _current_week, _teacher_uid

logger = logging.getLogger(__name__)
WEEKDAYS = ('周一', '周二', '周三', '周四', '周五', '周六', '周日')


def _unread(user):
    """未读通知数（通知库异常降级为 0）。"""
    try:
        from app.modules.notifications.services import (
            notification_service as svc_notice,
        )
        return svc_notice.get_unread_count(user)
    except Exception as _e:  # noqa: BLE001 通知库异常不影响首页
        logger.warning('工作台取未读数失败: %s', _e)
        return 0


def _today_courses(user, today):
    """今日课程：按教学周过滤后的本人课表条目。"""
    ts = get_active_schedule()
    uid = _teacher_uid(user)
    if ts is None or not uid:
        return []
    try:
        view = svc.get_teacher_view(ts.id, uid,
                                   weekday=today.isoweekday(),
                                   week=_current_week(ts))
    except Exception as _e:  # noqa: BLE001 课表异常不影响首页
        logger.warning('工作台取今日课程失败: %s', _e)
        return []
    return [_course_item(e) for e in (view.get('entries') or [])]


def _course_item(entry):
    """课程条目：节次 / 学科 / 班级 / 教室 / 周次角标。

    注意：`get_teacher_view` 返回的 entries 已序列化为 dict，
    这里要兼容 dict 与 ORM 对象两种形态（否则字段全空）。
    """
    if isinstance(entry, dict):
        data = entry
    elif hasattr(entry, 'to_dict'):
        data = entry.to_dict()
    else:
        data = {}
    return {
        'period': data.get('period_number') or 0,
        'subject': data.get('subject') or '',
        'grade': data.get('grade') or '',
        'class_name': data.get('class_name') or '',
        'room': data.get('room') or '',
        'teaching_class': data.get('teaching_class') or '',
        'week_badge': data.get('week_badge') or '',
    }


def _invig_rows(user_id, start, end, real_name=''):
    """某人在 [start, end] 区间的监考记录。

    与考务接口同口径：旧排班可能只落姓名快照、`user_id` 为空，
    因此再按「姓名一致且 user_id 为空」兜底，避免首页漏掉监考。
    """
    cond = AffairInvigilation.user_id == user_id
    if real_name:
        # 2026-10-19：同 routes_exam._invig_rows —— 同名教师不启用姓名兜底
        from app.models import User as _U
        if _U.query.filter(_U.real_name == real_name).count() == 1:
            cond = or_(cond, and_(AffairInvigilation.user_id.is_(None),
                                  AffairInvigilation.name == real_name))
    return (db.session.query(AffairInvigilation, AffairSchedule, ExamAffair)
            .join(AffairSchedule,
                  AffairInvigilation.schedule_id == AffairSchedule.id)
            .join(ExamAffair, AffairInvigilation.affair_id == ExamAffair.id)
            .filter(cond,
                    AffairSchedule.exam_date >= start,
                    AffairSchedule.exam_date <= end).all())


def _today_invig(user, today):
    """今日监考（按开始时间排序）。"""
    out = []
    for inv, sch, affair in _invig_rows(
            user.id, today, today, real_name=user.real_name or ''):
        out.append({
            'subject': sch.subject or '',
            'start': sch.start_time or '',
            'end': sch.end_time or '',
            'room_no': inv.room_no or '',
            'slot': inv.slot or 1,
            'affair_name': affair.name or '',
        })
    out.sort(key=lambda x: x['start'])
    return out


def _week_invig_count(user, today):
    """未来 7 天的监考场次数。"""
    return len(_invig_rows(user.id, today, today + timedelta(days=7),
                           real_name=user.real_name or ''))


def _todos(user):
    """教师待办（换课待确认 / 问卷待填）；异常降级为空。"""
    try:
        from app.modules.academic.services import teacher_service
        from app.modules.workbench.services.todo_service import (
            get_teacher_todo,
        )
        teacher = teacher_service.teacher_of_user(user)
        data = get_teacher_todo(user, teacher)
        return data.get('todos') or []
    except Exception as _e:  # noqa: BLE001 任一库异常不影响首页
        db.session.rollback()
        logger.warning('工作台取待办失败: %s', _e)
        return []


@bp.route('/workbench')
@wx_login_required
def wx_workbench():
    """首页聚合：今日课程 / 今日监考 / 未读通知 / 本周监考数。"""
    today = date.today()
    user = g.wx_user
    todos = _todos(user)
    return wx_ok({
        'date': today.strftime('%Y-%m-%d'),
        'weekday': WEEKDAYS[today.isoweekday() - 1],
        'courses': _today_courses(user, today),
        'invigilations': _today_invig(user, today),
        'unread': _unread(user),
        'week_invig': _week_invig_count(user, today),
        'todos': todos,
        'todo_count': len(todos),
    })
