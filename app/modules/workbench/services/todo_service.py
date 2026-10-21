# -*- coding: utf-8 -*-
# StuLink v1.19.51.64 2026-10-13
# 教师工作台「待办与通知」聚合
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""教师工作台「待办与通知」聚合（v1.19.51.64）。

**待办** = 需要本人**动手**的事（不是"等着别人处理"的事）：
    ① 同事发起、指定我配合的换课 / 代课（待我确认）
    ② 还没填写的问卷 / 材料收集

**通知** = 最近若干条 + 未读数，与顶部铃铛同源（`get_unread_count_accurate`）。

所有跨库读取都静默降级：任一库异常只让该项为空并记日志，不拖垮工作台首页。
"""
from flask import current_app, url_for

from app.extensions import db

TODO_LIMIT = 5          # 最近通知条数


def _brief(n):
    """通知 → 模板可用的简字段（避免模板直接依赖 ORM 对象）。"""
    return {
        'id': n.id,
        'title': n.title or '',
        'category': (n.category or 'system'),
        'category_label': (getattr(n, 'category_label', '') or '系统公告'),
        'priority': (n.priority or 'normal'),
        'is_read': bool(getattr(n, 'is_read', False)),
        'published_at': n.published_at,
        'link_url': (n.link_url or ''),
    }


def _swap_todo(uid):
    """① 待我确认的换课 / 代课（一条汇总，明细与确认按钮在下方调课卡片里）。"""
    from app.modules.academic.services import swap_service

    rows = swap_service.get_partner_pending(uid, limit=20) or []
    if not rows:
        return None
    return {
        'type': 'swap',
        'icon': 'bi-arrow-left-right',
        'text': f'{len(rows)} 条换课 / 代课待你确认',
        'url': '#partnerPendingCard',
        'count': len(rows),
    }


def _form_todo(user):
    """② 待填写的问卷 / 材料收集。"""
    from app.modules.academic.services import form_service

    if not user or not user.has_perm('academic.forms_view'):
        return None
    items = form_service.fill_items(user.id, submitter_type='teacher') or []
    pend = [it for it in items if not it.get('submitted')]
    if not pend:
        return None
    return {
        'type': 'form',
        'icon': 'bi-ui-checks',
        'text': f'{len(pend)} 份问卷 / 材料待填写',
        'url': url_for('academic.form_fill_list'),
        'count': len(pend),
    }


def get_teacher_todo(user, teacher=None, limit=TODO_LIMIT):
    """教师工作台「待办与通知」数据。

    返回 {'todos': [...], 'todo_count': n, 'unread': m, 'recent': [...]} ——
    任一数据源异常时该项降级为空，不抛异常。
    """
    todos, recent, unread = [], [], 0

    uid = teacher.teacher_uid if teacher else None
    if uid:
        try:
            t = _swap_todo(uid)
            if t:
                todos.append(t)
        except Exception:                                   # noqa: BLE001
            db.session.rollback()
            current_app.logger.exception('工作台待办：换课待确认加载失败 uid=%s', uid)
    try:
        t = _form_todo(user)
        if t:
            todos.append(t)
    except Exception:                                       # noqa: BLE001
        db.session.rollback()
        current_app.logger.exception('工作台待办：问卷待填加载失败 user_id=%s',
                                     getattr(user, 'id', None))

    try:
        from app.modules.notifications.services import notification_service as ns
        unread = int(ns.get_unread_count_accurate(user) or 0)
        res = ns.get_notifications_for_user(user, page=1, per_page=limit) or {}
        recent = [_brief(n) for n in (res.get('items') or [])]
    except Exception:                                       # noqa: BLE001
        db.session.rollback()
        current_app.logger.exception('工作台通知加载失败 user_id=%s',
                                     getattr(user, 'id', None))

    return {'todos': todos, 'todo_count': len(todos), 'unread': unread,
            'recent': recent}
