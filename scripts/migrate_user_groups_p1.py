"""P1 存量库迁移：建立「用户 ↔ 权限组」多身份关联表，并把旧字段灌入。

运行：python scripts/migrate_user_groups_p1.py
（新装库无需运行：db.create_all() 会自动建表，且用户创建路径已双写；
  存量库因 users.permission_group_id 已落库，需跑一次把关系补进新表）

背景（权限体系多身份改造 · P1）：
  users.permission_group_id 原为单值外键，一人只能有一个身份组。
  本脚本建立 user_permission_groups 关联表，把每个用户当前所属组迁入，
  并标记为「主组」（is_primary=True）。

行为等价保证：
  · 迁移只是把 permission_group_id「复制」为一条 is_primary 关联，
    不改变任何用户的功能权限与数据范围；
  · 幂等：已存在关联则跳过；
  · 改 system.db 前经 scripts._db_backup.backup_db 备份（与项目约定一致）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from app.models import User, UserPermissionGroup
from scripts._db_backup import backup_db


def main():
    app = create_app()
    with app.app_context():
        db.create_all()
        print('[OK] 数据表已确保（含 user_permission_groups）')

        users = User.query.all()
        existing = {(l.user_id, l.group_id) for l in UserPermissionGroup.query.all()}
        pending = []
        for u in users:
            gid = u.permission_group_id
            if not gid:
                continue
            if (u.id, gid) in existing:
                continue
            pending.append((u, gid))

        if not pending:
            print('[SKIP] 关联表已是最新，无需改动')
            return

        from flask import current_app
        uri = current_app.config['SQLALCHEMY_DATABASE_URI']
        db_path = uri.replace('sqlite:///', '')
        if os.path.exists(db_path):
            backup_db(db_path)
            print('[OK] 已备份 system.db')

        # 同一用户若有多条待迁（不应发生，permission_group_id 单值），只保留一条为主组
        for u, gid in pending:
            db.session.add(UserPermissionGroup(user_id=u.id, group_id=gid,
                                               is_primary=True))
        db.session.commit()
        print(f'[DONE] 已为 {len(pending)} 个用户建立主组关联')

        # 校验
        n_links = UserPermissionGroup.query.count()
        n_users_with_group = User.query.filter(
            User.permission_group_id.isnot(None)).count()
        print(f'[CHECK] 关联行数={n_links}，有组用户数={n_users_with_group}')


if __name__ == '__main__':
    main()
