#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""调课权限角色口径修正（幂等，可重复执行）

背景（2026-10-11 教务模块权限审计，见 docs/规范与说明/教务模块权限说明.md §9 问题 5/6）：
- 问题 5：年级长组种子权限缺 academic.swap，导致年级长本人任课时无法自助提交调课
  申请（/swap/apply 挂 @perm_required('academic.swap')，入口直接 403）。
- 问题 6：教务主任/教务员的教务模块取「写入档」，_module_keys 展开时把
  academic.swap_approve（一级调课审批）一并带上，使其意外成为全校口径的一级审批人；
  而一级审批本应仅由年级长承担。

实测补充：现有部署库的权限组整体比 app/__init__.py 的种子「旧一代」——种子只在
权限组表为空时写入，已部署库不会自动同步。因此库中年级长组同时缺
academic.swap 与 academic.swap_approve（swap_approval_mode 默认 two 时，
一级审批在生产环境形同虚设），而教务主任/教务员恰好也没有 swap_approve。

本脚本对已部署库做三件事（幂等，可重复执行）：
1) 为 role='grade_leader' 的权限组补齐 academic.swap + academic.swap_approve（只增）；
2) 从名为 教务主任/教务员 的权限组移除 academic.swap_approve（只删该一个 key），
   防止将来其权限组被重新写入时带上该 key；
3) 统一调课（2026-10-11 从 academic.swap 拆出为独立权限点 academic.swap_bulk）：
   为管理员组与 教务主任/教务员 补 academic.swap_bulk；普通教师/班主任/年级长
   不再因持有 academic.swap 而获得整批调课能力。

用法：python scripts/migrate_swap_perm_roles_20261011.py
"""
import sys
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _db_backup import backup_db  # noqa: E402  改库前先备份（项目约定）

# permission_groups 表落在主库 system.db
SYSTEM_DB = os.path.join(BASE, 'data', 'system.db')

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import PermissionGroup  # noqa: E402

# 2026-10-14 第四轮审计（CONS-02）：补迁移登记（只登记不早退）
SCRIPT_NAME = os.path.basename(__file__)
try:
    import _mig_log
except Exception as _e:  # noqa: BLE001
    _mig_log = None
    print(f'[WARN] 迁移登记模块不可用（迁移照常执行，仅不登记）：{_e}')


def _record():
    if _mig_log:
        _mig_log.record(SCRIPT_NAME, note='调课权限口径修正：年级长补 swap/swap_approve，教务去一级审批，拆 swap_bulk')


APPLY_KEY = 'academic.swap'            # 自助申请调课
APPROVE_KEY = 'academic.swap_approve'  # 一级调课审批（仅年级长）
BULK_KEY = 'academic.swap_bulk'        # 统一调课（整批），2026-10-11 拆点
EDU_GROUP_NAMES = ('教务主任', '教务员')


def main():
    # 改库前先备份：本脚本会改写 permission_groups.menu_keys
    backup_db(SYSTEM_DB)

    app = create_app()
    changed = 0
    with app.app_context():
        for g in PermissionGroup.query.all():
            before = list(g.get_menu_keys())
            keys = list(before)

            # 1) 年级长组：对齐种子（自助申请调课 + 一级调课审批，只增）
            if g.role == 'grade_leader':
                for _k in (APPLY_KEY, APPROVE_KEY):
                    if _k not in keys:
                        keys.append(_k)

            # 2) 教务主任/教务员：移除一级调课审批权（仅保留终审+执行）
            if g.name in EDU_GROUP_NAMES and APPROVE_KEY in keys:
                keys = [k for k in keys if k != APPROVE_KEY]

            # 3) 统一调课（整批）：仅管理员与教务身份（2026-10-11 由 academic.swap 拆出）
            if (g.role == 'admin' or g.name in EDU_GROUP_NAMES) and BULK_KEY not in keys:
                keys.append(BULK_KEY)

            if keys == before:
                continue
            g.set_menu_keys(keys)
            changed += 1
            added = [k for k in keys if k not in before]
            removed = [k for k in before if k not in keys]
            print('[~] %s（role=%s） 新增=%s 移除=%s'
                  % (g.name, g.role, added or '无', removed or '无'))

        if changed:
            db.session.commit()
        print('Done. 更新 %d 个权限组。' % changed)
    _record()


if __name__ == '__main__':
    main()
