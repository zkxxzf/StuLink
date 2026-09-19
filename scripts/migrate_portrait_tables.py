#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""迁移脚本：创建学生画像模块三张表（student_portraits / portrait_comments / portrait_events）

绑定库：portrait.db
幂等：表已存在则跳过，可安全重复运行。

用法：python scripts/migrate_portrait_tables.py
"""
import sqlite3
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
portrait_db = os.path.join(BASE, 'data', 'portrait.db')


def _table_exists(conn, table_name):
    """检查表是否已存在"""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,)
    )
    return cursor.fetchone() is not None


def main():
    print('=== 迁移脚本：创建学生画像表 ===')
    print(f'画像库: {portrait_db}')
    print()

    # 确保数据库文件存在
    os.makedirs(os.path.dirname(portrait_db), exist_ok=True)

    conn = sqlite3.connect(portrait_db)
    try:
        # 1) student_portraits 表
        if _table_exists(conn, 'student_portraits'):
            print('[SKIP] student_portraits 表已存在')
        else:
            print('[CREATE] student_portraits 表...')
            conn.execute('''
                CREATE TABLE student_portraits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_no VARCHAR(30) NOT NULL,
                    grade VARCHAR(20),
                    class_name VARCHAR(30),
                    academic_score REAL DEFAULT 0,
                    behavior_score REAL DEFAULT 0,
                    dormitory_score REAL DEFAULT 0,
                    attendance_rate REAL DEFAULT 100,
                    overall_level VARCHAR(20),
                    last_updated DATETIME DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT uq_portrait_student UNIQUE (student_no)
                )
            ''')
            conn.execute(
                'CREATE INDEX idx_portrait_student ON student_portraits (student_no)'
            )
            conn.commit()
            print('[OK] student_portraits 表创建成功')

        # 2) portrait_comments 表
        if _table_exists(conn, 'portrait_comments'):
            print('[SKIP] portrait_comments 表已存在')
        else:
            print('[CREATE] portrait_comments 表...')
            conn.execute('''
                CREATE TABLE portrait_comments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_no VARCHAR(30) NOT NULL,
                    teacher_id INTEGER NOT NULL,
                    comment_type VARCHAR(20),
                    content TEXT,
                    term VARCHAR(20),
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.execute(
                'CREATE INDEX idx_comment_student ON portrait_comments (student_no)'
            )
            conn.execute(
                'CREATE INDEX idx_comment_term ON portrait_comments (term)'
            )
            conn.commit()
            print('[OK] portrait_comments 表创建成功')

        # 3) portrait_events 表
        if _table_exists(conn, 'portrait_events'):
            print('[SKIP] portrait_events 表已存在')
        else:
            print('[CREATE] portrait_events 表...')
            conn.execute('''
                CREATE TABLE portrait_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_no VARCHAR(30) NOT NULL,
                    event_type VARCHAR(30),
                    title VARCHAR(100),
                    description TEXT,
                    event_date DATE,
                    evidence VARCHAR(200),
                    created_by INTEGER,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.execute(
                'CREATE INDEX idx_event_student ON portrait_events (student_no)'
            )
            conn.execute(
                'CREATE INDEX idx_event_type ON portrait_events (event_type)'
            )
            conn.execute(
                'CREATE INDEX idx_event_date ON portrait_events (event_date)'
            )
            conn.commit()
            print('[OK] portrait_events 表创建成功')

        print()
        print('=== 迁移完成 ===')

    except Exception as e:
        print(f'迁移失败: {e}')
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
