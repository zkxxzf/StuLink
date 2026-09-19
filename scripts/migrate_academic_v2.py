#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""迁移脚本：academic.db v2 - 调课管理 + 问卷表单收集

新增表：course_swaps, form_categories, form_templates, form_questions,
       form_submissions, form_answers
绑定库：academic.db
幂等设计：先检查表是否存在再创建，可安全重复运行。

用法：python scripts/migrate_academic_v2.py
"""
import sqlite3
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
academic_db = os.path.join(BASE, 'data', 'academic.db')


def _table_exists(conn, table_name):
    """检查表是否已存在"""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,)
    )
    return cursor.fetchone() is not None


def main():
    print('=== 迁移脚本：academic.db v2 - 调课管理 + 问卷表单收集 ===')
    print(f'教务库: {academic_db}')
    print()

    os.makedirs(os.path.dirname(academic_db), exist_ok=True)

    conn = sqlite3.connect(academic_db)
    try:
        # 1) course_swaps 表 —— 调课记录
        if _table_exists(conn, 'course_swaps'):
            print('[SKIP] course_swaps 表已存在')
        else:
            print('[CREATE] course_swaps 表...')
            conn.execute('''
                CREATE TABLE course_swaps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    swap_type VARCHAR(10) NOT NULL,
                    applicant_uid VARCHAR(16),
                    applicant_name VARCHAR(50),
                    original_timetable_entry_id INTEGER,
                    original_date DATE,
                    original_period INTEGER,
                    original_class VARCHAR(10),
                    original_subject VARCHAR(20),
                    new_date DATE,
                    new_period INTEGER,
                    new_room VARCHAR(30),
                    reason VARCHAR(200),
                    scope_grade VARCHAR(10),
                    scope_note VARCHAR(100),
                    status VARCHAR(10) DEFAULT 'pending',
                    reviewed_by INTEGER,
                    reviewed_at DATETIME,
                    reject_reason VARCHAR(200),
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.execute(
                'CREATE INDEX idx_swap_applicant ON course_swaps (applicant_uid)'
            )
            conn.commit()
            print('[OK] course_swaps 表创建成功')

        # 2) form_categories 表 —— 表单分类
        if _table_exists(conn, 'form_categories'):
            print('[SKIP] form_categories 表已存在')
        else:
            print('[CREATE] form_categories 表...')
            conn.execute('''
                CREATE TABLE form_categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name VARCHAR(30) NOT NULL UNIQUE,
                    sort_order INTEGER DEFAULT 0
                )
            ''')
            conn.commit()
            print('[OK] form_categories 表创建成功')

        # 插入默认表单分类
        default_categories = [
            ('教学材料', 1), ('教学反馈', 2), ('信息收集', 3), ('其他', 4),
        ]
        for name, order in default_categories:
            cursor = conn.execute(
                'SELECT id FROM form_categories WHERE name=?', (name,)
            )
            if cursor.fetchone() is None:
                conn.execute(
                    'INSERT INTO form_categories (name, sort_order) VALUES (?, ?)',
                    (name, order)
                )
                print(f'  [INSERT] 默认分类: {name}')
        conn.commit()

        # 3) form_templates 表 —— 表单模板
        if _table_exists(conn, 'form_templates'):
            print('[SKIP] form_templates 表已存在')
        else:
            print('[CREATE] form_templates 表...')
            conn.execute('''
                CREATE TABLE form_templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title VARCHAR(100) NOT NULL,
                    description TEXT,
                    category VARCHAR(30),
                    target_type VARCHAR(20) DEFAULT 'all',
                    target_scope TEXT,
                    start_time DATETIME,
                    deadline DATETIME,
                    max_file_size_mb INTEGER DEFAULT 10,
                    status VARCHAR(10) DEFAULT 'draft',
                    allow_multiple BOOLEAN DEFAULT 0,
                    created_by INTEGER,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()
            print('[OK] form_templates 表创建成功')

        # 4) form_questions 表 —— 表单题目
        if _table_exists(conn, 'form_questions'):
            print('[SKIP] form_questions 表已存在')
        else:
            print('[CREATE] form_questions 表...')
            conn.execute('''
                CREATE TABLE form_questions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    template_id INTEGER NOT NULL REFERENCES form_templates(id),
                    question_type VARCHAR(20) NOT NULL,
                    title VARCHAR(200) NOT NULL,
                    description VARCHAR(500),
                    options_json TEXT,
                    required BOOLEAN DEFAULT 0,
                    file_types VARCHAR(100),
                    max_file_size_mb INTEGER,
                    sort_order INTEGER DEFAULT 0
                )
            ''')
            conn.commit()
            print('[OK] form_questions 表创建成功')

        # 5) form_submissions 表 —— 提交记录
        if _table_exists(conn, 'form_submissions'):
            print('[SKIP] form_submissions 表已存在')
        else:
            print('[CREATE] form_submissions 表...')
            conn.execute('''
                CREATE TABLE form_submissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    template_id INTEGER NOT NULL REFERENCES form_templates(id),
                    submitter_type VARCHAR(10),
                    submitter_id INTEGER,
                    submitter_name VARCHAR(50),
                    submitter_uid VARCHAR(30),
                    submitter_grade VARCHAR(10),
                    submitter_class VARCHAR(10),
                    status VARCHAR(10) DEFAULT 'submitted',
                    reviewed_by INTEGER,
                    reviewed_at DATETIME,
                    review_note VARCHAR(200),
                    submitted_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()
            print('[OK] form_submissions 表创建成功')

        # 6) form_answers 表 —— 答案数据
        if _table_exists(conn, 'form_answers'):
            print('[SKIP] form_answers 表已存在')
        else:
            print('[CREATE] form_answers 表...')
            conn.execute('''
                CREATE TABLE form_answers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    submission_id INTEGER NOT NULL REFERENCES form_submissions(id),
                    question_id INTEGER NOT NULL REFERENCES form_questions(id),
                    answer_text TEXT,
                    answer_json TEXT,
                    file_path VARCHAR(200),
                    file_name VARCHAR(100),
                    file_size INTEGER
                )
            ''')
            conn.commit()
            print('[OK] form_answers 表创建成功')

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
