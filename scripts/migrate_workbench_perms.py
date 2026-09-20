"""补齐工作台权限 key（幂等，可重复执行）

背景（PR#5 安全审查 M1/M6）：
- 班主任工作台新增 6 个权限 key（workbench.records / class_view / attendance_view /
  attendance / notifications_view / notifications），同时 points.import / points.export /
  points.rules 也需落到权限组上；
- app/__init__.py 的种子只在 permission_groups 表为空时写入，已部署的库不会自动补；
- scripts/init_permission_groups.py 的预设与代码种子曾不一致。

本脚本按「组名 -> 种子权限」优先、按「角色 -> 默认权限」兜底的方式，为已存在的权限组
补齐缺失的 key（只增不删），使「菜单可见」与「路由可访问」一致，避免点进去 403。

用法：python scripts/migrate_workbench_perms.py
"""
import sys
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _db_backup import backup_db  # noqa: E402  改库前先备份（项目约定）

# permission_groups 表落在主库 system.db
SYSTEM_DB = os.path.join(BASE, 'data', 'system.db')

from app import create_app, PERMISSION_GROUPS  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import PermissionGroup  # noqa: E402

# 角色兜底映射（组名不在种子里时使用）
ROLE_FALLBACK = {
    'admin': ['workbench.records', 'workbench.class_view',
              'workbench.attendance_view', 'workbench.attendance',
              'workbench.notifications_view', 'workbench.notifications'],
    'grade_leader': ['workbench.class_view', 'workbench.attendance_view',
                     'workbench.notifications_view'],
    'homeroom_teacher': ['workbench.records', 'workbench.class_view',
                         'workbench.attendance_view', 'workbench.attendance',
                         'workbench.notifications_view'],
    'teacher': ['workbench.class_view', 'workbench.notifications_view'],
    # 校级只读身份（如领导组）：工作台只读
    'school_viewer': ['workbench.class_view', 'workbench.attendance_view',
                      'workbench.notifications_view'],
    'viewer': ['workbench.class_view', 'workbench.notifications_view'],
    # 通知收件箱是看自己的通知，所有登录身份都保留
    'dorm_manager': ['workbench.notifications_view'],
    'staff': ['workbench.notifications_view'],
}

# 任何身份都至少保留收件箱只读（与 app/__init__.py 的兜底保持一致）
UNIVERSAL = ['workbench.notifications_view']


def _seed_keys_by_name():
    """组名 -> 需要对齐的权限 key（工作台 6 个 + 积分导入/导出/规则 3 个）"""
    return {
        p['name']: [k for k in p['menu_keys']
                    if k.startswith('workbench.') or k.startswith('points.')]
        for p in PERMISSION_GROUPS
    }


def main():
    # 改库前先备份：本脚本会改写 permission_groups.menu_keys
    backup_db(SYSTEM_DB)

    app = create_app()
    seed = _seed_keys_by_name()
    changed = 0
    with app.app_context():
        for g in PermissionGroup.query.all():
            keys = list(g.get_menu_keys())
            if g.role == 'admin' or g.name == '管理员组':
                want = list(ROLE_FALLBACK['admin'])
                for k in seed.get('管理员组', []):
                    if k not in want:
                        want.append(k)
            elif g.name in seed:
                want = list(seed[g.name])
            else:
                want = list(ROLE_FALLBACK.get(g.role or '', []))
                for k in UNIVERSAL:
                    if k not in want:
                        want.append(k)

            added = [k for k in want if k not in keys]
            if not added:
                continue
            g.set_menu_keys(keys + added)
            changed += 1
            print('[+] %s（role=%s）补齐: %s' % (g.name, g.role, ', '.join(added)))
        if changed:
            db.session.commit()
        print('Done. 更新 %d 个权限组。' % changed)


if __name__ == '__main__':
    main()
