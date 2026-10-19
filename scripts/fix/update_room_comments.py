"""更新 init_db.py 中的注释：男生宿舍->西宿舍楼，女生宿舍->东宿舍楼"""
import os as _os
import sys as _sys
_HERE = _os.path.dirname(_os.path.abspath(__file__))
_SCRIPTS = _os.path.dirname(_HERE)
_ROOT = _os.path.dirname(_SCRIPTS)
for _p in (_ROOT, _SCRIPTS):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

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

# StuLink v1.7.0 2026-08-02
# Copyright (c) 2026 zkxxzf. Apache License 2.0


