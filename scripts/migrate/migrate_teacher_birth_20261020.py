# -*- coding: utf-8 -*-
# StuLink v1.19.49.44 2026-10-20
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""教师出生日期（2026-10-20）：给 teachers 补 birth_date 列。

背景
----
用户口径（隐私）：「为保护教师隐私，能不能只填写教师生日？能否满足教育统计需求？」
结论：**可以**。教育统计只需教师的**年龄分档**（教基4153），不需要身份证号；
身份证里能推出的只有年龄与性别，而性别已单独可填。因此：
  - `birth_date` 成为年龄的主口径（只填生日即可）；
  - `id_card_enc` 降为选填（仅人事档案需要时录入）；
  - 两者都有时按生日算，都没有则该教师不计入分年龄表（页面提示补录）。

幂等：列已存在则跳过；改库前备份 academic.db；执行后登记 migration_log。
表结构必须与 `app/models/academic.py::Teacher` 一致。

用法：
    py -3.12 scripts/migrate/migrate_teacher_birth_20261020.py          # 预览
    py -3.12 scripts/migrate/migrate_teacher_birth_20261020.py --apply  # 执行
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
DB = os.path.join(BASE_DIR, 'data', 'academic.db')
TABLE = 'teachers'
COLUMN = 'birth_date'
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


def main():
    apply = '--apply' in sys.argv
    if not os.path.exists(DB):
        print(f'库不存在：{DB}')
        return 1
    conn = sqlite3.connect(DB, timeout=15)
    try:
        have = {r[1] for r in conn.execute(f'PRAGMA table_info({TABLE})')}
        print(f'== {TABLE}：{"已有" if COLUMN in have else "缺"} {COLUMN} ==')
        if COLUMN in have:
            print('  结构已是最新，无需改动。')
            return 0
        if not apply:
            print('\n预览模式，未改动。执行：--apply')
            return 0

        if backup_db is not None:
            pairs = backup_db(DB, tag='before_teacher_birth')
            if not pairs or not pairs[0][1]:
                print('中止：备份失败，未执行 DDL。')
                return 2
            print(f'  已备份：{os.path.basename(pairs[0][1])}')

        conn.execute(f'ALTER TABLE {TABLE} ADD COLUMN {COLUMN} DATE')
        conn.commit()
        print(f'\n完成：已新增 {COLUMN}')
        if _mig_log is not None:
            _mig_log.record(SCRIPT_NAME,
                            note='teachers.birth_date（教育统计年龄主口径，身份证改选填）')
        return 0
    finally:
        conn.close()


if __name__ == '__main__':
    sys.exit(main())
