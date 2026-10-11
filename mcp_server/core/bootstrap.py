# -*- coding: utf-8 -*-
"""Flask 应用引导：创建 app 单例、加载绑定的服务账号。

MCP 服务不经过 HTTP 登录，所有操作以一个配置指定的 StuLink 账号身份执行，
因此现有权限校验（has_perm）与数据范围（student_in_scope / class_allowed）
可以原样复用，不需要在 MCP 侧另写一套鉴权。
"""
import os
import sys

# 把项目根目录加入 sys.path，使 mcp_server 可直接 import app.*
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_app = None
_uid = None          # 绑定账号的 id（不缓存 ORM 实例，避免跨 app context 持有分离对象）
_username = None
_info = None         # 绑定账号的展示信息（普通 dict，warm_up 幂等复用）


def get_app():
    """Flask app 单例（首次调用时 create_app()）。"""
    global _app
    if _app is None:
        from app import create_app
        _app = create_app()
    return _app


def warm_up(username):
    """启动时预热：建 app、校验绑定账号存在且启用、缓存其 id（幂等，重复调用只查一次库）。

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


def load_bound_user():
    """在当前 app context 内取回绑定的 User 实例（每次调用工具时执行）。

    必须每次重新查询：ORM 实例绑定在创建它的 app context 上，
    缓存实例会在后续 context 里变成分离对象而无法访问属性。
    """
    from app.extensions import db
    from app.models.user import User
    user = db.session.get(User, _uid)
    if user is None:
        raise RuntimeError(f'绑定账号已不存在（id={_uid}），请检查 config.json 的 bind_user')
    return user