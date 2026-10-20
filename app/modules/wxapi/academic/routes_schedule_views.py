# StuLink v1.19.49.46 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序课表视图：教师课表 / 班级课表 / 本人课表。

所有视图都支持 `week` 参数（缺省＝当前教学周），供小程序左右切周。
"""
from flask import request
from flask_login import current_user

from app.models import ScheduleEntry
from app.modules.academic.services import schedule_service as svc
from app.modules.wxapi import bp
from app.modules.wxapi.core.guards import wx_login_required, wx_perm_required
from app.modules.wxapi.core.response import wx_err, wx_ok
from app.modules.wxapi.academic.routes_schedule import (
    PERM,
    TEACHER_LIMIT,
    _periods,
    _teacher_uid,
    _term,
    _view_scope,
    _week,
)


@bp.route('/schedule/teachers')
@wx_perm_required(PERM)
def wx_schedule_teachers():
    """教师名单（教师课表选择器用；q 支持姓名 / 工号模糊）。"""
    q = (request.args.get('q') or '').strip().lower()
    rows = svc.get_all_teachers() or []
    if q:
        rows = [t for t in rows
                if q in (t.get('name') or '').lower()
                or q in str(t.get('uid') or '').lower()]
    return wx_ok(rows[:TEACHER_LIMIT])


@bp.route('/schedule/teacher')
@wx_perm_required(PERM)
def wx_schedule_teacher():
    """指定教师的一周课表（query: uid / sid / week）。"""
    uid = (request.args.get('uid') or '').strip()
    if not uid:
        return wx_err('缺少教师工号 uid')
    ts = _term(request.args.get('sid', type=int))
    if ts is None:
        return wx_err('暂无可用学期课表', 404)
    allowed_grades, allowed_classes = _view_scope()
    week = _week(ts)
    view = svc.get_teacher_view(ts.id, uid, week=week,
                                allowed_grades=allowed_grades,
                                allowed_classes=allowed_classes)
    teacher = next((t for t in (svc.get_all_teachers() or [])
                    if str(t.get('uid')) == uid), None)
    return wx_ok({
        'mode': 'teacher',
        'teacher': teacher or {'uid': uid, 'name': '', 'subject': ''},
        'term': {'id': ts.id, 'name': ts.name},
        'week': week,
        'grid': view.get('grid') or {},
        'periods': _periods(ts),
        'stats': view.get('stats') or {},
        'total': view.get('total') or 0,
    })


@bp.route('/schedule/class')
@wx_perm_required(PERM)
def wx_schedule_class():
    """班级一周课表（query: grade / class_name / sid / weekday / week）。"""
    grade = (request.args.get('grade') or '').strip()
    class_name = (request.args.get('class_name') or '').strip()
    if not grade or not class_name:
        return wx_err('缺少 grade / class_name 参数')
    ts = _term(request.args.get('sid', type=int))
    if ts is None:
        return wx_err('暂无可用学期课表', 404)
    allowed_grades, allowed_classes = _view_scope()
    week = _week(ts)
    view = svc.get_class_view(ts.id, grade, class_name,
                              weekday=request.args.get('weekday', type=int),
                              week=week,
                              allowed_grades=allowed_grades,
                              allowed_classes=allowed_classes)
    return wx_ok({
        'mode': 'class',
        'term': {'id': ts.id, 'name': ts.name},
        'grade': grade,
        'class_name': class_name,
        'week': week,
        'grid': view.get('grid') or {},
        'periods': _periods(ts),
        'stats': view.get('stats') or {},
        'total': view.get('total') or 0,
    })


@bp.route('/schedule/my')
@wx_login_required
def wx_schedule_my():
    """本人课表：优先按教师编号取视图，无编号时按姓名匹配条目。"""
    ts = _term(request.args.get('sid', type=int))
    if ts is None:
        return wx_err('暂无可用学期课表', 404)
    return wx_ok(_my_schedule(ts))


def _my_schedule(ts):
    """组装本人课表：{'mode', 'grid'|'entries', 'periods', 'stats'}。"""
    uid = _teacher_uid(current_user)
    week = _week(ts)
    if uid:
        view = svc.get_teacher_view(ts.id, uid, week=week)
        return {
            'mode': 'uid',
            'teacher_uid': uid,
            'teacher_name': current_user.real_name or '',
            'term': {'id': ts.id, 'name': ts.name},
            'week': week,
            'grid': view.get('grid') or {},
            'periods': _periods(ts),
            'stats': view.get('stats') or {},
            'total': view.get('total') or 0,
        }
    rows = (ScheduleEntry.query
            .filter(ScheduleEntry.term_schedule_id == ts.id,
                    ScheduleEntry.is_deleted.is_(False),
                    ScheduleEntry.teacher_name == current_user.real_name)
            .order_by(ScheduleEntry.period_number).all())
    # 与教师视图同口径：按单双周 / 周次范围过滤（否则切周不生效）
    rows = svc._filter_by_week(rows, week)
    return {
        'mode': 'name',
        'teacher_name': current_user.real_name or '',
        'term': {'id': ts.id, 'name': ts.name},
        'week': week,
        'entries': [e.to_dict() for e in rows],
        'periods': _periods(ts),
        'stats': _count_subjects(rows),
        'total': len(rows),
    }


def _count_subjects(rows):
    """按学科统计课时数（与 grid 视图的 stats 同口径）。"""
    stats = {}
    for e in rows:
        subject = getattr(e, 'subject', '') or ''
        stats[subject] = stats.get(subject, 0) + 1
    return stats
