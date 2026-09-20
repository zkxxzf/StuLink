#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""迁移脚本：创建独立课表库 timetable.db + 全新课表模型 5 张表

功能：
1. 创建 timetable.db 及全部 5 张新表（term_schedules / period_defs /
   schedule_entries / schedule_swaps / schedule_versions），使用 SQLAlchemy
   metadata create_all 绑定正确的 engine
2. 若目标学期不存在，创建默认 TermSchedule 并写入 13 条 PeriodDef（get_default_periods）
3. 从 academic.db 旧表迁移数据（timetable_entries -> schedule_entries,
   course_swaps -> schedule_swaps），旧表保留不删除（兼容过渡期）
4. 幂等：重复运行不产生重复数据（先检查是否已存在）
5. 打印清晰的迁移统计信息

用法：python scripts/migrate_timetable_db.py
"""
import os
import sqlite3
import sys
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _db_backup import backup_db  # noqa: E402  改库前先备份（项目约定）

DATA_DIR = os.path.join(BASE, 'data')
TIMETABLE_DB = os.path.join(DATA_DIR, 'timetable.db')
ACADEMIC_DB = os.path.join(DATA_DIR, 'academic.db')

DEFAULT_TERM_NAME = '2026-2027学年第一学期'
DEFAULT_SCHOOL_YEAR = '2026-2027'
DEFAULT_TERM = '第一学期'


def _table_exists(conn, table_name):
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,)
    )
    return cursor.fetchone() is not None


def _column_names(conn, table_name):
    cursor = conn.execute(f'PRAGMA table_info({table_name})')
    return [row[1] for row in cursor.fetchall()]


def create_tables_via_sqlalchemy():
    """通过 SQLAlchemy metadata 在 timetable 绑定库上建表（保证与模型定义完全一致）"""
    from flask import Flask
    from config import Config
    from app.extensions import db
    # 导入模型以注册到 metadata（绑定 key = 'timetable'）
    from app.models.timetable import (TermSchedule, PeriodDef, ScheduleEntry,  # noqa: F401
                                      ScheduleSwap, ScheduleVersion)

    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)
    with app.app_context():
        db.create_all(bind_key='timetable')
    print('[OK] 已通过 SQLAlchemy metadata 创建 timetable.db 全部表')


def seed_default_term_and_periods():
    """创建默认学期 + 13 条节次定义（幂等）"""
    from app.models.timetable import TermSchedule, PeriodDef, get_default_periods
    from app.extensions import db
    from flask import Flask
    from config import Config

    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)

    stats = {'term_created': False, 'periods_created': 0}
    term_id = None
    with app.app_context():
        term = TermSchedule.query.filter_by(name=DEFAULT_TERM_NAME).first()
        if term is None:
            term = TermSchedule(
                name=DEFAULT_TERM_NAME,
                school_year=DEFAULT_SCHOOL_YEAR,
                term=DEFAULT_TERM,
                status='active',
                description='迁移脚本自动创建的默认学期',
            )
            db.session.add(term)
            db.session.flush()
            stats['term_created'] = True
            print(f'[CREATE] 默认学期: {DEFAULT_TERM_NAME} (id={term.id}, status=active)')
        else:
            print(f'[SKIP] 默认学期已存在: {DEFAULT_TERM_NAME} (id={term.id})')

        # 在上下文内捕获 id，避免退出上下文后访问 detached 实例属性
        term_id = term.id
        for p in get_default_periods():
            exists = PeriodDef.query.filter_by(
                term_schedule_id=term_id, period_number=p['period_number']
            ).first()
            if exists is None:
                db.session.add(PeriodDef(term_schedule_id=term_id, **p))
                stats['periods_created'] += 1
        db.session.commit()
        total = PeriodDef.query.filter_by(term_schedule_id=term_id).count()
        print(f'[OK] 学期 id={term_id} 节次定义: 本次新增 {stats["periods_created"]} 条，当前共 {total} 条')
    return term_id, stats


def migrate_legacy_entries(default_term_id):
    """从 academic.db 旧表迁移课表明细与调课记录（旧表保留不删除）"""
    from app.models.timetable import ScheduleEntry, ScheduleSwap
    from app.extensions import db
    from flask import Flask
    from config import Config

    stats = {'entries_migrated': 0, 'entries_skipped': 0,
             'swaps_migrated': 0, 'swaps_skipped': 0}

    if not os.path.exists(ACADEMIC_DB):
        print('[SKIP] academic.db 不存在，跳过旧数据迁移')
        return stats

    legacy = sqlite3.connect(ACADEMIC_DB)
    legacy.row_factory = sqlite3.Row
    try:
        has_entries = _table_exists(legacy, 'timetable_entries')
        has_swaps = _table_exists(legacy, 'course_swaps')

        app = Flask(__name__)
        app.config.from_object(Config)
        db.init_app(app)

        with app.app_context():
            # 1) timetable_entries -> schedule_entries
            if has_entries:
                cols = _column_names(legacy, 'timetable_entries')
                rows = legacy.execute('SELECT * FROM timetable_entries').fetchall()
                print(f'[LEGACY] timetable_entries 共 {len(rows)} 条待迁移')
                for row in rows:
                    r = dict(row)
                    weekday = r.get('weekday')
                    period = r.get('period')
                    subject = r.get('subject')
                    if weekday is None or period is None or not subject:
                        stats['entries_skipped'] += 1
                        continue
                    # 幂等：按业务唯一键检查是否已迁移过
                    dup = ScheduleEntry.query.filter_by(
                        term_schedule_id=default_term_id,
                        weekday=weekday,
                        period_number=period,
                        subject=subject,
                        teacher_uid=r.get('teacher_uid'),
                        class_name=r.get('class_name') or '',
                    ).first()
                    if dup is not None:
                        stats['entries_skipped'] += 1
                        continue
                    entry = ScheduleEntry(
                        term_schedule_id=default_term_id,
                        grade=r.get('grade') or (r.get('class_name') or '')[:2] or '未知',
                        class_name=r.get('class_name') or '',
                        weekday=weekday,
                        period_number=period,
                        week_range=r.get('week_range') or '1-18',
                        subject=subject,
                        teacher_uid=r.get('teacher_uid'),
                        teacher_name=r.get('teacher_name'),
                        room=r.get('room'),
                        entry_type='normal',
                        note=('迁移自旧课表 id=%s' % r.get('id')) if r.get('id') else None,
                    )
                    db.session.add(entry)
                    stats['entries_migrated'] += 1
                db.session.commit()
            else:
                print('[SKIP] academic.db 中无 timetable_entries 表')

            # 2) course_swaps -> schedule_swaps
            if has_swaps:
                rows = legacy.execute('SELECT * FROM course_swaps').fetchall()
                print(f'[LEGACY] course_swaps 共 {len(rows)} 条待迁移')
                for row in rows:
                    r = dict(row)
                    applicant_uid = r.get('applicant_uid')
                    if not applicant_uid:
                        stats['swaps_skipped'] += 1
                        continue
                    # 幂等：同一申请人+原条目+新日期节次视为已迁移
                    new_date_raw = r.get('new_date')
                    swap_date = None
                    if new_date_raw:
                        try:
                            swap_date = datetime.strptime(str(new_date_raw)[:10], '%Y-%m-%d').date()
                        except ValueError:
                            swap_date = None
                    dup = ScheduleSwap.query.filter_by(
                        applicant_uid=applicant_uid,
                        original_entry_id=r.get('original_timetable_entry_id'),
                        new_period=r.get('new_period'),
                        swap_date=swap_date,
                    ).first()
                    if dup is not None:
                        stats['swaps_skipped'] += 1
                        continue
                    old_status = r.get('status') or 'pending'
                    if old_status not in ('pending', 'approved', 'rejected', 'executed'):
                        old_status = 'pending'
                    swap = ScheduleSwap(
                        term_schedule_id=default_term_id,
                        swap_type=r.get('swap_type') or 'personal',
                        original_entry_id=r.get('original_timetable_entry_id'),
                        applicant_uid=applicant_uid,
                        applicant_name=r.get('applicant_name'),
                        new_period=r.get('new_period'),
                        new_room=r.get('new_room'),
                        swap_date=swap_date,
                        is_permanent=False,
                        reason=r.get('reason'),
                        status=old_status,
                        reviewed_by=r.get('reviewed_by'),
                        review_note=r.get('reject_reason'),
                        reviewed_at=_parse_dt(r.get('reviewed_at')),
                        created_at=_parse_dt(r.get('created_at')) or datetime.now(),
                    )
                    db.session.add(swap)
                    stats['swaps_migrated'] += 1
                db.session.commit()
            else:
                print('[SKIP] academic.db 中无 course_swaps 表')
    finally:
        legacy.close()

    print(f'[OK] 旧课表明细迁移: 新增 {stats["entries_migrated"]} 条，跳过 {stats["entries_skipped"]} 条')
    print(f'[OK] 旧调课记录迁移: 新增 {stats["swaps_migrated"]} 条，跳过 {stats["swaps_skipped"]} 条')
    return stats


def _parse_dt(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d'):
        try:
            return datetime.strptime(str(value)[:26], fmt)
        except ValueError:
            continue
    return None


def verify_result():
    """验证 timetable.db 表结构与默认数据"""
    conn = sqlite3.connect(TIMETABLE_DB)
    try:
        expected = ['term_schedules', 'period_defs', 'schedule_entries',
                    'schedule_swaps', 'schedule_versions']
        missing = [t for t in expected if not _table_exists(conn, t)]
        if missing:
            print(f'[FAIL] 缺失表: {missing}')
            return False
        print(f'[VERIFY] 5 张表全部存在: {expected}')
        term_count = conn.execute('SELECT COUNT(*) FROM term_schedules').fetchone()[0]
        period_count = conn.execute('SELECT COUNT(*) FROM period_defs').fetchone()[0]
        entry_count = conn.execute('SELECT COUNT(*) FROM schedule_entries').fetchone()[0]
        swap_count = conn.execute('SELECT COUNT(*) FROM schedule_swaps').fetchone()[0]
        print(f'[VERIFY] term_schedules={term_count}, period_defs={period_count}, '
              f'schedule_entries={entry_count}, schedule_swaps={swap_count}')
        return period_count >= 13
    finally:
        conn.close()


def main():
    print('=== 迁移脚本：创建独立课表库 timetable.db ===')
    print(f'课表库: {TIMETABLE_DB}')
    print(f'旧教务库: {ACADEMIC_DB}')
    print()

    os.makedirs(DATA_DIR, exist_ok=True)

    # 0) 改库前先备份：建库/迁移出错可直接还原
    backup_db([TIMETABLE_DB, ACADEMIC_DB])
    print()

    # 1) 建表
    create_tables_via_sqlalchemy()

    # 2) 默认学期 + 13 节次
    default_term_id, seed_stats = seed_default_term_and_periods()

    # 3) 旧数据迁移（保留旧表不删除）
    migrate_stats = migrate_legacy_entries(default_term_id)

    print()
    ok = verify_result()
    print()
    print('=== 迁移完成 ===' if ok else '=== 迁移结束（验证未通过，请检查） ===')
    print(f'统计: 学期新建={seed_stats["term_created"]}, 节次新增={seed_stats["periods_created"]}, '
          f'课表条目迁移={migrate_stats["entries_migrated"]}, 调课记录迁移={migrate_stats["swaps_migrated"]}')
    if not ok:
        sys.exit(1)


if __name__ == '__main__':
    main()
