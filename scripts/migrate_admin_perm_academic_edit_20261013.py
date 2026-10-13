"""一次性脚本：给「管理员组」补回漏列的 `academic.edit`

运行：python scripts/migrate_admin_perm_academic_edit_20261013.py
（新安装无需运行：app/__init__.py PERMISSION_GROUPS 已含该 key；
  存量库因权限组 menu_keys 已落库，需跑一次才会生效）

背景（2026-10-13 权限体系文档整理时发现）：
管理员组种子 `menu_keys` 漏列 `academic.edit`（教务·「录入管理」子功能）——
全量注册表 56 个权限点里，管理员组只列了 55 个，唯一缺口即 `academic.edit`。
后果：权限矩阵中「管理员组 × 教务管理 × 录入管理」显示为**不可见**，
与该组"拥有全部权限"的描述不符；其余 40 个子功能显示正常。

影响面：**纯显示修正**。管理员 `role='admin'`，`has_perm()` 恒为 True、绕过 menu_keys，
故本脚本不改变任何实际权限（既不收敛也不放开），只让权限页显示与实际一致。

幂等：key 已在组内则跳过。改 system.db 前经 scripts/_db_backup.backup_db 备份
（与项目约定一致：改权限组前必须备份）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from app.models import PermissionGroup
from scripts._db_backup import backup_db

GROUP = '管理员组'
KEY = 'academic.edit'


def main():
    app = create_app()
    with app.app_context():
        db.create_all()
        print('[OK] 数据表已确保')

        g = PermissionGroup.query.filter_by(name=GROUP).first()
        if g is None:
            print(f'[SKIP] 未找到权限组「{GROUP}」（库可能未初始化，请先建库/初始化权限组）')
            return

        cur = list(g.get_menu_keys() or [])
        if KEY in cur:
            print(f'[SKIP] {GROUP} 已含 {KEY}，无需改动')
            return

        from flask import current_app
        uri = current_app.config['SQLALCHEMY_DATABASE_URI']
        db_path = uri.replace('sqlite:///', '')
        if os.path.exists(db_path):
            backup_db(db_path)
            print('[OK] 已备份 system.db')

        g.set_menu_keys(cur + [KEY])
        db.session.commit()
        print(f'[OK] {GROUP} 已追加 [{KEY}]（共 {len(cur) + 1} 个 key）')
        print('[DONE] 纯显示修正：管理员 has_perm 恒 True，实际权限不受影响')


if __name__ == '__main__':
    main()
