# -*- coding: utf-8 -*-
# StuLink v1.19.49.42 2026-10-20
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""校舍资产年度参数表（2026-10-20）：建 edu_stat_assets。

背景
----
教育事业统计基表里，校舍（教基5170）、资产与办学条件（教基5176）、达标项（教基1102）
在系统里没有任何对应字段 —— 这些是**学校层面的年度参数**，不属于学生/教师业务数据。

做法：一学年一行（`school_year` 唯一），在「系统管理 → 校舍资产」录入，逐年复用。
数据落 system.db。表结构必须与 `app/models/edu_stat.py::EduStatAsset` 一致。

幂等：表已存在则跳过；改库前备份 system.db；执行后登记 migration_log。

用法：
    py -3.12 scripts/migrate/migrate_edu_stat_asset_20261020.py          # 预览
    py -3.12 scripts/migrate/migrate_edu_stat_asset_20261020.py --apply  # 执行
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
DB = os.path.join(BASE_DIR, 'data', 'system.db')
TABLE = 'edu_stat_assets'
SCRIPT_NAME = os.path.basename(__file__)

try:
    from _db_backup import backup_db
except Exception as _e:  # noqa: BLE001
    backup_db = None
    print(f'[WARN] 备份模块不可用（跳过备份，谨慎执行）：{_e}')
try:
    import _mig_log
except Exception as _e:  # noqa: BLE001
    _mig_log = None
    print(f'[WARN] 迁移登记模块不可用（照常执行，仅不登记）：{_e}')

# 面积类 FLOAT，个数类 INTEGER，达标项 BOOLEAN（None=未填）
_FLOAT_COLS = [
    'building_total', 'building_c_danger', 'building_d_danger', 'building_rented',
    'room_teaching', 'room_classroom', 'room_special', 'room_lab',
    'room_special_other', 'room_public', 'room_library', 'room_sports_indoor',
    'room_psych', 'room_public_other', 'room_office', 'room_teacher_office',
    'room_office_other', 'room_living', 'room_staff_dorm',
    'room_teacher_turnover', 'room_student_dorm', 'room_canteen', 'room_toilet',
    'room_living_other', 'room_other', 'room_lent_out',
    'land_area', 'green_area', 'sports_field_area',
    'asset_fixed', 'asset_teaching_equipment',
]
_INT_COLS = [
    'football_11', 'football_7', 'football_5', 'books', 'terminal_total',
    'terminal_teacher', 'terminal_student', 'classroom_total',
    'classroom_multimedia',
]
_BOOL_COLS = [
    'ok_sports_venue', 'ok_sports_equipment', 'ok_music_equipment',
    'ok_art_equipment', 'ok_science_lab',
]

DDL = """CREATE TABLE IF NOT EXISTS {t} (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    school_year VARCHAR(20) NOT NULL UNIQUE,
{cols}
    note VARCHAR(200),
    created_at DATETIME,
    updated_at DATETIME,
    updated_by INTEGER
)"""


def _ddl():
    cols = ['    %s FLOAT' % c for c in _FLOAT_COLS]
    cols += ['    %s INTEGER' % c for c in _INT_COLS]
    cols += ['    %s BOOLEAN' % c for c in _BOOL_COLS]
    return DDL.format(t=TABLE, cols=',\n'.join(cols) + ',')


def main():
    apply = '--apply' in sys.argv
    if not os.path.exists(DB):
        print(f'库不存在：{DB}')
        return 1
    conn = sqlite3.connect(DB, timeout=15)
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (TABLE,)).fetchone()
        print(f'== {TABLE}：{"已存在" if exists else "不存在"} ==')
        if exists:
            print('  无需改动。')
            return 0
        print('  将新建表（%d 个参数字段 + 学年/备注/审计列）'
              % (len(_FLOAT_COLS) + len(_INT_COLS) + len(_BOOL_COLS)))
        if not apply:
            print('\n预览模式，未改动。执行：--apply')
            return 0

        if backup_db is not None:
            pairs = backup_db(DB, tag='before_edu_stat_assets')
            if not pairs or not pairs[0][1]:
                print('中止：备份失败，未建表。')
                return 2
            print(f'  已备份：{os.path.basename(pairs[0][1])}')

        conn.execute(_ddl())
        conn.commit()
        print(f'\n完成：已创建 {TABLE}')
        if _mig_log is not None:
            _mig_log.record(SCRIPT_NAME,
                            note='新建 edu_stat_assets（校舍/资产/达标项年度参数）')
        return 0
    finally:
        conn.close()


if __name__ == '__main__':
    sys.exit(main())
