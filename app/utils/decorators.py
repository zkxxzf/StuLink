# StuLink v1.7.0 2026-08-02
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from functools import wraps
from flask import abort
from flask_login import current_user, login_required


def role_required(*roles):
    """权限装饰器：限制只有指定角色可以访问（向后兼容）"""
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated_function(*args, **kwargs):
            if current_user.role not in roles:
                abort(403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def perm_required(perm_key):
    """模块化权限装饰器：检查用户所在组是否包含指定权限key"""
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated_function(*args, **kwargs):
            if not current_user.has_perm(perm_key):
                abort(403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def scope_required(checker=None, perm=None):
    """R-1：统一数据范围装饰器（H-2/H-3/H-4 及后续新路由的唯一入口）

    同类校验不写两份（清单第 0 节铁律②）：路由层不再内联 `if role == 'teacher'`
    或各自拼 `student_in_scope`，统一走本装饰器 + `grades/services/scope.py`。

    :param perm: 可选的功能权限 key（先鉴权，再校验数据范围）
    :param checker: `checker(*args, **kwargs)`；返回 False 或抛 PermissionError
                    视为越界 → 403；返回 True/None 放行
    """
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated_function(*args, **kwargs):
            if perm and not current_user.has_perm(perm):
                abort(403)
            if checker is not None:
                try:
                    allowed = checker(*args, **kwargs)
                except PermissionError:
                    allowed = False
                if allowed is False:
                    abort(403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator


