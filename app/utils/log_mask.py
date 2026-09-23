# StuLink v1.17.1 2026-09-23
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""日志脱敏（L-2）。

清单 L-2：`logs/bands.log` 等日志可能带出身份证号、手机号等业务上下文。
这里用 logging.Filter 在**落盘前**统一掩码，避免依赖每个调用点自觉脱敏。
"""
import logging
import re

_ID_CARD = re.compile(r'\b\d{17}[\dXx]\b')
_PHONE = re.compile(r'\b1[3-9]\d{9}\b')
_EMAIL = re.compile(r'\b[\w.+-]+@[\w-]+\.[\w.-]+\b')


def mask_text(text):
    """掩码常见敏感串：身份证（保留后 4 位便于排查）、手机号、邮箱"""
    if not isinstance(text, str):
        return text

    def _id(m):
        return '**************' + m.group(0)[-4:]

    text = _ID_CARD.sub(_id, text)
    text = _PHONE.sub(lambda m: m.group(0)[:3] + '****' + m.group(0)[-4:], text)
    text = _EMAIL.sub('***@***', text)
    return text


class MaskingFilter(logging.Filter):
    """对日志内容做脱敏（含异常栈中的敏感串）"""

    def filter(self, record):
        try:
            msg = record.getMessage()
            masked = mask_text(msg)
            if masked != msg:
                record.msg = masked
                record.args = ()
            if record.exc_text:
                record.exc_text = mask_text(record.exc_text)
        except Exception:  # noqa: BLE001  脱敏失败不应阻断日志
            pass
        return True


def install_log_masking(logger_names=('bands', 'stulink', 'stulink.auth',
                                      'stulink.crypto', 'stulink.dormitory',
                                      'stulink.academic', 'stulink.error')):
    """给指定 logger 装上脱敏过滤器（幂等）"""
    for name in logger_names:
        lg = logging.getLogger(name)
        if not any(isinstance(f, MaskingFilter) for f in lg.filters):
            lg.addFilter(MaskingFilter())
