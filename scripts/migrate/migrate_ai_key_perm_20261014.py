"""一次性脚本：新增「AI 全局 Key」独立权限点并补齐存量组（2026-10-14 第四轮审计 SEC-04）。

运行：python scripts/migrate/migrate_ai_key_perm_20261014.py
（新安装无需运行：app/__init__.py PERMISSION_GROUPS 已是新基线；
  存量库的权限组 menu_keys 已落库，需跑一次才会获得新权限点）

背景（SEC-04）：`/grades/ai/global-key` 三个端点的装饰器原为 `grades.view`，
真正的写门禁 `_guard_key_manage()`（admin 或 system.users）藏在函数体内 ——
一次重构漏拷守卫即让只读账号改写**全局** AI Key。修复：
  · 新权限点 `grades.ai_manage`（standalone，权限页「成绩管理 → AI 全局 Key」单独勾选）；
  · 装饰器与函数体守卫统一用该权限点；
  · 本脚本给「原本能管全局 Key 的人」补齐该 key，行为不倒退：
      - menu_keys 含 `system.users` 的组（原 _guard_key_manage 放行口径）；
      - role='admin' 的管理员组（has_perm 恒真，补上让权限页显示一致）。

幂等：key 已在组内则跳过；改 system.db 前经 scripts/_db_backup.backup_db 备份。
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
from scripts._db_backup import backup_db

SCRIPT_NAME = os.path.basename(__file__)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import _mig_log
except Exception as _e:  # noqa: BLE001
    _mig_log = None
    print(f'[WARN] 迁移登记模块不可用（迁移照常执行，仅不登记）：{_e}')

TARGET_KEY = 'grades.ai_manage'
SOURCE_KEY = 'system.users'      # 原 _guard_key_manage 的放行口径


def _record():
    if _mig_log:
        _mig_log.record(SCRIPT_NAME,
                        note='SEC-04：新增 grades.ai_manage 权限点并补齐存量组')


def main():
    app = create_app()
    with app.app_context():
        db.create_all()
        print('[OK] 数据表已确保')

        pending = []
        for g in PermissionGroup.query.all():
            cur = list(g.get_menu_keys() or [])
            if TARGET_KEY in cur:
                continue
            if g.role == 'admin' or SOURCE_KEY in cur:
                pending.append((g, cur))

        if not pending:
            print('[SKIP] 所有权限组已持有 grades.ai_manage，无需改动')
            _record()
            return

        from flask import current_app
        uri = current_app.config['SQLALCHEMY_DATABASE_URI']
        db_path = uri.replace('sqlite:///', '')
        if os.path.exists(db_path):
            backup_db(db_path)
            print('[OK] 已备份 system.db')

        for g, cur in pending:
            g.set_menu_keys(cur + [TARGET_KEY])
            why = 'admin' if g.role == 'admin' else f'含 {SOURCE_KEY}'
            print(f'[OK] {g.name}（{why}）已追加 {TARGET_KEY}')
        db.session.commit()
        print(f'[DONE] 共更新 {len(pending)} 个权限组')
        print('[提示] 其余身份需在「权限管理」页按需单独勾选「AI 全局 Key」。')
        _record()


if __name__ == '__main__':
    main()
