# StuLink v1.19.49.35 2026-10-18
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序课表接口：当前学期 / 节次 / 班级课表 / 本人课表。

范围口径与网页端一致：管理员全校，其余按 `visible_academic_grades`
（教师所在 / 任教年级）收敛，避免用参数遍历全校课表。
"""
from flask import request
from flask_login import current_user

from app.extensions import db
from app.models import ScheduleEntry, TermSchedule
from app.modules.academic.services import schedule_service as svc
from app.modules.academic.services.access_scope import (
    visible_academic_class_scope,
    visible_academic_grades,
)
from app.modules.academic.services.schedule_common import (
    get_active_schedule,
    get_periods,
)
from app.modules.wxapi import bp
from app.modules.wxapi.guards import wx_perm_required
from app.modules.wxapi.response import wx_err, wx_ok

PERM = 'academic.view'


def _term(sid=None):
    """取学期对象：指定 sid 优先，否则取当前学期。"""
    if sid:
        ts = db.session.get(TermSchedule, sid)
        if ts is not None:
            return ts
    return get_active_schedule()


def _current_week(ts):
    """当前教学周；无法确定时返回 None（服务层按"不过滤"处理）。"""
    try:
        return ts.get_current_week()
    except Exception:  # noqa: BLE001 周末 / 未配日期时取不到，不影响查课表
        return None


def _view_scope():
    """返回 (allowed_grades, allowed_classes)；None = 不限制。"""
    grades = visible_academic_grades(current_user)
    if grades is None:
        return None, None
    return set(grades), visible_academic_class_scope(current_user)


@bp.route('/schedule/current')
@wx_perm_required(PERM)
def wx_schedule_current():
    """当前学期信息（含当前教学周）。"""
    ts = _term(request.args.get('sid', type=int))
    if ts is None:
        return wx_err('暂无可用学期课表', 404)
    return wx_ok({
        'id': ts.id,
        'name': ts.name,
        'status': ts.status,
        'current_week': _current_week(ts),
    })


@bp.route('/schedule/periods')
@wx_perm_required(PERM)
def wx_schedule_periods():
    """节次时间表（含起止时间与节次类型）。"""
    ts = _term(request.args.get('sid', type=int))
    if ts is None:
        return wx_err('暂无可用学期课表', 404)
    return wx_ok([p.to_dict() for p in get_periods(ts.id)])


@bp.route('/schedule/class')
@wx_perm_required(PERM)
def wx_schedule_class():
    """班级一周课表（query: grade / class_name / sid / weekday）。"""
    grade = (request.args.get('grade') or '').strip()
    class_name = (request.args.get('class_name') or '').strip()
    if not grade or not class_name:
        return wx_err('缺少 grade / class_name 参数')
    ts = _term(request.args.get('sid', type=int))
    if ts is None:
        return wx_err('暂无可用学期课表', 404)
    allowed_grades, allowed_classes = _view_scope()
    view = svc.get_class_view(ts.id, grade, class_name,
                              weekday=request.args.get('weekday', type=int),
                              week=_current_week(ts),
                              allowed_grades=allowed_grades,
                              allowed_classes=allowed_classes)
    return wx_ok({
        'term': {'id': ts.id, 'name': ts.name},
        'week': _current_week(ts),
        'grid': view.get('grid') or {},
        'periods': [p.to_dict() for p in get_periods(ts.id)],
        'total': view.get('total') or 0,
    })


@bp.route('/schedule/my')
@wx_perm_required(PERM)
def wx_schedule_my():
    """本人课表：优先按教师编号取视图，无编号时按姓名匹配条目。"""
    ts = _term(request.args.get('sid', type=int))
    if ts is None:
        return wx_err('暂无可用学期课表', 404)
    return wx_ok(_my_schedule(ts))


def _my_schedule(ts):
    """组装本人课表：{'mode', 'grid'|'entries', 'periods'}。"""
    uid = _teacher_uid(current_user)
    if uid:
        view = svc.get_teacher_view(ts.id, uid, week=_current_week(ts))
        return {
            'mode': 'uid',
            'teacher_uid': uid,
            'grid': view.get('grid') or {},
            'periods': [p.to_dict() for p in get_periods(ts.id)],
            'total': view.get('total') or 0,
        }
    rows = (ScheduleEntry.query
            .filter(ScheduleEntry.term_schedule_id == ts.id,
                    ScheduleEntry.is_deleted.is_(False),
                    ScheduleEntry.teacher_name == current_user.real_name)
            .order_by(ScheduleEntry.period_number).all())
    return {
        'mode': 'name',
        'entries': [e.to_dict() for e in rows],
        'periods': [p.to_dict() for p in get_periods(ts.id)],
    }


def _teacher_uid(user):
    """按账号关联的教师档案取教师编号（跨库 academic.db）。"""
    from app.models.academic import Teacher
    t = Teacher.query.filter_by(user_id=user.id).first()
    return t.teacher_uid if t is not None else None
