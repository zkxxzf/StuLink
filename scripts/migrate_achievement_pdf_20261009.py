# -*- coding: utf-8 -*-
"""迁移：教师业绩库 —— 填表即上传 PDF 附件 + 附件材料分类 + 按类别的动态字段（2026-10-09）。

1. `achievement_attachments` 增加 `doc_type` 列（材料分类：证书/立项通知书/中期/结题…）；
2. `teacher_achievements` 增加 `extra_json` 列（按类别的动态字段 JSON：
   课题的课题编号/立项结题时间/本人角色、论文的期刊刊号/作者位次、培训的学时…）。

都在 academic.db。幂等：列已存在则跳过，不动既有数据。

用法：python scripts/migrate_achievement_pdf_20261009.py
"""
import os
import sys

import sqlalchemy as sa

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app                                   # noqa: E402
from app.extensions import db                                # noqa: E402
from scripts._ddl_guard import assert_ident, assert_table    # noqa: E402

JOBS = [
    ('achievement_attachments', [('doc_type', 'VARCHAR(20)')]),
    ('teacher_achievements', [('extra_json', 'TEXT')]),
]


def main():
    app = create_app()
    with app.app_context():
        engine = db.engines['academic']
        insp = sa.inspect(engine)
        for table, columns in JOBS:
            if not insp.has_table(table):
                print(f'[SKIP] {table} 表不存在（还没有业绩数据，无需迁移）')
                continue
            assert_table(table)
            cols = {c['name'] for c in insp.get_columns(table)}
            for name, ddl in columns:
                assert_ident(name, '列名')
                if name in cols:
                    print(f'[OK] {table}.{name} 已存在')
                    continue
                with engine.begin() as conn:
                    conn.exec_driver_sql(
                        f'ALTER TABLE {table} ADD COLUMN {name} {ddl}')
                print(f'[OK] {table} 已增加 {name} 列')
        print('[OK] 业绩库 PDF 附件 / 动态字段迁移完成')
        return 0


if __name__ == '__main__':
    sys.exit(main())
