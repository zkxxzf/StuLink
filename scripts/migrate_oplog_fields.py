"""�?operation_logs 表增�?module �?severity �?""
import sqlite3
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, 'data', 'dormitory.db')

if not os.path.exists(DB_PATH):
    print(f'[SKIP] 数据库文件不存在: {DB_PATH}')
    sys.exit(0)

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

cursor.execute("PRAGMA table_info(operation_logs)")
columns = [col[1] for col in cursor.fetchall()]

if 'module' not in columns:
    cursor.execute("ALTER TABLE operation_logs ADD COLUMN module VARCHAR(30) DEFAULT 'system'")
    print('[OK] 已添�?operation_logs.module �?(DEFAULT system)')
else:
    print('[SKIP] operation_logs.module 列已存在')

if 'severity' not in columns:
    cursor.execute("ALTER TABLE operation_logs ADD COLUMN severity VARCHAR(10) DEFAULT 'INFO'")
    print('[OK] 已添�?operation_logs.severity �?(DEFAULT INFO)')
else:
    print('[SKIP] operation_logs.severity 列已存在')

conn.commit()
conn.close()
print('[DONE] 迁移完成')

# StuLink v1.4.6 2026-06-30
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0
