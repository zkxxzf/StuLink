# StuLink 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""P2：AI 每日调用配额（按用户 × 自然日，落 system.db）。

设计：
- 只统计“发起对外 AI 请求”的次数，不把 Key 配置/连通性测试算进去；
- `BEGIN IMMEDIATE` 原子自增，避免并发多个请求同时越过上限；
- 配额为 0/负数时表示不限制；
- 失败调用不返还次数（外部请求可能已产生费用），需管理员手工调整。
"""
import logging
import os
import sqlite3
from datetime import datetime

logger = logging.getLogger(__name__)


def _db_path():
    try:
        from app.extensions import db
        return db.engine.url.database
    except RuntimeError:
        from config import BASE_DIR
        return os.path.join(BASE_DIR, 'data', 'system.db')


def _connect():
    con = sqlite3.connect(_db_path(), timeout=10)
    con.execute(
        'CREATE TABLE IF NOT EXISTS ai_usage ('
        'id INTEGER PRIMARY KEY AUTOINCREMENT, '
        'user_id INTEGER NOT NULL, '
        'day VARCHAR(10) NOT NULL, '
        'count INTEGER NOT NULL DEFAULT 0, '
        'updated_at DATETIME)')
    con.execute(
        'CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_usage_user_day '
        'ON ai_usage (user_id, day)')
    return con


def _limit(default=50):
    try:
        from flask import current_app, has_app_context
        if has_app_context():
            return int(current_app.config.get('AI_DAILY_QUOTA', default))
    except Exception:  # noqa: BLE001
        pass
    try:
        return int(os.environ.get('AI_DAILY_QUOTA', default))
    except (TypeError, ValueError):
        return default


def consume(user_id, limit=None, day=None):
    """尝试消费一次配额，返回 (allowed, used, limit)。"""
    limit = _limit() if limit is None else int(limit)
    if limit <= 0:
        return True, 0, 0
    day = day or datetime.now().strftime('%Y-%m-%d')
    con = _connect()
    try:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute(
            'SELECT count FROM ai_usage WHERE user_id=? AND day=?',
            (user_id, day)).fetchone()
        used = int(row[0]) if row else 0
        if used >= limit:
            con.rollback()
            return False, used, limit
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        if row:
            con.execute(
                'UPDATE ai_usage SET count=count+1, updated_at=? '
                'WHERE user_id=? AND day=?', (now, user_id, day))
        else:
            con.execute(
                'INSERT INTO ai_usage (user_id, day, count, updated_at) '
                'VALUES (?, ?, 1, ?)', (user_id, day, now))
        con.commit()
        return True, used + 1, limit
    except sqlite3.Error as exc:
        con.rollback()
        logger.warning('AI 配额写入失败（本次放行）：%s', exc)
        # 配额是成本控制，不是安全边界；存储故障时放行，避免 AI 功能整体不可用。
        return True, 0, limit
    finally:
        con.close()


def reset(user_id, day=None):
    """测试/运维用：清零某用户某天的计数。"""
    day = day or datetime.now().strftime('%Y-%m-%d')
    con = _connect()
    try:
        con.execute('DELETE FROM ai_usage WHERE user_id=? AND day=?',
                    (user_id, day))
        con.commit()
    finally:
        con.close()
