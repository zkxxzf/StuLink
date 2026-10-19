# -*- coding: utf-8 -*-
"""v1.19.8.0 一次性脚本：新增「成绩单改分」权限 grades.score_edit

口径（用户 2026-10-11 确认）：
- 「考务管理员」**单独建权限组**，不默认挂在教务主任/教务员上；
- 现阶段先用内置 admin 测试（管理员角色天然 bypass，无需授权）；
- 因此本脚本**只保证管理员组含该 key**，并从教务主任/教务员上**撤销**早前误加的授权。
- 之后要开给「考务管理员」：新建/指定权限组 → 在「系统管理 → 权限管理」页
  勾选「成绩管理 → 成绩修改（改分）」。

新安装无需运行：permission_map 已把该项标为 standalone（不随模块写入批发），
PERMISSION_GROUPS 中仅管理员组显式包含该 key。

运行：python scripts/migrate/migrate_score_edit_perm_20261011.py
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

# 2026-10-14 第四轮审计（CONS-02）：补迁移登记（只登记不早退）
SCRIPT_NAME = os.path.basename(__file__)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import _mig_log
except Exception as _e:  # noqa: BLE001
    _mig_log = None
    print(f'[WARN] 迁移登记模块不可用（迁移照常执行，仅不登记）：{_e}')


def _record():
    if _mig_log:
        _mig_log.record(SCRIPT_NAME, note='权限组同步 grades.score_edit（仅管理员组，撤销教务主任/教务员）')


KEY = 'grades.score_edit'
GRANT_GROUPS = {'管理员组'}          # 只有管理员组默认持有
REVOKE_GROUPS = {'教务主任', '教务员'}   # 早前误加，予以撤销


def _set_key(g, present):
    keys = list(g.get_menu_keys() or [])
    has = KEY in keys
    if present and not has:
        keys.append(KEY)
        g.set_menu_keys(keys)
        return 'grant'
    if not present and has:
        keys.remove(KEY)
        g.set_menu_keys(keys)
        return 'revoke'
    return ''


def main():
    app = create_app()
    with app.app_context():
        ng = nr = 0
        for g in PermissionGroup.query.all():
            if g.name in GRANT_GROUPS:
                if _set_key(g, True) == 'grant':
                    ng += 1
                    print(f'[OK] {g.name} 已追加 {KEY}')
            elif g.name in REVOKE_GROUPS:
                if _set_key(g, False) == 'revoke':
                    nr += 1
                    print(f'[OK] {g.name} 已撤销 {KEY}')
        db.session.commit()
        print(f'[DONE] 授权 {ng} 个组，撤销 {nr} 个组')
        for g in PermissionGroup.query.all():
            if g.name in (GRANT_GROUPS | REVOKE_GROUPS):
                print('   %s -> %s' % (g.name, KEY in (g.get_menu_keys() or [])))
    _record()


if __name__ == '__main__':
    main()
