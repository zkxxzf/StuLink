# -*- coding: utf-8 -*-
"""执行身份上下文：认证中间件写入，工具执行时读取。

为什么用 contextvar（而不是全局变量）：
MCP 服务是多用户并发进程，"当前是谁在调用"必须随请求走。SDK（mcp 2.3.0）为此
提供了官方支持路径——StreamableHTTP 的 legacy 链路用 ContextSendStream 快照
「写消息那一刻」的 contextvars，分发时再以 sender_ctx.run(...) 还原
（mcp/shared/_context_streams.py、mcp/shared/jsonrpc_dispatcher.py 的注释明确说明
这是为 ASGI 中间件设置 contextvar 而设计）；现代链路走 task context 复制。
因此「ASGI 中间件 set 身份 → 工具处理逻辑读取」是可靠的：

  · async 工具：同 task，直接可读；
  · 同步工具：anyio.to_thread.run_sync 会 copy_context 后在工作线程内执行，也可读；
  · 工具内部的 DB 操作由 core/db.py 的线程池执行——那里不依赖 contextvar，
    toolkit 在 async 侧读出面身份后显式传参进线程（见 toolkit._dispatch）。

注意：loop.run_in_executor 不复制 context，所以身份绝不依赖它跨线程传播。
"""
import contextvars

_current = contextvars.ContextVar('stulink_mcp_user_id', default=None)
_static = None      # stdio 模式：进程级静态身份（启动时认证一次）


def set_current(user_id):
    """设置请求级身份（由认证中间件在 ASGI 层调用），返回用于 reset 的句柄。"""
    return _current.set(user_id)


def reset(handle):
    """还原请求级身份（中间件 finally 中调用）。"""
    try:
        _current.reset(handle)
    except ValueError:      # 跨 context reset（理论不可达）不应影响请求
        pass


def set_static(user_id):
    """设置进程级身份（stdio 模式启动时认证一次，对全部调用生效）。"""
    global _static
    _static = user_id


def current_user_id():
    """当前执行身份 id：请求级优先，其次进程级（stdio）；None = 未认证。"""
    return _current.get() or _static
