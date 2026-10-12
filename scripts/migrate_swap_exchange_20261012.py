# -*- coding: utf-8 -*-
"""迁移：两人换课（2026-10-12，v1.19.47.8）。

给 timetable.db 的 `schedule_swaps` 增加 8 列（全部可空，旧数据不受影响）：
- exchange_kind            VARCHAR(10)  换课类型 time/class/cover（空=普通调课）
- partner_uid              VARCHAR(16)  对方教师编号
- partner_name             VARCHAR(50)  对方教师姓名（快照）
- partner_entry_id         INTEGER      对方课表条目 id（换时段/换班）
- partner_status           VARCHAR(10)  对方确认 pending/agreed/refused
- partner_note             VARCHAR(200)  对方回复备注
- partner_responded_at     DATETIME     对方回复时间
- partner_target_entry_id  INTEGER      对方侧落位条目 id（临时/永久换课都会产生）

幂等：列已存在则跳过；既有数据不动。
用法：python scripts/migrate_swap_exchange_20261012.py
"""
import os
import sys

import sqlalchemy as sa

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app                                   # noqa: E402
from app.extensions import db                                # noqa: E402
from scripts._ddl_guard import assert_ident, assert_table    # noqa: E402

TABLE = 'schedule_swaps'
COLUMNS = [
    ('exchange_kind', 'VARCHAR(10)'),
    ('partner_uid', 'VARCHAR(16)'),
    ('partner_name', 'VARCHAR(50)'),
    ('partner_entry_id', 'INTEGER'),
    ('partner_status', 'VARCHAR(10)'),
    ('partner_note', 'VARCHAR(200)'),
    ('partner_responded_at', 'DATETIME'),
    ('partner_target_entry_id', 'INTEGER'),
]


def main():
    app = create_app()
    with app.app_context():
        engine = db.engines['timetable']
        insp = sa.inspect(engine)
        if not insp.has_table(TABLE):
            print(f'[SKIP] {TABLE} 表不存在（尚未建过课表，无需迁移）')
            return 0
        cols = {c['name'] for c in insp.get_columns(TABLE)}
        assert_table(TABLE)
        for name, ddl in COLUMNS:
            assert_ident(name, '列名')
            if name in cols:
                print(f'[OK] {TABLE}.{name} 已存在')
                continue
            with engine.begin() as conn:
                conn.exec_driver_sql(f'ALTER TABLE {TABLE} ADD COLUMN {name} {ddl}')
            print(f'[OK] {TABLE} 已增加 {name} 列')
        print(f'[OK] 迁移完成：{TABLE}（两人换课 8 列）')
        return 0


if __name__ == '__main__':
    sys.exit(main())
