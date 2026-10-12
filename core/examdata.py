# -*- coding: utf-8 -*-
"""全场分析数据取用：复用 stats_service.cached_exam_data，并按 exam_cache_ttl 主动失效。

背景：ExamData 的进程级缓存 TTL 为 900 秒，且主应用"导入 / 重算 / 改分 / 划线"
只会清主应用进程内的缓存；MCP 是独立进程，感知不到这些变更。若沿用 900 秒，
AI 最长会在 15 分钟内看到与网页不一致的旧数据。

做法：MCP 自己记录每场考试上次重建的时间，超过 config.exam_cache_ttl 就调用
invalidate_exam_cache（它同时清 ExamData 缓存）后重建。置 0 可关闭本机制。
"""
import threading
import time

from .. import config

_last_build = {}     # exam_id -> 上次主动重建时间
_lock = threading.Lock()


def get_exam_data(exam_id):
    """取该考试的全场分析数据；超出 exam_cache_ttl 时先清 MCP 进程内缓存再重建。"""
    from app.modules.grades.services import stats_service
    try:
        ttl = int(config.load_config().get('exam_cache_ttl') or 0)
    except (TypeError, ValueError):   # 配置非法时按默认 TTL 处理，避免所有成绩工具报错
        ttl = 300
    if ttl > 0:
        now = time.time()
        with _lock:
            last = _last_build.get(exam_id)
            stale = last is None or now - last >= ttl
            if stale:
                _last_build[exam_id] = now
        if stale:
            try:
                from app.modules.grades.utils import invalidate_exam_cache
                invalidate_exam_cache(exam_id)
            except Exception:  # noqa: BLE001  失效失败不阻断（最坏是读到旧快照）
                pass
    return stats_service.cached_exam_data(exam_id)
