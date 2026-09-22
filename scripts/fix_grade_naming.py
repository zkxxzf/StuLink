# StuLink v1.17.0 2026-09-21
# 幂等脚本：把 grade='01'/'02'/'03' 修正为 '高一'/'高二'/'高三'
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""修正课表条目的年级命名格式。

旧数据中 grade 字段存储为 '01'/'02'/'03'，
统一为 '高一'/'高二'/'高三' 以与学籍系统保持一致。
幂等：若已无 '01'/'02'/'03' 格式则跳过。
"""
import os
import sys
import sqlite3

# 项目根目录
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMETABLE_DB = os.path.join(ROOT, 'data', 'timetable.db')

GRADE_MAP = {
    '01': '高一',
    '02': '高二',
    '03': '高三',
}


def main():
    if not os.path.exists(TIMETABLE_DB):
        print(f'[SKIP] {TIMETABLE_DB} 不存在，无需修正')
        return

    conn = sqlite3.connect(TIMETABLE_DB)
    cur = conn.cursor()

    # 检查表是否存在
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schedule_entries'")
    if not cur.fetchone():
        print('[SKIP] schedule_entries 表不存在')
        conn.close()
        return

    total_updated = 0
    for old_val, new_val in GRADE_MAP.items():
        # 先检查是否有需要修正的记录
        cur.execute("SELECT COUNT(*) FROM schedule_entries WHERE grade=? AND is_deleted=0", (old_val,))
        count = cur.fetchone()[0]
        if count == 0:
            print(f'[OK] grade={old_val!r} → 无需要修正的记录')
            continue
        cur.execute("UPDATE schedule_entries SET grade=? WHERE grade=? AND is_deleted=0", (new_val, old_val))
        total_updated += cur.rowcount
        print(f'[OK] grade={old_val!r} → {new_val!r}：修正 {cur.rowcount} 条')

    conn.commit()

    # 验证结果
    cur.execute("SELECT grade, COUNT(*) FROM schedule_entries WHERE is_deleted=0 GROUP BY grade ORDER BY grade")
    rows = cur.fetchall()
    print(f'\n[STAT] 修正后 grade 分布（未删除条目）：')
    for g, c in rows:
        print(f'  {g!r}: {c} 条')
    print(f'[DONE] 共修正 {total_updated} 条记录')

    conn.close()


if __name__ == '__main__':
    main()
