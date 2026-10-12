#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""迁移登记公共模块（`_` 前缀防误执行，需随仓库提交）。

用法（迁移脚本内）：
    SCRIPT_NAME = os.path.basename(__file__)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _mig_log

    if _mig_log.is_applied(SCRIPT_NAME):
        print(f'[SKIP] {SCRIPT_NAME} 已应用过，跳过迁移')
        sys.exit(0)
    ... 迁移主体 ...
    try:
        _mig_log.record(SCRIPT_NAME, note='...')
    except Exception:
        pass  # 登记失败绝不阻塞迁移

设计决策：
- 登记表 = MigrationLog（app/models/migration_log.py，主库 data/system.db）；
- 本模块**裸 sqlite3 直连主库，不 import Flask/SQLAlchemy**：迁移脚本本就直连
  data/ 真实库执行，为登记一行记录拉起 create_app 会触发种子数据/全库建表，
  副作用大且慢；DDL 与模型的列名/类型/unique 约束保持一致；
- 幂等：record() 先查后插，重复执行不产生第二行。
"""
import os
import sqlite3
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, 'data', 'system.db')

_DDL = """
CREATE TABLE IF NOT EXISTS migration_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    script VARCHAR(200) NOT NULL UNIQUE,
    applied_at DATETIME NOT NULL,
    note VARCHAR(200)
)
"""


def _connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(_DDL)
    conn.commit()
    return conn


def is_applied(script):
    """该脚本是否已登记过。查询异常时视为未应用（保证迁移不被误跳过）。"""
    try:
        conn = _connect()
        try:
            row = conn.execute(
                'SELECT 1 FROM migration_log WHERE script=?', (script,)).fetchone()
        finally:
            conn.close()
        return row is not None
    except Exception as _e:  # noqa: BLE001
        print(f'[WARN] _mig_log.is_applied 查询失败（视为未应用）：{_e}')
        return False


def record(script, note=''):
    """登记脚本已应用；已登记则跳过（幂等）。返回 True=本次写入 / False=已存在。"""
    try:
        conn = _connect()
        try:
            if conn.execute('SELECT 1 FROM migration_log WHERE script=?',
                            (script,)).fetchone():
                return False
            conn.execute(
                'INSERT INTO migration_log (script, applied_at, note) VALUES (?,?,?)',
                (script, datetime.now(), (note or '')[:200] or None))
            conn.commit()
        finally:
            conn.close()
        print(f'[MIGLOG] 已登记迁移：{script}')
        return True
    except Exception as _e:  # noqa: BLE001
        print(f'[WARN] 迁移登记失败（不影响迁移结果）：{_e}')
        return False


def all_applied():
    """返回 [(script, applied_at, note), ...]，按登记顺序升序"""
    try:
        conn = _connect()
        try:
            rows = conn.execute(
                'SELECT script, applied_at, note FROM migration_log ORDER BY id'
            ).fetchall()
        finally:
            conn.close()
        return rows
    except Exception as _e:  # noqa: BLE001
        print(f'[WARN] _mig_log.all_applied 查询失败：{_e}')
        return []


if __name__ == '__main__':
    raise SystemExit('这是公共模块，请在其他迁移脚本里 import 使用')
