# StuLink v1.18.9.2 2026-10-10
# 教务模块：教师名单 / 课表 / 查课记录 / 教师业绩 / 调课 / 表单收集
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from flask import Blueprint

bp = Blueprint('academic', __name__, url_prefix='/academic')

# 各子模块通过 @bp.route 注册（延迟 import，保证 bp 已定义）
# 注意 duty 不能删：任课安排（/academic/duty*）确已于 2026-10-10 下线（旧址一律 404 是刻意行为），
# 但 duty.py 只是沿用旧文件名，现承载「备课组长」路由（/academic/leaders*）；删掉此 import
# 会让 /academic/leaders 直接 404、备课组长功能整体失效（见 smoke_academic / academic_regression）。
from app.modules.academic.routes import (home, teachers,  # noqa: E402,F401
                                         inspection, achievements, swap, forms,
                                         duty)
