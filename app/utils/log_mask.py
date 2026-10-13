# StuLink v1.18.0.0 2026-09-23
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""日志脱敏（L-2）。

清单 L-2：`logs/bands.log` 等日志可能带出身份证号、手机号等业务上下文。
这里用 logging.Filter 在**落盘前**统一掩码，避免依赖每个调用点自觉脱敏。
"""
import logging
import re

# 2026-10-10 审计说明：本文件的 `except: pass` 经评估**保持静默**（见下方 filter 注释），
# 原因是脱敏器内部打日志会递归且绕过脱敏。此处不引入 logger，避免残留无用代码。

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
        except Exception:  # noqa: BLE001
            # 2026-10-10：**此处禁止打日志** —— ① 本 Filter 被装到各 logger 上，
            # 内部 logger.warning 会触发 filter 重入（递归风险）；
            # ② 脱敏器自身输出的日志不经过脱敏，反而可能泄露未掩码的敏感串。
            # 维持原「静默降级」（返回 True 让日志照常输出），仅加说明。
            pass
        return True


_FACTORY_INSTALLED = False


def _install_on_handlers():
    """把脱敏挂到**已存在**的 handler 上。

    logger 级过滤器只作用于"通过该 logger 发出"的记录，别的 logger 传播过来的不会过；
    handler 级过滤器则对**所有到达该 handler 的记录**生效 —— 这才能覆盖
    `logging.getLogger(__name__)`（形如 app.modules.*）这一类不在名单里的模块 logger。
    """
    targets = list(logging.getLogger().handlers)            # root（文件/控制台 handler）
    for name in ('stulink', 'stulink.app', 'bands', 'stulink.bands'):
        targets += list(logging.getLogger(name).handlers)
    for h in targets:
        if not any(isinstance(f, MaskingFilter) for f in h.filters):
            h.addFilter(MaskingFilter())


def install_masking_factory():
    """把脱敏挂到 LogRecord 工厂上（全局、幂等）。

    2026-10-13 全面审计 M-4：`logger.addFilter` / `handler.addFilter` 都追不上
    **之后才创建**的 handler（例如 bands.py 首次写日志时才 new 出来的 FileHandler、
    第三方库自建的 handler）。`logging.setLogRecordFactory` 在**记录创建瞬间**掩码，
    对所有 logger、所有 handler（含晚挂的）一律生效，是唯一能兜住这种缺口的方式。
    代价：每条日志多一次正则匹配（三条正则，可忽略）；异常栈在工厂阶段尚未生成，
    仍由 logger 级 / handler 级过滤器兜住。
    """
    global _FACTORY_INSTALLED
    if _FACTORY_INSTALLED:
        return
    prev = logging.getLogRecordFactory()

    def _factory(*args, **kwargs):
        record = prev(*args, **kwargs)
        try:
            msg = record.getMessage()
            masked = mask_text(msg)
            if masked != msg:
                record.msg = masked
                record.args = ()
        except Exception:  # noqa: BLE001  脱敏失败绝不能吞掉日志本身
            pass
        return record

    logging.setLogRecordFactory(_factory)
    _FACTORY_INSTALLED = True


def install_log_masking(logger_names=('bands', 'stulink', 'stulink.auth',
                                      'stulink.crypto', 'stulink.dormitory',
                                      'stulink.academic', 'stulink.error'),
                        handlers=True, record_factory=True):
    """给 logger / handler / 日志记录工厂装上脱敏（幂等）。

    2026-10-13 全面审计 M-4：原实现只按 **logger 名** 安装，而各模块普遍使用
    `logging.getLogger(__name__)`（名称形如 `app.modules.system.routes.students`），
    不在名单里 —— 这些 logger 打出的身份证/手机号会原样落盘。现三层补齐：
      ① 名单 logger（保留原行为，兼容旧调用）；
      ② 现有 handler（覆盖"别人 logger 传播过来"的记录）；
      ③ LogRecord 工厂（覆盖此后新建的 handler 与第三方 logger）。
    """
    for name in logger_names:
        lg = logging.getLogger(name)
        if not any(isinstance(f, MaskingFilter) for f in lg.filters):
            lg.addFilter(MaskingFilter())
    if handlers:
        _install_on_handlers()
    if record_factory:
        install_masking_factory()
