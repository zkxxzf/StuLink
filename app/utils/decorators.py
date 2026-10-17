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


# 2026-10-10 架构审计：此处的 `scope_required(checker, perm)` 已删除。
# 它自 2026-09 定义后**从未被任何路由调用**（全仓库 grep 零命中），却在注释里
# 自称「H-2/H-3/H-4 及后续新路由的唯一入口」—— 这种"定义了却零调用的机制"
# 比没有机制更糟：它给维护者一种"已有防线"的错觉，实际各路由仍在内联
# `if current_user.role == ...`（全仓库 79 处）。
#
# 如需重建统一入口，请连同路由层的内联判断一起改造（见 docs/审计报告/架构审计报告_20261010.md
# 第 4.1 C2 项），不要只加装饰器。


