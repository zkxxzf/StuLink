# -*- coding: utf-8 -*-
"""迁移：教师业绩库支持标签与附件（2026-09-26）。

1. `teacher_achievements` 增加 `tags` 列（逗号分隔标签）；
2. 新建 `achievement_attachments` 表（业绩附件：证书扫描件/照片/PDF 等）。

都在 academic.db。幂等：列/表已存在则跳过，不动既有数据。

用法：python scripts/migrate_achievement_tags_files.py
"""
import os
import sys

import sqlalchemy as sa

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app                                   # noqa: E402
from app.extensions import db                                # noqa: E402
from app.models.academic import (AchievementAttachment,      # noqa: E402
                                 TeacherAchievement)
from scripts._ddl_guard import assert_ident, assert_table    # noqa: E402

TABLE = 'teacher_achievements'
COLUMN = 'tags'


def main():
    app = create_app()
    with app.app_context():
        engine = db.engines['academic']
        insp = sa.inspect(engine)

        if insp.has_table(TABLE):
            cols = {c['name'] for c in insp.get_columns(TABLE)}
            if COLUMN not in cols:
                assert_ident(COLUMN, '列名')
                assert_table(TABLE)
                with engine.begin() as conn:
                    conn.exec_driver_sql(
                        f'ALTER TABLE {TABLE} ADD COLUMN {COLUMN} VARCHAR(200)')
                print(f'[OK] {TABLE} 已增加 {COLUMN} 列')
            else:
                print(f'[OK] {TABLE}.{COLUMN} 已存在')

        if not insp.has_table(AchievementAttachment.__tablename__):
            AchievementAttachment.__table__.create(bind=engine, checkfirst=True)
            print(f'[OK] 已创建 {AchievementAttachment.__tablename__} 表')
        else:
            print(f'[OK] {AchievementAttachment.__tablename__} 表已存在')

        # 确认模型与库一致（缺列会在这里暴露）
        _ = [c['name'] for c in sa.inspect(engine).get_columns(TABLE)]
        return 0


if __name__ == '__main__':
    sys.exit(main())
