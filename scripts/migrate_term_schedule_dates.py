#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""迁移脚本（Task#27）：为 term_schedules 补齐学期周期维度字段

功能：
1. 幂等补列：ALTER TABLE term_schedules ADD COLUMN start_date/end_date/total_weeks/
   week_start_offset/is_current（先 PRAGMA table_info 判断列是否已存在）
2. 为已有学期回填合理起止日期（按 school_year/term 推断，start_date 对齐到最近周一），
   total_weeks 默认 20；把当前日期落在区间内的学期设 is_current=True
3. 打印清晰的迁移统计

说明（PR#5 安全审查 M5）：
- 本脚本早前还会额外造 2 个历史学期 + 26 条演示课表条目，在生产库上执行会凭空生成
  虚构数据，已被移除。需要演示数据时请用专门的 mock 脚本（见 scripts/import_mock_*.py）。
- 执行前自动备份 timetable.db（同目录 .bak-<时间戳>），与项目其他迁移脚本一致。

不删除/篡改已有的课表条目与调课记录（只补列、回填日期）。

用法：python scripts/migrate_term_schedule_dates.py
"""
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _db_backup import backup_db  # noqa: E402  改库前先备份（项目约定）

DATA_DIR = os.path.join(BASE, 'data')
TIMETABLE_DB = os.path.join(DATA_DIR, 'timetable.db')

# 需要补齐的列：(列名, DDL 类型与默认值)
_NEW_COLUMNS = [
    ('start_date', 'DATE'),
    ('end_date', 'DATE'),
    ('total_weeks', 'INTEGER DEFAULT 20'),
    ('week_start_offset', 'INTEGER DEFAULT 0'),
    ('is_current', 'BOOLEAN DEFAULT 0'),
]


def _column_names(conn, table_name):
    cursor = conn.execute(f'PRAGMA table_info({table_name})')
    return [row[1] for row in cursor.fetchall()]


def _table_exists(conn, table_name):
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
    return cur.fetchone() is not None


def add_columns():
    """幂等补列：为 term_schedules 增加学期周期维度字段"""
    if not os.path.exists(TIMETABLE_DB):
        print(f'[FAIL] 未找到 {TIMETABLE_DB}，请先运行 migrate_timetable_db.py')
        return False
    # 补列前先备份：后续回填会写库
    backup_db(TIMETABLE_DB)
    conn = sqlite3.connect(TIMETABLE_DB)
    added, skipped = [], []
    try:
        if not _table_exists(conn, 'term_schedules'):
            print('[FAIL] term_schedules 表不存在，请先运行 migrate_timetable_db.py')
            return False
        existing = _column_names(conn, 'term_schedules')
        from _ddl_guard import assert_ident   # R-11
        for col, ddl in _NEW_COLUMNS:
            if col in existing:
                skipped.append(col)
                continue
            assert_ident(col, '列名')
            conn.execute(f'ALTER TABLE term_schedules ADD COLUMN {col} {ddl}')
            added.append(col)
        conn.commit()
    finally:
        conn.close()
    print(f'[OK] 补列完成：新增 {added or "无"}，已存在跳过 {skipped or "无"}')
    return True


def _align_monday(d):
    """把日期对齐到所在周的周一"""
    return d - timedelta(days=d.weekday())


def _infer_dates(school_year, term):
    """按 学年/学期 推断起止日期（第一学期约 9/1~次年1月中，第二学期约 2月中~7月初）"""
    try:
        start_year = int(str(school_year).split('-')[0])
    except (ValueError, IndexError):
        start_year = date.today().year
    if term and '第二' in term:
        start = date(start_year + 1, 2, 23)
        end = date(start_year + 1, 7, 10)
    else:
        start = date(start_year, 9, 1)
        end = date(start_year + 1, 1, 15)
    return _align_monday(start), end


def seed():
    """回填已有学期的日期字段 + 设置当前学期（幂等）。

    注意：这里只补全「已有学期」缺失的元数据，绝不新建学期或课表条目。
    """
    from flask import Flask
    from config import Config
    from app.extensions import db
    from app.models.timetable import TermSchedule

    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)

    stats = {'dates_backfilled': 0, 'is_current_set': None}
    today = date.today()

    with app.app_context():
        # 1) 回填已有学期日期（仅处理 start_date 为空的）
        for ts in TermSchedule.query.all():
            if ts.start_date is None:
                sd, ed = _infer_dates(ts.school_year, ts.term)
                ts.start_date = sd
                ts.end_date = ed
                if not ts.total_weeks:
                    ts.total_weeks = 20
                if ts.week_start_offset is None:
                    ts.week_start_offset = 0
                stats['dates_backfilled'] += 1
                print(f'[BACKFILL] {ts.name}: {sd} ~ {ed}, total_weeks={ts.total_weeks}')
        db.session.commit()

        # 2) 设置 is_current：当前日期落在区间内的学期（互斥，只设一个）
        TermSchedule.query.update({TermSchedule.is_current: False},
                                  synchronize_session=False)
        db.session.commit()
        for ts in TermSchedule.query.all():
            if ts.contains_date(today):
                ts.is_current = True
                stats['is_current_set'] = ts.name
                db.session.commit()
                print(f'[CURRENT] 当前学期设为: {ts.name} (第 {ts.get_current_week()} 周)')
                break
        if not stats['is_current_set']:
            print('[WARN] 没有学期的日期区间包含今天，is_current 全部为 False')

    return stats


def verify():
    """列出全部学期的周期字段，验证迁移结果"""
    conn = sqlite3.connect(TIMETABLE_DB)
    try:
        cols = _column_names(conn, 'term_schedules')
        need = ['start_date', 'end_date', 'total_weeks', 'week_start_offset', 'is_current']
        missing = [c for c in need if c not in cols]
        if missing:
            print(f'[FAIL] 仍缺失列: {missing}')
            return False
        print('[VERIFY] term_schedules 周期字段齐全:', need)
        rows = conn.execute(
            'SELECT id,name,status,start_date,end_date,total_weeks,is_current '
            'FROM term_schedules ORDER BY id').fetchall()
        print(f'[VERIFY] 共 {len(rows)} 个学期:')
        for r in rows:
            print(f'    id={r[0]} [{r[2]}] {r[1]} | {r[3]} ~ {r[4]} | '
                  f'{r[5]}周 | is_current={r[6]}')
        entry_total = conn.execute(
            'SELECT COUNT(*) FROM schedule_entries WHERE is_deleted=0').fetchone()[0]
        swap_total = conn.execute('SELECT COUNT(*) FROM schedule_swaps').fetchone()[0]
        print(f'[VERIFY] 未删除条目总数={entry_total}, 调课记录总数={swap_total}')
        return True
    finally:
        conn.close()


def main():
    print('=== 迁移脚本（Task#27）：学期周期维度字段 ===')
    print(f'课表库: {TIMETABLE_DB}')
    print()
    if not add_columns():
        sys.exit(1)
    print()
    stats = seed()
    print()
    ok = verify()
    print()
    print('=== 迁移完成 ===' if ok else '=== 迁移结束（验证未通过，请检查） ===')
    print(f'统计: 回填日期={stats["dates_backfilled"]}, '
          f'当前学期={stats["is_current_set"]}')
    if not ok:
        sys.exit(1)


if __name__ == '__main__':
    main()
