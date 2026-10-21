# -*- coding: utf-8 -*-
"""工具输出工具包：JSON 序列化、字段裁剪、脱敏、行数与体积双重收敛。

设计原则：**显式挑字段**（pick）而不是把 ORM 对象转 __dict__ 全丢出去。
系统中不少表带敏感列（身份证密文、手机号、备注等），显式挑字段能从源头
避免"顺手把不该给 AI 看的列也带出去"。

2026-10-21「省额度」改造（用户反馈"用一次吃掉 7 天额度"）：
此前一律 `indent=2` 输出，加上大量 `"key": null`，同一份结果比紧凑写法
多付三到五成 token，而模型并不需要这些空白字符。现在是三层收敛：
  1. **紧凑序列化**（`to_json`）：`separators=(',', ':')`，缩进需显式开启；
  2. **剔除空值**（`drop_none`）：递归删 None 键——缺值与 null 对模型等价；
  3. **列式明细**（`table_view`）：逐生 / 逐场这类多行同构结果用
     `cols + rows`，省掉每行重复的键名（明细场景通常占体积六成以上）；
  4. **预算兜底**（`fit`）：超出 `max_response_chars` 时降级摘要，防止
     一次误用把客户端上下文（与额度）一次性吃光。
"""
import json
from datetime import date, datetime

# 响应中需要脱敏的字段名（按常见命名，大小写不敏感）
_SENSITIVE_KEYS = {'id_card_number', 'id_card', 'idcard', 'phone1', 'phone2',
                   'phone', 'mobile', 'password', 'password_hash'}


def to_json(payload, pretty=None):
    """统一 JSON 序列化：中文不转义、日期转 ISO、其余不可序列化对象转字符串。

    pretty：是否缩进（留空读 config.json 的 `pretty_json`，默认 false）。
    缩进只用于人肉排查：它让同一份数据多付三到五成 token。
    """
    if pretty is None:
        pretty = _cfg().get('pretty_json')
    if pretty:
        return json.dumps(payload, ensure_ascii=False, indent=2,
                          default=_fallback)
    return json.dumps(payload, ensure_ascii=False, separators=(',', ':'),
                      default=_fallback)


def _cfg():
    """读配置（异常时按默认处理，不能因为配置坏就返回不了数据）。"""
    from .. import config
    try:
        return config.load_config() or {}
    except Exception:  # noqa: BLE001
        return {}


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


def drop_none(obj):
    """递归剔除值为 None 的键（列表/元组逐元素处理，标量原样返回）。

    意义：`{"rank": null, "move": null}` 这种占位在 JSON 里每处约 12 字符，
    逐生明细 50 行 × 数个空字段就是几千字符的空转。
    """
    if isinstance(obj, dict):
        return {k: drop_none(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, (list, tuple)):
        return [drop_none(x) for x in obj]
    return obj


def row_limit(default=200):
    """单次返回行数上限：读 config.json 的 max_rows（配置异常时回落默认值）。"""
    try:
        return max(1, int(_cfg().get('max_rows') or default))
    except Exception:  # noqa: BLE001  配置异常不应阻断工具返回
        return default


def limit_rows(rows, max_rows=None, note=None):
    """行数截断：超出上限时截断并附提示，避免把超大结果塞给模型。

    max_rows 留空时取 config.json 的 max_rows（默认 200）。
    """
    if max_rows is None:
        max_rows = row_limit()
    if not isinstance(rows, list) or len(rows) <= max_rows:
        return rows
    return {
        'total': len(rows),
        'truncated': True,
        # 2026-10-20 第十六轮：工具没有分页参数，原文案"可用分页"是误导
        'message': note or f'结果共 {len(rows)} 行，已截断为前 {max_rows} 行；'
                           '请用更精确的筛选参数（年级/班级/学科/考试）缩小范围',
        'rows': rows[:max_rows],
    }


def table_view(rows, cols, labels=None):
    """把同构字典序列转成列式：{"cols": [...], "rows": [[...], ...]}。

    为什么：明细结果里每行都重复一遍键名，50 行 × 9 科时键名要付 450 次；
    列式只在 `cols` 里写一次，实测同等信息少三到四成体积。

    rows  : [dict] —— 按键取值，缺失补 None；
    cols  : [str]  —— 输出顺序即列的顺序；
    labels: 可选 {原名: 输出名} —— 需要缩写列名（如 no / name）时传。
    """
    names = [labels.get(c, c) if labels else c for c in cols]
    data = [[r.get(c) if isinstance(r, dict) else None for c in cols]
            for r in rows]
    return {'cols': names, 'rows': data}


def slim(rows, keys, drop_none_values=True):
    """只留指定键：FIN 列表瘦身的主力（如班级汇总行去掉 layers/std/各科均分）。

    rows 可能是 dict（limit_rows 截断后的形态），此时原样返回。
    """
    if isinstance(rows, dict):
        return rows
    out = [{k: r.get(k) for k in keys if k in r} for r in rows or []]
    return drop_none(out) if drop_none_values else out


def fit(payload, max_chars=None):
    """预算兜底：超长时降级为"摘要 + 提示"，保护客户端上下文。

    只在少数极端场景生效（例如既没传班级、又要求全量 viewing），正常调用
    由各工具自己的 `view=brief` 控制体积。触发时明确告诉模型该加什么参数，
    而不是默默丢数据。
    """
    if max_chars is None:
        try:
            max_chars = int(_cfg().get('max_response_chars') or 0)
        except (TypeError, ValueError):
            max_chars = 0
    if max_chars <= 0:
        return payload
    text = to_json(payload)
    if len(text) <= max_chars:
        return payload
    keys = list(payload.keys()) if isinstance(payload, dict) else []
    return {
        'too_large': True,
        'chars': len(text),
        'budget': max_chars,
        'fields': keys,
        'hint': '结果超出单次预算已省略，请用 view=brief（默认即是）并缩小范围：'
                '指定 class_name / subject / kind，或?q 关键字',
    }
