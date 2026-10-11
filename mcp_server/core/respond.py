# -*- coding: utf-8 -*-
"""工具输出工具包：JSON 序列化、字段裁剪、敏感信息脱敏、行数截断。

设计原则：**显式挑字段**（pick）而不是把 ORM 对象转 __dict__ 全丢出去。
系统中不少表带敏感列（身份证密文、手机号、备注等），显式挑字段能从源头
避免"顺手把不该给 AI 看的列也带出去"。
"""
import json
from datetime import date, datetime

# 响应中需要脱敏的字段名（按常见命名，大小写不敏感）
_SENSITIVE_KEYS = {'id_card_number', 'id_card', 'idcard', 'phone1', 'phone2',
                   'phone', 'mobile', 'password', 'password_hash'}


def to_json(payload):
    """统一 JSON 序列化：中文不转义、日期转 ISO、其余不可序列化对象转字符串。"""
    return json.dumps(payload, ensure_ascii=False, indent=2, default=_fallback)


def _fallback(obj):
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if hasattr(obj, 'isoformat'):
        return obj.isoformat()
    return str(obj)


def pick(obj, fields):
    """从 ORM 对象 / dict 中抽取指定字段（缺失记为 None），日期转 ISO 字符串。"""
    out = {}
    for name in fields:
        value = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
        if isinstance(value, (date, datetime)):
            value = value.isoformat()
        if name.lower() in _SENSITIVE_KEYS and isinstance(value, str):
            value = mask_secret(value)
        out[name] = value
    return out


def mask_secret(value, keep_head=3, keep_tail=4):
    """脱敏：保留首尾少量字符，中间替换为 *。短串整体打码。"""
    if not value:
        return value
    text = str(value)
    if len(text) <= keep_head + keep_tail:
        return '*' * len(text)
    return text[:keep_head] + '*' * (len(text) - keep_head - keep_tail) + text[-keep_tail:]


def limit_rows(rows, max_rows, note=None):
    """行数截断：超出上限时截断并附提示，避免把超大结果塞给模型。"""
    if not isinstance(rows, list) or len(rows) <= max_rows:
        return rows
    return {
        'total': len(rows),
        'truncated': True,
        'message': note or f'结果共 {len(rows)} 行，已截断为前 {max_rows} 行；可用分页或更精确的筛选参数缩小范围',
        'rows': rows[:max_rows],
    }