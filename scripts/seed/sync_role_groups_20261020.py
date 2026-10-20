"""存量库权限组同步：校长 / 教务主任 / 年级长（2026-10-20 用户口径）。

口径：
- 校长：全校查看 + 全校事务审批（调课/成绩复核/业绩表单/德育）；
- 教务主任：管理教务和成绩（全校），含调课一级审批与成绩复核（改分）审批；
- 年级长：管理本年级事务（含本年级调课一级审批），保持既有配置。

幂等：按 `app.PERMISSION_GROUPS` 种子为准 —— 组不存在则创建；已存在只
**补缺失键**（不删除现有自定义权限），并对校长组修正 role/scope_type。

运行：py -3.12 scripts/seed/sync_role_groups_20261020.py
      （新装库无需运行：create_all 时 `_init_system_data` 已按种子建组）
"""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_SCRIPTS = _os.path.dirname(_HERE)
_ROOT = _os.path.dirname(_SCRIPTS)
for _p in (_ROOT, _SCRIPTS):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

from app import create_app, PERMISSION_GROUPS  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import PermissionGroup  # noqa: E402

TARGETS = ('校长', '教务主任', '教务员', '年级长组')
SCRIPT_NAME = _os.path.basename(__file__)

try:
    import _mig_log
except Exception as _e:  # noqa: BLE001
    _mig_log = None
    print(f'[WARN] 迁移登记模块不可用（照常同步，仅不登记）：{_e}')


def main():
    app = create_app()
    with app.app_context():
        if _mig_log is not None and _mig_log.is_applied(SCRIPT_NAME):
            print(f'[SKIP] {SCRIPT_NAME} 已登记为已应用，跳过同步')
            return 0
        seeds = {p['name']: p for p in PERMISSION_GROUPS if p['name'] in TARGETS}
        changed = 0
        for name in TARGETS:
            p = seeds.get(name)
            if not p:
                print(f'[WARN] 种子中缺少「{name}」，跳过')
                continue
            g = PermissionGroup.query.filter_by(name=name).first()
            if g is None:
                g = PermissionGroup(
                    name=name,
                    role=p.get('role', 'staff'),
                    scope_type=p.get('scope_type', 'none'),
                    scope_kind=p.get('scope_kind'),
                    description=p.get('description', ''),
                )
                g.set_menu_keys(list(p['menu_keys']))
                db.session.add(g)
                changed += 1
                print(f'[OK] 新增身份组：{name}（{len(p["menu_keys"])} 个权限）')
                continue
            # 已有组：补缺失键 + 修正校长组的 role / 范围（不动其它自定义配置）
            keys = list(g.get_menu_keys() or [])
            add = [k for k in p['menu_keys'] if k not in keys]
            if add:
                g.set_menu_keys(keys + add)
                changed += 1
                print(f'[OK] {name}：补充 {len(add)} 个权限 -> {", ".join(add)}')
            if name == '校长':
                fix = False
                if (g.role or '') != 'principal':
                    g.role = 'principal'
                    fix = True
                if (g.scope_type or '') != 'school':
                    g.scope_type = 'school'
                    fix = True
                if (g.scope_kind or '') != 'school':
                    g.scope_kind = 'school'
                    fix = True
                if fix:
                    changed += 1
                    print('[OK] 校长：role=principal / 范围=全校')
            if not add and name != '校长':
                print(f'[SKIP] {name}：无变化')
        # 2026-10-20 审计加固：改的是生产权限组（决定谁能审批/改分），
        # 提交前必须整库备份；备份失败则回滚，不做不可恢复的权限变更。
        if changed:
            from _db_backup import backup_db
            pairs = backup_db(_os.path.join(_ROOT, 'data', 'system.db'),
                              tag='before_sync_role_groups')
            if not pairs or not pairs[0][1]:
                print('中止：system.db 备份失败，本次改动已回滚。')
                db.session.rollback()
                return 2
            print(f'已备份：{_os.path.basename(pairs[0][1])}')
        db.session.commit()
        print(f'[DONE] 共变更 {changed} 项')
        if _mig_log is not None:
            try:
                _mig_log.record(SCRIPT_NAME,
                                note='同步校长/教务主任/教务员/年级长组权限口径')
            except Exception as _e:  # noqa: BLE001
                print(f'[WARN] 迁移登记失败（不影响结果）：{_e}')
        return 0


if __name__ == '__main__':
    _sys.exit(main())
