# -*- coding: utf-8 -*-
# StuLink v1.19.49.30 2026-10-14
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""任课映射方案（2026-10-14）：建 teacher_link_plans / teacher_link_plan_rows + 收拢现有映射。

背景
----
用户口径：每学期任课安排都会变（分班 / 换老师），要有记录 —— 页面右上角「历史记录」
可以①使用历史方案 ②使用其他方案 ③新建方案。

实现口径（关键，勿改）
--------------------
``teacher_subject_links`` **保持"当前生效映射"语义不变**（成绩 / 教务 / 考务 / 工作台 /
MCP 等读取点零改动）；「方案」是它的留档：

- ``teacher_link_plans``      方案元信息（名称 / 学年学期 / 说明 / 是否当前生效）；
- ``teacher_link_plan_rows``  方案明细（方案 × 年级 × 班级 × 科目 → 教师账号快照）。

本脚本做两件事（都幂等）：

1. 在含 ``teacher_subject_links`` 的库（grades.db）里建上面两张表与索引；
2. 若还没有任何方案，且主表已有有效映射 → 建「初始方案」并把现有映射灌成它的快照
   （``is_current=1``），这样页面第一次打开就落在"当前方案"上，不会空转。

表结构必须与 ``app/models/grades.py::TeacherLinkPlan / TeacherLinkPlanRow`` 一致
（``tests/check_columns.py`` / ``check_indexes.py`` 会逐列逐索引比对）。
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
CREATE TABLE IF NOT EXISTS teacher_link_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(80) NOT NULL,
    school_year VARCHAR(20),
    term VARCHAR(10),
    note VARCHAR(200),
    is_current BOOLEAN,
    created_by INTEGER,
    created_at DATETIME,
    updated_at DATETIME
)
"""

DDL_ROWS = """
CREATE TABLE IF NOT EXISTS teacher_link_plan_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL,
    grade VARCHAR(10) NOT NULL,
    class_name VARCHAR(10) NOT NULL,
    subject VARCHAR(10) NOT NULL,
    user_id INTEGER NOT NULL,
    CONSTRAINT uq_plan_link_cell UNIQUE (plan_id, grade, class_name, subject)
)
"""

DDL_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_teacher_link_plans_is_current "
    "ON teacher_link_plans (is_current)",
    "CREATE INDEX IF NOT EXISTS ix_teacher_link_plan_rows_plan_id "
    "ON teacher_link_plan_rows (plan_id)",
)

INIT_PLAN_NAME = '初始方案'

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
        if not _has_table(conn, 'teacher_subject_links'):
            conn.close()
            continue                       # 不是成绩库
        existed = _has_table(conn, 'teacher_link_plans')
        conn.execute(DDL_PLANS)
        conn.execute(DDL_ROWS)
        for ddl in DDL_INDEXES:
            conn.execute(ddl)

        # 初始方案：仅在「一个方案都没有」且主表有有效映射时创建
        n_plans = conn.execute('SELECT COUNT(*) FROM teacher_link_plans').fetchone()[0]
        n_links = conn.execute(
            'SELECT COUNT(*) FROM teacher_subject_links WHERE active = 1').fetchone()[0]
        boot = 0
        if n_plans == 0 and n_links:
            cur = conn.execute(
                "INSERT INTO teacher_link_plans (name, school_year, term, note, is_current,"
                " created_at, updated_at) VALUES (?, '', '', ?, 1, datetime('now'),"
                " datetime('now'))",
                (INIT_PLAN_NAME, '由本次迁移自动创建：收拢迁移时的全部有效任课映射'))
            plan_id = cur.lastrowid
            conn.execute(
                "INSERT INTO teacher_link_plan_rows (plan_id, grade, class_name, subject,"
                " user_id) SELECT ?, grade, class_name, subject, user_id"
                " FROM teacher_subject_links WHERE active = 1", (plan_id,))
            boot = n_links
        conn.commit()
        n_rows = conn.execute('SELECT COUNT(*) FROM teacher_link_plan_rows').fetchone()[0]
        conn.close()
        if existed:
            skipped += 1
        else:
            touched += 1
        print(f'[OK]   {name}：表已就绪（方案 {n_plans if not boot else n_plans + 1} 个 /'
              f' 明细 {n_rows} 行）' + (f'；初始方案收拢 {boot} 条映射' if boot else ''))
    except Exception as e:  # noqa: BLE001  单库失败不影响其它库
        print(f'[ERR]  {name}: {e}')

print(f'[DONE] 迁移完成：新建 {touched} 库，已存在 {skipped} 库')

if _mig_log is not None:
    try:
        _mig_log.record(SCRIPT_NAME,
                        note=f'任课映射方案建表 + 初始方案收拢（{touched} 建 / {skipped} 跳过）')
    except Exception as _e:  # noqa: BLE001
        print(f'[WARN] 迁移登记失败（不影响迁移结果）：{_e}')
