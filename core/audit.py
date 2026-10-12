# -*- coding: utf-8 -*-
"""MCP 调用审计：把工具调用写进系统既有的操作日志表（OperationLog）。

复用 app.utils.helpers.log_operation，好处是管理端"操作日志"页面
能直接看到 AI 的每一次动作，不必另建一套日志表。
"""
import logging

logger = logging.getLogger('stulink.mcp.audit')

_MAX_DETAIL = 500


def _brief(params):
    """压缩参数以便入库：长值截断，整体限制长度。"""
    if not params:
        return ''
    parts = []
    for key, value in params.items():
        text = str(value)
        if len(text) > 80:
            text = text[:80] + '…'
        parts.append(f'{key}={text}')
    joined = ', '.join(parts)
    return joined[:_MAX_DETAIL]


def write_log(user, tool, params, ok=True, error=None):
    """写审计日志（失败只记 MCP 侧日志，绝不打断工具返回）。"""
    try:
        from app.utils.helpers import log_operation
        detail = {'tool': tool, 'params': _brief(params), 'ok': ok}
        if error:
            detail['error'] = str(error)[:300]
        log_operation(user, action='MCP调用', target_type='MCP工具', target_id=None,
                      detail=detail, module='mcp',
                      severity='INFO' if ok else 'WARNING')
    except Exception as exc:  # noqa: BLE001  审计失败不影响主流程，但必须留痕
        logger.warning('MCP 审计日志写入失败（tool=%s）: %s', tool, exc)