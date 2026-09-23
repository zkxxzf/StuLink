# StuLink v1.18.1.0 2026-09-23
# 教务模块：教师名单 / 课表 / 查课记录 / 教师业绩 / 调课 / 表单收集
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from flask import Blueprint

bp = Blueprint('academic', __name__, url_prefix='/academic')

# 各子模块通过 @bp.route 注册（延迟 import，保证 bp 已定义）
from app.modules.academic.routes import (teachers, timetable,  # noqa: E402,F401
                                         inspection, achievements, swap, forms)
