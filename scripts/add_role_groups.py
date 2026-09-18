"""一次性脚本：为存量安装新增 v1.9.2 身份权限组
（校长/副校长/教务主任/教务员/备课组长/教研组长/学生发展中心）

运行：python scripts/add_role_groups.py
新安装无需运行（app/__init__.py PERMISSION_GROUPS 已含这些身份）。
幂等：已存在的组按名称跳过（不覆盖现有配置）；顺带为管理员组补充 academic.edit。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, PERMISSION_GROUPS  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import PermissionGroup  # noqa: E402

NEW_NAMES = {'校长', '副校长', '教务主任', '教务员', '备课组长', '教研组长',
             '学生发展中心'}


def main():
    app = create_app()
    with app.app_context():
        # 1) 建表（幂等，含 user_data_scopes）
        db.create_all()
        print('[OK] 数据表已确保（含 user_data_scopes）')

        # 2) 新增身份组（从种子定义取配置，避免两处维护）
        presets = [p for p in PERMISSION_GROUPS if p['name'] in NEW_NAMES]
        n = 0
        for p in presets:
            g = PermissionGroup.query.filter_by(name=p['name']).first()
            if g:
                # 幂等修正：种子中为全校范围的组，若历史创建为 none 则更新
                if p.get('scope_type') == 'school' and g.scope_type != 'school':
                    g.scope_type = 'school'
                    print(f'[FIX] {p["name"]} 范围修正为 school')
                else:
                    print(f'[SKIP] {p["name"]} 已存在')
                continue
            g = PermissionGroup(name=p['name'], role=p.get('role', 'staff'),
                                scope_type=p.get('scope_type', 'none'),
                                description=p.get('description', ''))
            g.set_menu_keys(p['menu_keys'])
            db.session.add(g)
            n += 1
            print(f'[OK] 新增身份组：{p["name"]}')

        # 3) 管理员组补充 academic.edit（教务写入拆分用）
        admin_g = PermissionGroup.query.filter_by(name='管理员组').first()
        if admin_g:
            keys = admin_g.get_menu_keys() or []
            if 'academic.edit' not in keys:
                keys.append('academic.edit')
                admin_g.set_menu_keys(keys)
                print('[OK] 管理员组 已补充 academic.edit')
        db.session.commit()
        print(f'[DONE] 共新增 {n} 个身份组')


if __name__ == '__main__':
    main()
