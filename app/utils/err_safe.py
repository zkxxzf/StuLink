# StuLink v1.17.1 2026-09-23
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""异常文案脱敏（M-12：不让内部异常文本经响应回传前端）。

清单 M-12：多处把 `str(e)` / `traceback.format_exc()` 直接 jsonify 给前端，
攻击者可据此获得绝对路径、源码行、SQL 片段、表结构，用于辅助下一步攻击。

口径：异常详情一律进服务端日志；返回给前端的文案走本模块脱敏
（剔绝对路径 / 盘符 / 文件行号 / 常见 SQL 关键字片段），并统一加错误编号便于排查。
"""
import logging
import re
import uuid

_log = logging.getLogger('stulink.error')

_PATH_RE = re.compile(r'[A-Za-z]:[\\/][^\s\'"]*|/[A-Za-z0-9_./\-]+(?:\.py|\.html|\.db)')
_SQL_RE = re.compile(r'\b(SELECT|INSERT|UPDATE|DELETE|FROM|WHERE|JOIN|sqlite_master)\b',
                     re.IGNORECASE)
_LINE_RE = re.compile(r'(File\s+"[^"]+"|line\s+\d+|Traceback\s*\(most recent call last\))',
                      re.IGNORECASE)


def safe_error(e, default='操作失败，请稍后重试'):
    """把异常转成对用户安全的文案（含错误编号，便于对照服务端日志）"""
    ref = uuid.uuid4().hex[:8]
    _log.error('[M-12][ref=%s] %s: %s', ref, type(e).__name__, e)
    text = str(e) or ''
    text = _PATH_RE.sub('[路径已隐藏]', text)
    text = _SQL_RE.sub('[SQL]', text)
    text = _LINE_RE.sub('', text).strip()
    # 脱敏后过短/为空 → 用通用文案，避免把原始内部信息带出去
    if len(text) < 2 or len(text) > 200:
        text = default
    return f'{text}（错误编号 {ref}）'
