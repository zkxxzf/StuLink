# StuLink v1.18.9.2 2026-10-10
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""请求级审计兜底网（after_request）。

背景：全库有 322 处写操作（db.session.add/commit/delete），但只有约 1/3
手工调用了 log_operation。逐个补埋点成本高、且将来新增接口还会再漏，
因此在这里统一兜底：任何写请求（POST/PUT/PATCH/DELETE）无论业务代码有没有
埋点，都会落一条 operation_logs，保证审计不留空洞。

去重：业务代码若已调用 log_operation，helper 会在 g 上置 `_audit_logged`，
本钩子直接跳过，同一请求不会产生两条重复记录。

不记录什么：表单/JSON 请求体（可能含密码、身份证等敏感信息），
只记 endpoint / method / path / 状态码 / request_id。
"""

from flask import g, request
from flask_login import current_user

_WRITE_METHODS = {'POST', 'PUT', 'PATCH', 'DELETE'}
_SKIP_ENDPOINTS = {'static'}
_ACTION_BY_METHOD = {'POST': '创建', 'PUT': '更新', 'PATCH': '更新', 'DELETE': '删除'}


def _target_type(endpoint):
    """endpoint 推导目标类型：system.students.create → students"""
    parts = (endpoint or '').split('.')
    if len(parts) >= 2:
        return parts[1][:30]
    return (endpoint or '')[:30]


def record_request_audit(response):
    """写请求兜底审计。任何异常一律静默，绝不阻断响应。"""
    if request.method not in _WRITE_METHODS:
        return
    endpoint = request.endpoint
    if not endpoint or endpoint in _SKIP_ENDPOINTS:
        return
    if getattr(g, '_audit_logged', False):
        return  # 业务代码已写过精确埋点

    status_code = getattr(response, 'status_code', None)
    if status_code and status_code >= 500:
        severity = 'ERROR'
    elif status_code and status_code >= 400:
        severity = 'WARNING'
    else:
        severity = 'INFO'

    from app.utils.helpers import log_operation
    log_operation(
        current_user if getattr(current_user, 'is_authenticated', False) else None,
        _ACTION_BY_METHOD.get(request.method, request.method),
        _target_type(endpoint),
        detail={'path': (request.path or '')[:500],
                'request_id': getattr(g, 'request_id', None)},
        module=(endpoint or 'system').split('.')[0][:30],
        severity=severity,
        endpoint=endpoint[:120],
        method=request.method[:10],
        status_code=status_code,
    )
