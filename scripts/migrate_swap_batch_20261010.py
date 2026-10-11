# -*- coding: utf-8 -*-
"""迁移：统一调课批次号（2026-10-10）。

给 timetable.db 的 `schedule_swaps` 增加 1 列：
- batch_id  TEXT  统一调课批次号 —— 同一次「统一调课」生成的多条记录共享同一值，
          列表按批次折叠成**一条记录**、整批一次性审批/执行，不必几十条逐个点。

另外回填**历史统一调课**（batch_id 出现之前提交的）批次号：
判据 = swap_type='bulk' + 同一次提交（`created_at` 精确到微秒完全相同）+ 同申请人 /
同学期 / 同目标条件（星期、节次、教室、日期、是否永久）。个人调课一次只写一条记录，
不可能与别的记录共享微秒时间戳，所以这个判据可靠；只给 >=2 条的分组归批，单条不动。

幂等：列已存在则跳过；回填只处理 batch_id 为空的记录（跑第二遍不会重复归批）。

用法：python scripts/migrate_swap_batch_20261010.py
"""
import os
import sys
import uuid

import sqlalchemy as sa

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app                                   # noqa: E402
from app.extensions import db                                # noqa: E402
from scripts._ddl_guard import assert_ident, assert_table    # noqa: E402

TABLE = 'schedule_swaps'
COLUMNS = [
    ('batch_id', 'TEXT'),
]

# 历史统一调课探测：一次提交的记录除 id 外完全相同（含微秒级 created_at）
_SELECT_LEGACY = """
SELECT id, applicant_uid, created_at, term_schedule_id,
       IFNULL(new_weekday, -1), IFNULL(new_period, -1), IFNULL(new_room, ''),
       IFNULL(swap_date, ''), IFNULL(is_permanent, 0)
FROM {table}
WHERE batch_id IS NULL AND swap_type = 'bulk'
ORDER BY id
"""

_UPDATE_BATCH = sa.text(
    'UPDATE {table} SET batch_id = :bid WHERE id IN :ids'.format(table=TABLE)
).bindparams(sa.bindparam('ids', expanding=True))


def backfill_legacy_batches(engine):
    """给历史（无批次号的）统一调课归批复号，返回 (组数, 记录数)。"""
    with engine.begin() as conn:
        rows = conn.exec_driver_sql(_SELECT_LEGACY.format(table=TABLE)).fetchall()
        groups = {}
        for r in rows:
            key = (r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8])
            groups.setdefault(key, []).append(r[0])
        made_groups = made_rows = 0
        for ids in groups.values():
            if len(ids) < 2:          # 单条记录 = 本来就独立的申请，不归批
                continue
            conn.execute(_UPDATE_BATCH, {'bid': uuid.uuid4().hex, 'ids': ids})
            made_groups += 1
            made_rows += len(ids)
    return made_groups, made_rows


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
        # 批次号查询索引（列表按 batch_id 聚合）
        with engine.begin() as conn:
            conn.exec_driver_sql(
                f'CREATE INDEX IF NOT EXISTS ix_{TABLE}_batch_id ON {TABLE} (batch_id)')
        print(f'[OK] {TABLE} 批次号索引就绪')
        groups, rows = backfill_legacy_batches(engine)
        if rows:
            print(f'[OK] 历史统一调课回填批次号：{groups} 批 / {rows} 条记录')
        else:
            print('[OK] 历史统一调课无需回填（无未归批的批量记录）')
        print(f'[OK] 迁移完成：{TABLE}')
        return 0


if __name__ == '__main__':
    sys.exit(main())
