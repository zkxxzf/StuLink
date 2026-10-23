# StuLink v1.19.71.64 2026-10-13
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""德育奖惩模块：奖惩信息登记（含附件）

页面前缀 /morality；数据独立库 morality.db（见 config.SQLALCHEMY_BINDS）。
结构照抄 academic 模块（蓝图 + routes/ + services/ 三层）。
"""
from flask import Blueprint

bp = Blueprint('morality', __name__, url_prefix='/morality')

# 延迟 import：bp 先定义，子路由再用 @bp.route 注册
from app.modules.morality.routes import records  # noqa: E402,F401
