# -*- coding: utf-8 -*-
# StuLink v1.19.49.39 2026-10-20
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""考试「年级学期」标签（2026-10-20）：给 exams 补 grade_semester 列。

背景
----
用户口径：历次考试名称里手写的「（高三上）」要独立成标签，创建考试时可选、
不选则按日期自动推算。此前这个信息只存在于 name 文本里（唯一的解析点是
tab_service._short_exam_name），既不能筛选也不保证一致。

本脚本只加一列（幂等）：
    exams.grade_semester VARCHAR(10) NOT NULL DEFAULT ''
    ''    = 自动（按 grade + exam_date 推算，见 app/models/grades.py::exam_semester）
    其它  = 手工指定，取值只能是 GRADE_SEMESTERS（高一上…高三下）

**不回填**：历史考试的标签由推算得出（与名称末尾括号逐条核对一致，本地 32 场
全中），回填反而会把「跟随日期自动」固化成死值 —— 之后改考试日期，标签就对不上
名称了。需要固定的场次在详情页「修改考试信息」里手工选即可。

口径（app/models/grades.py 是唯一维护点）
----------------------------------------
- 学年以 8 月 1 日为界：2026 级在 2026-08-01 ~ 2027-07-31 都是高一；
- 学期以春节为界：春节（正月初一）前都是上学期，春节起算下学期。

表结构必须与 `app/models/grades.py::Exam` 一致（`scripts/check_columns.py` 逐列比对）。
"""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_SCRIPTS = _os.path.dirname(_HERE)
_ROOT = _os.path.dirname(_SCRIPTS)
for _p in (_ROOT, _SCRIPTS):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import os
import sqlite3
import sys

BASE_DIR = _ROOT
DB = os.path.join(BASE_DIR, 'data', 'grades.db')

SCRIPT_NAME = os.path.basename(__file__)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _db_backup import backup_db
except Exception as _e:  # noqa: BLE001
    backup_db = None
    print(f'[WARN] 备份模块不可用（跳过备份，谨慎执行）：{_e}')
try:
    import _mig_log
except Exception as _e:  # noqa: BLE001
    _mig_log = None
    print(f'[WARN] 迁移登记模块不可用（迁移照常执行，仅不登记）：{_e}')

TABLE = 'exams'
COLUMN = 'grade_semester'
DDL = f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} VARCHAR(10) NOT NULL DEFAULT ''"


def _columns(conn, table):
    """返回表的列名列表；表不存在返回 []"""
    try:
        return [r[1] for r in conn.execute(f'PRAGMA table_info({table})').fetchall()]
    except sqlite3.Error:
        return []


def main():
    if not os.path.exists(DB):
        print(f'grades.db 不存在，跳过：{DB}')
        return
    if _mig_log is not None and _mig_log.is_applied(SCRIPT_NAME):
        print(f'[SKIP] {SCRIPT_NAME} 已应用过，跳过迁移')
        return
    if backup_db is not None:
        print('备份 ->', backup_db(DB))
    conn = sqlite3.connect(DB)
    try:
        cols = _columns(conn, TABLE)
        if not cols:
            print(f'[WARN] 未发现 {TABLE} 表（库未初始化属正常，建表时自带该列）')
            if _mig_log is not None:
                _mig_log.record(SCRIPT_NAME, note=f'表不存在（新装库自带 {COLUMN}），跳过')
            return
        if COLUMN in cols:
            print(f'{TABLE}.{COLUMN} 已存在，无需迁移')
            if _mig_log is not None:
                _mig_log.record(SCRIPT_NAME, note=f'{COLUMN} 已存在，跳过')
            return
        conn.execute(DDL)
        conn.commit()
        n = conn.execute(f'SELECT COUNT(*) FROM {TABLE}').fetchone()[0]
        print(f'{TABLE} 补列完成：{COLUMN}'
              f'（{n} 行留空 = 按日期自动推算年级学期）')
    finally:
        conn.close()
    if _mig_log is not None:
        _mig_log.record(SCRIPT_NAME,
                        note='考试年级学期标签：exams.grade_semester（空=自动推算）')
    print('完成。请重启应用。')


if __name__ == '__main__':
    main()
