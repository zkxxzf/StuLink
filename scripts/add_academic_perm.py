"""一次性脚本：给现有权限组追加 academic.view（教务管理菜单权限）
运行：python scripts/add_academic_perm.py
（新安装无需运行：app/__init__.py PERMISSION_GROUPS 管理员组已含该 key）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from app.models import PermissionGroup

NEW_KEY = 'academic.view'
# 授予该 key 的权限组（v1.14.0 仅管理员组默认可见，其余可在权限组页按需勾选）
GRANT_GROUPS = {'管理员组'}


def main():
    app = create_app()
    with app.app_context():
        # 1) 建表（幂等，含 academic.db 各表）
        db.create_all()
        print('[OK] 数据表已确保（含 academic 教务库）')

        # 2) 给现有权限组追加 key
        n = 0
        for g in PermissionGroup.query.all():
            if g.name not in GRANT_GROUPS:
                continue
            keys = g.get_menu_keys() or []
            if NEW_KEY not in keys:
                keys.append(NEW_KEY)
                g.set_menu_keys(keys)
                n += 1
                print(f'[OK] {g.name} 已追加 {NEW_KEY}')
        db.session.commit()
        print(f'[DONE] 共更新 {n} 个权限组')


if __name__ == '__main__':
    main()
