# StuLink 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""L-04：统一的真实客户端 IP 口径。

只有请求来自 `TRUSTED_PROXIES` 里的地址（或配置为 `*`）时才读
`X-Real-IP` / `X-Forwarded-For`；直连部署永远用 `request.remote_addr`，
避免攻击者伪造转发头重置限流计数。
"""
from flask import current_app, request


def get_client_ip(default='0.0.0.0'):
    remote = request.remote_addr or default
    trusted = current_app.config.get('TRUSTED_PROXIES') or ()
    if not trusted or (remote not in trusted and '*' not in trusted):
        return remote
    fwd = (request.headers.get('X-Real-IP')
           or (request.headers.get('X-Forwarded-For') or '').split(',')[0].strip())
    return fwd or remote
