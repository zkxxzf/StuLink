# -*- coding: utf-8 -*-
# StuLink v1.19.49.35 2026-10-15
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""组合总分线（2026-10-15）：给 exam_bands 补 selection 列 + 唯一键扩到含 selection。

背景
----
用户口径：物化生 / 物化地 / 物化政 三个组合的**总分**特控线各自按本组合前 50% 划
（系统原有维度只有 方向×学科，总分线一条方向线表达不了「三个组合各一条」）。

本脚本做两件事（都幂等）：
1. 给 `exam_bands` 加 `selection VARCHAR(10) NOT NULL DEFAULT ''`
   （空 = 按方向/全体，原口径；非空 = 该选科组合自己的线，挂在所属方向下）；
2. 唯一键 `uq_band_seq` 从 (exam_id, direction, subject, seq)
   扩为 (exam_id, direction, selection, subject, seq) —— 否则组合总分线与方向总分线
   同 seq 会撞唯一键。SQLite 改唯一键必须重建表（exam_bands 行数小，逐行拷贝，秒级）。

重建后数据原样保留（selection 一律补 ''），行为零变化；
组合线由 scripts/apply_gaokao_bands.py 按需写入。

表结构必须与 `app/models/grades.py::ExamBand` 一致（`scripts/check_columns.py` 逐列比对）。
"""
import os
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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

TABLE = 'exam_bands'
NEW_TABLE = 'exam_bands_new_mig'
NEW_DDL = f"""
CREATE TABLE {NEW_TABLE} (
    id INTEGER NOT NULL PRIMARY KEY,
    exam_id INTEGER NOT NULL,
    direction VARCHAR(4),
    selection VARCHAR(10) NOT NULL,
    subject VARCHAR(10) NOT NULL,
    seq INTEGER NOT NULL,
    name VARCHAR(20) NOT NULL,
    lower_mode VARCHAR(8) NOT NULL,
    lower_value FLOAT NOT NULL,
    CONSTRAINT uq_band_seq UNIQUE (exam_id, direction, selection, subject, seq)
)
"""


def _columns(conn, table):
    try:
        return [r[1] for r in conn.execute(f'PRAGMA table_info({table})').fetchall()]
    except sqlite3.Error:
        return []


def _uq_cols(conn, table):
    """现有唯一约束（名为 uq_band_seq）的列列表；无则 []"""
    try:
        for row in conn.execute(f'PRAGMA index_list({table})').fetchall():
            # row: seq, name, unique, origin, partial
            if row[2] and row[1] == 'uq_band_seq':
                cols = [r[2] for r in
                        conn.execute(f'PRAGMA index_info({row[1]})').fetchall()]
                return cols
    except sqlite3.Error:
        pass
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
            print(f'[WARN] 未发现 {TABLE} 表（库未初始化属正常，建表时自带 selection 列）')
            # 2026-10-15 第六轮审计 S1-3：早退分支也要登记，否则 migration_status
            # 会把这条永久显示为「待执行」（新装库噪音）
            if _mig_log is not None:
                _mig_log.record(SCRIPT_NAME, note='表不存在（新装库自带 selection 列），跳过')
            return
        uq = _uq_cols(conn, TABLE)
        if 'selection' in cols and 'selection' in uq:
            print(f'{TABLE}.selection 与新唯一键均已存在，无需迁移')
            if _mig_log is not None:
                _mig_log.record(SCRIPT_NAME, note='已存在 selection 列与唯一键，跳过')
            return
        if 'selection' in cols and uq and 'selection' not in uq:
            # 只差唯一键（理论不会出现）：同样走重建
            print(f'{TABLE}.selection 已存在但唯一键仍是旧结构，重建表补键')
        base_cols = [c for c in cols if c != 'selection']
        missing = {'exam_id', 'direction', 'subject', 'seq', 'name',
                   'lower_mode', 'lower_value'} - set(base_cols)
        if missing:
            print(f'[ABORT] {TABLE} 缺少预期列 {sorted(missing)}，表结构与预期不符，请人工核对')
            sys.exit(1)
        conn.execute(NEW_DDL)
        col_list = ', '.join(base_cols)
        conn.execute(
            f'INSERT INTO {NEW_TABLE} (id, exam_id, direction, selection, subject, '
            f'seq, name, lower_mode, lower_value) '
            f'SELECT id, exam_id, direction, \'\', subject, seq, name, lower_mode, '
            f'lower_value FROM {TABLE}')
        conn.execute(f'DROP TABLE {TABLE}')
        conn.execute(f'ALTER TABLE {NEW_TABLE} RENAME TO {TABLE}')
        conn.commit()
        n = conn.execute(f'SELECT COUNT(*) FROM {TABLE}').fetchone()[0]
        print(f'{TABLE} 重建完成：补 selection 列 + 唯一键含 selection，原样保留 {n} 行')
    finally:
        conn.close()
    if _mig_log is not None:
        _mig_log.record(SCRIPT_NAME, note='组合总分线：exam_bands.selection 列 + 唯一键扩维')
    print('完成。请重启应用。')


if __name__ == '__main__':
    main()
