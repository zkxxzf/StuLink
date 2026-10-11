# -*- coding: utf-8 -*-
"""数据库访问调度：所有 ORM 操作在专用单线程 + 独立 app context 内串行执行。

为什么单线程串行（而不是并发跑）：
  1. 底层是 SQLite，写操作本就整库串行，多线程只会把等待从"锁"搬到"排队"，
     反而更容易触发 database is locked；
  2. Flask-SQLAlchemy 的 session 绑定在 app context 上，串行可彻底避免
     跨 context 会话串用导致的 PendingRollbackError（会话中毒）；
  3. 每个 app context 退出时 Flask-SQLAlchemy 会自动 remove 会话，
     因此在多次工具调用之间不会残留脏会话。
"""
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

from . import bootstrap

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='stulink-mcp-db')
_lock = threading.Lock()


def run(fn):
    """线程安全入口：串行 + 独立 app context 内执行 fn。"""
    with _lock:
        app = bootstrap.get_app()
        with app.app_context():
            return fn()


async def run_async(fn):
    """异步入口：把同步 DB 工作丢进专用线程，避免阻塞 MCP 的事件循环。"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, run, fn)