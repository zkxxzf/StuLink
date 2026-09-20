#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""迁移脚本：工作台模块四张表（work_records / attendance_records / notifications / notification_reads）

work_records + attendance_records → academic.db
notifications + notification_reads → system.db

幂等：表已存在则跳过，可安全重复运行。

用法：python scripts/migrate_workbench_tables.py
"""
import sqlite3
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _db_backup import backup_db  # noqa: E402  改库前先备份（项目约定）

academic_db = os.path.join(BASE, 'data', 'academic.db')
system_db = os.path.join(BASE, 'data', 'system.db')


def _table_exists(conn, table_name):
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,)
    )
    return cursor.fetchone() is not None


def _ensure_db(db_path):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    return sqlite3.connect(db_path)


def main():
    print('=== 迁移脚本：工作台模块表 ===')
    print(f'academic 库: {academic_db}')
    print(f'system   库: {system_db}')
    print()

    # 改库前先备份：出错可直接用同名 .bak-<时间戳> 还原
    backup_db([academic_db, system_db])
    print()

    # ── academic.db：work_records + attendance_records ──────────────────────
    conn_a = _ensure_db(academic_db)
    try:
        # 1) work_records
        if _table_exists(conn_a, 'work_records'):
            print('[SKIP] work_records 表已存在（academic.db）')
        else:
            print('[CREATE] work_records 表...')
            conn_a.execute('''
                CREATE TABLE work_records (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    teacher_uid   VARCHAR(16)  NOT NULL,
                    teacher_name  VARCHAR(50),
                    record_type   VARCHAR(10)  NOT NULL,   -- meeting/visit/talk
                    student_no    VARCHAR(30),
                    student_name  VARCHAR(50),
                    class_name    VARCHAR(10),
                    date          DATE         NOT NULL,
                    title         VARCHAR(100) NOT NULL,
                    content       TEXT,
                    follow_up     TEXT,
                    attachments_json TEXT,
                    created_at    DATETIME     DEFAULT CURRENT_TIMESTAMP,
                    updated_at    DATETIME     DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn_a.execute('CREATE INDEX idx_wr_teacher  ON work_records (teacher_uid)')
            conn_a.execute('CREATE INDEX idx_wr_student  ON work_records (student_no)')
            conn_a.execute('CREATE INDEX idx_wr_class    ON work_records (class_name)')
            conn_a.commit()
            print('[OK] work_records 表创建成功')

        # 2) attendance_records
        if _table_exists(conn_a, 'attendance_records'):
            print('[SKIP] attendance_records 表已存在（academic.db）')
        else:
            print('[CREATE] attendance_records 表...')
            conn_a.execute('''
                CREATE TABLE attendance_records (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_no    VARCHAR(30)  NOT NULL,
                    student_name  VARCHAR(50),
                    grade         VARCHAR(10),
                    class_name    VARCHAR(10),
                    attend_date   DATE         NOT NULL,
                    period        INTEGER,                  -- NULL = 全天
                    status        VARCHAR(10)  NOT NULL,    -- present/absent/late/leave
                    recorded_by   INTEGER,
                    remark        VARCHAR(200),
                    created_at    DATETIME     DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn_a.execute('CREATE INDEX idx_ar_student ON attendance_records (student_no)')
            conn_a.execute('CREATE INDEX idx_ar_grade   ON attendance_records (grade)')
            conn_a.execute('CREATE INDEX idx_ar_class   ON attendance_records (class_name)')
            conn_a.execute('CREATE INDEX idx_ar_date    ON attendance_records (attend_date)')
            conn_a.commit()
            print('[OK] attendance_records 表创建成功')

    except Exception as e:
        print(f'[ERROR] academic.db 迁移失败: {e}')
        conn_a.rollback()
        conn_a.close()
        sys.exit(1)
    finally:
        conn_a.close()

    print()

    # ── system.db：notifications + notification_reads ───────────────────────
    conn_s = _ensure_db(system_db)
    try:
        # 3) notifications
        if _table_exists(conn_s, 'notifications'):
            print('[SKIP] notifications 表已存在（system.db）')
        else:
            print('[CREATE] notifications 表...')
            conn_s.execute('''
                CREATE TABLE notifications (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    title          VARCHAR(100) NOT NULL,
                    content        TEXT         NOT NULL,
                    target_type    VARCHAR(20)  DEFAULT 'all',  -- all/grade/class/role
                    target_scope   TEXT,                         -- JSON
                    priority       VARCHAR(10)  DEFAULT 'normal',
                    published_by   INTEGER,
                    published_at   DATETIME     DEFAULT CURRENT_TIMESTAMP,
                    is_active      BOOLEAN      DEFAULT 1,
                    created_at     DATETIME     DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn_s.commit()
            print('[OK] notifications 表创建成功')

        # 4) notification_reads
        if _table_exists(conn_s, 'notification_reads'):
            print('[SKIP] notification_reads 表已存在（system.db）')
        else:
            print('[CREATE] notification_reads 表...')
            conn_s.execute('''
                CREATE TABLE notification_reads (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    notification_id INTEGER NOT NULL,
                    user_id         INTEGER NOT NULL,
                    read_at         DATETIME DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT uq_notif_read UNIQUE (notification_id, user_id),
                    FOREIGN KEY (notification_id) REFERENCES notifications(id)
                )
            ''')
            conn_s.execute('CREATE INDEX idx_nr_notif ON notification_reads (notification_id)')
            conn_s.execute('CREATE INDEX idx_nr_user  ON notification_reads (user_id)')
            conn_s.commit()
            print('[OK] notification_reads 表创建成功')

    except Exception as e:
        print(f'[ERROR] system.db 迁移失败: {e}')
        conn_s.rollback()
        conn_s.close()
        sys.exit(1)
    finally:
        conn_s.close()

    print()
    print('=== 迁移完成 ===')


if __name__ == '__main__':
    main()
