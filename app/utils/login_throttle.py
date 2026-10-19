# StuLink 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""登录限流/账号锁定的 SQLite 持久化实现（P1 M-01）。

设计：
- 每次读改写都在 `BEGIN IMMEDIATE` 事务内完成，waitress 多线程不会互相覆盖；
- `check()` 只读：已锁定返回剩余秒数；窗口内达到上限也返回剩余窗口秒数；
- `register_failure()` 原子自增：窗口过期自动重置；达到上限写 `locked_until`；
- 登录成功 `clear()` 清两条键（IP 与 来源+账号）。

表由模型 `app/models/login_throttle.py` 声明，create_all 会建；生产用
`scripts/migrate/migrate_login_throttle_20261019.py` 显式建表（幂等）。
"""
import logging
import os
import sqlite3
import time

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS login_throttles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key VARCHAR(160) NOT NULL UNIQUE,
    count INTEGER NOT NULL DEFAULT 0,
    window_start FLOAT NOT NULL,
    locked_until FLOAT,
    updated_at FLOAT NOT NULL
)
"""
_IDX = ('CREATE INDEX IF NOT EXISTS ix_login_throttles_key '
        'ON login_throttles(key)')

_last_cleanup = 0.0


def _db_path():
    """主库（system.db）路径。

    优先进 app context 取 db.engine（测试/多库绑定下路径可能被改）；
    脚本或 after_request 之外的调用没有 app context 时，退回 config.BASE_DIR/data。
    """
    try:
        from app.extensions import db
        return db.engine.url.database
    except RuntimeError:
        from config import BASE_DIR
        return os.path.join(BASE_DIR, 'data', 'system.db')


def _connect():
    path = _db_path()
    conn = sqlite3.connect(path, timeout=10)
    conn.execute(_DDL)
    conn.execute(_IDX)
    return conn


def _cleanup_old(conn, days=7):
    """惰性清理 7 天没更新的行；每小时最多一次，避免每次登录都扫表。"""
    global _last_cleanup
    now = time.time()
    if now - _last_cleanup < 3600:
        return
    _last_cleanup = now
    try:
        conn.execute('DELETE FROM login_throttles WHERE updated_at < ?',
                     (now - days * 86400,))
    except sqlite3.Error:
        logger.warning('[login_throttle] 清理过期行失败', exc_info=True)


def check(throttle_key, limit, window, lock_seconds=0):
    """返回 (allowed, retry_after_seconds)。

    - 已锁定：allowed=False，retry_after=锁剩余秒；
    - 窗口内计数达上限：allowed=False，retry_after=窗口剩余秒（lock_seconds=0 时）；
    - 其他：allowed=True。
    """
    now = time.time()
    try:
        conn = _connect()
        try:
            row = conn.execute(
                'SELECT count, window_start, locked_until '
                'FROM login_throttles WHERE key=?', (throttle_key,)).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        # 限流表不可用时 fail-open：不能因为审计/限流把正常登录全堵死。
        logger.warning('[login_throttle] 读取失败，本次放行', exc_info=True)
        return True, 0

    if not row:
        return True, 0
    count, window_start, locked_until = row
    if locked_until and now < float(locked_until):
        return False, max(1, int(float(locked_until) - now))
    if (not locked_until and lock_seconds == 0
            and count >= limit and now - float(window_start) < window):
        return False, max(1, int(window - (now - float(window_start))))
    return True, 0


def register_failure(throttle_key, limit, window, lock_seconds=0):
    """原子记录一次失败；返回 (fails, locked_until_epoch)。"""
    now = time.time()
    conn = None
    try:
        conn = _connect()
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute(
            'SELECT count, window_start, locked_until '
            'FROM login_throttles WHERE key=?', (throttle_key,)).fetchone()
        if row is None:
            count, window_start, locked_until = 1, now, None
            conn.execute(
                'INSERT INTO login_throttles '
                '(key, count, window_start, locked_until, updated_at) '
                'VALUES (?,?,?,?,?)',
                (throttle_key, count, window_start, locked_until, now))
        else:
            count, window_start, locked_until = row
            # 锁已过期且窗口也过期 → 重置计数；否则在窗口内累加
            if locked_until and now >= float(locked_until):
                count, window_start, locked_until = 0, now, None
            elif now - float(window_start) >= window:
                count, window_start, locked_until = 0, now, None
            count = int(count) + 1
            if lock_seconds and count >= limit:
                locked_until = now + lock_seconds
            conn.execute(
                'UPDATE login_throttles SET count=?, window_start=?, '
                'locked_until=?, updated_at=? WHERE key=?',
                (count, window_start, locked_until, now, throttle_key))
        _cleanup_old(conn)
        conn.commit()
        return count, locked_until
    except sqlite3.Error:
        logger.warning('[login_throttle] 写入失败（本次不计数）', exc_info=True)
        return 0, None
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass


def clear(throttle_key):
    """登录成功后清除该键；失败只记日志，不影响登录。"""
    try:
        conn = _connect()
        try:
            conn.execute('DELETE FROM login_throttles WHERE key=?',
                         (throttle_key,))
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        logger.warning('[login_throttle] 清除失败 key=%s', throttle_key,
                       exc_info=True)


def prune(days=30):
    """脚本/维护用：删除 N 天未更新的行，返回删除行数。"""
    conn = _connect()
    try:
        cur = conn.execute('DELETE FROM login_throttles WHERE updated_at < ?',
                           (time.time() - days * 86400,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def table_exists():
    """供启动自检/验证脚本使用。"""
    try:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='login_throttles'").fetchone()
            return row is not None
        finally:
            conn.close()
    except sqlite3.Error:
        return False
