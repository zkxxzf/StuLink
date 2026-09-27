# -*- coding: utf-8 -*-
"""为 teacher_achievements 增加 review_note 列（审核意见/驳回原因）。

- 幂等：已存在则 SKIP；
- 列名与类型为脚本内硬编码常量，无外部输入拼接。
"""
import glob
import os
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')

NEW_COLUMNS = [('review_note', 'VARCHAR(200)')]

if not os.path.isdir(DATA_DIR):
    print(f'[SKIP] 数据目录不存在: {DATA_DIR}')
    sys.exit(0)

touched = 0
for db_path in sorted(glob.glob(os.path.join(DATA_DIR, '*.db'))):
    try:
        conn = sqlite3.connect(db_path)
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='teacher_achievements'"
        ).fetchone()
        if not exists:
            conn.close()
            continue
        columns = [c[1] for c in conn.execute('PRAGMA table_info(teacher_achievements)')]
        for col, ddl in NEW_COLUMNS:
            if col in columns:
                print(f'[SKIP] {os.path.basename(db_path)} 已存在 teacher_achievements.{col}')
                continue
            conn.execute(f'ALTER TABLE teacher_achievements ADD COLUMN {col} {ddl}')
            print(f'[OK]   {os.path.basename(db_path)} 新增 teacher_achievements.{col} {ddl}')
            touched += 1
        conn.commit()
        conn.close()
    except Exception as e:  # noqa: BLE001  单库失败不影响其余库
        print(f'[ERR]  {os.path.basename(db_path)}: {e}')

print(f'[DONE] 迁移完成，共新增 {touched} 列')

# StuLink v1.18.2.1 2026-09-24
# Copyright (c) 2026 zkxxzf. Apache License 2.0
