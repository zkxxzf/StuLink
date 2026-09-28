# -*- coding: utf-8 -*-
"""新建 subject_leaders 表（备课组长登记，academic.db）。

- 幂等：已存在则跳过；
- 表名/列名全部来自本文件硬编码常量，无外部输入拼接（无注入面）；
- 唯一约束 (school_year, term, grade, subject)：同学年同学期同年级同学科只保留一条。
"""
import glob
import os
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')

DDL_TABLE = """
CREATE TABLE IF NOT EXISTS subject_leaders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    school_year VARCHAR(20) NOT NULL,
    term VARCHAR(10) DEFAULT '',
    grade VARCHAR(10) DEFAULT '',
    subject VARCHAR(20) NOT NULL,
    leader_uid VARCHAR(16),
    leader_name VARCHAR(50) NOT NULL,
    members VARCHAR(300),
    duty VARCHAR(500),
    sort_order INTEGER DEFAULT 0,
    updated_by INTEGER,
    created_at DATETIME,
    updated_at DATETIME,
    CONSTRAINT uq_subject_leader UNIQUE (school_year, term, grade, subject)
)
"""

DDL_INDEX = ("CREATE INDEX IF NOT EXISTS idx_leader_year_subject "
             "ON subject_leaders (school_year, subject)")

if not os.path.isdir(DATA_DIR):
    print(f'[SKIP] 数据目录不存在: {DATA_DIR}')
    sys.exit(0)

touched = 0
for db_path in sorted(glob.glob(os.path.join(DATA_DIR, '*.db'))):
    try:
        conn = sqlite3.connect(db_path)
        # 只处理教务库（含 teachers 表的库），避免在其它模块库里建无关表
        has_teachers = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='teachers'"
        ).fetchone()
        if not has_teachers:
            conn.close()
            continue
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='subject_leaders'"
        ).fetchone()
        conn.execute(DDL_TABLE)
        conn.execute(DDL_INDEX)
        conn.commit()
        conn.close()
        name = os.path.basename(db_path)
        print(f'[SKIP] {name} subject_leaders 已存在' if exists
              else f'[OK]   {name} 已创建 subject_leaders 表 + 索引')
        touched += 0 if exists else 1
    except Exception as e:  # noqa: BLE001  单库失败不影响其它库
        print(f'[ERR]  {os.path.basename(db_path)}: {e}')

print(f'[DONE] 迁移完成，新建 {touched} 张表')

# StuLink v1.18.2.1 2026-09-24
# Copyright (c) 2026 zkxxzf. Apache License 2.0
