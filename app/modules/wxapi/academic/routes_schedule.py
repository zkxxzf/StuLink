# StuLink v1.19.53.64 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序课表接口（基础）：学期 / 节次 / 年级班级。

范围口径与网页端一致：管理员全校，其余按 `visible_academic_grades`
（教师所在 / 任教年级）收敛，避免用参数遍历全校课表。
班级 / 教师 / 本人三种视图在 routes_schedule_views.py。
"""
from flask import request
from flask_login import current_user

from app.extensions import db
from app.models import TermSchedule
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
from app.modules.wxapi.core.guards import wx_login_required, wx_perm_required
from app.modules.wxapi.core.response import wx_err, wx_ok

PERM = 'academic.view'
TERM_LIMIT = 30
TEACHER_LIMIT = 500


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


def _week(ts):
    """本次请求要展示的教学周：显式 week 优先，否则当前周。"""
    val = request.args.get('week', type=int)
    if val and val > 0:
        return val
    return _current_week(ts)


def _view_scope():
    """返回 (allowed_grades, allowed_classes)；None = 不限制。"""
    grades = visible_academic_grades(current_user)
    if grades is None:
        return None, None
    return set(grades), visible_academic_class_scope(current_user)


def _periods(ts):
    """节次列表（dict 形态，直接 JSON 化）。"""
    return [p.to_dict() for p in get_periods(ts.id)]


def _teacher_uid(user):
    """按账号关联的教师档案取教师编号（跨库 academic.db）。"""
    from app.models.academic import Teacher
    t = Teacher.query.filter_by(user_id=user.id).first()
    return t.teacher_uid if t is not None else None


@bp.route('/schedule/terms')
@wx_login_required
def wx_schedule_terms():
    """学期列表（供小程序切换历史 / 当前学期）。"""
    rows = svc.list_schedules()[:TERM_LIMIT]
    out = []
    for ts in rows:
        out.append({
            'id': ts.id,
            'name': ts.name,
            'status': ts.status,
            'is_current': bool(getattr(ts, 'is_current', False)),
            'current_week': _current_week(ts),
            'total_weeks': getattr(ts, 'total_weeks', None),
        })
    return wx_ok(out)


@bp.route('/schedule/current')
@wx_login_required
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
        'total_weeks': getattr(ts, 'total_weeks', None),
    })


@bp.route('/schedule/periods')
@wx_login_required
def wx_schedule_periods():
    """节次时间表（含起止时间与节次类型）。"""
    ts = _term(request.args.get('sid', type=int))
    if ts is None:
        return wx_err('暂无可用学期课表', 404)
    return wx_ok(_periods(ts))


@bp.route('/schedule/classes')
@wx_perm_required(PERM)
def wx_schedule_classes():
    """可见年级 + 班级列表（班级课表级联选择器用）。"""
    ts = _term(request.args.get('sid', type=int))
    if ts is None:
        return wx_err('暂无可用学期课表', 404)
    allowed_grades, allowed_classes = _view_scope()
    mapping = svc.get_grade_class_list(ts.id, allowed_grades=allowed_grades,
                                       allowed_classes=allowed_classes)
    return wx_ok([{'grade': g, 'classes': cs}
                  for g, cs in (mapping or {}).items()])
