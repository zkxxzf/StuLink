# StuLink v1.7.0 2026-08-02
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from flask import (Blueprint, render_template, redirect, url_for, flash, request,
                   session, jsonify)
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
        # v1.18.8.0 S-7：归一化 —— strip().lower() 避免“ Admin”“ADMIN” 重置计数绕过锁定
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
            # v1.18.8.0 M-15：新口令必须过弱口令黑名单与强度校验，防止把弱口令写入库
            from app.utils.password_policy import validate_password
            ok, msg = validate_password(form.new_password.data,
                                        username=current_user.username,
                                        real_name=current_user.real_name)
            if not ok:
                flash(msg, 'danger')
                return render_template('auth/change_password.html', form=form)
            current_user.set_password(form.new_password.data)
            current_user.must_change_pwd = False
            from app.extensions import db
            db.session.commit()
            # M-2：更新本设备会话摘要（保持登录）；其它设备的旧摘要失效 → 被踢下线
            stamp(current_user)
            flash('密码修改成功，其它设备的登录状态已失效', 'success')
            return redirect(url_for('welcome.index'))
    return render_template('auth/change_password.html', form=form)


# ==================== v1.19.0 用户设置（右上角用户名入口） ====================
# 内容：改密码 / 我的 AI（Key 存浏览器本地，不上服务器）/ 我的权限 / 界面偏好

# 数据范围展示名
SCOPE_LABELS = {'school': '全校', 'grade': '本年级', 'class': '本班',
                'self': '仅本人', 'none': '受限（仅基础页面）'}


def _my_permission_rows():
    """当前用户的功能权限清单（按 模块 × 功能项）——只读展示用

    判定口径与后端 perm_required 一致：任一项的 read/write 权限键命中即算通过，
    admin 恒为全部通过（has_perm 内部已处理）。
    """
    from app.utils import permission_map as pm
    rows = []
    for m in pm.MODULES:
        entries = []
        for it in (m.get('items') or []):
            keys = list(it.get('read') or []) + list(it.get('write') or [])
            if not keys:
                continue
            allowed = any(current_user.has_perm(k) for k in keys)
            entries.append({'name': it.get('name') or it.get('key'), 'allowed': allowed})
        if not entries:
            continue
        # 注意：键名不能叫 items —— Jinja 里 `m.items` 会取到 dict 的 items 方法
        rows.append({'name': m.get('name') or m.get('key'),
                     'icon': m.get('icon') or 'bi-grid',
                     'perms': entries,
                     'allowed_n': sum(1 for i in entries if i['allowed']),
                     'any': any(i['allowed'] for i in entries)})
    return rows


@bp.route('/my/settings')
@login_required
def my_settings():
    """用户设置：改密码入口 + 我的 AI（本地 Key）+ 我的权限 + 界面偏好"""
    pg = current_user.permission_group
    scope = (getattr(pg, 'scope_type', '') or 'none') if pg else (
        'school' if current_user.role == 'admin' else 'none')
    # 供应商清单（含自定义 OpenAI 兼容）：前端下拉用，避免在模板里再维护一份
    from app.modules.grades.services import ai_providers as _ap
    providers = [dict(key=k, **v) for k, v in _ap.PROVIDERS.items()]
    # v1.19.0 管理员可见：AI 出站域名白名单（自定义 OpenAI 兼容地址需先批准）
    from app.utils import url_guard as _ug
    is_admin = (current_user.role == 'admin')
    return render_template(
        'auth/my_settings.html',
        modules=_my_permission_rows(),
        pg=pg,
        providers=providers,
        default_provider=_ap.DEFAULT_PROVIDER,
        approved_hosts=_ug.list_approved_hosts() if is_admin else [],
        builtin_hosts=_ug.builtin_hosts() if is_admin else [],
        whitelist_file=_ug.APPROVED_FILE if is_admin else '',
        scope_label=SCOPE_LABELS.get(scope, scope),
        is_admin=is_admin,
    )


@bp.route('/my/ai/whitelist', methods=['POST'])
@login_required
def my_ai_whitelist():
    """AI 出站域名白名单（仅管理员）：批准/撤销自定义 OpenAI 兼容地址

    背景：自定义 base_url 会带着用户的 Key 出站，为防 SSRF/内网探测必须有白名单。
    本接口让管理员在页面上直接批准，写入 data/ai_approved_base_urls.txt（不重启即生效）。
    """
    if current_user.role != 'admin':
        return jsonify(success=False, message='仅管理员可管理出站域名白名单')
    data = request.get_json(silent=True) or {}
    action = (data.get('action') or 'add').strip()
    host = (data.get('host') or '').strip()
    from app.utils import url_guard
    if action == 'remove':
        ok, msg = url_guard.remove_approved_host(host)
    else:
        ok, msg = url_guard.add_approved_host(host)
    if ok:
        log_operation(current_user, '配置', 'AI出站白名单', 0,
                      '%s %s' % (action, host), module='grades')
    return jsonify(success=ok, message=msg,
                   hosts=url_guard.list_approved_hosts())


@bp.route('/my/ai/test', methods=['POST'])
@login_required
def my_ai_test():
    """测试 AI 连接：只用请求携带的 Key（**不落库、不记日志**），发一条最小请求

    支持标准 OpenAI 兼容地址（自定义 base_url + model）。
    """
    data = request.get_json(silent=True) or {}
    ai = data.get('ai') if isinstance(data.get('ai'), dict) else {}
    client_cfg = {'api_key': (ai.get('api_key') or '').strip(),
                  'base_url': (ai.get('base_url') or '').strip(),
                  'model': (ai.get('model') or '').strip(),
                  'provider': (ai.get('provider') or '').strip()}
    if not client_cfg['api_key']:
        return jsonify(success=False, message='请先填写 API Key')
    from app.modules.grades.services import ai_service
    try:
        cfg = ai_service.resolve_key(current_user, client_cfg)
    except ValueError as e:
        return jsonify(success=False, message=str(e))
    if not cfg:
        return jsonify(success=False, message='配置不完整：需要 Key + 接口地址 + 模型')
    try:
        ok, payload, sec = ai_service._chat_completions(
            cfg, [{'role': 'user', 'content': 'ping'}], timeout=20, max_tokens=8)
    except Exception as e:                     # 网络/解析异常都要给出可读提示
        return jsonify(success=False, message='连接异常：%s' % str(e)[:180])
    if ok:
        # 回显本次实际生效的可选参数，方便用户核对
        eff = []
        if cfg.get('max_tokens'):
            eff.append('输出上限 %s' % cfg['max_tokens'])
        if cfg.get('max_input'):
            eff.append('输入上限 %s' % cfg['max_input'])
        if cfg.get('reasoning_effort'):
            eff.append('思考强度 %s' % cfg['reasoning_effort'])
        return jsonify(success=True,
                       message='连接成功（%.1fs）｜%s / %s%s'
                               % (sec, cfg['provider_name'], cfg['model'],
                                  ('；生效参数：' + '、'.join(eff)) if eff else
                                  '；未设置高级参数（按服务商默认）'))
    return jsonify(success=False, message='连接失败：%s' % str(payload)[:200])


