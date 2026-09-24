# StuLink v1.18.2.0 2026-09-24
# 工作台数据范围校验：班级归属白名单（admin 全放行）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from app.models import UserClassLink


def managed_class_pairs(user):
    """当前用户管辖的 (grade, class_name) 集合；admin/系统管理员返回 None 表示不限制"""
    if user.role == 'admin':
        return None
    links = UserClassLink.query.filter_by(user_id=user.id).all()
    return {(lk.grade, lk.class_name) for lk in links}


def is_class_in_scope(user, grade, class_name):
    """校验 (grade, class_name) 是否在当前用户管辖范围内（admin 全通过）"""
    allowed = managed_class_pairs(user)
    if allowed is None:
        return True
    if not grade:
        return class_name in {c for _, c in allowed}
    return (grade, class_name) in allowed


def scope_denied_response():
    """统一的越权响应（API 用）"""
    return {'success': False, 'message': '无该班级的访问权限（超出管辖范围）'}, 403
