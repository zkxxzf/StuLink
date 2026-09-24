# StuLink v1.7.0 2026-08-02
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""系统管理模块概览页与系统设置"""
import os

from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user
from app.utils.decorators import perm_required
from app.utils.helpers import get_school_name, set_school_name, log_operation

bp = Blueprint('system_dashboard', __name__, url_prefix='/system')


@bp.route('/')
@perm_required('system.users')   # M-1：系统概览（含操作轨迹）同样收敛
def index():
    return render_template('system/dashboard.html')


@bp.route('/settings', methods=['GET', 'POST'])
@login_required
@perm_required('system.settings')
def settings():
    """系统设置：学校名称等运行期可维护的全局配置"""
    env_override = bool((os.environ.get('SCHOOL_NAME') or '').strip())

    if request.method == 'POST':
        name = (request.form.get('school_name') or '').strip()
        if not name:
            flash('学校名称不能为空', 'danger')
        elif len(name) > 60:
            flash('学校名称过长（最多 60 个字）', 'danger')
        else:
            old_name = get_school_name()
            set_school_name(name, user_id=current_user.id)
            log_operation(current_user, '修改', '系统设置', None,
                          f'学校名称：{old_name} → {name}', module='system')
            flash('学校名称已保存', 'success')
        return redirect(url_for('system_dashboard.settings'))

    return render_template('system/settings.html',
                           school_name=get_school_name(),
                           env_override=env_override)
