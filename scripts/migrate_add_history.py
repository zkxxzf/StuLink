"""创建 history.db 及 assignment_history 表"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from config import BASE_DIR

app = create_app()
with app.app_context():
    db.create_all(bind_key='history')
    print('[OK] history.db 及 assignment_history 表创建完成')
    db_path = os.path.join(BASE_DIR, 'data', 'history.db')
    print(f'     位置: {db_path}')

# StuLink v1.5.0 2026-07-01
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0
