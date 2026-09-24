"""线程安全的内存缓存工具（TTL + LRU 容量上限）

# StuLink v1.18.2.1 2026-09-24
# Copyright (c) 2026 zkxxzf. Apache License 2.0

设计参照 app/modules/grades/services/stats_service.py 的 ExamData 缓存范式：
- threading.RLock 保护读写与过期清理（waitress 多线程下无竞态）
- OrderedDict 实现 LRU：超过 maxsize 时优先淘汰过期项，再淘汰最久未使用项
- 只应缓存纯数据（dict/list/tuple/标量）；不要缓存 ORM 对象，
  若必须缓存 ORM 查询结果，先转 dict 或将实例 expunge 出会话

向后兼容说明：
- get/set/delete/clear 签名与旧 SimpleCache 一致（clear 新增可选 prefix 参数）
- `_cache` 仍为「键 → 条目」的 dict（OrderedDict），旧代码对其 keys() 的
  遍历（helpers.clear_dict_cache / grades.utils / scripts/clear_cache.py）不受影响；
  条目内部结构为 (value, expires_at)，取值请一律走 get()，不要直接下标访问
"""
import threading
import time
from collections import OrderedDict
from functools import wraps

# get() 的哨兵默认值：区分「缓存了 None」与「未命中」
_MISSING = object()


class SimpleCache:
    """线程安全内存缓存：TTL 过期 + LRU 容量上限

    :param default_timeout: set() 未显式给 timeout 时的默认 TTL（秒）
    :param maxsize: 最大条目数；超限先清过期项，仍超则按 LRU 淘汰最旧项。
                    0 或 None 表示不限容量（不建议，长驻进程有内存泄漏风险）
    """

    def __init__(self, default_timeout=300, maxsize=1024):
        # key -> (value, expires_at|None)；None 表示永不过期
        self._cache = OrderedDict()
        self._lock = threading.RLock()
        self.default_timeout = default_timeout
        self.maxsize = maxsize

    # ---------- 基础 API ----------

    def get(self, key, default=None):
        """获取缓存值；未命中或已过期返回 default（默认 None）"""
        now = time.time()
        with self._lock:
            ent = self._cache.get(key)
            if ent is None:
                return default
            value, expires_at = ent
            if expires_at is not None and now >= expires_at:
                del self._cache[key]
                return default
            # LRU：命中即视为最近使用
            self._cache.move_to_end(key)
            return value

    def set(self, key, value, timeout=None):
        """写入缓存。timeout=None 用默认 TTL；timeout<=0 表示永不过期"""
        if timeout is None:
            timeout = self.default_timeout
        expires_at = time.time() + timeout if timeout and timeout > 0 else None
        with self._lock:
            self._cache[key] = (value, expires_at)
            self._cache.move_to_end(key)
            self._evict_locked()

    def delete(self, key):
        """删除单个键（不存在时静默）"""
        with self._lock:
            self._cache.pop(key, None)

    def clear(self, prefix=None):
        """清空缓存。prefix 为 None 清空全部；否则只清除以 prefix 开头的键"""
        with self._lock:
            if prefix is None:
                self._cache.clear()
                return
            for key in [k for k in self._cache if k.startswith(prefix)]:
                del self._cache[key]

    # ---------- 内部 ----------

    def _evict_locked(self):
        """容量控制（调用方必须已持锁）：先惰性清过期项，仍超限则 LRU 淘汰"""
        if not self.maxsize or len(self._cache) <= self.maxsize:
            return
        now = time.time()
        for key in list(self._cache.keys()):
            if len(self._cache) <= self.maxsize:
                break
            _, expires_at = self._cache[key]
            if expires_at is not None and now >= expires_at:
                del self._cache[key]
        while self.maxsize and len(self._cache) > self.maxsize:
            self._cache.popitem(last=False)  # 弹出最久未使用项

    def __len__(self):
        with self._lock:
            return len(self._cache)


# 全局缓存实例（与旧版同名同用法）
cache = SimpleCache(default_timeout=300, maxsize=1024)


def cached(ttl=300, maxsize=256, key_prefix=None):
    """函数级缓存装饰器：按「key_prefix + 参数」缓存返回值

    - 仅适用于返回纯数据（dict/list/tuple/标量）的函数，不要缓存 ORM 对象
    - 每个被装饰函数持有独立的 SimpleCache 实例（互不挤占容量）
    - 通过 wrapper.invalidate()（清空该函数全部缓存）或
      wrapper.cache.delete(key) 主动失效；数据写入路径应调用 invalidate()

    用法::

        @cached(ttl=600)
        def get_options(code):
            ...

        get_options.invalidate()  # 数据变更后主动失效
    """
    def decorator(fn):
        prefix = key_prefix or f'{fn.__module__}.{fn.__qualname__}'
        local_cache = SimpleCache(default_timeout=ttl, maxsize=maxsize)

        @wraps(fn)
        def wrapper(*args, **kwargs):
            key = f'{prefix}:{args!r}:{sorted(kwargs.items())!r}'
            value = local_cache.get(key, default=_MISSING)
            if value is not _MISSING:
                return value
            value = fn(*args, **kwargs)
            local_cache.set(key, value, timeout=ttl)
            return value

        wrapper.cache = local_cache
        wrapper.invalidate = local_cache.clear
        return wrapper
    return decorator
