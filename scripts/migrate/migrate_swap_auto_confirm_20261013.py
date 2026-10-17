# -*- coding: utf-8 -*-
"""迁移：调课「无需对方同意确认」选项（2026-10-13，v1.19.47.15）。

给 timetable.db 的 `schedule_swaps` 增加 1 列：
- need_partner_confirm  BOOLEAN  是否需要对方点【同意】

语义（用户口径）：
    False（默认）＝「无需对方同意确认」**默认勾选** —— 教务审批通过即生效，
                   对方只收到一条知会通知，不阻塞流程；
    True              ＝ 取消勾选，走「对方确认 → 教务审批」，未确认前不进待审队列。

历史数据：本列出现前的记录一律按“无需确认”（0）处理，避免旧申请被“待确认”卡住。

幂等：列已存在则跳过；加列前经 scripts/_db_backup.backup_db 备份（保留最近 5 份）。

用法：python scripts/migrate/migrate_swap_auto_confirm_20261013.py
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

# 2026-10-14 第四轮审计（CONS-02）：补迁移登记（只登记不早退）
SCRIPT_NAME = os.path.basename(__file__)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import _mig_log
except Exception as _e:  # noqa: BLE001
    _mig_log = None
    print(f'[WARN] 迁移登记模块不可用（迁移照常执行，仅不登记）：{_e}')


def _record():
    if _mig_log:
        _mig_log.record(SCRIPT_NAME, note='schedule_swaps 增 need_partner_confirm（无需对方确认）列')


TABLE = 'schedule_swaps'
BIND = 'timetable'
COLUMN = 'need_partner_confirm'
DDL = 'BOOLEAN DEFAULT 0'


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
        assert_ident(COLUMN, '列名')
        cols = {c['name'] for c in insp.get_columns(TABLE)}
        if COLUMN in cols:
            print(f'[OK] {TABLE}.{COLUMN} 已存在')
        else:
            # 2026-10-13 审计修复 #15：改为走统一 backup_db（自动保留最近 5 份），
            # 不再裸 shutil.copy2（原实现不 prune，且与项目约定不一致）。
            if os.path.exists(db_path):
                backup_db(db_path)
                print('[OK] 已备份原库')
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
    _record()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
