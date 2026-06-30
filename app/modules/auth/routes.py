# StuLink v1.4.5 2026-06-30
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from app.forms.auth_forms import LoginForm, ChangePasswordForm
from app.models import User
from app.utils.cache import cache
from app.utils.helpers import log_operation
from urllib.parse import urlparse
import time

bp = Blueprint('auth', __name__)

# 登录频率限制：每 IP 每分钟最多 10 次尝试
_LOGIN_RATE_LIMIT = 10
_LOGIN_RATE_WINDOW = 60


def _is_safe_redirect(target):
    """验证重定向目标是否安全（仅允许相对路径或同站）"""
    if not target:
        return False
    # 相对路径安全
    if target.startswith('/') and not target.startswith('//'):
        return True
    # 使用 Flask 内置检查
    from flask import current_app
    ref_url = urlparse(request.host_url)
    test_url = urlparse(target)
    return test_url.netloc == '' or test_url.netloc == ref_url.netloc


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('welcome.index'))

    form = LoginForm()
    if form.validate_on_submit():
        # 频率限制检查
        client_ip = request.remote_addr or 'unknown'
        cache_key = f'login_attempts_{client_ip}'
        attempts = cache.get(cache_key) or []
        now = time.time()
        # 清理过期记录
        attempts = [t for t in attempts if now - t < _LOGIN_RATE_WINDOW]
        if len(attempts) >= _LOGIN_RATE_LIMIT:
            flash(f'登录尝试过于频繁，请等待 {_LOGIN_RATE_WINDOW} 秒后再试', 'danger')
            return render_template('auth/login.html', form=form)

        user = User.query.filter_by(username=form.username.data).first()
        if user and user.is_active and user.check_password(form.password.data):
            # 登录成功，清除尝试记录
            cache.delete(cache_key)
            login_user(user)
            log_operation(user, '登录', '用户', user.id, f'{user.real_name} 登录系统')
            flash(f'欢迎回来，{user.real_name}！', 'success')
            next_page = request.args.get('next')
            if next_page and _is_safe_redirect(next_page):
                return redirect(next_page)
            return redirect(url_for('welcome.index'))

        # 登录失败，记录尝试
        attempts.append(now)
        cache.set(cache_key, attempts, timeout=_LOGIN_RATE_WINDOW * 2)
        flash('用户名或密码错误', 'danger')
    return render_template('auth/login.html', form=form)


@bp.route('/logout')
@login_required
def logout():
    log_operation(current_user, '登出', '用户', current_user.id, f'{current_user.real_name} 退出登录')
    logout_user()
    flash('已退出登录', 'info')
    return redirect(url_for('auth.login'))


@bp.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.old_password.data):
            flash('原密码不正确', 'danger')
        else:
            current_user.set_password(form.new_password.data)
            current_user.must_change_pwd = False
            from app.extensions import db
            db.session.commit()
            flash('密码修改成功', 'success')
            return redirect(url_for('welcome.index'))
    return render_template('auth/change_password.html', form=form)
