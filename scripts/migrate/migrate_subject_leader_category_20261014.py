# -*- coding: utf-8 -*-
"""学科组长升级（2026-10-14）：subject_leaders 加 category 列 + 重建唯一约束 + 删职责列。

背景
----
原「备课组长」页面升级为「学科组长」：一页两类组长 ——

- **备课组长**：年级 × 学科（高一 / 高二 / 高三 各学科各一名），年级必填；
- **教研组长**：只按学科（不分年级），年级留空＝全校。

因此 subject_leaders 需要：

1. 新增 `category` 列（'备课组长' / '教研组长'），历史数据统一回填为「备课组长」
   （老数据都是年级 × 学科形态；确属全校教研组长的请到页面改类别）；
2. 唯一键从 (school_year, term, grade, subject) 扩到 (…, category)，
   否则同一学科无法同时登记「高一备课组长」与「全校教研组长」；
3. **删除职责 / 组范围三列**（`members` / `duty` / `sort_order`）：该功能（明细页签 +
   编辑弹窗 + 职责文本）2026-10-14 用户确认从未使用，已整功能下线，页面只留矩阵维护。

实现方式
--------
SQLite 不支持 ALTER CONSTRAINT / DROP COLUMN 组合，故走「建新表 → 拷数据 → 换名」：

    subject_leaders_new      →  subject_leaders

拷数据用显式列名（不依赖列序），职责三列直接不拷（等于删除），
id 原样保留，AUTOINCREMENT 续用不丢号。

- 幂等：**唯一键已含 category 且职责三列都已不存在**时整体跳过（由 _mig_log 二次兜底）；
- 表名 / 列名全部来自本文件常量，无外部输入拼接（无注入面）；
- 只处理教务库（含 teachers 表的库），不在其它模块库里建无关表。
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
import re
import sqlite3
import sys

BASE_DIR = _ROOT
DATA_DIR = os.path.join(BASE_DIR, 'data')

SCRIPT_NAME = os.path.basename(__file__)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# 迁移登记（只用于事后核对 migration_status.py，不作为早退条件）
#
# 为什么不按登记早退：本脚本先后有两个版本 —— 第一版只加 category 列 + 重建唯一键，
# 第二版（删除职责 / 组范围三列）沿用同一脚本名。若按登记早退，跑过第一版的库
# 会永远跳过「删列」，结构就停在中间态。故幂等只由「结构是否已是目标形态」判定。
try:
    import _mig_log
except Exception as _e:  # noqa: BLE001
    _mig_log = None
    print(f'[WARN] 迁移登记模块不可用（迁移照常执行，仅不登记）：{_e}')

DEFAULT_CATEGORY = '备课组长'

# 已下线的职责 / 组范围列（2026-10-14 整功能删除；重建表时不再拷入）
LEGACY_DUTY_COLUMNS = ('members', 'duty', 'sort_order')

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
    updated_by INTEGER,
    created_at DATETIME,
    updated_at DATETIME,
    CONSTRAINT uq_subject_leader UNIQUE (school_year, term, grade, subject, category)
)
"""

COPY_SQL = """
INSERT INTO subject_leaders_new
    (id, school_year, term, grade, subject, category, leader_uid, leader_name,
     updated_by, created_at, updated_at)
SELECT id, school_year, term, grade, subject,
       COALESCE(NULLIF(TRIM(category), ''), '备课组长'),
       leader_uid, leader_name,
       updated_by, created_at, updated_at
FROM subject_leaders
"""

DDL_INDEX = ("CREATE INDEX IF NOT EXISTS idx_leader_year_subject "
             "ON subject_leaders (school_year, subject)")

# 2026-10-14：模型还声明了两个**单列索引**（app/models/academic.py::SubjectLeader 的
# school_year / subject 均为 index=True → ix_subject_leaders_school_year / _subject）。
# 本脚本重建表时必须一并补建，否则 scripts/check_indexes.py 会以
# 「模型声明、现网缺失」报红（run_all_regression 的索引自检因此失败）。
DDL_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_subject_leaders_school_year "
    "ON subject_leaders (school_year)",
    "CREATE INDEX IF NOT EXISTS ix_subject_leaders_subject "
    "ON subject_leaders (subject)",
)

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


def _is_target_structure(conn, table, cols):
    """是否已是目标结构：唯一键含 category 且职责三列都已不存在。"""
    if not _has_category_in_unique(conn, table):
        return False
    return not (set(LEGACY_DUTY_COLUMNS) & set(cols))


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

        if _is_target_structure(conn, 'subject_leaders', cols):
            conn.execute("UPDATE subject_leaders SET category=? "
                         "WHERE category IS NULL OR TRIM(category)=''",
                         (DEFAULT_CATEGORY,))
            # 幂等补索引：老版本脚本重建表时只建了复合索引，
            # 缺模型声明的两个单列索引（本脚本第二版起不再漏）
            for ddl in (DDL_INDEX,) + DDL_INDEXES:
                conn.execute(ddl)
            conn.commit()
            conn.close()
            skipped += 1
            print(f'[SKIP] {name} 已是目标结构（含 category、无职责列），补齐空值与索引')
            continue

        legacy = sorted(set(LEGACY_DUTY_COLUMNS) & cols)
        if legacy:
            print(f'[INFO] {name} 将删除已下线的职责 / 组范围列：{"、".join(legacy)}')

        conn.execute("UPDATE subject_leaders SET category=? "
                     "WHERE category IS NULL OR TRIM(category)=''",
                     (DEFAULT_CATEGORY,))
        conn.execute('DROP TABLE IF EXISTS subject_leaders_old_20261014')
        conn.execute(DDL_NEW)
        conn.execute(COPY_SQL)
        conn.execute('DROP TABLE subject_leaders')
        conn.execute('ALTER TABLE subject_leaders_new RENAME TO subject_leaders')
        conn.execute(DDL_INDEX)
        for ddl in DDL_INDEXES:
            conn.execute(ddl)
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
if _mig_log is not None:
    try:
        _mig_log.record(SCRIPT_NAME,
                        note=f'subject_leaders 加 category + 重建唯一键 + 删职责列（{touched} 张）')
    except Exception as _e:  # noqa: BLE001
        print(f'[WARN] 迁移登记失败（不影响迁移结果）：{_e}')

# StuLink v1.19.48.1 2026-10-14
# Copyright (c) 2026 zkxxzf. Apache License 2.0
