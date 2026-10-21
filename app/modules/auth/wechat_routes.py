# StuLink v1.19.51.64 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""网页端微信扫码登录 / 微信绑定。"""
import logging

from flask import (jsonify, redirect, render_template, request, session,
                   url_for)
from flask_login import current_user, login_required, login_user

from app.extensions import db
from app.models import User, WeChatBinding
from app.modules.auth.routes import bp
from app.modules.wxapi.core import wechat
from app.utils.cache import cache
from app.utils.helpers import log_operation
from app.utils.session_guard import stamp

logger = logging.getLogger(__name__)


def _client_ip():
    """L-04：统一口径，只有可信反代才信 X-Real-IP（见 utils/client_ip）。"""
    from app.utils.client_ip import get_client_ip
    return get_client_ip('?')


def _rate(key, limit, window):
    k = 'qrweb_%s_%s' % (key, _client_ip())
    # L-10：原子自增，避免并发 get→set 超限
    return cache.incr(k, 1, timeout=window) <= limit


def _qr_payload(row):
    return {
        'ok': True,
        'ticket': row.ticket,
        'svg': wechat.qr_svg('STULINK:' + row.ticket),
        'expires_in': wechat.ttl_seconds(row),
    }


@bp.route('/qr-login')
def qr_login():
    if current_user.is_authenticated:
        return redirect(url_for('welcome.index'))
    return render_template('auth/qr_login.html')


@bp.route('/qr/new')
def qr_new():
    if not _rate('new', 20, 60):
        return jsonify({'ok': False, 'message': '请求过于频繁'}), 429
    row = wechat.new_ticket('login')
    session['wx_qr_ticket'] = row.ticket
    return jsonify(_qr_payload(row))


@bp.route('/qr/status/<ticket>')
def qr_status(ticket):
    # L-04：公开轮询端点也要限流，防票据枚举/刷接口
    if not _rate('status', 120, 60):
        return jsonify({'ok': False, 'message': '请求过于频繁'}), 429
    row = wechat.get_ticket(ticket)
    if row is None:
        return jsonify({'ok': False, 'status': 'missing'}), 404
    return jsonify({
        'ok': True,
        'status': row.status,
        'expires_in': wechat.ttl_seconds(row),
    })


@bp.route('/qr/consume', methods=['POST'])
def qr_consume():
    """扫码确认后换取登录会话。

    2026-10-19 审计：去掉 CSRF 豁免，页面已带 X-CSRFToken。
    二维码本身是随机且绑定当前会话（session['wx_qr_ticket']），
    再加 CSRF 是第二道防线，避免安全扫描长期红项。
    """
    body = request.get_json(silent=True) or {}
    ticket = (body.get('ticket') or '').strip()
    if not ticket or session.get('wx_qr_ticket') != ticket:
        return jsonify({'ok': False, 'message': '请重新生成二维码'}), 403
    row = wechat.get_ticket(ticket)
    if row is None or row.action != 'login':
        return jsonify({'ok': False, 'message': '二维码无效'}), 404
    if row.status != 'approved':
        return jsonify({'ok': False, 'message': '尚未确认'}), 409
    user = db.session.get(User, row.user_id) if row.user_id else None
    if user is None or not user.is_active:
        return jsonify({'ok': False, 'message': '账号不可用'}), 403
    if not wechat.consume_ticket(row):
        return jsonify({'ok': False, 'message': '二维码已使用'}), 409
    session.clear()
    login_user(user)
    session.permanent = True
    stamp(user)
    try:
        from flask_wtf.csrf import generate_csrf
        generate_csrf()
    except Exception as exc:  # noqa: BLE001
        logger.warning('二维码登录后刷新 CSRF 失败: %s', exc)
    log_operation(user, '登录', '用户', user.id, '%s 微信扫码登录' % user.real_name)
    return jsonify({
        'ok': True,
        'redirect': url_for('welcome.index'),
    })


@bp.route('/wechat-bind')
@login_required
def wechat_bind():
    rows = (WeChatBinding.query
            .filter_by(user_id=current_user.id)
            .order_by(WeChatBinding.id).all())
    bindings = [{
        'id': r.id,
        'openid': r.openid or '',
        'openid_tail': (r.openid or '')[-6:],
        'created_at': r.created_at,
        'last_login_at': r.last_login_at,
    } for r in rows]
    return render_template('auth/wechat_bind.html', bindings=bindings)


@bp.route('/wechat-bind/new')
@login_required
def wechat_bind_new():
    if not _rate('bind', 20, 60):
        return jsonify({'ok': False, 'message': '请求过于频繁'}), 429
    row = wechat.new_ticket('bind', current_user.id)
    session['wx_bind_ticket'] = row.ticket
    return jsonify(_qr_payload(row))


@bp.route('/wechat-bind/status/<ticket>')
@login_required
def wechat_bind_status(ticket):
    row = wechat.get_ticket(ticket)
    if (row is None or row.action != 'bind'
            or row.user_id != current_user.id):
        return jsonify({'ok': False, 'status': 'missing'}), 404
    return jsonify({
        'ok': True,
        'status': row.status,
        'expires_in': wechat.ttl_seconds(row),
    })


@bp.route('/wechat-bind/unbind/<int:binding_id>', methods=['POST'])
@login_required
def wechat_unbind(binding_id):
    row = db.session.get(WeChatBinding, binding_id)
    if row is None or row.user_id != current_user.id:
        return redirect(url_for('auth.wechat_bind'))
    wechat.unbind(row)
    return redirect(url_for('auth.wechat_bind'))
