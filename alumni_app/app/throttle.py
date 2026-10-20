# StuLink 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""往届站登录失败限流（进程内，替代此前的"零限流"）。

背景（2026-10-19 第十三轮审计）：往届站 `/login` 没有任何失败计数/锁定，
可对共用 `users` 表的主站账号做在线口令爆破（主站的持久化限流只作用于主站）。

实现：按「来源 IP + 账号」计数，5 次失败锁 5 分钟；成功登录清零。
进程内状态，重启即清 —— 往届站低流量、单进程部署，够用；多进程部署时
需改为与主站 `login_throttles` 同表（见 docs/规范与说明/数据库迁移清单.md）。
"""
import threading
import time

MAX_FAILURES = 5
WINDOW_SECONDS = 300
LOCK_SECONDS = 300

_lock = threading.Lock()
_fails = {}          # key -> [count, first_ts]
_locked = {}         # key -> unlock_ts


def _key(remote_addr, username):
    return f'{(remote_addr or "?")}|{(username or "").lower()}'


def is_locked(remote_addr, username):
    """是否处于锁定期；到期自动清理"""
    key = _key(remote_addr, username)
    now = time.time()
    with _lock:
        until = _locked.get(key)
        if until and until > now:
            return True
        if until:
            _locked.pop(key, None)
        return False


def register_failure(remote_addr, username):
    """记一次失败；达到阈值则锁定"""
    key = _key(remote_addr, username)
    now = time.time()
    with _lock:
        cnt, first = _fails.get(key, [0, now])
        if now - first > WINDOW_SECONDS:
            cnt, first = 0, now
        cnt += 1
        _fails[key] = [cnt, first]
        if cnt >= MAX_FAILURES:
            _locked[key] = now + LOCK_SECONDS
            _fails.pop(key, None)


def clear(remote_addr, username):
    """登录成功后清零"""
    key = _key(remote_addr, username)
    with _lock:
        _fails.pop(key, None)
        _locked.pop(key, None)
