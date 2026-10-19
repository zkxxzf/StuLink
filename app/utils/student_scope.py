# StuLink v1.18.0.0 2026-09-23
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""Student 查询的数据范围过滤（H-2 的唯一口径实现）。

背景（清单 H-2）：学生列表/搜索/导出里散落着内联的 `if role == 'homeroom_teacher'`
之类判断，`role == 'teacher'` 不命中任何分支 → 任课教师能拉全校名单；导出函数
（export_helpers）又是另一份更宽松的复制品。

本模块把「当前用户能看到哪些学生」收敛成一个函数，所有 Student 查询统一调用，
禁止再各自内联角色判断（清单第 0 节铁律②）。

P2（多身份改造）：底层原语（UserDataScope / UserClassLink / TeacherSubjectLink 查询）
统一委托 app.utils.scope；本函数保留原有角色分支与返回行为。
"""
from sqlalchemy import and_, or_

from app.models import Student
from app.utils.scope import (user_scope_detail, class_pairs, teaching_grades,
                             teaching_pairs)


def apply_student_scope(query, user=None):
    """把 Student 查询收敛到该用户可见范围，返回新的 query。

    admin / 全校范围 → 原样返回；无任何可见范围 → 过滤成空集（而非放行全校）。
    """
    if user is None:
        from flask_login import current_user
        user = current_user

    if getattr(user, 'role', None) == 'admin':
        return query

    from app.modules.grades.services.scope import get_scope, SCOPE_SCHOOL, SCOPE_GRADE

    # 1) 用户级数据范围（权限管理页第二张表）优先
    # 2026-10-14 第四轮审计修复（多身份 B 项）：吸收 UserDataScope.class_name 班级白名单 ——
    # 此前只按年级过滤，把"仅授权某班"放大为整个年级（与成绩侧 student_in_scope 口径分裂）。
    has_scope, grade_wide, pairs = user_scope_detail(user)
    if has_scope:
        if pairs and not grade_wide:
            return query.filter(or_(*[and_(Student.grade == g, Student.class_name == c)
                                      for g, c in sorted(pairs)]))
        # 含整年级授权（或混合形态）：按年级口径（数据完整优先，同 resolve_scope 取舍）
        return query.filter(Student.grade.in_(
            sorted(grade_wide | {g for g, _ in pairs})))

    # 2) 班主任 / 年级长沿用原有字段口径
    # 2026-10-19：管理年级可多选。班主任在保存时已强制只能 1 个年级
    # （一个班只属于一个年级），这里取首个防止历史脏数据跨年级同名班误伤。
    if user.role == 'homeroom_teacher':
        if not user.grades:
            return query.filter(Student.id == -1)
        return query.filter_by(grade=user.grades[0], class_name=user.class_name)
    if user.role == 'grade_leader':
        return query.filter(Student.grade.in_(user.grades or [])) \
            if user.grades else query.filter(Student.id == -1)

    # 3) 任课教师等：班级映射 > 任课映射年级 > 组级范围
    # 2026-10-18 审计修复（P1 数据范围过宽）：此前只取 UserClassLink（class_pairs），
    # **没有**并入 TeacherSubjectLink 里的精确班级（teaching_pairs），于是任课教师
    # 直接退化到"整个年级"。实测：licancan 只教 2024级 01/04 班，却能打开 02 班
    # 学生详情（页面含身份证号），可遍历全年级 30 个班。
    # 现在优先用精确班级（UserClassLink ∪ TeacherSubjectLink）；只有确实存在
    # "整年级任教"（class_name 为空）时才按年级兜底。
    pairs = class_pairs(user) | teaching_pairs(user)
    if pairs:
        return query.filter(or_(*[and_(Student.grade == g, Student.class_name == c)
                                  for g, c in sorted(pairs)]))
    grades = teaching_grades(user)
    if grades:
        return query.filter(Student.grade.in_(sorted(grades)))

    scope, grade = get_scope(user)
    if scope == SCOPE_SCHOOL:
        return query
    if scope == SCOPE_GRADE:
        from app.modules.grades.services.scope import get_scope_grades
        grades = get_scope_grades(user)
        if grades:
            return query.filter(Student.grade.in_(grades))
        return query.filter(Student.id == -1)
    # 无任何可见范围（如未配置身份/范围的账号）→ 空集，杜绝"回落全校"
    return query.filter(Student.id == -1)
