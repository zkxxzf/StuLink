"""更新 init_db.py 中的注释：男生宿舍->西宿舍楼，女生宿舍->东宿舍楼"""
import os

script_dir = os.path.dirname(os.path.abspath(__file__))
file_path = os.path.join(script_dir, 'init_db.py')

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 替换注释
content = content.replace('男生宿舍', '西宿舍楼')
content = content.replace('女生宿舍', '东宿舍楼')

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print('init_db.py 已更新完成')

# StuLink v1.6.1 2026-07-09
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0


