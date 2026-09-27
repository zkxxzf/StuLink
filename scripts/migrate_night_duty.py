# -*- coding: utf-8 -*-
"""迁移：创建 night_duties 表（晚自习值班表）。

只建缺失的表，不动任何既有表/数据。绑定库是 timetable.db
（与 term_schedules / schedule_entries 同库）。

用法：python scripts/migrate_night_duty.py
"""
import os
import sys

import sqlalchemy as sa

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app                      # noqa: E402
from app.extensions import db                   # noqa: E402
from app.models.timetable import NightDuty      # noqa: E402


def main():
    app = create_app()
    with app.app_context():
        engine = db.engines['timetable']
        if sa.inspect(engine).has_table(NightDuty.__tablename__):
            print('[OK] night_duties 表已存在，无需迁移')
            return 0
        NightDuty.__table__.create(bind=engine, checkfirst=True)
        print('[OK] 已创建 night_duties 表（timetable.db）')
        return 0


if __name__ == '__main__':
    sys.exit(main())
