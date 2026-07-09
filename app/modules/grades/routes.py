# StuLink v1.6.1 2026-07-09
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0
from flask import Blueprint, render_template
from flask_login import login_required

bp = Blueprint('grades', __name__, url_prefix='/grades')


@bp.route('/')
@login_required
def index():
    return render_template('grades/index.html')


@bp.route('/history')
@login_required
def history():
    """成绩历史查询（功能开发中）"""
    return render_template('grades/history.html')


@bp.route('/alumni')
@login_required
def alumni():
    """往届成绩查询（功能开发中）"""
    return render_template('grades/alumni.html')


