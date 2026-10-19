# -*- coding: utf-8 -*-
"""Flask 应用引导：创建 app 单例、准备执行身份。

认证有两条路径（见 core/auth.py 与 docs/mcp/MCP服务-认证与授权设计.md）：
  · account 模式（默认）：每个请求带自己的 StuLink 账号密码，认证通过后由
    core/identity.py 的请求上下文给出执行身份（toolkit 调 load_user(uid)）；
  · token 模式（兼容）：启动时校验 bind_user 并缓存其 id（warm_up），
    之后所有调用固定以该账号执行（default_user_id）。

两条路径都复用主应用现成的 has_perm / student_in_scope / class_allowed 等
权限与数据范围函数，不需要在 MCP 侧另写一套鉴权。
"""
import os
import sys

# 把项目根目录加入 sys.path，使 mcp_server 可直接 import app.*
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_app = None
_uid = None          # token 模式的执行账号 id（account 模式为 None）
_username = None
_info = None         # token 模式账号的展示信息（普通 dict，warm_up 幂等复用）


def get_app():
    """Flask app 单例（首次调用时 create_app()）。"""
    global _app
    if _app is None:
        from app import create_app
        _app = create_app()
    return _app


def prepare_app():
    """account 模式预热：只保证 app 单例可用（多用户模式没有固定账号要校验）。"""
    return get_app()


def warm_up(username):
    """token 模式预热：建 app、校验绑定账号存在且启用、缓存其 id（幂等）。

    提前失败好于运行期第一次调用工具才报错。切换账号名时会重新校验并更新缓存。
    """
    global _uid, _username, _info
    if _uid is not None and _username == username and _info is not None:
        return dict(_info)
    from app.models.user import User
    app = get_app()
    with app.app_context():
        user = User.query.filter_by(username=username).first()
        if user is None:
            raise RuntimeError(
                f'config.json 的 bind_user="{username}" 在系统中不存在，请改成有效账号名')
        if not user.is_active:
            raise RuntimeError(f'绑定账号 "{username}" 已停用，请更换 bind_user')
        _uid = user.id
        _username = username
        _info = {'id': user.id, 'username': user.username,
                 'real_name': user.real_name, 'role': user.role}
        return dict(_info)


def default_user_id():
    """token 模式的执行身份 id（account 模式为 None，由请求上下文提供身份）。"""
    return _uid


def load_user(user_id):
    """按 id 取回 User 实例（每次调用工具时执行；不存在/停用抛 RuntimeError）。

    必须每次重新查询：ORM 实例绑定在创建它的 app context 上，
    缓存实例会在后续 context 里变成分离对象而无法访问属性；
    同时"每次重新查"也保证停用/删除立即生效。
    """
    from app.extensions import db
    from app.models.user import User
    user = db.session.get(User, user_id)
    if user is None:
        raise RuntimeError(f'执行账号不存在（id={user_id}），请检查账号是否已被删除')
    if not user.is_active:
        raise RuntimeError(f'执行账号 "{user.username}" 已停用，请更换账号')
    return user


def load_bound_user():
    """token 模式：取回绑定的 User 实例（保持原有语义与调用方兼容）。"""
    if _uid is None:
        raise RuntimeError('执行身份未初始化：token 模式需先调用 bootstrap.warm_up(bind_user)')
    return load_user(_uid)
