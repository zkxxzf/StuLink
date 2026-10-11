# -*- coding: utf-8 -*-
"""v1.19.7.0 一次性脚本：新增「成绩单改分」权限 grades.score_edit

- 改分（赋分/原始分）从此单独授权：仅管理员（role=admin，天然 bypass）
  与教务处/考务管理员（默认「教务主任」「教务员」）可改；
  年级长等「能看成绩单」的身份默认不再能改分。
- 新安装无需运行：permission_map.MODULES 已含该子功能，
  且 PERMISSION_GROUPS 中管理员组已含该 key、教务主任/教务员经 grades 模块 write 自动获得。

运行：python scripts/migrate_score_edit_perm_20261011.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from app.models import PermissionGroup

NEW_KEY = 'grades.score_edit'
# 默认授予：管理员组 + 教务处（考务管理员）。
# 若本校「考务管理员」是别的权限组，可在「系统管理 → 权限管理」页勾选「成绩修改」。
GRANT_GROUPS = {'管理员组', '教务主任', '教务员'}


def main():
    app = create_app()
    with app.app_context():
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
        # 复核
        for g in PermissionGroup.query.all():
            if g.name in GRANT_GROUPS:
                print('   %s -> %s' % (g.name, NEW_KEY in (g.get_menu_keys() or [])))


if __name__ == '__main__':
    main()
