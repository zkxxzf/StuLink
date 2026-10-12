# -*- coding: utf-8 -*-
"""迁移：调课「无需对方同意确认」选项（2026-10-13，v1.19.47.15）。

给 timetable.db 的 `schedule_swaps` 增加 1 列：
- need_partner_confirm  BOOLEAN  是否需要对方点【同意】

语义（用户口径）：
    False（默认）＝「无需对方同意确认」**默认勾选** —— 教务审批通过即生效，
                   对方只收到一条知会通知，不阻塞流程；
    True              ＝ 取消勾选，走「对方确认 → 教务审批」，未确认前不进待审队列。

历史数据：本列出现前的记录一律按“无需确认”（0）处理，避免旧申请被“待确认”卡住。

幂等：列已存在则跳过；加列前备份库文件（scripts/_db_backup 若存在则走统一入口）。

用法：python scripts/migrate_swap_auto_confirm_20261013.py
"""
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

import sqlalchemy as sa

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app                                   # noqa: E402
from app.extensions import db                                # noqa: E402
from scripts._ddl_guard import assert_ident, assert_table    # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / 'data' / 'timetable.db'

TABLE = 'schedule_swaps'
COLUMN = 'need_partner_confirm'
DDL = 'BOOLEAN DEFAULT 0'
_stamp = datetime.now().strftime('%Y%m%d%H%M%S')


def main():
    app = create_app()
    with app.app_context():
        engine = db.engines['timetable']
        insp = sa.inspect(engine)
        if not insp.has_table(TABLE):
            print(f'[SKIP] {TABLE} 表不存在（尚未建过课表，无需迁移）')
            return 0
        assert_table(TABLE)
        assert_ident(COLUMN, '列名')
        cols = {c['name'] for c in insp.get_columns(TABLE)}
        if COLUMN in cols:
            print(f'[OK] {TABLE}.{COLUMN} 已存在')
        else:
            print(f'[..] 备份 timetable.db -> schedule_swaps.bak-{_stamp}')
            shutil.copy2(DB, DB.with_name(f'schedule_swaps.bak-{_stamp}'))
            with engine.begin() as conn:
                conn.exec_driver_sql(
                    f'ALTER TABLE {TABLE} ADD COLUMN {COLUMN} {DDL}')
            with engine.begin() as conn:      # 旧记录：按“无需确认”处理
                conn.exec_driver_sql(
                    f'UPDATE {TABLE} SET {COLUMN} = 0 WHERE {COLUMN} IS NULL')
            print(f'[OK] 已增加 {TABLE}.{COLUMN}')
        with engine.connect() as conn:
            n = conn.exec_driver_sql(
                f'SELECT COUNT(*) FROM {TABLE} WHERE {COLUMN} = 1').scalar()
        print(f'[OK] schedule_swaps 共 {n} 条记录选择了「需对方确认」')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
