# StuLink v1.19.50.56 2026-10-18
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序接口统一响应封装。

沿用教务模块 `_json_ok/_json_err` 的 `{success, message, data}` 约定，
额外补 `code`（业务码），方便小程序端按码分支而无需解析文案。
"""
from flask import jsonify

CODE_OK = 0
CODE_BAD_REQUEST = 400
CODE_UNAUTHORIZED = 401
CODE_FORBIDDEN = 403
CODE_NOT_FOUND = 404
CODE_TOO_MANY = 429
CODE_SERVER_ERROR = 500


def wx_ok(data=None, message='ok'):
    """构造成功响应。

    入参：data=业务数据（任意可 JSON 化对象）；message=提示语。
    返回：flask Response，body 形如 {success, code, message, data}。
    """
    return jsonify({'success': True, 'code': CODE_OK,
                    'message': message, 'data': data})


def wx_err(message, code=CODE_BAD_REQUEST, http_status=None, data=None):
    """构造失败响应。

    入参：message=提示语；code=业务码；http_status=HTTP 状态码，
    缺省取 code（code < 400 时按 400 返回）。
    返回：(Response, status) 二元组。
    """
    status = http_status or (code if code >= 400 else CODE_BAD_REQUEST)
    return jsonify({'success': False, 'code': code,
                    'message': message, 'data': data}), status
