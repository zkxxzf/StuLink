# StuLink v1.18.1.1 2026-09-24
# 工作台数据范围校验：班级归属白名单（admin 全放行）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""⚠️ 本模块已降级为**转发层**，请勿在此新增判定逻辑（R-1）。

历史问题：这里曾有一套「只看 UserClassLink」的班级归属判定，与
`workbench/services/scope.py`（委托成绩模块 `grades/services/scope.py`）并存，
正是清单第 0 节所说的「同类校验写两份 → 漂移」来源。

现在所有判定统一委托 `app.modules.workbench.services.scope`，本文件仅在
`workbench/routes/attendance.py` 等历史调用点保留同名函数，避免一次性改崩调用方。
"""
from app.modules.workbench.services.scope import class_allowed, managed_class_pairs as _managed_pairs


def managed_class_pairs(user):
    """保持历史语义：admin 返回 None（表示不限制），其余返回 (grade, class_name) 集合"""
    if not user or getattr(user, 'role', None) == 'admin':
        return None
    return {(g, c) for g, c in (_managed_pairs(user) or [])}


def is_class_in_scope(user, grade, class_name):
    """(grade, class_name) 是否在用户范围内 —— 委唯一口径 class_allowed"""
    return class_allowed(user, grade, class_name)


def scope_denied_response():
    """统一的越权响应（API 用）"""
    return {'success': False, 'message': '无该班级的访问权限（超出管辖范围）'}, 403
