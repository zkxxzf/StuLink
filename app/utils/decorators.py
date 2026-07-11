# StuLink v1.6.1 2026-07-09
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


