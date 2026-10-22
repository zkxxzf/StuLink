# -*- coding: utf-8 -*-
"""清理审计残留（TMP 前缀的临时账号/学生/字典项/归档记录）。

独立 sqlite3 连接：避免 ORM 同 session 先读后写在 SQLite 上读锁升级失败。
"""
import os
import sqlite3
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(_ROOT)

TASKS = [
    ('system', "DELETE FROM users WHERE username LIKE 'TMP%'"),
    ('system', "DELETE FROM students WHERE student_number LIKE 'TMP%'"),
    ('system', "DELETE FROM dict_items WHERE value LIKE 'TMP%'"),
    ('system', "DELETE FROM trash_records WHERE summary LIKE '%TMP%' "
               "OR summary LIKE '%审计%'"),
    ('grades', "DELETE FROM exam_affairs WHERE name LIKE 'TMP%'"),
]

for db, sql in TASKS:
    try:
        con = sqlite3.connect(f'data/{db}.db', timeout=15)
        n = con.execute(sql).rowcount
        con.commit()
        con.close()
        print(f'  {db}: -{n} 行 ← {sql[:46]}')
    except Exception as e:                     # noqa: BLE001
        print(f'  {db}: 跳过（{type(e).__name__}: {e}）')

print('清理完成')
sys.exit(0)
