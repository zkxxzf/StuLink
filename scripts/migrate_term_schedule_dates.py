#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""迁移脚本（Task#27）：为 term_schedules 补齐学期周期维度字段 + 历史学期演示数据

功能：
1. 幂等补列：ALTER TABLE term_schedules ADD COLUMN start_date/end_date/total_weeks/
   week_start_offset/is_current（先 PRAGMA table_info 判断列是否已存在）
2. 为已有学期回填合理起止日期（按 school_year/term 推断，start_date 对齐到最近周一），
   total_weeks 默认 20；把当前日期落在区间内的学期设 is_current=True
3. 额外造 2 个历史学期作为演示数据（幂等，已存在则跳过），各带 13 条 PeriodDef，
   并生成一批与当前学期明显不同的 ScheduleEntry
4. 打印清晰的迁移统计

不删除/篡改已有的 12 条课表条目与 2 条调课记录（只新增历史学期与其条目）。

用法：python scripts/migrate_term_schedule_dates.py
"""
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

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

# 历史学期演示数据：name / school_year / term / start / end / total_weeks
_HISTORY_TERMS = [
    {
        'name': '2025-2026学年第一学期', 'school_year': '2025-2026', 'term': '第一学期',
        'start_date': date(2025, 9, 1), 'end_date': date(2026, 1, 16),
        'total_weeks': 20, 'status': 'archived',
        'description': '历史演示学期（Task#27 迁移脚本创建）',
    },
    {
        'name': '2025-2026学年第二学期', 'school_year': '2025-2026', 'term': '第二学期',
        'start_date': date(2026, 2, 23), 'end_date': date(2026, 7, 10),
        'total_weeks': 20, 'status': 'archived',
        'description': '历史演示学期（Task#27 迁移脚本创建）',
    },
]

# 各历史学期的课表条目（grade/class_name/weekday/period_number/subject/teacher_name/room）
# 与当前学期明显不同：同学段但学科/教师/时段重排，让用户直观看到"每学期课表不一样"
_HISTORY_ENTRIES = {
    '2025-2026学年第一学期': [
        ('01', '01班', 1, 1, '语文', '王志明', 'A101'),
        ('01', '01班', 1, 2, '英语', '李秀英', 'A101'),
        ('01', '01班', 2, 1, '数学', '张建国', 'A101'),
        ('01', '01班', 2, 3, '物理', '陈海燕', 'A101'),
        ('01', '01班', 3, 2, '化学', '刘德芳', 'A101'),
        ('01', '01班', 4, 1, '历史', '赵春华', 'A101'),
        ('01', '01班', 5, 3, '地理', '孙立军', 'A101'),
        ('02', '02班', 1, 1, '数学', '张建国', 'A102'),
        ('02', '02班', 2, 2, '语文', '王志明', 'A102'),
        ('02', '02班', 3, 3, '英语', '李秀英', 'A102'),
        ('02', '02班', 4, 4, '生物', '周美玲', 'A102'),
        ('02', '02班', 5, 1, '政治', '吴国强', 'A102'),
        ('03', '03班', 1, 3, '物理', '陈海燕', 'A103'),
        ('03', '03班', 2, 1, '化学', '刘德芳', 'A103'),
        ('03', '03班', 3, 1, '语文', '王志明', 'A103'),
        ('03', '03班', 5, 2, '数学', '张建国', 'A103'),
    ],
    '2025-2026学年第二学期': [
        ('01', '01班', 1, 2, '英语', '李秀英', 'A101'),
        ('01', '01班', 2, 2, '语文', '王志明', 'A101'),
        ('01', '01班', 3, 1, '数学', '张建国', 'A101'),
        ('01', '01班', 4, 3, '化学', '刘德芳', 'A101'),
        ('02', '02班', 1, 4, '物理', '陈海燕', 'A102'),
        ('02', '02班', 2, 1, '数学', '张建国', 'A102'),
        ('02', '02班', 3, 2, '英语', '李秀英', 'A102'),
        ('02', '02班', 5, 4, '政治', '吴国强', 'A102'),
        ('03', '03班', 1, 1, '语文', '王志明', 'A103'),
        ('03', '03班', 4, 2, '生物', '周美玲', 'A103'),
    ],
}


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
    conn = sqlite3.connect(TIMETABLE_DB)
    added, skipped = [], []
    try:
        if not _table_exists(conn, 'term_schedules'):
            print('[FAIL] term_schedules 表不存在，请先运行 migrate_timetable_db.py')
            return False
        existing = _column_names(conn, 'term_schedules')
        for col, ddl in _NEW_COLUMNS:
            if col in existing:
                skipped.append(col)
                continue
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
    """回填已有学期日期 + 创建历史学期演示数据（幂等）"""
    from flask import Flask
    from config import Config
    from app.extensions import db
    from app.models.timetable import (TermSchedule, PeriodDef, ScheduleEntry,
                                      get_default_periods)

    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)

    stats = {'dates_backfilled': 0, 'terms_created': 0,
             'periods_created': 0, 'entries_created': 0, 'is_current_set': None}
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

        # 2) 创建历史学期演示数据（幂等，按 name 判断）
        for spec in _HISTORY_TERMS:
            term = TermSchedule.query.filter_by(name=spec['name']).first()
            if term is None:
                term = TermSchedule(
                    name=spec['name'], school_year=spec['school_year'],
                    term=spec['term'], status=spec['status'],
                    description=spec['description'],
                    start_date=spec['start_date'], end_date=spec['end_date'],
                    total_weeks=spec['total_weeks'], week_start_offset=0,
                    is_current=False,
                    created_at=datetime.now(), updated_at=datetime.now(),
                )
                db.session.add(term)
                db.session.flush()
                stats['terms_created'] += 1
                print(f'[CREATE] 历史学期: {term.name} (id={term.id}, '
                      f'{term.start_date} ~ {term.end_date}, {term.total_weeks}周)')
                # 13 条节次
                for p in get_default_periods():
                    db.session.add(PeriodDef(term_schedule_id=term.id, **p))
                    stats['periods_created'] += 1
                db.session.flush()
                # 课表条目
                for (grade, cn, wd, pn, subj, tname, room) in _HISTORY_ENTRIES.get(spec['name'], []):
                    db.session.add(ScheduleEntry(
                        term_schedule_id=term.id, grade=grade, class_name=cn,
                        weekday=wd, period_number=pn, week_range='1-20',
                        subject=subj, teacher_name=tname, room=room,
                        entry_type='normal', is_deleted=False,
                        note='历史演示数据（迁移脚本）',
                    ))
                    stats['entries_created'] += 1
                db.session.commit()
            else:
                # 已存在：补齐可能缺失的日期字段（不覆盖已有）
                changed = False
                if term.start_date is None:
                    term.start_date = spec['start_date']; changed = True
                if term.end_date is None:
                    term.end_date = spec['end_date']; changed = True
                if not term.total_weeks:
                    term.total_weeks = spec['total_weeks']; changed = True
                if changed:
                    db.session.commit()
                print(f'[SKIP] 历史学期已存在: {term.name} (id={term.id})')

        # 3) 设置 is_current：当前日期落在区间内的学期（互斥，只设一个）
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
    print('=== 迁移脚本（Task#27）：学期周期维度 + 历史课表演示数据 ===')
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
    print(f'统计: 补列后回填日期={stats["dates_backfilled"]}, 新建历史学期={stats["terms_created"]}, '
          f'新增节次={stats["periods_created"]}, 新增条目={stats["entries_created"]}, '
          f'当前学期={stats["is_current_set"]}')
    if not ok:
        sys.exit(1)


if __name__ == '__main__':
    main()
