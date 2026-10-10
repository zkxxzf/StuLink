# -*- coding: utf-8 -*-
"""旧版课表下线（2026-10-10）：删除 academic.db 的 timetables / timetable_entries 两张旧表。

背景：课表体系已统一到 timetable.db（term_schedules / schedule_entries / …）；
旧课表功能（查看 / Excel 导入 / 教室课表）已从代码中整模块移除，这里清理残留表结构。

- 幂等：`DROP TABLE IF EXISTS`，可重复执行；
- 改库前自动备份 academic.db（项目约定，见 scripts/_db_backup.py）；
- 回滚：用脚本打印的备份文件覆盖 data/academic.db 即可（备份名含时间戳）。

用法：python scripts/migrate_drop_legacy_timetable_20261010.py
"""
import os
import sqlite3
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _db_backup import backup_db      # noqa: E402
from _ddl_guard import assert_table   # noqa: E402

DB_PATH = os.path.join(BASE, 'data', 'academic.db')
# 先删明细表再删档案表（顺序不影响，但保持语义清晰）
TABLES = ('timetable_entries', 'timetables')


def main():
    if not os.path.exists(DB_PATH):
        print(f'[SKIP] 库文件不存在：{DB_PATH}')
        return 0

    backup_db(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    try:
        for name in TABLES:
            table = assert_table(name)      # 白名单校验（纵深防御，见 _ddl_guard）
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,)).fetchone()
            if not exists:
                print(f'[SKIP] 表不存在：{table}')
                continue
            rows = conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
            conn.execute(f'DROP TABLE {table}')
            print(f'[OK] 已删除表 {table}（原 {rows} 行）')
        conn.commit()
    finally:
        conn.close()

    print('[完成] 旧课表两张表已清理；如需回滚，用上面的备份文件覆盖 data/academic.db')
    return 0


if __name__ == '__main__':
    sys.exit(main())
