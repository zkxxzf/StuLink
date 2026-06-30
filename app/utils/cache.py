"""简单的内存缓存工具"""
import time

class SimpleCache:
    """简单的内存缓存实现"""
# StuLink v1.4.5 2026-06-30
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0
    
    def __init__(self, default_timeout=300):
        self._cache = {}
        self._timeouts = {}
        self.default_timeout = default_timeout
    
    def get(self, key):
        """获取缓存值"""
        if key in self._cache:
            timeout = self._timeouts.get(key)
            if timeout is None or time.time() < timeout:
                return self._cache[key]
            else:
                # 过期删除
                self.delete(key)
        return None
    
    def set(self, key, value, timeout=None):
        """设置缓存值"""
        if timeout is None:
            timeout = self.default_timeout
        
        self._cache[key] = value
        if timeout > 0:
            self._timeouts[key] = time.time() + timeout
        else:
            self._timeouts[key] = None
    
    def delete(self, key):
        """删除缓存"""
        self._cache.pop(key, None)
        self._timeouts.pop(key, None)
    
    def clear(self):
        """清空所有缓存"""
        self._cache.clear()
        self._timeouts.clear()


# 全局缓存实例
cache = SimpleCache(default_timeout=300)
