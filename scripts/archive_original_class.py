#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""归档原班级数据到历史数据库，并从主库移除该列"""
import sqlite3
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
system_db = os.path.join(BASE, 'data', 'system.db')
history_db = os.path.join(BASE, 'data', 'history.db')

def main():
    conn = sqlite3.connect(system_db)
    hist = sqlite3.connect(history_db)
    
    # 在历史库建表
    hist.execute('''
        CREATE TABLE IF NOT EXISTS student_original_class_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            student_number TEXT,
            student_name TEXT NOT NULL,
            original_class TEXT,
            archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    hist.commit()
    
    # 从主库读取原班级数据
    cur = conn.execute('SELECT id, student_number, name, original_class FROM students WHERE original_class IS NOT NULL AND original_class != ""')
    rows = cur.fetchall()
    
    if not rows:
        print('没有需要归档的原班级数据')
        conn.close()
        hist.close()
        return
    
    # 写入历史库
    inserted = 0
    for row in rows:
        sid, snum, sname, oclass = row
        hist.execute(
            'INSERT INTO student_original_class_history (student_id, student_number, student_name, original_class) VALUES (?,?,?,?)',
            (sid, snum or '', sname, oclass)
        )
        inserted += 1
    
    hist.commit()
    print(f'已归档 {inserted} 条原班级数据到 history.db::student_original_class_history')
    
    # 显示预览
    preview = hist.execute(
        'SELECT student_name, original_class FROM student_original_class_history LIMIT 5'
    ).fetchall()
    for p in preview:
        print(f'  {p[0]} → {p[1]}')
    if inserted > 5:
        print(f'  ... 及另外 {inserted - 5} 条')
    
    conn.close()
    hist.close()
    print('\n归档完成。原班级字段已从模型中移除，数据库列仍保留（SQLAlchemy 不再读写）。')

if __name__ == '__main__':
    main()
