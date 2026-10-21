# StuLink v1.19.53.64 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序「学生画像 / 德育」列表：对齐网页 portrait.index 的筛选 + 卡片。

网页端是按年级/班级/评级筛选后看学生卡片，点卡片进详情；
小程序此前只有关键词搜索，这里补成同口径的浏览式列表。
学生范围仍走 `_student_query_in_scope`（fail-closed）。
"""
from flask import g, request
from sqlalchemy import or_

from app.extensions import db
from app.models import Student
from app.models.portrait import StudentPortrait
from app.modules.wxapi import bp
from app.modules.wxapi.core.guards import wx_login_required
from app.modules.wxapi.core.response import wx_err, wx_ok
from app.modules.wxapi.grades.routes_grades import _student_query_in_scope

LEVELS = ('A', 'B', 'C', 'D')
PER_PAGE = 20
MAX_PER_PAGE = 50


def _brief(s):
    return {
        'no': s.student_number,
        'name': s.name or '',
        'grade': s.grade or '',
        'class_name': s.class_name or '',
        'gender': s.gender or '',
        'selection': s.subject_selection or '',
    }


def _portrait(p):
    """只回卡片需要的画像字段，不下发整行。"""
    if p is None:
        return None
    return {
        'overall_level': p.overall_level or '',
        'academic_score': p.academic_score,
        'behavior_score': p.behavior_score,
        'dormitory_score': p.dormitory_score,
        'attendance_rate': p.attendance_rate,
        'last_updated': (p.last_updated.strftime('%Y-%m-%d')
                         if p.last_updated else ''),
    }


def _options(user):
    """可见年级 / 班级组合，供前端级联筛选。"""
    rows = (_student_query_in_scope(user)
            .with_entities(Student.grade, Student.class_name)
            .distinct().all())
    class_map = {}
    for grade, cls in rows:
        if not grade:
            continue
        class_map.setdefault(grade, set())
        if cls:
            class_map[grade].add(cls)
    grades = sorted(class_map)
    return {
        'grades': grades,
        'class_map': {k: sorted(v) for k, v in class_map.items()},
        'levels': list(LEVELS),
    }


@bp.route('/portrait/students')
@wx_login_required
def wx_portrait_students():
    """学生列表（query: q / grade / class_name / level / page / per_page）。"""
    user = g.wx_user
    can_portrait = user.has_perm('portrait.view')
    can_moral = (user.has_perm('points.view')
                 or user.has_perm('morality.view'))
    can_base = user.has_perm('grades.student_query')
    if not (can_portrait or can_moral or can_base):
        return wx_err('无权查看学生列表', 403)

    q = (request.args.get('q') or '').strip()
    grade = (request.args.get('grade') or '').strip()
    cls = (request.args.get('class_name') or '').strip()
    level = (request.args.get('level') or '').strip().upper()
    page = max(1, request.args.get('page', type=int) or 1)
    size = request.args.get('per_page', type=int) or PER_PAGE
    size = max(1, min(size, MAX_PER_PAGE))

    base = _student_query_in_scope(user)
    if q:
        base = base.filter(or_(
            Student.student_number.like('%' + q + '%'),
            Student.name.like('%' + q + '%')))
    if grade:
        base = base.filter(Student.grade == grade)
    if cls:
        base = base.filter(Student.class_name == cls)
    if level and can_portrait:
        if level not in LEVELS:
            return wx_err('评级只能是 A/B/C/D')
        nos = [row[0] for row in
               db.session.query(StudentPortrait.student_no)
               .filter(StudentPortrait.overall_level == level).all()]
        if nos:
            # SQLite 参数上限 999；按 500 分组 OR，避免大校评级筛选直接报错
            conds = [Student.student_number.in_(nos[i:i + 500])
                     for i in range(0, len(nos), 500)]
            base = base.filter(or_(*conds))
        else:
            base = base.filter(Student.id == -1)

    total = base.count()
    rows = (base.order_by(Student.grade.desc(), Student.class_name,
                          Student.student_number)
            .offset((page - 1) * size).limit(size).all())
    portraits = {}
    if can_portrait and rows:
        nos = [s.student_number for s in rows]
        for p in StudentPortrait.query.filter(
                StudentPortrait.student_no.in_(nos)).all():
            portraits[p.student_no] = p
    return wx_ok({
        'rows': [{
            'student': _brief(s),
            'portrait': _portrait(portraits.get(s.student_number)),
        } for s in rows],
        'total': total,
        'page': page,
        'per_page': size,
        'pages': (total + size - 1) // size,
        'options': _options(user),
        'caps': {
            'portrait': can_portrait,
            'moral': can_moral,
            'base': can_base,
        },
    })
