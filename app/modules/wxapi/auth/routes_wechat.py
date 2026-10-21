# StuLink v1.19.54.64 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序微信登录、绑定与扫码接口。

一个微信号最多绑定 2 个系统账号；首次绑定必须用账号密码验证，
后续用 wx.login 的 code 直接换令牌。
"""
import logging
from datetime import datetime

from flask import g, request

from app.extensions import db
from app.models import User, WeChatBinding
from app.modules.wxapi import bp
from app.modules.wxapi.auth.routes_auth import login_payload
from app.modules.wxapi.core import wechat
from app.modules.wxapi.core.guards import (
    client_ip,
    hit_limit,
    wx_login_required,
)
from app.modules.wxapi.core.response import wx_err, wx_ok

logger = logging.getLogger(__name__)


def _body():
    return request.get_json(silent=True) or {}


def _bind_code():
    """换 openid；异常统一成 wx_err。"""
    try:
        return wechat.exchange_code(_body().get('code') or '')
    except wechat.WeChatError as exc:
        return wx_err(str(exc), 400)
    except Exception as exc:  # noqa: BLE001
        logger.warning('微信 code2session 异常: %s', exc)
        return wx_err('微信登录暂时不可用', 502)


def _account_rows(openid):
    rows = []
    for binding, user in wechat.binding_users(openid):
        rows.append({
            'binding_id': binding.id,
            'openid': binding.openid,
            'user_id': user.id,
            'username': user.username,
            'real_name': user.real_name,
            'role': user.role,
            'role_display': user.display_role,
        })
    return rows


def _touch(binding):
    binding.last_login_at = datetime.now()
    db.session.commit()


@bp.route('/wechat/login', methods=['POST'])
def wx_wechat_login():
    """用 wx.login 的 code 登录；未绑定返回 bound=false。"""
    if not hit_limit('wxlog_%s' % client_ip(), 30, 300):
        return wx_err('尝试过于频繁，请稍后再试', 429)
    data = _bind_code()
    if not isinstance(data, dict):
        return data
    openid = data['openid']
    rows = wechat.binding_users(openid)
    if not rows:
        return wx_ok({'bound': False, 'max_bindings':
                      wechat.MAX_WECHAT_BINDINGS})
    if len(rows) == 1:
        binding, user = rows[0]
        _touch(binding)
        p = login_payload(user, None, openid)
        p['bound'] = True
        return wx_ok(p)
    return wx_ok({
        'bound': True,
        'need_select': True,
        'select_token': wechat.select_token(openid),
        'accounts': _account_rows(openid),
    })


@bp.route('/wechat/select', methods=['POST'])
def wx_wechat_select():
    """多账号时选择本次登录账号。"""
    body = _body()
    openid = wechat.parse_select_token(body.get('select_token'))
    if not openid:
        return wx_err('选择已过期，请重新微信登录', 401)
    # 2026-10-19 第十三轮审计：非数字 user_id 原来直接 ValueError → 500
    try:
        user_id = int(body.get('user_id') or 0)
    except (TypeError, ValueError):
        return wx_err('参数不正确')
    binding = WeChatBinding.query.filter_by(
        openid=openid, user_id=user_id).first()
    if binding is None:
        return wx_err('该微信未绑定此账号', 403)
    _touch(binding)
    p = login_payload(binding.user, None, openid)
    p['bound'] = True
    return wx_ok(p)


@bp.route('/wechat/bind', methods=['POST'])
def wx_wechat_bind():
    """首次绑定：账号密码 + wx.login code。"""
    body = _body()
    username = (body.get('username') or '').strip()
    if not hit_limit('wxbind_%s' % client_ip(), 20, 300):
        return wx_err('尝试过于频繁，请稍后再试', 429)
    # 2026-10-20 第十六轮：加账号级限流（键**不拼 IP**）—— 只按 IP 计数时
    # 换一个出口就能对同一账号无限猜口令（对照 routes_auth.login 的同款修复）。
    if username and not hit_limit('wxbind_acct_%s' % username.lower(), 10, 300):
        return wx_err('该账号尝试过于频繁，请稍后再试', 429)
    password = body.get('password') or ''
    if not username or not password:
        return wx_err('请输入账号和密码')
    user = User.query.filter_by(username=username).first()
    if user is None or not user.check_password(password):
        return wx_err('账号或密码不正确', 401)
    if not user.is_active:
        return wx_err('账号已停用', 403)
    data = _bind_code()
    if not isinstance(data, dict):
        return data
    try:
        binding = wechat.bind_user(
            data['openid'], data.get('unionid', ''), user)
    except wechat.WeChatError as exc:
        return wx_err(str(exc), 409)
    _touch(binding)
    p = login_payload(user, None, data['openid'])
    p['bound'] = True
    return wx_ok(p)


@bp.route('/wechat/bind-current', methods=['POST'])
@wx_login_required
def wx_wechat_bind_current():
    """已登录用户把当前微信绑定到本账号（先登录、后绑定）。"""
    if not hit_limit('wxcur_%s' % g.wx_user.id, 10, 300):
        return wx_err('操作过于频繁，请稍后再试', 429)
    data = _bind_code()
    if not isinstance(data, dict):
        return data
    try:
        binding = wechat.bind_user(
            data['openid'], data.get('unionid', ''), g.wx_user)
    except wechat.WeChatError as exc:
        return wx_err(str(exc), 409)
    _touch(binding)
    p = login_payload(
        g.wx_user, getattr(g, 'wx_group_id', None), data['openid'])
    p['bound'] = True
    return wx_ok(p)


@bp.route('/wechat/bindings')
@wx_login_required
def wx_wechat_bindings():
    """当前微信已绑定的账号（密码登录的令牌没有 openid → 空列表）。"""
    openid = getattr(g, 'wx_openid', '')
    if not openid:
        return wx_ok({
            'max_bindings': wechat.MAX_WECHAT_BINDINGS,
            'items': [],
        })
    items = _account_rows(openid)
    for item in items:
        item['is_current'] = item['user_id'] == g.wx_user.id
    return wx_ok({
        'openid': openid,
        'max_bindings': wechat.MAX_WECHAT_BINDINGS,
        'items': items,
    })


@bp.route('/wechat/bindings/<int:binding_id>', methods=['DELETE'])
@wx_login_required
def wx_wechat_unbind(binding_id):
    openid = getattr(g, 'wx_openid', '')
    row = db.session.get(WeChatBinding, binding_id)
    if row is None or not openid or row.openid != openid:
        return wx_err('绑定记录不存在', 404)
    wechat.unbind(row)
    return wx_ok(message='已解绑')


@bp.route('/wechat/scan', methods=['POST'])
def wx_wechat_scan():
    """小程序扫网页二维码：登录网页 / 把微信绑定到网页账号。"""
    if not hit_limit('wxscan_%s' % client_ip(), 30, 300):
        return wx_err('操作过于频繁，请稍后再试', 429)
    body = _body()
    ticket = wechat.get_ticket(body.get('ticket') or '')
    if ticket is None:
        return wx_err('二维码无效或已过期', 404)
    if ticket.status not in ('pending', 'scanned'):
        return wx_ok({'status': ticket.status})
    data = _bind_code()
    if not isinstance(data, dict):
        return data
    openid = data['openid']
    rows = wechat.binding_users(openid)
    if ticket.action == 'login':
        if not rows:
            return wx_err('该微信尚未绑定系统账号', 404)
        if len(rows) > 1:
            return wx_ok({
                'need_select': True,
                'select_token': wechat.select_token(openid),
                'accounts': _account_rows(openid),
            })
        binding, user = rows[0]
        wechat.approve_ticket(ticket, user.id, openid)
        _touch(binding)
        return wx_ok({'status': 'approved'})
    # bind：二维码由已登录网页生成，目标是网页当前账号
    user = db.session.get(User, ticket.user_id) if ticket.user_id else None
    if user is None or not user.is_active:
        return wx_err('网页账号不可用', 404)
    try:
        binding = wechat.bind_user(
            openid, data.get('unionid', ''), user)
    except wechat.WeChatError as exc:
        return wx_err(str(exc), 409)
    wechat.approve_ticket(ticket, user.id, openid)
    _touch(binding)
    return wx_ok({'status': 'approved', 'user': {
        'id': user.id, 'real_name': user.real_name,
    }})


@bp.route('/wechat/scan/confirm', methods=['POST'])
def wx_wechat_scan_confirm():
    """扫码登录多账号时，在小程序里选定账号后确认。"""
    body = _body()
    openid = wechat.parse_select_token(body.get('select_token'))
    if not openid:
        return wx_err('选择已过期，请重新扫码', 401)
    ticket = wechat.get_ticket(body.get('ticket') or '')
    if ticket is None or ticket.action != 'login':
        return wx_err('二维码无效或已过期', 404)
    # 2026-10-19：同 wx_wechat_select，非数字不再 500
    try:
        user_id = int(body.get('user_id') or 0)
    except (TypeError, ValueError):
        return wx_err('参数不正确')
    binding = WeChatBinding.query.filter_by(
        openid=openid, user_id=user_id).first()
    if binding is None:
        return wx_err('该微信未绑定此账号', 403)
    if not wechat.approve_ticket(ticket, user_id, openid):
        return wx_err('二维码已使用或已过期', 409)
    return wx_ok({'status': 'approved'})
