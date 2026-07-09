# StuLink v1.6.1 2026-07-09
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0
from flask import Blueprint, render_template
from flask_login import login_required

bp = Blueprint('points', __name__, url_prefix='/points')


@bp.route('/')
@login_required
def index():
    return render_template('points/index.html')


@bp.route('/history')
@login_required
def history():
    """积分历史查询（功能开发中）"""
    return render_template('points/history.html')


@bp.route('/alumni')
@login_required
def alumni():
    """往届积分查询（功能开发中）"""
    return render_template('points/alumni.html')


