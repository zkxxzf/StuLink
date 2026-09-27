# -*- coding: utf-8 -*-
"""迁移：给 schedule_entries 增加 teaching_class 列（走班教学班）。

新高考 3+1+2 下，选考科目按教学班走班：同一个行政班的学生会去不同的教学班上课。
本列为空＝行政班课（既有数据全部如此，行为不变）；非空＝走班课。

- 幂等：列已存在则跳过；
- 跨库：扫 data/*.db，只处理含 schedule_entries 表的库（timetable.db）；
- 只加列，不写数据、不改既有行；
- DDL 标识符走 scripts/_ddl_guard.py 白名单校验（清单 R-11）。

用法：python scripts/migrate_teaching_class.py
"""
import glob
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts._ddl_guard import assert_ident, assert_table  # noqa: E402

TABLE = 'schedule_entries'
COLUMN = 'teaching_class'
DDL = 'VARCHAR(30)'


def db_files():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return sorted(glob.glob(os.path.join(root, 'data', '*.db')))


def columns_of(con, table):
    try:
        return {r[1] for r in con.execute(f'PRAGMA table_info({table})')}
    except sqlite3.Error:
        return set()


def main():
    assert_ident(COLUMN, '列名')
    assert_table(TABLE)

    touched = []
    for path in db_files():
        try:
            con = sqlite3.connect(path)
            cols = columns_of(con, TABLE)
            if not cols:            # 不是课表库
                con.close()
                continue
            if COLUMN in cols:      # 已迁移
                con.close()
                continue
            con.execute(f'ALTER TABLE {TABLE} ADD COLUMN {COLUMN} {DDL}')
            con.commit()
            touched.append(os.path.basename(path))
            con.close()
        except Exception as exc:  # noqa: BLE001  单库失败不影响其它库
            print(f'[WARN] {os.path.basename(path)}: {exc}')

    if touched:
        print(f'[OK] 已为 {TABLE} 增加 {COLUMN} 列：{", ".join(touched)}')
    else:
        print('[OK] 无需迁移（列已存在或没有课表库）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
