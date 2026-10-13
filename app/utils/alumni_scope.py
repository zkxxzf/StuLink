# StuLink v1.19.41.2 2026-10-12
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""往届（已毕业年级）数据可见范围——全项目审计 P1 的唯一口径。

背景：`/dormitory/statistics/alumni` 与 `/grade-mgmt/alumni` 两条往届查询路由
原先零数据范围校验，任意持 `statistics.view` / `system.grade_mgmt` 的账号可按
毕业年级拉取全校往届住宿分布 / 往届生全字段档案（H-2 在往届数据上的复活）。

口径与 `grades/services/scope.py::check_exam_visible` 对齐：
**已毕业年级只有 admin / 校级范围 / 用户级授权年级可见**；
年级长仅本人当前年级（该年级未毕业则实际为空）；
班主任 / 任课教师 / 无范围账号一律空集（fail-closed）。
"""
from app.modules.grades.services.scope import (
    user_grade_scope, get_scope, SCOPE_SCHOOL, SCOPE_GRADE)


def alumni_visible_grades(user):
    """可见毕业年级集合；None = 不限（admin / 校级范围）"""
    if getattr(user, 'role', None) == 'admin':
        return None
    ug = user_grade_scope(user)
    if ug is not None:
        return set(ug)
    scope, _ = get_scope(user)
    if scope == SCOPE_SCHOOL:
        return None
    if scope == SCOPE_GRADE:
        return {user.grade} if user.grade else set()
    # 班主任 / 任课教师 / 无有效范围：往届数据一律不可见
    return set()


def filter_alumni_grades(user, grades):
    """把库里的毕业年级列表收敛为当前用户可见（保持原顺序）"""
    allowed = alumni_visible_grades(user)
    if allowed is None:
        return list(grades)
    return [g for g in grades if g in allowed]


def assert_alumni_grade_visible(user, grade):
    """查询单个毕业年级前的校验；越界抛 PermissionError（调用方转 403）"""
    allowed = alumni_visible_grades(user)
    if allowed is None:
        return
    if grade not in allowed:
        raise PermissionError
