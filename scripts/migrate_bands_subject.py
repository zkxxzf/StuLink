# -*- coding: utf-8 -*-
"""迁移 exam_bands 表：新增 subject 列，唯一键改为 (exam_id, direction, subject, seq)

SQLite 不支持直接修改唯一约束，故采用「建新表 → 复制 → 重命名」方式。
已有数据全部视为 subject='总分'（与迁移前语义一致），幂等可重复执行。
"""
import os
import sqlite3
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, 'data', 'grades.db')

NEW_DDL = """
CREATE TABLE exam_bands_new (
    id INTEGER NOT NULL,
    exam_id INTEGER NOT NULL,
    direction VARCHAR(4),
    subject VARCHAR(10) NOT NULL DEFAULT '总分',
    seq INTEGER NOT NULL,
    name VARCHAR(20) NOT NULL,
    lower_mode VARCHAR(8) NOT NULL,
    lower_value FLOAT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_band_seq UNIQUE (exam_id, direction, subject, seq)
)
"""


def main():
    if not os.path.exists(DB):
        print(f'未找到 {DB}，跳过（首次启动会自动建表）')
        return 0
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='exam_bands'")
    row = cur.fetchone()
    if row is None:
        print('exam_bands 表不存在，无需迁移')
        conn.close()
        return 0
    if 'subject' in (row[0] or '') and 'exam_id, direction, subject, seq' in row[0]:
        print('exam_bands 已迁移（含 subject 列与新唯一键），跳过')
        conn.close()
        return 0

    before = cur.execute('SELECT count(1) FROM exam_bands').fetchone()[0]
    try:
        cur.execute('DROP TABLE IF EXISTS exam_bands_new')
        cur.execute(NEW_DDL)
        cur.execute("""
            INSERT INTO exam_bands_new
                (id, exam_id, direction, subject, seq, name, lower_mode, lower_value)
            SELECT id, exam_id, COALESCE(direction, ''), '总分', seq, name,
                   lower_mode, lower_value
            FROM exam_bands
        """)
        cur.execute('DROP TABLE exam_bands')
        cur.execute('ALTER TABLE exam_bands_new RENAME TO exam_bands')
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f'迁移失败，已回滚：{e}')
        return 1
    after = cur.execute('SELECT count(1) FROM exam_bands').fetchone()[0]
    conn.close()
    print(f'迁移完成：exam_bands {before} 行 → {after} 行（全部标记为 总分）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
