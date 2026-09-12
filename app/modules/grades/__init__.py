# StuLink v1.9.0 2026-09-03
# 成绩管理与可视化分析系统 模块入口（单蓝图多模块注册，url_prefix=/grades）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from flask import Blueprint

bp = Blueprint('grades', __name__, url_prefix='/grades')

# 各子模块通过 @bp.route 注册（延迟 import，保证 bp 已定义）
from app.modules.grades.routes import exams, bands, teachers, analysis, export, ai  # noqa: E402,F401
