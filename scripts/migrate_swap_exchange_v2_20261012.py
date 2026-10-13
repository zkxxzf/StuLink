# -*- coding: utf-8 -*-
"""迁移：两人换课「对调内容」双勾选 + 对方日期（2026-10-12）。

给 timetable.db 的 `schedule_swaps` 增加 3 列：
- swap_time         BOOLEAN  是否对调上课时间（星期 + 节次）
- swap_class        BOOLEAN  是否对调上课班级（年级/班/学科/教室）
- partner_swap_date DATE     对方那节课所在日期（临时换课："换课有两个日期"）

并把旧的 exchange_kind 取值回填为新口径（用户反馈「换时段 / 换班上课」重复，
已合并为一个"两人对调"+ 两个勾选）：
    time   → exchange (swap_time=1, swap_class=0)   只换时间
    class  → exchange (swap_time=1, swap_class=1)   整条互换
    cover  → cover    (swap_time=0, swap_class=0)   请人代课，无关对调内容

幂等：列已存在则跳过；回填用受限 UPDATE（exchange_kind 取值枚举），可重复执行。

用法：python scripts/migrate_swap_exchange_v2_20261012.py
"""
import os
import sys

import sqlalchemy as sa

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app                                   # noqa: E402
from app.extensions import db                                # noqa: E402
from scripts._db_backup import backup_db                     # noqa: E402
from scripts._ddl_guard import assert_ident, assert_table    # noqa: E402

TABLE = 'schedule_swaps'
BIND = 'timetable'
COLUMNS = [
    ('swap_time', 'BOOLEAN DEFAULT 1'),
    ('swap_class', 'BOOLEAN DEFAULT 1'),
    ('partner_swap_date', 'DATE'),
]
# (旧 kind, 新 kind, swap_time, swap_class)
BACKFILL = [
    ('time', 'exchange', 1, 0),
    ('class', 'exchange', 1, 1),
    ('cover', 'cover', 0, 0),
]


def main():
    app = create_app()
    with app.app_context():
        from flask import current_app
        uri = current_app.config['SQLALCHEMY_BINDS'][BIND]
        db_path = uri.replace('sqlite:///', '')
        print(f'[{BIND}] 数据库：{db_path}')

        engine = db.engines[BIND]
        insp = sa.inspect(engine)
        if not insp.has_table(TABLE):
            print(f'[SKIP] {TABLE} 表不存在（尚未建过课表，无需迁移）')
            return 0
        assert_table(TABLE)
        cols = {c['name'] for c in insp.get_columns(TABLE)}
        pending = [(n, ddl) for n, ddl in COLUMNS if n not in cols]
        # 2026-10-13 审计修复：本项目约定「改动 .db 前必须先备份」。
        # 本脚本既有加列又有批量回填 UPDATE，风险更高，一律先备份。
        if pending and os.path.exists(db_path):
            backup_db(db_path)          # 保留最近 5 份
            print('[OK] 已备份原库')
        for name, ddl in pending:
            assert_ident(name, '列名')
            with engine.begin() as conn:
                conn.exec_driver_sql(f'ALTER TABLE {TABLE} ADD COLUMN {name} {ddl}')
            print(f'[OK] {TABLE} 已增加 {name} 列')
        if not pending:
            print('[OK] 3 列均已存在，仅执行幂等回填')

        with engine.begin() as conn:
            for old, new, st, sc in BACKFILL:
                r = conn.exec_driver_sql(
                    f'UPDATE {TABLE} SET exchange_kind = ?, swap_time = ?, swap_class = ? '
                    f'WHERE exchange_kind = ?', (new, st, sc, old))
                n = r.rowcount or 0
                print(f'[OK] 回填 {old} → {new}(time={st}, class={sc})：{n} 行')
        print(f'[OK] 迁移完成：{TABLE}')
        return 0


if __name__ == '__main__':
    sys.exit(main())
