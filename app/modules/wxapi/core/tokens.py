# StuLink v1.19.49.46 2026-10-18
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序访问令牌：签发与校验。

基于 itsdangerous 的 URLSafeTimedSerializer（密钥取 app 的 SECRET_KEY），
不落库、不依赖 cookie。载荷含 user_id 与身份组 id（gid），
gid 用于复用网页端的"多身份"权限判定。
"""
import time

from flask import current_app
from itsdangerous import BadSignature, SignatureExpired
from itsdangerous import URLSafeTimedSerializer

SALT = 'stulink-wx-token-v1'
DEFAULT_TTL = 12 * 3600        # 令牌默认有效期（秒）：12 小时
MAX_TTL = 30 * 24 * 3600       # 单令牌上限，防止请求超大 ttl


def _serializer():
    """按当前 app 的 SECRET_KEY 构造序列化器（每次请求新建，开销极小）。"""
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'],
                                  salt=SALT)


def issue_token(user_id, group_id=None, ttl=DEFAULT_TTL, openid=None, ver=0):
    """签发访问令牌。

    入参：user_id=用户 id；group_id=本次登录使用的身份组 id（可空）；
    ttl=有效期秒数（超出 MAX_TTL 会被截断）；openid=微信登录时的绑定标识；
    ver=用户当前 wx_token_ver（登出/改密/停用后递增，旧令牌立即失效）。
    返回：(token 字符串, 实际有效期秒数)。
    """
    ttl = max(60, min(int(ttl or DEFAULT_TTL), MAX_TTL))
    payload = {'uid': int(user_id), 'gid': group_id,
               'iat': int(time.time()), 'ver': int(ver or 0)}
    if openid:
        payload['oid'] = openid
    return _serializer().dumps(payload), ttl


def parse_token(token, max_age=DEFAULT_TTL):
    """校验令牌。

    入参：token=令牌字符串；max_age=允许的最大存活秒数。
    返回：成功为 payload dict（{'uid','gid','iat'}），任何失败均为 None。
    """
    if not token:
        return None
    try:
        return _serializer().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    except Exception:  # noqa: BLE001 密钥轮换等异常一律视为令牌无效
        return None
