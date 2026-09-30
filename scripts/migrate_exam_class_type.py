# -*- coding: utf-8 -*-
"""迁移 exam_scores 表：新增 class_type 列（班型快照）

背景：去差均分等分析实时读 ClassProfile.class_type，班型一改（强基↔卓越）历史考试全部失真。
本迁移只加列（幂等，可重复执行）；列的填充由「成绩导入」自动完成。

历史数据（本迁移之前导入的考试）列值为 NULL —— 分析层做了回落：
    trimmed_nos() 优先用成绩行快照，快照为空时回落读 ClassProfile（过渡兼容）。
待补齐历史班型数据后，可用 scripts/backfill_exam_class_type.py 一次性回填。
"""
import os
import sqlite3
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, 'data', 'grades.db')

COL_DDL = 'ALTER TABLE exam_scores ADD COLUMN class_type VARCHAR(20)'


def main():
    if not os.path.exists(DB):
        print(f'未找到 {DB}，跳过（首次启动会自动建表）')
        return 0
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    row = cur.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='exam_scores'"
    ).fetchone()
    if row is None:
        print('exam_scores 表不存在，无需迁移')
        conn.close()
        return 0

    cols = [r[1] for r in cur.execute('PRAGMA table_info(exam_scores)').fetchall()]
    if 'class_type' in cols:
        n_null = cur.execute('SELECT count(1) FROM exam_scores WHERE class_type IS NULL').fetchone()[0]
        total = cur.execute('SELECT count(1) FROM exam_scores').fetchone()[0]
        print(f'exam_scores 已含 class_type 列，跳过（共 {total} 行，其中 {n_null} 行待回填）')
        conn.close()
        return 0

    before = cur.execute('SELECT count(1) FROM exam_scores').fetchone()[0]
    try:
        cur.execute(COL_DDL)
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f'迁移失败，已回滚：{e}')
        conn.close()
        return 1
    after = cur.execute('SELECT count(1) FROM exam_scores').fetchone()[0]
    cols2 = [r[1] for r in cur.execute('PRAGMA table_info(exam_scores)').fetchall()]
    conn.close()

    if 'class_type' not in cols2:
        print('迁移异常：列未创建成功')
        return 1
    print(f'迁移完成：exam_scores 新增 class_type 列；行数 {before} → {after}（未变动）')
    print('提示：历史考试的 class_type 为 NULL，分析层会回落读班型设置；'
          '补齐历史数据后可运行回填脚本。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
