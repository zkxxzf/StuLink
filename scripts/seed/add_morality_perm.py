"""一次性脚本：给现有权限组追加德育奖惩权限（morality.view / morality.edit）

运行：python scripts/seed/add_morality_perm.py
（新安装无需运行：app/__init__.py PERMISSION_GROUPS 已包含这些 key；
  存量库因权限组 menu_keys 已落库，需跑一次才能看到菜单）

授予口径：
  管理员组        view + edit（全校）
  年级长组        view + edit（本年级）
  班主任组        view（本班，只读；登记/处理由德育处统一口径）

幂等：key 已在组内则跳过。改 system.db 前经 scripts/_db_backup.backup_db 备份
（2026-10-13 审计修复 #16：原先直接改权限组无备份，与项目约定不一致）。
"""
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
from app.models import PermissionGroup
from scripts._db_backup import backup_db

# 组名 → 追加的 key 列表
GRANTS = {
    '管理员组': ['morality.view', 'morality.edit'],
    '年级长组': ['morality.view', 'morality.edit'],
    '班主任组': ['morality.view'],
}


def main():
    app = create_app()
    with app.app_context():
        db.create_all()
        print('[OK] 数据表已确保')

        # 先算出哪些组真的要改，只有确有改动才备份（避免重复执行堆备份）
        pending = []
        for g in PermissionGroup.query.all():
            keys = GRANTS.get(g.name)
            if not keys:
                continue
            cur = g.get_menu_keys() or []
            added = [k for k in keys if k not in cur]
            if added:
                pending.append((g, cur, added))
        if pending:
            from flask import current_app
            uri = current_app.config['SQLALCHEMY_DATABASE_URI']
            db_path = uri.replace('sqlite:///', '')
            if os.path.exists(db_path):
                backup_db(db_path)
                print('[OK] 已备份 system.db')

        for g, cur, added in pending:
            g.set_menu_keys(cur + added)
            print(f'[OK] {g.name} 已追加 {added}')
        db.session.commit()
        print(f'[DONE] 共更新 {len(pending)} 个权限组')


if __name__ == '__main__':
    main()
