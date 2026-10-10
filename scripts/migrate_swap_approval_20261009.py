# -*- coding: utf-8 -*-
"""迁移：调课分级审批 + 调休（2026-10-09）。

给 timetable.db 的 `schedule_swaps` 增加 4 列：
- source_date      DATE      原课所在日期（跨天/调休：这天按 source_weekday 的课表上课）
- source_weekday   INTEGER   原课那天上的是"周几的课"
- approval_step    INTEGER   分级审批：当前停在第几级（0 起）
- approvals_json   TEXT      审批轨迹 [{step, role, role_text, user_id, user_name,
                                        action, action_text, note, at}]

幂等：列已存在则跳过；既有数据不动（approval_step 默认 0 = 从第一级开始）。

用法：python scripts/migrate_swap_approval_20261009.py
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
    ('source_date', 'DATE'),
    ('source_weekday', 'INTEGER'),
    ('approval_step', 'INTEGER DEFAULT 0'),
    ('approvals_json', 'TEXT'),
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
        print(f'[OK] 迁移完成：{TABLE}')
        return 0


if __name__ == '__main__':
    sys.exit(main())
