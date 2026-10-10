# -*- coding: utf-8 -*-
"""迁移：班级启用标记 `class_profiles.is_active`（2026-10-09）。

背景：班级档案里混着"批量导入/历史"的班级（例如每届 160 个班），而真正在用的是
课表里排了课的那批班。教务各页的班级下拉如果直接读档案，会被几百个未启用班级撑爆。

做法（**不删任何数据**）：
1. `class_profiles` 增加 `is_active` 列（默认 1）；
2. 回填：课表（timetable.db 的 schedule_entries 未删除条目）里出现过的班级 → 1，
   其余 → 0；
3. 打印启用/未启用数量与示例，便于人工核对。

幂等：列已存在则跳过加列，但**每次都会重新回填**（课表变了再跑一次即可对齐）。
若想手动启用某个班：`UPDATE class_profiles SET is_active=1 WHERE grade='?' AND class_name='?'`。

用法：python scripts/migrate_class_active_20261009.py
"""
import os
import sys

import sqlalchemy as sa

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app                                   # noqa: E402
from app.extensions import db                                # noqa: E402
from scripts._ddl_guard import assert_ident, assert_table    # noqa: E402

TABLE = 'class_profiles'
COLUMN = 'is_active'


def main():
    app = create_app()
    with app.app_context():
        engine = db.engines['system']
        insp = sa.inspect(engine)
        if not insp.has_table(TABLE):
            print(f'[SKIP] {TABLE} 不存在')
            return 0
        assert_table(TABLE)
        assert_ident(COLUMN, '列名')

        cols = {c['name'] for c in insp.get_columns(TABLE)}
        if COLUMN not in cols:
            with engine.begin() as conn:
                conn.exec_driver_sql(
                    f'ALTER TABLE {TABLE} ADD COLUMN {COLUMN} INTEGER DEFAULT 1')
            print(f'[OK] {TABLE} 已增加 {COLUMN} 列')
        else:
            print(f'[OK] {TABLE}.{COLUMN} 已存在')

        # 课表里出现过的班级（跨库：timetable.db 取集合，system.db 回填）
        with db.engines['timetable'].begin() as c:
            rows = c.exec_driver_sql(
                'SELECT DISTINCT grade, class_name FROM schedule_entries '
                'WHERE COALESCE(is_deleted,0)=0').fetchall()
        used = {(g, cn) for g, cn in rows if g and cn}
        print(f'[INFO] 课表里有课的班级：{len(used)} 个')

        # 安全闸（2026-10-10 生产事故后补）：课表里一个班都没有 ⇒ 无法据此判断在用班级
        # （典型：学校尚未导入课表）。此时若按原逻辑回填，会把**所有**班级判为未启用，
        # 并让下游 migrate_teaching_links 清空全部任课映射。故此时一律保持启用。
        if not used:
            with engine.begin() as conn:
                total = conn.exec_driver_sql(
                    f'SELECT COUNT(*) FROM {TABLE}').scalar()
                conn.exec_driver_sql(f'UPDATE {TABLE} SET {COLUMN}=1')
            print(f'[WARN] 课表为空，无法判定在用班级 → 保持全部启用（{total} 个）。'
                  f'请在导入课表后重跑本脚本以对齐。')
            return 0

        with engine.begin() as conn:
            all_rows = conn.exec_driver_sql(
                f'SELECT id, grade, class_name FROM {TABLE}').fetchall()
            on = off = 0
            for cid, g, cn in all_rows:
                flag = 1 if (g, cn) in used else 0
                conn.exec_driver_sql(
                    f'UPDATE {TABLE} SET {COLUMN}=? WHERE id=?', (flag, cid))
                on, off = (on + 1, off) if flag else (on, off + 1)
        print(f'[OK] 回填完成：启用 {on} 个班 / 未启用 {off} 个班')
        with engine.begin() as conn:
            sample = conn.exec_driver_sql(
                f'SELECT grade, class_name FROM {TABLE} WHERE {COLUMN}=1 '
                f'ORDER BY grade, class_name LIMIT 8').fetchall()
        print('[INFO] 启用示例：', sample)
        return 0


if __name__ == '__main__':
    sys.exit(main())
