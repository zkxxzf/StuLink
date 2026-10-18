# StuLink v1.19.49.35 2026-10-18
# 微信小程序 API 模块（/api/wx）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""微信小程序 API 模块。

网页端走 cookie session，小程序无法可靠回传 cookie，故本模块统一用
`X-Wx-Token` 请求头鉴权；业务数据一律复用既有服务层（成绩 / 课表 /
宿舍），**不复制任何计算逻辑**。

文件按职责拆分：
response（统一响应） / tokens（令牌签发校验） / guards（鉴权限流） /
serializers（数据裁剪） / routes_*（按领域分组的接口）。
"""
from flask import Blueprint

bp = Blueprint('wxapi', __name__, url_prefix='/api/wx')

# 必须放在 bp 定义之后：各 routes_* 会反向导入本模块的 bp
from app.modules.wxapi import (  # noqa: E402,F401
    guards,
    response,
    routes_auth,
    routes_dorm,
    routes_grades,
    routes_schedule,
    serializers,
    tokens,
)
