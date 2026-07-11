"""更新 init_db.py 中的学生数据：女走读->走读"""
import os

script_dir = os.path.dirname(os.path.abspath(__file__))
file_path = os.path.join(script_dir, 'init_db.py')

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 替换学生数据中的值
content = content.replace("'女走读'", "'走读'")
content = content.replace("'男走读'", "'走读'")

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print('init_db.py 中的学生数据已更新')

# StuLink v1.6.1 2026-07-09
# Copyright (c) 2026 zkxxzf. Apache License 2.0


