# StuLink v1.18.0.0 2026-09-23
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""成绩模块「考试/年级」范围校验守卫（H-4 的唯一入口）。

背景（清单 H-4）：考试详情/成绩页/删除、分档线、考务编排等 40 余条路由只有
`perm_required('grades.edit')` 之类功能权限，不校验**考试所属年级**，
2025 级年级长可直接看/删/改 2024 级的考试与考务。

本模块把「这个考试/这个年级我能不能碰」收敛成两个函数，路由层统一调用，
禁止再内联 `if role == 'grade_leader'` 之类的判断（清单第 0 节铁律②）。
"""
from flask import abort
from flask_login import current_user

from app.extensions import db
from app.models.grades import Exam, TeacherSubjectLink
from app.modules.grades.services.scope import (
    check_exam_visible, user_grade_scope, get_scope,
    SCOPE_SCHOOL, SCOPE_GRADE)


def assert_exam_visible(exam_id):
    """取考试并校验年级范围：不存在 → 404，越界 → 403"""
    exam = db.session.get(Exam, exam_id)
    if not exam:
        abort(404)
    try:
        check_exam_visible(current_user, exam)
    except PermissionError:
        abort(403)
    return exam


def assert_grade_visible(grade):
    """按年级校验可见性（考务批次等没有 Exam 对象、但有 grade 的资源）"""
    if current_user.role == 'admin':
        return True
    ug = user_grade_scope(current_user)
    if ug is not None:
        if grade not in ug:
            abort(403)
        return True
    if current_user.role == 'teacher':
        grades = {l.grade for l in
                  TeacherSubjectLink.query.filter_by(user_id=current_user.id,
                                                     active=True).all()}
        if grade not in grades:
            abort(403)
        return True
    scope, g = get_scope(current_user)
    if scope == SCOPE_SCHOOL:
        return True
    if scope == SCOPE_GRADE:
        if grade != g:
            abort(403)
        return True
    # 无有效数据范围（如未配置范围的身份）一律拒绝
    abort(403)
