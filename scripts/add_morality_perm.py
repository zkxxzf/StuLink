"""一次性脚本：给现有权限组追加德育奖惩权限（morality.view / morality.edit）

运行：python scripts/add_morality_perm.py
（新安装无需运行：app/__init__.py PERMISSION_GROUPS 已包含这些 key；
  存量库因权限组 menu_keys 已落库，需跑一次才能看到菜单）

授予口径：
  管理员组        view + edit（全校）
  年级长组        view + edit（本年级）
  班主任组        view（本班，只读；登记/处理由德育处统一口径）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from app.models import PermissionGroup

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

        n = 0
        for g in PermissionGroup.query.all():
            keys = GRANTS.get(g.name)
            if not keys:
                continue
            cur = g.get_menu_keys() or []
            added = [k for k in keys if k not in cur]
            if not added:
                print(f'[OK] {g.name} 已包含 {keys}，跳过')
                continue
            g.set_menu_keys(cur + added)
            n += 1
            print(f'[OK] {g.name} 已追加 {added}')
        db.session.commit()
        print(f'[DONE] 共更新 {n} 个权限组')


if __name__ == '__main__':
    main()
