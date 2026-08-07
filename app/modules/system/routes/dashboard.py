# StuLink v1.7.0 2026-08-02
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""系统管理模块概览页"""
from flask import Blueprint, render_template, redirect, url_for
from flask_login import login_required

bp = Blueprint('system_dashboard', __name__, url_prefix='/system')


@bp.route('/')
@login_required
def index():
    return render_template('system/dashboard.html')


