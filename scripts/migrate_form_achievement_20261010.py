# -*- coding: utf-8 -*-
"""表单收集 → 教师业绩库打通：建轮次表 + 补映射/归属/来源列（2026-10-10）。

改动：
1. 新表 `form_rounds`（一次收集 = 一轮，同模板可多轮）；
2. `form_templates` 增 6 列：to_achievement / ach_category / ach_level /
   ach_title_mode / ach_title_question_id / ach_tags（发起时指定的入账规则）；
3. `form_submissions` 增 `round_id`（提交归属哪一round）；
4. `teacher_achievements` 增 4 列：source_type / source_id / source_round_id /
   source_label（来源追溯，支持按轮次筛选分组）。

历史数据：为每个已有模板补一条 round_no=1 的轮次（时间窗取模板的
start_time/deadline，已关闭的模板对应 closed），并把该模板下所有提交的
round_id 回填到这一轮，保证「一次收集 = 一轮」口径统一。

幂等：列/表/数据已存在则 SKIP，可重复执行。
- 标识符全部来自脚本内硬编码常量，经 `_ddl_guard` 白名单校验后拼 DDL。
"""
import glob
import os
import sqlite3
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _ddl_guard import assert_ident, assert_table  # noqa: E402

# 新列：(表名, 列名, DDL)
NEW_COLUMNS = [
    ('form_templates', 'to_achievement', 'BOOLEAN DEFAULT 0'),
    ('form_templates', 'ach_category', 'VARCHAR(20)'),
    ('form_templates', 'ach_level', 'VARCHAR(20)'),
    ('form_templates', 'ach_title_mode', "VARCHAR(10) DEFAULT 'template'"),
    ('form_templates', 'ach_title_question_id', 'INTEGER'),
    ('form_templates', 'ach_tags', 'VARCHAR(100)'),
    ('form_submissions', 'round_id', 'INTEGER'),
    ('teacher_achievements', 'source_type', 'VARCHAR(12)'),
    ('teacher_achievements', 'source_id', 'INTEGER'),
    ('teacher_achievements', 'source_round_id', 'INTEGER'),
    ('teacher_achievements', 'source_label', 'VARCHAR(120)'),
]

CREATE_ROUNDS = """
CREATE TABLE IF NOT EXISTS form_rounds (
    id INTEGER NOT NULL PRIMARY KEY,
    template_id INTEGER NOT NULL,
    round_no INTEGER NOT NULL DEFAULT 1,
    name VARCHAR(100),
    term VARCHAR(20),
    start_time DATETIME,
    deadline DATETIME,
    status VARCHAR(10) DEFAULT 'open',
    created_by INTEGER,
    created_at DATETIME,
    UNIQUE (template_id, round_no)
)
"""

# 为无轮次的模板补第一轮（已关闭的模板对应 closed，其余 open）
SEED_ROUNDS = """
INSERT INTO form_rounds (template_id, round_no, name, term, start_time,
                         deadline, status, created_by, created_at)
SELECT t.id, 1, NULL, NULL, t.start_time, t.deadline,
       CASE WHEN t.status = 'closed' THEN 'closed' ELSE 'open' END,
       t.created_by, :now
  FROM form_templates t
 WHERE NOT EXISTS (SELECT 1 FROM form_rounds r WHERE r.template_id = t.id)
"""

# 历史提交回填到该模板的第一轮
BACKFILL_SUBMISSIONS = """
UPDATE form_submissions
   SET round_id = (SELECT r.id FROM form_rounds r
                    WHERE r.template_id = form_submissions.template_id
                      AND r.round_no = 1)
 WHERE round_id IS NULL
   AND EXISTS (SELECT 1 FROM form_rounds r2
                WHERE r2.template_id = form_submissions.template_id
                  AND r2.round_no = 1)
"""


# 历史提交：submitter_uid 曾误存为 users.id（纯数字），按 Teacher.user_id 回查归一
BACKFILL_UID = """
UPDATE form_submissions
   SET submitter_uid = (SELECT t.teacher_uid FROM teachers t
                         WHERE t.user_id = form_submissions.submitter_id)
 WHERE submitter_type = 'teacher'
   AND submitter_id IS NOT NULL
   AND (submitter_uid IS NULL OR submitter_uid GLOB '[0-9]*')
   AND EXISTS (SELECT 1 FROM teachers t2
                WHERE t2.user_id = form_submissions.submitter_id)
"""


def ensure_columns(conn, db_name):
    touched = 0
    for table, col, ddl in NEW_COLUMNS:
        assert_table(table)
        assert_ident(col)
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            continue
        cols = [c[1] for c in conn.execute(f'PRAGMA table_info({table})')]
        if col in cols:
            print(f'[SKIP] {db_name} 已存在 {table}.{col}')
            continue
        conn.execute(f'ALTER TABLE {table} ADD COLUMN {col} {ddl}')
        print(f'[OK]   {db_name} 新增 {table}.{col} {ddl}')
        touched += 1
    return touched


def main():
    if not os.path.isdir(DATA_DIR):
        print(f'[SKIP] 数据目录不存在: {DATA_DIR}')
        return 0
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    total_cols = total_rounds = total_subs = 0

    for db_path in sorted(glob.glob(os.path.join(DATA_DIR, '*.db'))):
        db_name = os.path.basename(db_path)
        try:
            conn = sqlite3.connect(db_path)
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if 'form_templates' not in tables:
                conn.close()
                continue

            assert_table('form_rounds')
            conn.execute(CREATE_ROUNDS)
            total_cols += ensure_columns(conn, db_name)

            cur = conn.execute(SEED_ROUNDS, {'now': now})
            if cur.rowcount:
                print(f'[OK]   {db_name} 补建首轮 {cur.rowcount} 条')
            total_rounds += cur.rowcount or 0

            cur = conn.execute(BACKFILL_SUBMISSIONS)
            if cur.rowcount:
                print(f'[OK]   {db_name} 回填提交轮次 {cur.rowcount} 条')
            total_subs += cur.rowcount or 0

            if 'teachers' in tables:
                cur = conn.execute(BACKFILL_UID)
                if cur.rowcount:
                    print(f'[OK]   {db_name} 归一教师提交者编号 {cur.rowcount} 条')
                total_subs += cur.rowcount or 0

            conn.commit()
            conn.close()
        except Exception as e:  # noqa: BLE001  单库失败不影响其余库
            print(f'[ERR]  {db_name}: {e}')

    print(f'[DONE] 迁移完成：新增列 {total_cols}、补建轮次 {total_rounds}、'
          f'回填提交 {total_subs}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

# StuLink v1.18.8.0 2026-10-10
# Copyright (c) 2026 zkxxzf. Apache License 2.0
