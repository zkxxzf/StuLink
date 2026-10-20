# StuLink v1.19.49.44 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序学生档案：画像 / 积分 / 奖惩 / 考勤（只读）。

权限按模块分别判定，未授权的板块直接不返回（前端也不渲染）；
学生归属统一走 `grades.services.scope.student_in_scope`（fail-closed）。
"""
from flask import g, request
from sqlalchemy import func

from app.extensions import db
from app.models import Student
from app.models.academic import ATTENDANCE_STATUS, AttendanceRecord
from app.models.morality import RewardPunishment
from app.models.points import PointRecord
from app.models.portrait import StudentPortrait
from app.modules.grades.services.scope import student_in_scope
from app.modules.morality.services import reward_service as reward_svc
from app.modules.portrait.services import portrait_aggregation as agg
from app.modules.wxapi import bp
from app.modules.wxapi.core.guards import wx_login_required
from app.modules.wxapi.core.response import wx_err, wx_ok

ATTEND_TEXT = dict(ATTENDANCE_STATUS)


def _brief(student):
    """学生基础信息（跨模块共用的最小字段）。"""
    return {
        'no': student.student_number,
        'name': student.name,
        'grade': student.grade or '',
        'class_name': student.class_name or '',
        'gender': student.gender or '',
        'selection': student.subject_selection or '',
    }


def _points_of(user, no):
    """积分汇总 + 最近 20 条；无 points.view 返回 None。"""
    if not user.has_perm('points.view'):
        return None
    summary = agg.get_points_summary(no)
    rows = (PointRecord.query.filter_by(student_no=no)
            .order_by(PointRecord.recorded_at.desc(),
                      PointRecord.id.desc())
            .limit(20).all())
    return {
        'total': int(summary.get('total') or 0),
        'this_month': int(summary.get('this_month') or 0),
        'this_semester': int(summary.get('this_semester') or 0),
        'categories': summary.get('category_breakdown') or [],
        'shown': len(rows),
        'records': [{
            'id': r.id,
            'date': r.recorded_at.strftime('%Y-%m-%d')
            if r.recorded_at else '',
            'points': r.points,
            'category': r.category or '其他',
            'reason': r.reason or '',
            'operator': r.operator_name or '',
        } for r in rows],
    }


def _rewards_of(user, no):
    """奖惩记录；无 morality.view 返回 None。"""
    if not user.has_perm('morality.view'):
        return None
    base = reward_svc.apply_scope(
        RewardPunishment.query.filter_by(student_no=no), user)
    total = base.count()
    rows = (base.order_by(RewardPunishment.happen_date.desc(),
                          RewardPunishment.id.desc())
            .limit(20).all())
    return {'count': total, 'shown': len(rows),
            'items': [r.to_dict() for r in rows]}


def _attendance_of(user, no):
    """考勤统计 + 最近 30 条；无 workbench.attendance_view 返回 None。"""
    if not user.has_perm('workbench.attendance_view'):
        return None
    grouped = (db.session.query(AttendanceRecord.status,
                                func.count(AttendanceRecord.id))
               .filter(AttendanceRecord.student_no == no)
               .group_by(AttendanceRecord.status).all())
    counts = {code: 0 for code, _label in ATTENDANCE_STATUS}
    for code, cnt in grouped:
        counts[code or ''] = int(cnt or 0)
    rows = (AttendanceRecord.query.filter_by(student_no=no)
            .order_by(AttendanceRecord.attend_date.desc(),
                      AttendanceRecord.id.desc())
            .limit(30).all())
    return {
        'total': sum(counts.values()),
        'shown': len(rows),
        'counts': counts,
        'records': [{
            'id': r.id,
            'date': r.attend_date.strftime('%Y-%m-%d')
            if r.attend_date else '',
            'period': r.period,
            'period_text': '全天' if r.period is None
            else '第%d节' % r.period,
            'status': r.status or '',
            'status_text': ATTEND_TEXT.get(r.status, r.status or '—'),
            'remark': r.remark or '',
        } for r in rows],
    }


def _portrait_of(user, no):
    """画像评分 + 评语 + 事件；无 portrait.view 返回 (None, [], [])。

    评语/事件只回前端实际展示的字段，不下发 teacher_id / created_by。
    """
    if not user.has_perm('portrait.view'):
        return None, [], []
    rec = StudentPortrait.query.filter_by(student_no=no).first()
    comments = agg.get_portrait_comments(no)[:20]
    events = agg.get_portrait_events(no)[:20]
    return (
        rec.to_dict() if rec else None,
        [{k: c.get(k) for k in
          ('id', 'comment_type', 'content', 'term', 'created_at')}
         for c in comments],
        [{k: e.get(k) for k in
          ('id', 'event_type', 'title', 'description', 'event_date')}
         for e in events],
    )


@bp.route('/student/profile')
@wx_login_required
def wx_student_profile():
    """学生档案聚合接口（只读，按模块权限裁剪）。"""
    no = (request.args.get('student_no') or '').strip()
    if not no:
        return wx_err('缺少学号')
    student = Student.query.filter_by(student_number=no).first()
    if student is None:
        return wx_err('学生不存在', 404)
    try:
        student_in_scope(g.wx_user, no)
    except PermissionError:
        return wx_err('无权查看该学生档案', 403)

    user = g.wx_user
    points = _points_of(user, no)
    rewards = _rewards_of(user, no)
    attendance = _attendance_of(user, no)
    portrait, comments, events = _portrait_of(user, no)
    return wx_ok({
        'student': _brief(student),
        'sections': {
            'portrait': user.has_perm('portrait.view'),
            'points': user.has_perm('points.view'),
            'rewards': user.has_perm('morality.view'),
            'attendance': user.has_perm('workbench.attendance_view'),
        },
        # 没有成绩权限时，前端隐藏「成绩」入口，避免点进去 403
        'can_grades': user.has_perm('grades.student_query'),
        'portrait': portrait,
        'points': points,
        'rewards': rewards,
        'attendance': attendance,
        'comments': comments,
        'events': events,
    })
