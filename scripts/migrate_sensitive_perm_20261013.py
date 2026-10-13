"""一次性脚本：同步「敏感字段字段级权限 + 最小权限重排」到存量库权限组

运行：python scripts/migrate_sensitive_perm_20261013.py
（新安装无需运行：app/__init__.py PERMISSION_GROUPS 已是新基线；
  存量库因权限组 menu_keys 已落库，需跑一次才会生效）

背景（2026-10-13 权限审计）：
  1. 学生身份证号 / 联系电话此前只有「导出」维度有权限点，**页面展示**完全没有控制 ——
     只要拿到 students.view 就能看到明文。新增三个字段级权限点（permission_map 中
     standalone=True，不随模块写入批发）：
       students.view_id_card      查看身份证号明文
       students.view_phone        查看联系电话明文
       academic.view_teacher_phone 查看教师手机号明文
     无权限时页面/接口一律返回脱敏值（app/utils/sensitive.py）。
  2. 最小权限重排：收掉年级长/班主任的敏感导出与批量导入/跨班调动等越权项。

授予口径：
  追加（字段级查看）：
    管理员组    view_id_card + view_phone + academic.view_teacher_phone（唯一明文豁免）
    班主任组    view_phone（本班联系电话明文；身份证仍脱敏）
    宿管组      view_phone（联系住宿学生；身份证仍脱敏）
  移除（越权）：
    年级长组    students.import / students.export_id_card / students.export_phone
    班主任组    students.import / students.transfer / students.export_id_card /
                students.export_phone / students.export_graduation_school /
                students.export_enrollment

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
    '管理员组': ['students.view_id_card', 'students.view_phone',
                 'academic.view_teacher_phone'],
    '班主任组': ['students.view_phone'],
    '宿管组': ['students.view_phone'],
}

# 组名 → 需要移除的 key（最小权限重排）
REMOVE = {
    '年级长组': ['students.import',
                 'students.export_id_card', 'students.export_phone'],
    '班主任组': ['students.import', 'students.transfer',
                 'students.export_id_card', 'students.export_phone',
                 'students.export_graduation_school',
                 'students.export_enrollment'],
}


def main():
    app = create_app()
    with app.app_context():
        db.create_all()
        print('[OK] 数据表已确保')

        # 先算出哪些组真的要改，只有确有改动才备份（避免重复执行堆备份）
        pending = []
        for g in PermissionGroup.query.all():
            cur = list(g.get_menu_keys() or [])
            add = [k for k in ADD.get(g.name, []) if k not in cur]
            rm = [k for k in REMOVE.get(g.name, []) if k in cur]
            if add or rm:
                pending.append((g, cur, add, rm))

        if not pending:
            print('[SKIP] 所有权限组已是最新口径，无需改动')
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


if __name__ == '__main__':
    main()
