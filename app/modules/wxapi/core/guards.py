# StuLink v1.19.49.45 2026-10-18
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序接口守卫：令牌校验、身份装载、权限校验、限流。

设计要点：校验通过后调用 flask-login 的 login_user 装载会话，
使既有服务层里所有依赖 `current_user` / `session['active_group_id']`
的逻辑（权限、数据范围）无需改动即可复用。
"""
import time
from functools import wraps

from flask import current_app, g, request, session
from flask_login import login_user

from app.extensions import db
from app.models import User
from app.modules.wxapi.core.response import CODE_TOO_MANY, wx_err
from app.modules.wxapi.core.tokens import parse_token
from app.utils.cache import cache

TOKEN_HEADERS = ('X-Wx-Token',)

# 初始口令未修改的账号：只放行引导类接口，业务接口一律 403
# （小程序不提供改密入口，改密必须走网页端，避免绕过强制改密策略）
MUST_CHANGE_ALLOW = ('wxapi.wx_bootstrap', 'wxapi.wx_profile')


def client_ip():
    """取客户端 IP；只有来自可信反向代理时才信转发头。

    2026-10-19 审计：原实现无条件信任 ``X-Real-IP``，攻击者每次随机填一个
    就能重置 IP 级与账号级限流计数（登录可无限猜口令）。``TRUSTED_PROXIES``
    为空（默认）＝直连部署，永不读转发头；反代部署请显式配置代理地址。
    """
    remote = request.remote_addr or '0.0.0.0'
    trusted = current_app.config.get('TRUSTED_PROXIES') or ()
    if not trusted or (remote not in trusted and '*' not in trusted):
        return remote
    fwd = (request.headers.get('X-Real-IP')
           or (request.headers.get('X-Forwarded-For') or '').split(',')[0].strip())
    return fwd or remote


def hit_limit(key, limit, window):
    """滑动窗口限流：窗口内已命中 limit 次则返回 False。

    入参：key=限流维度键（自行带上 ip / 账号等）；limit=窗口内允许次数；
    window=窗口秒数。返回：True=放行，False=拒绝。
    """
    now = time.time()
    cache_key = 'wxrl_%s' % key
    hits = [t for t in (cache.get(cache_key) or []) if now - t < window]
    if len(hits) >= limit:
        cache.set(cache_key, hits, timeout=window)
        return False
    hits.append(now)
    cache.set(cache_key, hits, timeout=window)
    return True


def _extract_token():
    """从 X-Wx-Token 头或 Authorization: Bearer 中提取令牌。"""
    for name in TOKEN_HEADERS:
        val = request.headers.get(name)
        if val:
            return val.strip()
    auth = request.headers.get('Authorization') or ''
    if auth.lower().startswith('bearer '):
        return auth[7:].strip()
    return None


def _load_user(payload):
    """按载荷取用户；不存在、未启用或令牌版本过期返回 None。"""
    user = db.session.get(User, payload.get('uid'))
    if user is None or not user.is_active:
        return None
    # P1 M-03：登出/改密/停用会递增 wx_token_ver；旧令牌的 ver 不再匹配。
    if int(payload.get('ver') or 0) != int(user.wx_token_ver or 0):
        return None
    return user


def _attach_identity(user, group_id):
    """装载登录态与身份组（复用网页端多身份判定）。

    入参：user=用户对象；group_id=请求的身份组 id（须属于该用户）。
    注意：必须写 session_guard 的口令摘要，否则下一个请求会被全局
    `_enforce_session_validity` 判定为失效会话并 302 到登录页。
    """
    from app.utils.session_guard import stamp
    login_user(user, remember=False, fresh=True)
    stamp(user)
    if group_id:
        owned = {l.group_id for l in user.group_links}
        if group_id in owned:
            session['active_group_id'] = group_id


def wx_login_required(f):
    """要求携带有效令牌；通过后 `g.wx_user` 即当前用户。"""

    @wraps(f)
    def wrapper(*args, **kwargs):
        payload = parse_token(_extract_token())
        if not payload:
            return wx_err('登录已失效，请重新登录', 401)
        user = _load_user(payload)
        if user is None:
            return wx_err('账号不可用', 401)
        if not hit_limit('ip_%s' % client_ip(), 600, 300):
            return wx_err('请求过于频繁，请稍后再试', CODE_TOO_MANY)
        if _must_change_blocked(user):
            return wx_err('首次登录请先在电脑端修改密码后再使用', 403)
        _attach_identity(user, payload.get('gid'))
        g.wx_user = user
        g.wx_openid = payload.get('oid') or ''
        g.wx_group_id = payload.get('gid')
        return f(*args, **kwargs)

    return wrapper


def _must_change_blocked(user):
    """是否因「初始口令未修改」而拦截本次请求。

    引导类接口（bootstrap / profile）放行，好让前端能拿到标记并提示；
    其余业务接口一律拦截，避免绕过网页端的强制改密策略。
    """
    if not user.must_change_pwd:
        return False
    return request.endpoint not in MUST_CHANGE_ALLOW


def wx_perm_required(perm_key):
    """要求当前身份具备指定权限键（admin 恒通过）。"""

    def decorator(f):
        @wraps(f)
        @wx_login_required
        def wrapper(*args, **kwargs):
            if not g.wx_user.has_perm(perm_key):
                return wx_err('暂无该模块的访问权限', 403)
            return f(*args, **kwargs)

        return wrapper

    return decorator
