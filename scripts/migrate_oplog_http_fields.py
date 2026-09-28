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
import glob
import os
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')

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

# StuLink v1.18.2.1 2026-09-24
# Copyright (c) 2026 zkxxzf. Apache License 2.0
