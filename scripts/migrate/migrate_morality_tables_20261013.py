# -*- coding: utf-8 -*-
"""迁移：德育奖惩模块建表（2026-10-13，v1.19.47.22）

新建 `morality.db` 里的两张表：
  - reward_punishments            奖惩记录
  - reward_punishment_attachments 奖惩附件

幂等：表已存在则跳过，不动既有数据。建库前先备份（项目约定）。

用法：python scripts/migrate/migrate_morality_tables_20261013.py
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
from app.models.morality import (RewardPunishment,           # noqa: E402
                                 RewardPunishmentAttachment)
from scripts._db_backup import backup_db                      # noqa: E402

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
        _mig_log.record(SCRIPT_NAME, note='morality.db 建奖惩记录 / 奖惩附件两表')


BIND = 'morality'


def main():
    app = create_app()
    with app.app_context():
        from flask import current_app
        uri = current_app.config['SQLALCHEMY_BINDS'][BIND]
        db_path = uri.replace('sqlite:///', '')
        print(f'[{BIND}] 数据库：{db_path}')

        engine = db.engines[BIND]
        insp = sa.inspect(engine)

        need_backup = False
        for model in (RewardPunishment, RewardPunishmentAttachment):
            if not insp.has_table(model.__tablename__):
                need_backup = True
        if need_backup and os.path.exists(db_path):
            backup_db(db_path)          # 改库前先备份（保留最近 5 份）
            print('[OK] 已备份原库')

        for model, label in ((RewardPunishment, '奖惩记录'),
                             (RewardPunishmentAttachment, '奖惩附件')):
            table = model.__tablename__
            if insp.has_table(table):
                print(f'[OK] {table} 已存在（{label}）')
                continue
            model.__table__.create(bind=engine, checkfirst=True)
            print(f'[OK] 已创建 {table}（{label}）')

        # 自校验：模型与库是否一致（缺表会在这里暴露）
        insp2 = sa.inspect(engine)
        missing = [m.__tablename__ for m in
                   (RewardPunishment, RewardPunishmentAttachment)
                   if not insp2.has_table(m.__tablename__)]
        if missing:
            print(f'[FAIL] 以下表缺失：{missing}')
            return 1
        cols = {c['name'] for c in insp2.get_columns('reward_punishments')}
        for must in ('student_no', 'kind', 'category', 'status', 'happen_date'):
            if must not in cols:
                print(f'[FAIL] reward_punishments 缺列：{must}')
                return 1
        print('[OK] 表结构校验通过')
        _record()
        return 0


if __name__ == '__main__':
    sys.exit(main())
