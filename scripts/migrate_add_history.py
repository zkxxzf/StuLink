"""创建 history.db �?assignment_history �?""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from config import BASE_DIR

app = create_app()
with app.app_context():
    db.create_all(bind_key='history')
    print('[OK] history.db �?assignment_history 表创建完�?)
    db_path = os.path.join(BASE_DIR, 'data', 'history.db')
    print(f'     位置: {db_path}')

# StuLink v1.4.6 2026-06-30
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0
