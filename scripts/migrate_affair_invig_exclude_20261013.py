# -*- coding: utf-8 -*-
"""迁移：环节⑥ 监考排除名单表 affair_invig_excludes（2026-10-13）。

支持两类排除：
  - schedule_id IS NULL → 整批次不监考
  - schedule_id = 某场次 → 不监考指定场次

幂等：表已存在则跳过；建表前经 scripts/_db_backup.backup_db 备份（保留最近 5 份）。

用法：python scripts/migrate_affair_invig_exclude_20261013.py
"""
import os
import sys

import sqlalchemy as sa

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app                                   # noqa: E402
from app.extensions import db                                 # noqa: E402
from scripts._db_backup import backup_db                      # noqa: E402
from scripts._ddl_guard import assert_table                   # noqa: E402

TABLE = 'affair_invig_excludes'
BIND = 'grades'

DDL = """
CREATE TABLE affair_invig_excludes (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    affair_id INTEGER NOT NULL,
    name VARCHAR(50) NOT NULL,
    user_id INTEGER,
    schedule_id INTEGER,
    note VARCHAR(100),
    created_at DATETIME
)
"""
INDEXES = (
    'CREATE INDEX idx_affair_invig_ex_affair '
    'ON affair_invig_excludes (affair_id)',
    'CREATE INDEX idx_affair_invig_ex_sch '
    'ON affair_invig_excludes (affair_id, schedule_id)',
)


def main():
    app = create_app()
    with app.app_context():
        from flask import current_app
        uri = current_app.config['SQLALCHEMY_BINDS'][BIND]
        db_path = uri.replace('sqlite:///', '')
        print(f'[{BIND}] 数据库：{db_path}')

        engine = db.engines[BIND]
        insp = sa.inspect(engine)
        if not insp.has_table('affairs') and not insp.has_table('exam_affairs'):
            print('[SKIP] grades 库尚未初始化（没有考务表），无需迁移')
            return 0
        assert_table(TABLE)
        if insp.has_table(TABLE):
            print(f'[OK] {TABLE} 已存在')
            return 0
        # 2026-10-13 审计修复 #15：改走统一 backup_db（自动保留最近 5 份）
        if os.path.exists(db_path):
            backup_db(db_path)
            print('[OK] 已备份原库')
        with engine.begin() as conn:
            conn.exec_driver_sql(DDL)
            for sql in INDEXES:
                conn.exec_driver_sql(sql)
        print(f'[OK] 已建表 {TABLE}（含 {len(INDEXES)} 个索引）')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
