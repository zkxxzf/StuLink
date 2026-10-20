# StuLink 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""跨进程文件锁（P1 M-18）。

用于「毕业归档」这类不可并发执行的整批操作：waitress 是多线程，同一进程内
`threading.Lock` 够用；但运维可能同时起第二个进程/脚本，因此用 OS 文件锁。

Windows 用 msvcrt.locking（锁 1 字节），POSIX 用 fcntl.flock。
锁文件放在系统临时目录，不放 data/（避免被同步盘复制/锁语义异常）。
"""
import contextlib
import os
import tempfile
import time

_LOCK_DIR = os.path.join(tempfile.gettempdir(), 'stulink_locks')


def _lock_path(name):
    os.makedirs(_LOCK_DIR, exist_ok=True)
    safe = ''.join(c if c.isalnum() or c in '-_.' else '_' for c in name)
    return os.path.join(_LOCK_DIR, safe + '.lock')


@contextlib.contextmanager
def file_lock(name, timeout=10.0):
    """独占锁上下文；超时抛 TimeoutError。"""
    path = _lock_path(name)
    f = open(path, 'a+b')
    start = time.time()
    locked = False
    try:
        while True:
            try:
                f.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError:
                if time.time() - start >= timeout:
                    raise TimeoutError(f'操作繁忙（锁 {name} 超时），请稍后重试')
                time.sleep(0.05)
        yield
    finally:
        if locked:
            try:
                f.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        f.close()


def with_file_lock(name, timeout=10.0):
    """视图装饰器：拿不到锁返回 503 JSON，不执行业务。"""
    def deco(fn):
        from functools import wraps

        @wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                with file_lock(name, timeout):
                    return fn(*args, **kwargs)
            except TimeoutError as exc:
                from flask import jsonify
                return jsonify(success=False, message=str(exc)), 503
        return wrapper
    return deco
