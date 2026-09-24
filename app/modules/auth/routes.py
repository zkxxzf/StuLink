# StuLink v1.7.0 2026-08-02
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from flask import Blueprint, render_template, redirect, url_for, flash, request, session
from flask_login import login_user, logout_user, login_required, current_user
from app.forms.auth_forms import LoginForm, ChangePasswordForm
from app.models import User
from app.utils.cache import cache
from app.utils.helpers import log_operation
from app.utils.session_guard import stamp
from urllib.parse import urlparse
import logging
import time

_sec_logger = logging.getLogger('stulink.auth')

bp = Blueprint('auth', __name__)

# 登录频率限制：每 IP 每分钟最多 10 次尝试
_LOGIN_RATE_LIMIT = 10
_LOGIN_RATE_WINDOW = 60
# M-3：账号维度限流（此前只有 IP 维度，多 IP 分布式爆破可绕过）
_LOGIN_ACCT_FAIL_LIMIT = 5        # 同一账号连续失败次数上限
_LOGIN_ACCT_LOCK_SECONDS = 900    # 触发后锁定时长（15 分钟）


def _is_safe_redirect(target):
    """验证重定向目标是否安全（M-13：只允许站内相对路径）

    旧实现问题：① 放行 `/\evil.com`（浏览器会把反斜杠当斜杠 → 外跳）；
    ② `netloc == ''` 时放行 `javascript:` / `data:` 等伪协议（无 netloc）。
    现在：必须是单个斜杠开头的相对路径、不含反斜杠、无 scheme、无 netloc。
    """
    if not target:
        return False
    if '\\' in target:
        return False
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return False
    if not parsed.path.startswith('/'):
        return False
    if parsed.path.startswith('//'):
        return False
    return True


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

        # M-3：账号维度锁定（多 IP 分布式爆破时，IP 限流形同虚设）
        # v1.18.2.1 S-7：归一化 —— strip().lower() 避免“ Admin”“ADMIN” 重置计数绕过锁定
        username_typed = (form.username.data or '').strip().lower()
        acct_fail_key = f'login_fail_{username_typed}'
        acct_lock_key = f'login_lock_{username_typed}'
        locked_until = cache.get(acct_lock_key)
        if locked_until and time.time() < float(locked_until):
            left = int(float(locked_until) - time.time()) // 60 + 1
            flash(f'该账号连续登录失败次数过多，已临时锁定，请 {left} 分钟后再试', 'danger')
            return render_template('auth/login.html', form=form)

        user = User.query.filter_by(username=(form.username.data or '').strip()).first()
        if user and user.is_active and user.check_password(form.password.data):
            # 登录成功，清除尝试记录
            cache.delete(cache_key)
            cache.delete(acct_fail_key)   # M-3：成功后清零账号失败计数
            # M-2：登录前清空旧会话（防会话固定）→ 登录后写入口令摘要并设为持久会话
            session.clear()
            login_user(user)
            session.permanent = True
            stamp(user)
            # 会话已清空，重新签发 CSRF token（否则后续页面的 token 校验会失败）
            try:
                from flask_wtf.csrf import generate_csrf
                generate_csrf()
            except Exception:  # noqa: BLE001
                pass
            log_operation(user, '登录', '用户', user.id, f'{user.real_name} 登录系统')
            flash(f'欢迎回来，{user.real_name}！', 'success')
            next_page = request.args.get('next')
            if next_page and _is_safe_redirect(next_page):
                return redirect(next_page)
            return redirect(url_for('welcome.index'))

        # 登录失败，记录尝试（IP 维度保留）
        attempts.append(now)
        cache.set(cache_key, attempts, timeout=_LOGIN_RATE_WINDOW * 2)

        # M-3：账号维度计数 + 触发锁定 + 安全日志
        fails = (cache.get(acct_fail_key) or 0) + 1
        cache.set(acct_fail_key, fails, timeout=_LOGIN_ACCT_LOCK_SECONDS)
        _sec_logger.warning('登录失败：账号=%s 来源IP=%s 连续失败=%s',
                            username_typed, client_ip, fails)
        if fails >= _LOGIN_ACCT_FAIL_LIMIT:
            cache.set(acct_lock_key, time.time() + _LOGIN_ACCT_LOCK_SECONDS,
                      timeout=_LOGIN_ACCT_LOCK_SECONDS)
            _sec_logger.warning('账号已临时锁定：账号=%s 来源IP=%s 时长=%ss',
                                username_typed, client_ip, _LOGIN_ACCT_LOCK_SECONDS)
            flash(f'该账号连续登录失败 {fails} 次，已临时锁定 15 分钟', 'danger')
        else:
            flash('用户名或密码错误', 'danger')
    return render_template('auth/login.html', form=form)


@bp.route('/logout')
@login_required
def logout():
    log_operation(current_user, '登出', '用户', current_user.id, f'{current_user.real_name} 退出登录')
    logout_user()
    session.clear()   # M-2：登出彻底清空会话（此前只 logout_user，session 数据残留）
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
            # M-2：更新本设备会话摘要（保持登录）；其它设备的旧摘要失效 → 被踢下线
            stamp(current_user)
            flash('密码修改成功，其它设备的登录状态已失效', 'success')
            return redirect(url_for('welcome.index'))
    return render_template('auth/change_password.html', form=form)


