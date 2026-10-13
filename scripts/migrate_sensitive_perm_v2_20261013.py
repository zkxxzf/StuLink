"""一次性脚本：按用户口径收敛「学生身份证号 / 联系电话」明文查看权限

运行：python scripts/migrate_sensitive_perm_v2_20261013.py
（新安装无需运行：app/__init__.py PERMISSION_GROUPS 已是新基线；
  存量库因权限组 menu_keys 已落库，需跑一次才会生效）

用户口径（2026-10-13）：
  「系统默认只有管理员和班主任可以看到身份证号和电话，其他人都不需要。」

变更（相对上一版 migrate_sensitive_perm_20261013.py）：
  班主任组  追加 students.view_id_card（原先只有 view_phone，身份证脱敏）
  宿管组    移除 students.view_phone（原为「联系住宿学生」给的，现按口径收回）
  管理员组  不变（仍是唯一三项齐全的明文豁免）

收敛后明文查看权分布（学生字段）：
  students.view_id_card  →  管理员组、班主任组（限本班）
  students.view_phone    →  管理员组、班主任组（限本班）
  其余身份（年级长/宿管/教务/校长/任课教师等）→ 一律脱敏展示。

幂等：追加项已在组内则跳过、移除项不存在则跳过。
改 system.db 前经 scripts/_db_backup.backup_db 备份（与项目约定一致）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from app.models import PermissionGroup
from scripts._db_backup import backup_db

# 组名 → 需要追加的 key
ADD = {
    '班主任组': ['students.view_id_card'],
}

# 组名 → 需要移除的 key
REMOVE = {
    '宿管组': ['students.view_phone'],
}


def main():
    app = create_app()
    with app.app_context():
        db.create_all()
        print('[OK] 数据表已确保')

        pending = []
        for g in PermissionGroup.query.all():
            cur = list(g.get_menu_keys() or [])
            add = [k for k in ADD.get(g.name, []) if k not in cur]
            rm = [k for k in REMOVE.get(g.name, []) if k in cur]
            if add or rm:
                pending.append((g, cur, add, rm))

        if not pending:
            print('[SKIP] 所有权限组已符合口径，无需改动')
            return

        from flask import current_app
        uri = current_app.config['SQLALCHEMY_DATABASE_URI']
        db_path = uri.replace('sqlite:///', '')
        if os.path.exists(db_path):
            backup_db(db_path)
            print('[OK] 已备份 system.db')

        for g, cur, add, rm in pending:
            new_keys = [k for k in cur if k not in rm] + add
            g.set_menu_keys(new_keys)
            parts = []
            if add:
                parts.append(f'追加 {add}')
            if rm:
                parts.append(f'移除 {rm}')
            print(f'[OK] {g.name} 已更新：' + '；'.join(parts))
        db.session.commit()
        print(f'[DONE] 共更新 {len(pending)} 个权限组')
        print('[提示] 明文查看权现仅管理员组与班主任组持有；其余身份一律脱敏。')


if __name__ == '__main__':
    main()
