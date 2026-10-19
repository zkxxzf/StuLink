# -*- coding: utf-8 -*-
"""为 operation_logs 增加 HTTP 审计字段（请求级兜底网配套）。

新增列：
  endpoint     VARCHAR(120)  -- Flask endpoint，如 system.students.create
  method       VARCHAR(10)   -- POST/PUT/PATCH/DELETE
  status_code  INTEGER       -- 响应状态码
  request_id   VARCHAR(36)   -- 同一次请求的多条日志串联

说明：
  - 列名/类型全部来自本文件硬编码常量（不拼接外部输入），无注入面；
  - 幂等：已存在则 SKIP，可重复执行；
  - operation_logs 位于主库 data/system.db，但历史迁移脚本曾指向 dormitory.db，
    故这里扫描 data/*.db，对所有含该表的库统一补齐，避免漏库。
"""
import os as _os
import sys as _sys
_HERE = _os.path.dirname(_os.path.abspath(__file__))
_SCRIPTS = _os.path.dirname(_HERE)
_ROOT = _os.path.dirname(_SCRIPTS)
for _p in (_ROOT, _SCRIPTS):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import glob
import os
import sqlite3
import sys

BASE_DIR = _ROOT
DATA_DIR = os.path.join(BASE_DIR, 'data')

SCRIPT_NAME = os.path.basename(__file__)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# 2026-10-12：迁移登记（幂等）——已登记过则整体跳过；登记查询失败不阻塞迁移
try:
    import _mig_log
    if _mig_log.is_applied(SCRIPT_NAME):
        print(f'[SKIP] {SCRIPT_NAME} 已登记为已应用，跳过迁移')
        sys.exit(0)
except Exception as _e:  # noqa: BLE001
    print(f'[WARN] 读取迁移登记失败（继续执行迁移）：{_e}')

# (列名, DDL 类型) —— 硬编码常量
NEW_COLUMNS = [
    ('endpoint', 'VARCHAR(120)'),
    ('method', 'VARCHAR(10)'),
    ('status_code', 'INTEGER'),
    ('request_id', 'VARCHAR(36)'),
]

if not os.path.isdir(DATA_DIR):
    print(f'[SKIP] 数据目录不存在: {DATA_DIR}')
    sys.exit(0)

touched = 0
for db_path in sorted(glob.glob(os.path.join(DATA_DIR, '*.db'))):
    try:
        conn = sqlite3.connect(db_path)
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='operation_logs'"
        ).fetchone()
        if not exists:
            conn.close()
            continue
        columns = [c[1] for c in conn.execute('PRAGMA table_info(operation_logs)')]
        for col, ddl in NEW_COLUMNS:
            if col in columns:
                print(f'[SKIP] {os.path.basename(db_path)} 已存在 operation_logs.{col}')
                continue
            conn.execute(f'ALTER TABLE operation_logs ADD COLUMN {col} {ddl}')
            print(f'[OK]   {os.path.basename(db_path)} 新增 operation_logs.{col} {ddl}')
            touched += 1
        conn.commit()
        conn.close()
    except Exception as e:  # noqa: BLE001  单库失败不影响其余库
        print(f'[ERR]  {os.path.basename(db_path)}: {e}')

print(f'[DONE] 迁移完成，共新增 {touched} 列')

# 迁移登记（失败不得阻塞迁移）
try:
    _mig_log.record(SCRIPT_NAME, note=f'operation_logs 新增 {touched} 个 HTTP 审计列')
except Exception as _e:  # noqa: BLE001
    print(f'[WARN] 迁移登记失败（不影响迁移结果）：{_e}')

# StuLink v1.18.2.1 2026-09-24
# Copyright (c) 2026 zkxxzf. Apache License 2.0
