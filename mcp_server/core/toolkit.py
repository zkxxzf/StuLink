# -*- coding: utf-8 -*-
"""工具执行中枢：统一处理"写开关 → 串行 DB 执行 → 审计 → JSON 封装 → 异常兜底"。

所有工具函数都通过 db_call / db_write 执行，保证：
  · 写操作受 config.json 的 allow_write 总开关约束；
  · ORM 操作串行在专用线程 + 独立 app context 内跑；
  · 越界（PermissionError）与参数问题（ValueError）转成模型能看懂的中文提示，
    而不是把 Python 堆栈直接抛给模型；
  · 返回值统一为 {"ok": true, "data": ...} / {"ok": false, "error": "..."} 的 JSON 文本。
"""
import logging

from .. import config
from . import audit, bootstrap, db, guards, respond

logger = logging.getLogger('stulink.mcp.toolkit')


def ok(data):
    """成功响应。"""
    return respond.to_json({'ok': True, 'data': data})


def fail(message):
    """失败响应（预期内的错误，如权限不足 / 参数非法）。"""
    return respond.to_json({'ok': False, 'error': message})


async def db_call(fn, *, tool, params=None, write=False, perm=None):
    """只读工具入口：fn(user) -> 可 JSON 化的数据；perm 为网页端同名权限点。"""
    return await _dispatch(fn, tool=tool, params=params, write=write, perm=perm)


async def db_write(fn, *, tool, params=None, perm=None):
    """写入工具入口（受 allow_write 总开关约束）。"""
    return await _dispatch(fn, tool=tool, params=params, write=True, perm=perm)


async def _dispatch(fn, *, tool, params, write, perm=None):
    cfg = config.load_config()
    if write and not cfg.get('allow_write'):
        return fail('写操作已被禁用：请在 mcp_server/config.json 中把 allow_write 改为 true 并重启服务')
    audit_on = bool(write or cfg.get('audit_reads'))
    try:
        data = await db.run_async(lambda: _execute(fn, tool, params, audit_on, perm))
        return ok(data)
    except PermissionError as exc:
        return fail(f'权限不足：{exc}（MCP 绑定的账号没有该数据范围的访问权限）')
    except ValueError as exc:
        return fail(f'参数错误：{exc}')
    except Exception as exc:  # noqa: BLE001  兜底：不让堆栈与内部细节直接暴露给模型
        logger.exception('MCP 工具执行失败：%s', tool)
        return fail(f'执行失败：{type(exc).__name__}；详细原因已记录在 MCP 服务端日志')


def _execute(fn, tool, params, audit_on, perm=None):
    """在 app context 内执行（由 db.run 提供 context 与串行保证）。

    perm 非空时先做功能权限校验（对应网页端 @perm_required），越界抛 PermissionError。
    """
    user = bootstrap.load_bound_user()
    error = None
    try:
        if perm:
            guards.perm(user, perm)
        return fn(user)
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        if audit_on:
            audit.write_log(user, tool, params, ok=error is None, error=error)