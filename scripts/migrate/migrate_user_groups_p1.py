"""P1 存量库迁移：建立「用户 ↔ 权限组」多身份关联表，并把旧字段灌入。

运行：python scripts/migrate/migrate_user_groups_p1.py
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
from app.models import User, UserPermissionGroup
from scripts._db_backup import backup_db

# 2026-10-14 第三轮审计（CONS3-02）：本脚本原属 master 侧"多身份改造"，未按本仓库
# 约定登记迁移；补 `_mig_log` 登记（只 record，不 early-exit —— 脚本自身幂等，
# 每次跑都能核对到达目标状态），这样 `scripts/migration_status.py --pending` 可反查。
SCRIPT_NAME = os.path.basename(__file__)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import _mig_log
except Exception as _e:  # noqa: BLE001
    _mig_log = None
    print(f'[WARN] 迁移登记模块不可用（迁移照常执行，仅不登记）：{_e}')


def _record():
    if _mig_log:
        _mig_log.record(SCRIPT_NAME,
                        note='多身份 P1：users.permission_group_id → user_permission_groups 主组关联')


def main():
    app = create_app()
    with app.app_context():
        db.create_all()
        print('[OK] 数据表已确保（含 user_permission_groups）')

        users = User.query.all()
        existing = {(l.user_id, l.group_id): l
                    for l in UserPermissionGroup.query.all()}
        pending = []        # 需新建的主组关联 (user, gid)
        fix_primary = []    # 已有同组关联但未标 is_primary（2026-10-14 第四轮审计补）
        for u in users:
            gid = u.permission_group_id
            if not gid:
                continue
            link = existing.get((u.id, gid))
            if link is None:
                pending.append((u, gid))
            elif not link.is_primary:
                fix_primary.append(link)

        if not pending and not fix_primary:
            print('[SKIP] 关联表已是最新，无需改动')
            _record()
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
        for link in fix_primary:      # 已有同组关联但未标主组 → 补标记（幂等）
            link.is_primary = True
        db.session.commit()
        print(f'[DONE] 新建主组关联 {len(pending)} 条，补主组标记 {len(fix_primary)} 条')

        # 校验
        n_links = UserPermissionGroup.query.count()
        n_users_with_group = User.query.filter(
            User.permission_group_id.isnot(None)).count()
        print(f'[CHECK] 关联行数={n_links}，有组用户数={n_users_with_group}')
        _record()


if __name__ == '__main__':
    main()
