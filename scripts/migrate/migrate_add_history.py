"""创建 history.db 及 assignment_history 表"""
import os as _os
import sys as _sys
_HERE = _os.path.dirname(_os.path.abspath(__file__))
_SCRIPTS = _os.path.dirname(_HERE)
_ROOT = _os.path.dirname(_SCRIPTS)
for _p in (_ROOT, _SCRIPTS):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import os
import sys
sys.path.insert(0, _ROOT)

from app import create_app
from app.extensions import db
from config import BASE_DIR

app = create_app()
with app.app_context():
    db.create_all(bind_key='history')
    print('[OK] history.db 及 assignment_history 表创建完成')
    db_path = os.path.join(BASE_DIR, 'data', 'history.db')
    print(f'     位置: {db_path}')

# StuLink v1.7.0 2026-08-02
# Copyright (c) 2026 zkxxzf. Apache License 2.0


