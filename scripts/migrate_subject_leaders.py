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

# 2026-10-14 第三轮审计（CONS3-03）：本脚本原先建的是**旧结构**（含已下线的
# members / duty / sort_order 三列、唯一键 4 列、无 category）。执行器里它排在
# `migrate_subject_leader_category_20261014.py`（重建表）**之后**，而重建脚本对
# 「无表库」是 SKIP → 缺表的存量库上线后会停在旧结构（且 unique 键也错）。
# 现在：①本脚本直接建**目标结构**；②执行器把本脚本移到重建脚本**之前**
# （先建表 → 再重建/规范化），两条腿互相兜底。
# 结构与 app/models/academic.py::SubjectLeader 一致（check_columns/check_indexes 逐列比对）。
DDL_TABLE = """
CREATE TABLE IF NOT EXISTS subject_leaders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    school_year VARCHAR(20) NOT NULL,
    term VARCHAR(10) DEFAULT '',
    grade VARCHAR(10) DEFAULT '',
    subject VARCHAR(20) NOT NULL,
    category VARCHAR(10) NOT NULL DEFAULT '备课组长',
    leader_uid VARCHAR(16),
    leader_name VARCHAR(50) NOT NULL,
    updated_by INTEGER,
    created_at DATETIME,
    updated_at DATETIME,
    CONSTRAINT uq_subject_leader UNIQUE (school_year, term, grade, subject, category)
)
"""

# 模型声明了 3 个索引（idx_leader_year_subject 复合 + school_year / subject 两个单列），
# 新装库必须一并建出，否则索引自检报「模型声明、现网缺失」。
DDL_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_leader_year_subject "
    "ON subject_leaders (school_year, subject)",
    "CREATE INDEX IF NOT EXISTS ix_subject_leaders_school_year "
    "ON subject_leaders (school_year)",
    "CREATE INDEX IF NOT EXISTS ix_subject_leaders_subject "
    "ON subject_leaders (subject)",
)

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
        for ddl in DDL_INDEXES:
            conn.execute(ddl)
        conn.commit()
        conn.close()
        name = os.path.basename(db_path)
        print(f'[SKIP] {name} subject_leaders 已存在' if exists
              else f'[OK]   {name} 已创建 subject_leaders 表 + 索引')
        touched += 0 if exists else 1
    except Exception as e:  # noqa: BLE001  单库失败不影响其它库
        print(f'[ERR]  {os.path.basename(db_path)}: {e}')

print(f'[DONE] 迁移完成，新建 {touched} 张表')

# 迁移登记（失败不得阻塞迁移）
try:
    _mig_log.record(SCRIPT_NAME, note=f'新建 {touched} 张 subject_leaders 表')
except Exception as _e:  # noqa: BLE001
    print(f'[WARN] 迁移登记失败（不影响迁移结果）：{_e}')

# StuLink v1.18.2.1 2026-09-24
# Copyright (c) 2026 zkxxzf. Apache License 2.0
