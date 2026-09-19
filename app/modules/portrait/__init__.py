# StuLink v1.9.3 2026-09-19
# 学生画像模块（占位）：规划中，仅提供说明页面与独立数据库
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from flask import Blueprint, render_template
from flask_login import login_required

bp = Blueprint('portrait', __name__, url_prefix='/portrait')


@bp.route('/')
@login_required
def index():
    """学生画像占位页（规划中）"""
    return render_template('portrait/index.html')
