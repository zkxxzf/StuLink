# StuLink v1.19.49.47 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""微信小程序登录 / 绑定 / 扫码票据的公共实现。"""
import json
import secrets
from datetime import datetime, timedelta
from urllib.parse import urlencode
from urllib.request import urlopen

from flask import current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.extensions import db
from app.models import MAX_WECHAT_BINDINGS, WeChatBinding, WeChatTicket


class WeChatError(Exception):
    """微信侧错误（code2session 失败、未配置等）。"""


def exchange_code(code):
    """用 wx.login 的 code 换 openid/unionid；不把 session_key 外传。"""
    code = (code or '').strip()
    if not code:
        raise WeChatError('缺少微信登录 code')
    appid = current_app.config.get('WX_MP_APPID', '')
    secret = current_app.config.get('WX_MP_SECRET', '')
    mock_ok = (current_app.config.get('WX_MP_MOCK')
               and current_app.config.get('WX_ENV') != 'prod')
    if mock_ok and code.startswith('mock:'):
        openid = code[5:].strip() or 'mock-openid'
        return {'openid': openid, 'unionid': ''}
    if not appid or not secret:
        raise WeChatError('服务端未配置小程序 AppID / AppSecret')
    query = urlencode({
        'appid': appid,
        'secret': secret,
        'js_code': code,
        'grant_type': 'authorization_code',
    })
    url = 'https://api.weixin.qq.com/sns/jscode2session?' + query
    try:
        with urlopen(url, timeout=6) as resp:
            body = json.loads(resp.read().decode('utf-8'))
    except Exception as exc:  # noqa: BLE001 网络/解析异常统一转业务错误
        raise WeChatError('微信登录服务暂时不可用') from exc
    if body.get('errcode'):
        raise WeChatError('微信登录失败（%s）' % body.get('errcode'))
    openid = (body.get('openid') or '').strip()
    if not openid:
        raise WeChatError('微信未返回 openid')
    return {'openid': openid, 'unionid': body.get('unionid') or ''}


def bindings_of(openid):
    return (WeChatBinding.query.filter_by(openid=openid)
            .order_by(WeChatBinding.id).all())


def binding_users(openid):
    from app.models import User
    rows = bindings_of(openid)
    ids = [b.user_id for b in rows]
    users = {u.id: u for u in User.query.filter(User.id.in_(ids)).all()
             if u.is_active}
    return [(b, users.get(b.user_id)) for b in rows if users.get(b.user_id)]


def bind_user(openid, unionid, user, nickname=''):
    """把 openid 绑到用户；同一微信最多 MAX_WECHAT_BINDINGS 个账号。"""
    hit = WeChatBinding.query.filter_by(
        openid=openid, user_id=user.id).first()
    if hit:
        hit.unionid = unionid or hit.unionid
        hit.nickname = nickname or hit.nickname
        db.session.commit()
        return hit
    count = WeChatBinding.query.filter_by(openid=openid).count()
    if count >= MAX_WECHAT_BINDINGS:
        raise WeChatError('该微信最多绑定 %d 个账号' % MAX_WECHAT_BINDINGS)
    row = WeChatBinding(
        openid=openid, unionid=unionid or '', user_id=user.id,
        nickname=nickname or '',
    )
    db.session.add(row)
    db.session.commit()
    return row


def unbind(row):
    db.session.delete(row)
    db.session.commit()


def select_token(openid):
    """多账号选择用的短期签名令牌（5 分钟）。"""
    s = URLSafeTimedSerializer(current_app.secret_key, salt='wx-select')
    return s.dumps({'openid': openid})


def parse_select_token(token):
    s = URLSafeTimedSerializer(current_app.secret_key, salt='wx-select')
    try:
        data = s.loads(token or '', max_age=300)
    except (BadSignature, SignatureExpired):
        return None
    return (data or {}).get('openid')


def new_ticket(action, user_id=None, ttl=None):
    """创建一次性扫码票据；action=login / bind。"""
    if action not in ('login', 'bind'):
        raise WeChatError('未知的扫码动作')
    ttl = int(ttl or current_app.config.get('WX_QR_TTL', 120))
    row = WeChatTicket(
        ticket=secrets.token_urlsafe(24),
        action=action,
        status='pending',
        user_id=user_id,
        expires_at=datetime.now() + timedelta(seconds=ttl),
    )
    db.session.add(row)
    db.session.commit()
    return row


def get_ticket(ticket):
    row = WeChatTicket.query.filter_by(ticket=(ticket or '').strip()).first()
    if row is None:
        return None
    if row.status in ('pending', 'scanned') and row.expires_at < datetime.now():
        row.status = 'expired'
        db.session.commit()
    return row


def approve_ticket(row, user_id, openid=''):
    if row.status not in ('pending', 'scanned'):
        return False
    if row.expires_at < datetime.now():
        row.status = 'expired'
        db.session.commit()
        return False
    row.status = 'approved'
    row.user_id = user_id
    row.openid = openid or ''
    row.approved_at = datetime.now()
    db.session.commit()
    return True


def consume_ticket(row):
    if row.status != 'approved':
        return False
    row.status = 'consumed'
    row.consumed_at = datetime.now()
    db.session.commit()
    return True


def qr_svg(content, size=240):
    """生成 SVG 字符串，不依赖 Pillow。"""
    import qrcode
    import qrcode.image.svg
    img = qrcode.make(
        content, image_factory=qrcode.image.svg.SvgPathImage,
        box_size=max(2, size // 40), border=2,
    )
    raw = img.to_string()
    if isinstance(raw, bytes):
        return raw.decode('utf-8', 'replace')
    return raw


def ttl_seconds(row):
    return max(0, int((row.expires_at - datetime.now()).total_seconds()))
