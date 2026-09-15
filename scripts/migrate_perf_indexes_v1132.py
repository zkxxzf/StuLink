"""性能索引迁移（v1.13.2）

压测发现：student_service.student_exams 中
    ExamScore.query.filter_by(student_no=..., subject='总分')
不带 exam_id，而 exam_scores 现有索引全部以 exam_id 打头，
导致该查询对全表（31 万+行）做 SCAN。学生查询接口并发下重复全表扫描，
是 /api/student-query/data 热缓存 P95 达 400ms 的主因之一。

本脚本补建覆盖索引 (student_no, subject, exam_id)，
使上述查询只走索引、不回表。

幂等：IF NOT EXISTS，可重复执行；执行前自动备份 grades.db。

用法：
    python scripts/migrate_perf_indexes_v1132.py
"""
import os
import shutil
import sqlite3
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE_DIR, 'data', 'grades.db')

INDEXES = [
    ('idx_scores_stu_subject_exam',
     'CREATE INDEX IF NOT EXISTS idx_scores_stu_subject_exam '
     'ON exam_scores (student_no, subject, exam_id)'),
]


def backup(path):
    if not os.path.exists(path):
        return None
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    dst = f'{path}.bak-{stamp}'
    shutil.copy2(path, dst)
    return dst


def main():
    if not os.path.exists(DB):
        print('grades.db 不存在，跳过')
        return
    print('备份 ->', os.path.basename(backup(DB)))

    conn = sqlite3.connect(DB)
    for name, ddl in INDEXES:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
            (name,)).fetchone()
        if exists:
            print(f'{name} 已存在，跳过')
            continue
        conn.execute(ddl)
        print(f'{name} 已创建')
    conn.execute('ANALYZE')
    conn.commit()

    # 验证查询计划已走新索引
    plan = conn.execute(
        "EXPLAIN QUERY PLAN SELECT exam_id FROM exam_scores "
        "WHERE student_no='20260001' AND subject='总分'").fetchall()
    print('查询计划:', plan)
    conn.close()
    print('完成。请重启应用。')


if __name__ == '__main__':
    main()
