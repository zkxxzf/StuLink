# StuLink v1.19.52.64 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序通知公告接口：列表 / 未读数 / 详情 / 已读。

可见性、已读状态、未读数缓存全部复用通知模块服务层
（`notification_service`），本文件只做裁剪与登录校验。
"""
import re

from flask import g, request

from app.models.notification import CATEGORY_LABELS
from app.modules.notifications.services import notification_service as svc
from app.modules.wxapi import bp
from app.modules.wxapi.core.guards import wx_login_required
from app.modules.wxapi.core.response import wx_err, wx_ok

PAGE_SIZE = 20
SUMMARY_LEN = 120
_TAG_RE = re.compile(r'<[^>]+>')
_SPACE_RE = re.compile(r'\s+')


def _summary(text):
    """正文摘要：去标签、压空白、截断（列表页展示用）。"""
    plain = _TAG_RE.sub(' ', text or '')
    plain = _SPACE_RE.sub(' ', plain).strip()
    return plain[:SUMMARY_LEN]


def _item(notif):
    """通知列表项（只回列表需要的字段，正文另走详情接口）。"""
    category = notif.category or 'system'
    return {
        'id': notif.id,
        'title': notif.title or '',
        'summary': _summary(notif.content),
        'category': category,
        'category_label': CATEGORY_LABELS.get(category, ''),
        'priority': notif.priority or 'normal',
        'published_at': (notif.published_at.strftime('%Y-%m-%d %H:%M')
                         if notif.published_at else ''),
        'is_read': bool(getattr(notif, 'is_read', False)),
        'link_url': notif.link_url or '',
    }


def _categories():
    """分类字典（供小程序做筛选条）。"""
    return [{'key': k, 'label': v} for k, v in CATEGORY_LABELS.items()]


@bp.route('/notices')
@wx_login_required
def wx_notices():
    """通知列表（query: page / category / only_unread）。"""
    page = max(1, request.args.get('page', type=int) or 1)
    category = (request.args.get('category') or '').strip()
    if category and category not in CATEGORY_LABELS:
        return wx_err('未知的通知分类')
    only_unread = request.args.get('only_unread') in ('1', 'true', 'yes')
    data = svc.get_notifications_for_user(
        g.wx_user, page=page, per_page=PAGE_SIZE,
        category=category or None, only_unread=only_unread)
    return wx_ok({
        'items': [_item(n) for n in data['items']],
        'page': data['page'],
        'per_page': data['per_page'],
        'total': data['total'],
        'total_pages': data['total_pages'],
        'unread': svc.get_unread_count(g.wx_user),
        'categories': _categories(),
    })


@bp.route('/notices/unread')
@wx_login_required
def wx_notice_unread():
    """未读通知数（首页角标用）。"""
    return wx_ok({'count': svc.get_unread_count(g.wx_user)})


@bp.route('/notices/<int:nid>')
@wx_login_required
def wx_notice_detail(nid):
    """通知详情。

    L-01：GET 默认不再写已读；客户端看完后调 POST /notices/<id>/read。
    兼容期可用 WX_NOTICE_DETAIL_AUTO_READ=1 恢复旧的“打开即已读”。
    """
    from flask import current_app
    auto_read = bool(current_app.config.get(
        'WX_NOTICE_DETAIL_AUTO_READ', False))
    notif, meta = svc.get_notification_detail(
        nid, g.wx_user, mark_read_now=auto_read)
    if meta is None:
        return wx_err('通知不存在或无权限查看', 404)
    data = _item(notif)
    data['content'] = notif.content or ''
    data['is_read'] = bool(meta.get('is_read'))
    data['mark_read_required'] = not auto_read and not data['is_read']
    return wx_ok(data)


@bp.route('/notices/<int:nid>/read', methods=['POST'])
@wx_login_required
def wx_notice_read(nid):
    """标记单条已读（详情接口已自动标记，此接口给列表预读用）。"""
    svc.mark_read(nid, g.wx_user.id)
    return wx_ok({'unread': svc.get_unread_count(g.wx_user)})


@bp.route('/notices/read-all', methods=['POST'])
@wx_login_required
def wx_notice_read_all():
    """全部标记已读。"""
    svc.mark_all_read(g.wx_user.id)
    return wx_ok({'unread': 0})


@bp.route('/notices/<int:nid>', methods=['DELETE'])
@wx_login_required
def wx_notice_delete(nid):
    """收件人侧删除（只影响本人，不影响其他收件人）。"""
    svc.delete_for_user(nid, g.wx_user.id)
    return wx_ok({'unread': svc.get_unread_count(g.wx_user)})
