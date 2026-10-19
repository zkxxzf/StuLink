#!/usr/bin/env python
import os as _os
import sys as _sys
_HERE = _os.path.dirname(_os.path.abspath(__file__))
_SCRIPTS = _os.path.dirname(_HERE)
_ROOT = _os.path.dirname(_SCRIPTS)
for _p in (_ROOT, _SCRIPTS):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import sqlite3
import os

BASE = _ROOT
db_path = os.path.join(BASE, 'data', 'system.db')

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

cursor.execute("SELECT id FROM dict_categories WHERE code='class'")
class_cat_id = cursor.fetchone()[0]

cursor.execute("SELECT value FROM dict_items WHERE category_id=?", (class_cat_id,))
existing_classes = [r[0] for r in cursor.fetchall()]

print(f"现有班级字典项: {existing_classes}")

if '02班' not in existing_classes:
    cursor.execute("SELECT MAX(sort_order) FROM dict_items WHERE category_id=?", (class_cat_id,))
    max_sort = cursor.fetchone()[0] or 0
    
    cursor.execute('''
        INSERT INTO dict_items (category_id, value, sort_order, is_active)
        VALUES (?, ?, ?, ?)
    ''', (class_cat_id, '02班', 1, 1))
    conn.commit()
    print("已添加 '02班' 到字典表")
else:
    print("'02班' 已存在于字典表")

cursor.execute('''
    SELECT di.value, di.sort_order 
    FROM dict_items di 
    WHERE di.category_id = ? 
    ORDER BY di.sort_order
''', (class_cat_id,))
print("\n更新后的班级字典项:")
for row in cursor.fetchall():
    print(f"  {row[0]} (sort={row[1]})")

conn.close()
