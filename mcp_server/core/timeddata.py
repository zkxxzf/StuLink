# -*- coding: utf-8 -*-
"""限时练派生缓存的失效控制（MCP 独立进程专用）。

背景：`app.modules.grades.services.timed_analysis` 把「名次 / T 分 / 趋势」
按场次缓存 120 秒。主应用导入成绩后会主动 `invalidate()`，但 **MCP 是独立
进程**，感知不到；沿用 120 秒意味着 AI 可能读到导入前的旧名次。

做法与 `examdata.py` 同一套路：MCP 自己记上次清理时间，超过
config.json 的 `timed_cache_ttl`（默认 300 秒）就整表清一次再取数。
置 0 可关闭（只用主程序自带的 120 秒缓存）。
"""
import threading
import time

from .. import config

_last_clean = 0.0
_lock = threading.Lock()


def touch():
    """按需清一次限时练派生缓存（幂等，成本＝一次重建）。"""
    try:
        ttl = int(config.load_config().get('timed_cache_ttl') or 0)
    except (TypeError, ValueError):   # 配置非法时按默认 TTL 处理
        ttl = 300
    if ttl <= 0:
        return
    global _last_clean
    now = time.time()
    with _lock:
        if _last_clean and now - _last_clean < ttl:
            return
        _last_clean = now
    try:
        from app.modules.grades.services import timed_analysis
        timed_analysis.invalidate()
    except Exception:  # noqa: BLE001  失效失败不阻断（最坏是读到旧快照）
        pass
