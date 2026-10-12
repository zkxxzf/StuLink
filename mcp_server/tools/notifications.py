# -*- coding: utf-8 -*-
"""通知工具（2026-10-11 第 2 批）：发布 / 删除。

权限与网页端 notifications/routes.py 完全一致，**三条路由层限制必须复刻**
（服务层 create_notification 一条都没有，直接调服务层会绕过这些限制）：
  1. 写权限闸：workbench.notifications 或 system.settings 任一；
  2. 群发限制：target_type 为 all / role 时**必须**持 system.settings
     （否则只能发 grade / class / users），防止越权广播全校；
  3. 链接协议白名单（M-11，防存储型 XSS）：只允许 http/https；
  4. 长度上限（L-12）：标题 100、正文 5000、链接 500。

target_type 与范围参数配合：
  · grade → target_grades（年级列表）
  · class → target_classes（班级名列表）
  · role  → target_roles（角色列表，需 system.settings）
  · users → target_uids（username / 学号 / teacher_uid，可混填）
  · all   → 全体（需 system.settings）

刻意未开放：删除任意通知（网页端 delete 允许删他人通知，风险高）；
只保留"删除自己发布的通知"。
"""
import json

from ..core import respond, toolkit


def _can_publish(user):
    """发布/删除通知的准入闸。

    2026-10-12 审计修复：网页端 `notification_create` 是「装饰器
    `workbench.notifications_view` + 函数内 `_can_publish()`」**两道**；
    原 MCP 只做了第二道，仅持 system.settings 而无 notifications_view 的账号
    在网页端进不去、却能通过 MCP 发布。这里补齐第一道。
    """
    if user is None:
        raise PermissionError('未获取到执行身份')
    if not user.has_perm('workbench.notifications_view'):
        raise PermissionError('账号缺少权限：workbench.notifications_view'
                              '（查看通知列表）')
    if user.has_perm('workbench.notifications') or user.has_perm('system.settings'):
        return True
    raise PermissionError('账号没有发布通知的权限（需 workbench.notifications '
                          '或 system.settings）')


def _safe_link(url):
    """M-11 同款：只允许 http/https（防 javascript: 等存储型 XSS）。"""
    from urllib.parse import urlparse
    if not url:
        return None
    try:
        parts = urlparse(url)
    except Exception:  # noqa: BLE001
        raise ValueError('通知链接格式不正确')
    if parts.scheme not in ('http', 'https') or not parts.netloc:
        raise ValueError('通知链接只允许 http:// 或 https:// 开头的地址')
    return url


def _build_scope(target_type, target_grades, target_classes, target_roles,
                 target_uids, user):
    """按 target_type 组装 scope JSON 与 uid 列表，并做群发权限校验。"""
    scope = {}
    uids = None
    if target_type == 'grade':
        scope['grades'] = [str(x).strip() for x in (target_grades or []) if str(x).strip()]
    elif target_type == 'class':
        scope['classes'] = [str(x).strip() for x in (target_classes or []) if str(x).strip()]
    elif target_type == 'role':
        scope['roles'] = [str(x).strip() for x in (target_roles or []) if str(x).strip()]
    elif target_type == 'users':
        uids = [str(x).strip() for x in (target_uids or []) if str(x).strip()]
        if not uids:
            raise ValueError('target_type=users 时必须提供非空的 target_uids')
    elif target_type != 'all':
        raise ValueError('target_type 只能是 all / grade / class / role / users')

    # v1.17.0：无 system.settings 者不得面向全体或按角色群发
    if target_type in ('all', 'role') and not user.has_perm('system.settings'):
        raise PermissionError('当前权限仅支持面向指定年级、班级或人员发布通知'
                              '（全体/按角色群发需 system.settings）')
    if target_type in ('grade', 'class') and not scope.get(
            'grades' if target_type == 'grade' else 'classes'):
        raise ValueError(f'target_type={target_type} 时必须提供对应范围参数')
    return json.dumps(scope, ensure_ascii=False), uids


def _create(user, title, content, target_type, target_grades, target_classes,
            target_roles, target_uids, priority, category, link_url):
    from app.modules.notifications.services import notification_service
    from app.utils.helpers import log_operation
    from app.utils.text_guard import clamp_text

    _can_publish(user)
    title = clamp_text((title or '').strip(), 100)
    content = clamp_text((content or '').strip(), 5000)
    if not title or not content:
        raise ValueError('标题和内容不能为空')
    link = _safe_link(clamp_text((link_url or '').strip(), 500) or None)
    target_type = (target_type or 'all').strip()

    scope_json, uids = _build_scope(target_type, target_grades, target_classes,
                                    target_roles, target_uids, user)
    ok, msg, notif = notification_service.create_notification(
        title=title, content=content, target_type=target_type,
        target_scope=scope_json, priority=(priority or 'normal').strip(),
        published_by=user.id, target_uids=uids,
        category=(category or 'system').strip(), link_url=link,
        creator_id=user.id, creator_name=user.real_name, publish=True)
    if not ok or notif is None:
        raise ValueError(msg or '通知发布失败')
    log_operation(user, '发布', '通知', notif.id,
                  f'{title}（{target_type}，{notif.recipient_count or 0} 人，MCP）',
                  module='notifications')
    return {'published': True,
            'notification': respond.pick(
                notif, ('id', 'title', 'target_type', 'priority', 'category',
                        'recipient_count', 'is_active', 'created_at')),
            'message': f'通知已发布，覆盖 {notif.recipient_count or 0} 人'}


def _delete_mine(user, notification_id):
    """删除自己发布的通知（不开放删他人通知）。"""
    from app.extensions import db
    from app.models.notification import Notification
    from app.utils.helpers import log_operation

    _can_publish(user)
    notif = db.session.get(Notification, int(notification_id)) if notification_id else None
    if notif is None:
        raise ValueError(f'通知不存在：{notification_id}')
    if notif.published_by != user.id and not user.has_perm('system.settings'):
        raise PermissionError('只能删除自己发布的通知')
    db.session.delete(notif)
    db.session.commit()
    log_operation(user, '删除', '通知', notification_id,
                  f'删除通知 #{notification_id}（MCP）', module='notifications')
    return {'deleted': True, 'notification_id': notification_id,
            'message': f'已删除通知 #{notification_id}'}


def register(server):
    """把本模块的工具注册到 MCP 服务。"""

    @server.tool(name='publish_notification',
                 description='【写操作】发布站内通知。target_type 取 '
                             'all(全体，需 system.settings) / grade(需 target_grades) / '
                             'class(需 target_classes) / role(需 target_roles + '
                             'system.settings) / users(需 target_uids)；'
                             'priority 取 normal/urgent；链接只允许 http(s)')
    async def publish_notification(title: str, content: str,
                                   target_type: str = 'grade',
                                   target_grades: list = None,
                                   target_classes: list = None,
                                   target_roles: list = None,
                                   target_uids: list = None,
                                   priority: str = 'normal',
                                   category: str = 'system',
                                   link_url: str = '') -> str:
        params = {'title': title, 'target_type': target_type,
                  'priority': priority}
        return await toolkit.db_write(
            lambda u: _create(u, title, content, target_type, target_grades,
                              target_classes, target_roles, target_uids,
                              priority, category, link_url),
            tool='publish_notification', params=params,
            perm=('workbench.notifications', 'system.settings'))

    @server.tool(name='delete_my_notification',
                 description='【写操作】删除自己发布的通知'
                             '（system.settings 持有者可删任意通知）')
    async def delete_my_notification(notification_id: int) -> str:
        return await toolkit.db_write(
            lambda u: _delete_mine(u, notification_id),
            tool='delete_my_notification',
            params={'notification_id': notification_id},
            perm=('workbench.notifications', 'system.settings'))
