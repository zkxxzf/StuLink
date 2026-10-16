# -*- coding: utf-8 -*-
# StuLink v1.19.49.30 2026-10-14
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""考场分组顺序（2026-10-14）：给 exam_room_group_map 补 sort_order 列 + 按现有顺序回填。

背景
----
用户口径：「（考场维护的分组里）加个顺序，这样带着顺序，可以直接复用」——
分组内房间的先后顺序要能调（上移 / 下移），并且考务步骤②「一键带入」时**沿用**这个顺序
生成 01、02… 考场编号。

改之前：组内顺序其实等于 `exam_room_group_map.id`（= 加入分组的先后），页面上没得调，
「加入 →」时的插入顺序还会随着 set_groups 的「整条删了重插」漂移 —— 复用不可控。

本脚本做两件事（都幂等）：
1. 给 `exam_room_group_map` 加 `sort_order INTEGER DEFAULT 0`（列已存在则跳过）；
2. 组内按**房间位置**升序**回填** 10、20、30…（= 迁移前页面上的实际显示顺序：右列一直
   是按 `affair_room_libs.location` 排的，行为零变化；之后页面上的上移 / 下移会按
   「重排为 10、20、30…」重写，永远无重复值）。

表结构必须与 `app/models/grades.py::ExamRoomGroupMap` 一致
（`scripts/check_columns.py` 会逐列比对）。
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

TABLE = 'exam_room_group_map'
DDL_COL = f'ALTER TABLE {TABLE} ADD COLUMN sort_order INTEGER DEFAULT 0'
STEP = 10          # 组内顺序步进（方便以后中间插值；页面重排也按 10、20、30…）


def _columns(conn, table):
    try:
        return {r[1] for r in conn.execute(f'PRAGMA table_info({table})').fetchall()}
    except sqlite3.Error:
        return set()


def _backfill(conn):
    """组内按**房间位置**回填 10、20、30…（保住迁移前的显示顺序）；
    已排过序的组不动（防重跑打乱用户顺序）"""
    touched_groups = touched_rows = 0
    gids = [r[0] for r in conn.execute(
        f'SELECT DISTINCT group_id FROM {TABLE}').fetchall()]
    for gid in gids:
        rows = conn.execute(
            f'SELECT m.id, m.sort_order FROM {TABLE} m'
            f' LEFT JOIN affair_room_libs l ON l.id = m.lib_id'
            f' WHERE m.group_id=? ORDER BY l.location, m.id', (gid,)).fetchall()
        if not rows or not all((r[1] or 0) == 0 for r in rows):
            continue
        for i, (rid, _so) in enumerate(rows, start=1):
            conn.execute(f'UPDATE {TABLE} SET sort_order=? WHERE id=?', (i * STEP, rid))
        touched_groups += 1
        touched_rows += len(rows)
    return touched_groups, touched_rows


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
            print(f'[WARN] 未发现 {TABLE} 表（若库未初始化属正常，建表时会带 sort_order 列）')
        elif 'sort_order' not in cols:
            conn.execute(DDL_COL)
            print(f'{TABLE}.sort_order 已补列（DEFAULT 0）')
        else:
            print(f'{TABLE}.sort_order 已存在，跳过补列')
        g, r = _backfill(conn)
        print(f'顺序回填：{g} 个分组 / {r} 行按现有（id）顺序写成 10、20、30…')
        conn.commit()
    finally:
        conn.close()
    if _mig_log is not None:
        _mig_log.record(SCRIPT_NAME, note='考场分组顺序：exam_room_group_map.sort_order + 回填')
    print('完成。请重启应用。')


if __name__ == '__main__':
    main()
