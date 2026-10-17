# -*- coding: utf-8 -*-
# StuLink v1.19.49.30 2026-10-14
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""学科组长方案（2026-10-14）：建 subject_leader_plans + 为已有学年学期补方案记录。

背景
----
用户口径：任课映射与学科组长都要"做成方案"——每学期会变、要有记录、可新建方案，
右上角「历史记录」里可以使用历史方案 / 使用其他方案 / 新建方案。

口径
----
学科组长的方案 ＝ 一个「学年 × 学期」（与 ``subject_leaders`` 的唯一键
``学年 × 学期 × 年级 × 学科 × 类别`` 同一坐标）：

- ``subject_leader_plans`` 只登记方案元信息（名称 / 说明 / 是否当前学期 / 创建人）；
- 组长数据本身仍在 ``subject_leaders`` 里按学年学期并存（课表「备课组 / 教研组课表」的
  徽章按课表自己的学年学期回查，不受"当前方案"影响）；
- 删除方案会连同该学期登记一起删（服务层行为，页面二次确认）。

本脚本做两件事（都幂等）：

1. 在含 ``subject_leaders`` 的库（academic.db）里建 ``subject_leader_plans`` 表与索引；
2. 为 ``subject_leaders`` 里已存在的每个 (学年, 学期) 补一条方案记录
   （名称＝「2026-2027学年 第一学期」），并把最新的一个标为当前学期方案。
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
try:
    import _mig_log
except Exception as _e:  # noqa: BLE001
    _mig_log = None
    print(f'[WARN] 迁移登记模块不可用（迁移照常执行，仅不登记）：{_e}')

DDL_PLANS = """
CREATE TABLE IF NOT EXISTS subject_leader_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(80) NOT NULL,
    school_year VARCHAR(20) NOT NULL,
    term VARCHAR(10) DEFAULT '',
    note VARCHAR(200) DEFAULT '',
    is_current BOOLEAN,
    created_by INTEGER,
    created_at DATETIME,
    updated_at DATETIME,
    CONSTRAINT uq_leader_plan_term UNIQUE (school_year, term)
)
"""

DDL_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_subject_leader_plans_is_current "
    "ON subject_leader_plans (is_current)",
)

if not os.path.isdir(DATA_DIR):
    print(f'[SKIP] 数据目录不存在: {DATA_DIR}')
    sys.exit(0)


def _has_table(conn, table):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (table,)).fetchone() is not None


touched = skipped = 0
for db_path in sorted(glob.glob(os.path.join(DATA_DIR, '*.db'))):
    name = os.path.basename(db_path)
    try:
        conn = sqlite3.connect(db_path)
        if not _has_table(conn, 'subject_leaders'):
            conn.close()
            continue                       # 不是教务库
        existed = _has_table(conn, 'subject_leader_plans')
        conn.execute(DDL_PLANS)
        for ddl in DDL_INDEXES:
            conn.execute(ddl)

        combos = [r for r in conn.execute(
            'SELECT DISTINCT school_year, COALESCE(term, "") FROM subject_leaders'
            ' ORDER BY school_year, COALESCE(term, "")').fetchall() if r[0]]
        added = 0
        for school_year, term in combos:
            row = conn.execute(
                'SELECT id FROM subject_leader_plans WHERE school_year=? AND term=?',
                (school_year, term)).fetchone()
            if row:
                continue
            conn.execute(
                "INSERT INTO subject_leader_plans (name, school_year, term, note, is_current,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, 0, datetime('now'),"
                " datetime('now'))",
                (f'{school_year}学年 {term or "整学年"}', school_year, term,
                 '由本次迁移按已有登记自动补建'))
            added += 1

        # 当前学期方案：还没有任何 is_current 时，取最新的一个（学年大 → 学期大）
        has_cur = conn.execute(
            'SELECT 1 FROM subject_leader_plans WHERE is_current = 1').fetchone()
        if not has_cur:
            last = conn.execute(
                'SELECT id FROM subject_leader_plans'
                ' ORDER BY school_year DESC, term DESC LIMIT 1').fetchone()
            if last:
                conn.execute('UPDATE subject_leader_plans SET is_current = 1 WHERE id = ?',
                             (last[0],))
        conn.commit()
        n = conn.execute('SELECT COUNT(*) FROM subject_leader_plans').fetchone()[0]
        n_cur = conn.execute(
            'SELECT COUNT(*) FROM subject_leader_plans WHERE is_current = 1').fetchone()[0]
        conn.close()
        if existed:
            skipped += 1
        else:
            touched += 1
        print(f'[OK]   {name}：表已就绪（方案 {n} 个 / 当前 {n_cur} 个'
              + (f'；本次补建 {added} 个' if added else '') + '）')
    except Exception as e:  # noqa: BLE001
        print(f'[ERR]  {name}: {e}')

print(f'[DONE] 迁移完成：新建 {touched} 库，已存在 {skipped} 库')

if _mig_log is not None:
    try:
        _mig_log.record(SCRIPT_NAME,
                        note=f'学科组长方案建表 + 按已有学年学期补方案（{touched} 建 / {skipped} 跳过）')
    except Exception as _e:  # noqa: BLE001
        print(f'[WARN] 迁移登记失败（不影响迁移结果）：{_e}')
