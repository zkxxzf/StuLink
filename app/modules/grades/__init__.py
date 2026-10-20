# StuLink v1.19.49.50 2026-10-12
# 成绩管理与可视化分析系统 模块入口（单蓝图多模块注册，url_prefix=/grades）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from flask import Blueprint

bp = Blueprint('grades', __name__, url_prefix='/grades')

# 各子模块通过 @bp.route 注册（延迟 import，保证 bp 已定义）
from app.modules.grades.routes import (dashboard, exams, bands, teachers, analysis,  # noqa: E402,F401
                                       export, export_analysis, ai,
                                       student_query, pivot, band_templates, exam_affairs,
                                       report, report_pivot, subject_mgmt, global_compare,
                                       ai_import, exam_rooms,
                                       affair_invig, score_review,
                                       score_review_entry,  # 复核录入（搜学生/批量改分）
                                       honor,  # 表彰名单
                                       near_line_export,  # 临界生名单导出
                                       timed, timed_import_routes,
                                       timed_ai_routes, timed_export,
                                       timed_files)  # 限时练（独立表）
# v1.19.49.35 说明：dashboard 必须最先 import —— 它占用模块根路径 /grades/
# （首页仪表盘，模块默认落地页），其余子模块按各自 url 注册。
