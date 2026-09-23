# StuLink v1.18.0.0 2026-09-23
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""Student 查询的数据范围过滤（H-2 的唯一口径实现）。

背景（清单 H-2）：学生列表/搜索/导出里散落着内联的 `if role == 'homeroom_teacher'`
之类判断，`role == 'teacher'` 不命中任何分支 → 任课教师能拉全校名单；导出函数
（export_helpers）又是另一份更宽松的复制品。

本模块把「当前用户能看到哪些学生」收敛成一个函数，所有 Student 查询统一调用，
禁止再各自内联角色判断（清单第 0 节铁律②）。
"""
from sqlalchemy import and_, or_

from app.models import Student, UserClassLink
from app.models.grades import TeacherSubjectLink


def apply_student_scope(query, user=None):
    """把 Student 查询收敛到该用户可见范围，返回新的 query。

    admin / 全校范围 → 原样返回；无任何可见范围 → 过滤成空集（而非放行全校）。
    """
    if user is None:
        from flask_login import current_user
        user = current_user

    if getattr(user, 'role', None) == 'admin':
        return query

    from app.modules.grades.services.scope import (
        user_grade_scope, get_scope, SCOPE_SCHOOL, SCOPE_GRADE)

    # 1) 用户级数据范围（权限管理页第二张表）优先
    ug = user_grade_scope(user)
    if ug is not None:
        return query.filter(Student.grade.in_(ug))

    # 2) 班主任 / 年级长沿用原有字段口径
    if user.role == 'homeroom_teacher':
        return query.filter_by(grade=user.grade, class_name=user.class_name)
    if user.role == 'grade_leader':
        return query.filter_by(grade=user.grade)

    # 3) 任课教师等：班级映射 > 任课映射年级 > 组级范围
    pairs = [(l.grade, l.class_name)
             for l in UserClassLink.query.filter_by(user_id=user.id).all()]
    if pairs:
        return query.filter(or_(*[and_(Student.grade == g, Student.class_name == c)
                                  for g, c in pairs]))
    grades = {l.grade for l in
              TeacherSubjectLink.query.filter_by(user_id=user.id, active=True).all()}
    if grades:
        return query.filter(Student.grade.in_(grades))

    scope, grade = get_scope(user)
    if scope == SCOPE_SCHOOL:
        return query
    if scope == SCOPE_GRADE and grade:
        return query.filter_by(grade=grade)
    # 无任何可见范围（如未配置身份/范围的账号）→ 空集，杜绝"回落全校"
    return query.filter(Student.id == -1)
