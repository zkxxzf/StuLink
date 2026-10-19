# -*- coding: utf-8 -*-
"""成绩首页「功能入口」的用户偏好：显示 / 隐藏 / 排序（2026-10-19）。

存储口径：沿用 `system_settings` 键值表（key = ``grades_entries_u<user_id>``），
值是 ``{"order": [...], "hidden": [...]}``。**不新建表、不需要数据库迁移**；
键名长度 20 + 用户 id，远小于 `system_settings.key` 的 50 字符上限。

坏数据一律 fail-open（按默认顺序、全部显示）：偏好只是界面布局，
读不出来不应该让仪表盘打不开。
"""
import json
import logging

from app.extensions import db
from app.models.system_setting import SystemSetting

logger = logging.getLogger(__name__)

KEY_PREFIX = 'grades_entries_u'
MAX_KEYS = 200                     # 入口总数远小于此，纯防脏数据写爆


def _key(user):
    """偏好键名（按用户隔离）"""
    return f'{KEY_PREFIX}{user.id}'


def _clean(values):
    """字符串去重保序 + 限长（忽略非字符串）"""
    seen = [str(v) for v in (values or []) if isinstance(v, str)]
    return list(dict.fromkeys(seen))[:MAX_KEYS]


def load(user):
    """读取偏好；返回 {'order': [...], 'hidden': [...]}（缺省为空）"""
    try:
        raw = SystemSetting.get(_key(user))
        data = json.loads(raw) if raw else {}
    except (ValueError, TypeError) as e:
        logger.warning('成绩首页入口偏好解析失败，按默认处理: %s', e)
        return {}
    if not isinstance(data, dict):
        return {}
    return {'order': _clean(data.get('order')),
            'hidden': _clean(data.get('hidden'))}


def save(user, order, hidden):
    """保存偏好（整份覆盖），返回写入后的 dict；调用方负责提示成功"""
    payload = {'order': _clean(order), 'hidden': _clean(hidden)}
    SystemSetting.set(_key(user), json.dumps(payload, ensure_ascii=False),
                      user_id=user.id, description='成绩首页功能入口偏好')
    db.session.commit()
    return payload
