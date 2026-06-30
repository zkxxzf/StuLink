# StuLink v1.4.5 2026-06-30
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0
import sqlite3
import os

db_path = r'c:\Users\lenovo\Desktop\宿舍管理系统 - Lingma\data\dormitory.db'

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# 1. 删除旧的字典项
cursor.execute("DELETE FROM dict_items WHERE category_id = (SELECT id FROM dict_categories WHERE code = 'boarding_type')")

# 2. 获取 category_id
cursor.execute("SELECT id FROM dict_categories WHERE code = 'boarding_type'")
cat_id = cursor.fetchone()[0]

# 3. 插入新的字典项
cursor.execute("INSERT INTO dict_items (category_id, value, sort_order, is_active) VALUES (?, ?, ?, ?)", (cat_id, '住校', 1, 1))
cursor.execute("INSERT INTO dict_items (category_id, value, sort_order, is_active) VALUES (?, ?, ?, ?)", (cat_id, '走读', 2, 1))
cursor.execute("INSERT INTO dict_items (category_id, value, sort_order, is_active) VALUES (?, ?, ?, ?)", (cat_id, '离校', 3, 1))

# 4. 更新学生记录
cursor.execute("UPDATE students SET boarding_type = '走读' WHERE boarding_type IN ('男走读', '女走读')")

# 5. 统计
cursor.execute("SELECT COUNT(*) FROM students WHERE boarding_type = '走读'")
count = cursor.fetchone()[0]

conn.commit()
conn.close()

print(f'字典已更新：住校、走读、离校')
print(f'共有 {count} 名学生为走读类型')
print('\n更新完成！')
