# -*- coding: utf-8 -*-
"""学科组长升级（2026-10-14）：subject_leaders 加 category 列 + 重建唯一约束。

背景
----
原「备课组长」页面升级为「学科组长」：一页两类组长 ——

- **备课组长**：年级 × 学科（高一 / 高二 / 高三 各学科各一名），年级必填；
- **教研组长**：只按学科（不分年级），年级留空＝全校。

因此 subject_leaders 需要：

1. 新增 `category` 列（'备课组长' / '教研组长'），历史数据统一回填为「备课组长」
   （老数据都是年级 × 学科形态；确属全校教研组长的请到页面改类别）；
2. 唯一键从 (school_year, term, grade, subject) 扩到 (…, category)，
   否则同一学科无法同时登记「高一备课组长」与「全校教研组长」。

实现方式
--------
SQLite 不支持 ALTER CONSTRAINT，故走「建新表 → 拷数据 → 换名」：

    subject_leaders          →  subject_leaders_old_20261014
    subject_leaders_new      →  subject_leaders

拷数据用显式列名（不依赖列序），id 原样保留，AUTOINCREMENT 续用不丢号。

- 幂等：列已存在且唯一键已含 category 时整体跳过（由 _mig_log 二次兜底）；
- 表名 / 列名全部来自本文件常量，无外部输入拼接（无注入面）；
- 只处理教务库（含 teachers 表的库），不在其它模块库里建无关表。
"""
import glob
import os
import re
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')

SCRIPT_NAME = os.path.basename(__file__)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# 迁移登记（幂等）——已登记过则整体跳过；登记查询失败不阻塞迁移
try:
    import _mig_log
    if _mig_log.is_applied(SCRIPT_NAME):
        print(f'[SKIP] {SCRIPT_NAME} 已登记为已应用，跳过迁移')
        sys.exit(0)
except Exception as _e:  # noqa: BLE001
    print(f'[WARN] 读取迁移登记失败（继续执行迁移）：{_e}')

DEFAULT_CATEGORY = '备课组长'

# 新表结构（与 app/models/academic.py::SubjectLeader 一致）
DDL_NEW = """
CREATE TABLE subject_leaders_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    school_year VARCHAR(20) NOT NULL,
    term VARCHAR(10) DEFAULT '',
    grade VARCHAR(10) DEFAULT '',
    subject VARCHAR(20) NOT NULL,
    category VARCHAR(10) NOT NULL DEFAULT '备课组长',
    leader_uid VARCHAR(16),
    leader_name VARCHAR(50) NOT NULL,
    members VARCHAR(300),
    duty VARCHAR(500),
    sort_order INTEGER DEFAULT 0,
    updated_by INTEGER,
    created_at DATETIME,
    updated_at DATETIME,
    CONSTRAINT uq_subject_leader UNIQUE (school_year, term, grade, subject, category)
)
"""

COPY_SQL = """
INSERT INTO subject_leaders_new
    (id, school_year, term, grade, subject, category, leader_uid, leader_name,
     members, duty, sort_order, updated_by, created_at, updated_at)
SELECT id, school_year, term, grade, subject,
       COALESCE(NULLIF(TRIM(category), ''), '备课组长'),
       leader_uid, leader_name, members, duty, sort_order,
       updated_by, created_at, updated_at
FROM subject_leaders
"""

DDL_INDEX = ("CREATE INDEX IF NOT EXISTS idx_leader_year_subject "
             "ON subject_leaders (school_year, subject)")

if not os.path.isdir(DATA_DIR):
    print(f'[SKIP] 数据目录不存在: {DATA_DIR}')
    sys.exit(0)


def _columns(conn, table):
    return {r[1] for r in conn.execute(f'PRAGMA table_info({table})').fetchall()}


def _has_category_in_unique(conn, table):
    """唯一键里是否已含 category（只看 UNIQUE(...) 的列清单）。

    注意：ALTER TABLE ADD COLUMN 会把列名追加进 sqlite_master.sql，
    所以不能只判断 'category' 是否出现在建表 SQL 里（那样会误判为已迁移）。
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not row or not row[0]:
        return False
    for m in re.finditer(r'UNIQUE\s*\(([^)]*)\)', row[0], re.I):
        if 'category' in m.group(1):
            return True
    return False


touched = skipped = 0
for db_path in sorted(glob.glob(os.path.join(DATA_DIR, '*.db'))):
    name = os.path.basename(db_path)
    try:
        conn = sqlite3.connect(db_path)
        has_teachers = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='teachers'"
        ).fetchone()
        if not has_teachers:
            conn.close()
            continue
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='subject_leaders'"
        ).fetchone()
        if not exists:
            # 表还没建（新环境交给 create_all / migrate_subject_leaders.py）
            conn.close()
            print(f'[SKIP] {name} 无 subject_leaders 表（由建表迁移负责）')
            continue

        cols = _columns(conn, 'subject_leaders')
        if 'category' not in cols:
            conn.execute(
                f"ALTER TABLE subject_leaders ADD COLUMN category VARCHAR(10) "
                f"NOT NULL DEFAULT '{DEFAULT_CATEGORY}'"
            )
            conn.commit()
            print(f'[OK]   {name} 已加列 category（默认「{DEFAULT_CATEGORY}」）')

        if _has_category_in_unique(conn, 'subject_leaders'):
            conn.execute("UPDATE subject_leaders SET category=? "
                         "WHERE category IS NULL OR TRIM(category)=''",
                         (DEFAULT_CATEGORY,))
            conn.commit()
            conn.close()
            skipped += 1
            print(f'[SKIP] {name} 唯一键已含 category，仅补齐空值')
            continue

        conn.execute("UPDATE subject_leaders SET category=? "
                     "WHERE category IS NULL OR TRIM(category)=''",
                     (DEFAULT_CATEGORY,))
        conn.execute('DROP TABLE IF EXISTS subject_leaders_old_20261014')
        conn.execute(DDL_NEW)
        conn.execute(COPY_SQL)
        conn.execute('DROP TABLE subject_leaders')
        conn.execute('ALTER TABLE subject_leaders_new RENAME TO subject_leaders')
        conn.execute(DDL_INDEX)
        conn.commit()
        # 老备份表不再需要（数据已完整拷入新表），清掉避免误用
        conn.execute('DROP TABLE IF EXISTS subject_leaders_old_20261014')
        conn.commit()
        n = conn.execute('SELECT COUNT(*) FROM subject_leaders').fetchone()[0]
        conn.close()
        touched += 1
        print(f'[OK]   {name} 唯一键已重建（含 category），保留 {n} 条记录')
    except Exception as e:  # noqa: BLE001  单库失败不影响其它库
        print(f'[ERR]  {name}: {e}')

print(f'[DONE] 迁移完成：重建 {touched} 张表，跳过 {skipped} 张')

# 迁移登记（失败不得阻塞迁移）
try:
    _mig_log.record(SCRIPT_NAME, note=f'subject_leaders 加 category + 重建唯一键（{touched} 张）')
except Exception as _e:  # noqa: BLE001
    print(f'[WARN] 迁移登记失败（不影响迁移结果）：{_e}')

# StuLink v1.19.48.1 2026-10-14
# Copyright (c) 2026 zkxxzf. Apache License 2.0
