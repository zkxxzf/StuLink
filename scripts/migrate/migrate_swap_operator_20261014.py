# -*- coding: utf-8 -*-
"""迁移：调课申请「代办人」标记（2026-10-14 第四轮审计 SEC-03）。

给 timetable.db 的 `schedule_swaps` 增加 2 列：
- operator_uid   VARCHAR(16)  实际操作人教师编号（本人申请为空）
- operator_name  VARCHAR(50)  实际操作人姓名快照

背景（SEC-03）：教务/领导在「单节总览 → 就地代课/调课」里替原任课教师发起申请时，
申请人记的是**原任课教师**（业务口径），但单据原先看不出是谁操作的 —— 对方收到的
通知看起来像教师本人发的，事后也无从追溯。修复后该入口会把实际操作人落库，
通知文案（`_notify_exchange`）与详情页（`swap_detail.html`）都会标注「由 XX 代办」。

历史数据：本列出现前的记录一律按"本人申请"（两列为 NULL）处理。

幂等：列已存在则跳过；加列前经 scripts/_db_backup.backup_db 备份（保留最近 5 份）。

用法：python scripts/migrate/migrate_swap_operator_20261014.py
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
import sys

import sqlalchemy as sa

sys.path.insert(0, _ROOT)

from app import create_app                                   # noqa: E402
from app.extensions import db                                # noqa: E402
from scripts._db_backup import backup_db                     # noqa: E402
from scripts._ddl_guard import assert_ident, assert_table    # noqa: E402

SCRIPT_NAME = os.path.basename(__file__)
try:
    import _mig_log
except Exception as _e:  # noqa: BLE001
    _mig_log = None
    print(f'[WARN] 迁移登记模块不可用（迁移照常执行，仅不登记）：{_e}')

TABLE = 'schedule_swaps'
BIND = 'timetable'
NEW_COLUMNS = (('operator_uid', 'VARCHAR(16)'),
               ('operator_name', 'VARCHAR(50)'))


def _record():
    if _mig_log:
        _mig_log.record(SCRIPT_NAME,
                        note='SEC-03：schedule_swaps 增加 operator_uid/operator_name（代办标记）')


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
            _record()
            return 0
        assert_table(TABLE)
        cols = {c['name'] for c in insp.get_columns(TABLE)}
        missing = [(n, d) for n, d in NEW_COLUMNS if n not in cols]
        if not missing:
            print(f'[OK] {TABLE} 代办两列已存在')
        else:
            for name, _ddl in missing:
                assert_ident(name, '列名')
            if os.path.exists(db_path):
                backup_db(db_path)
                print('[OK] 已备份原库')
            for name, ddl in missing:
                with engine.begin() as conn:
                    conn.exec_driver_sql(
                        f'ALTER TABLE {TABLE} ADD COLUMN {name} {ddl}')
            print(f'[OK] 已增加 {TABLE} 列：{[n for n, _ in missing]}')
        with engine.connect() as conn:
            n = conn.exec_driver_sql(
                f'SELECT COUNT(*) FROM {TABLE} WHERE operator_uid IS NOT NULL'
            ).scalar()
        print(f'[OK] schedule_swaps 共 {n} 条记录带代办标记')
        _record()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
