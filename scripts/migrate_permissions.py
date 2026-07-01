"""迁移脚本：创建权限组+用户班级关联表，种子数据"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from app.models import User, PermissionGroup, UserClassLink

app = create_app()

# 全部菜单键（与base.html侧边栏对应）
ALL_MENU_KEYS = [
    'dashboard',      # 首页概览
    'search',         # 学生查询
    'students',       # 学生列表
    'rooms',          # 宿舍列表
    'assign',         # 分配宿舍
    'beds',           # 床位分配
    'overview',       # 宿舍总览
    'statistics',     # 统计报表
    'users',          # 用户管理
    'dictionary',     # 字典管理
    'class_profile',  # 班型设置
    'perm_groups',    # 权限组管理
]

DEFAULT_GROUPS = [
    {
        'name': '管理员组',
        'scope_type': 'school',
        'menu_keys': ALL_MENU_KEYS,
        'description': '系统管理员，拥有全部权限',
    },
    {
        'name': '班主任组',
        'scope_type': 'class',
        'menu_keys': ['dashboard', 'search', 'students', 'overview', 'statistics', 'beds'],
        'description': '管理所带班级的学生宿舍及床位',
    },
    {
        'name': '年级长组',
        'scope_type': 'grade',
        'menu_keys': ['dashboard', 'search', 'students', 'rooms', 'assign', 'beds', 'overview', 'statistics'],
        'description': '管理所管年级的学生宿舍',
    },
    {
        'name': '全校查看组',
        'scope_type': 'school',
        'menu_keys': ['dashboard', 'search', 'students', 'rooms', 'assign', 'beds', 'overview', 'statistics'],
        'description': '查看全校所有数据',
    },
    {
        'name': '宿管组',
        'scope_type': 'none',
        'menu_keys': ['dashboard', 'search', 'rooms', 'assign', 'beds', 'overview'],
        'description': '宿舍管理教师，管理房间和床位',
    },
]

# 角色 → 权限组映射
ROLE_TO_GROUP = {
    'admin': '管理员组',
    'homeroom_teacher': '班主任组',
    'grade_leader': '年级长组',
    'school_viewer': '全校查看组',
    'dorm_manager': '宿管组',
    'teacher': '班主任组',
}

with app.app_context():
    # 1. 建表
    db.create_all()
    print("✓ 新表已创建")

    # 2. 种子权限组
    import json
    created_groups = {}
    for g in DEFAULT_GROUPS:
        existing = PermissionGroup.query.filter_by(name=g['name']).first()
        if existing:
            created_groups[g['name']] = existing
            print(f"  跳过权限组: {g['name']}（已存在）")
        else:
            pg = PermissionGroup(
                name=g['name'],
                scope_type=g['scope_type'],
                description=g['description'],
            )
            pg.set_menu_keys(g['menu_keys'])
            db.session.add(pg)
            db.session.flush()
            created_groups[g['name']] = pg
            print(f"✓ 创建权限组: {g['name']} ({g['scope_type']})")
    db.session.commit()

    # 3. 迁移现有用户的 permission_group_id
    updated = 0
    for user in User.query.all():
        if user.permission_group_id is None:
            group_name = ROLE_TO_GROUP.get(user.role)
            if group_name and group_name in created_groups:
                user.permission_group_id = created_groups[group_name].id
                updated += 1
    db.session.commit()
    print(f"✓ {updated} 个用户已关联权限组")

    # 4. 迁移现有班主任→UserClassLink
    link_count = 0
    for user in User.query.filter(
        User.role.in_(['homeroom_teacher', 'teacher']),
        User.grade.isnot(None),
        User.class_name.isnot(None)
    ).all():
        exists = UserClassLink.query.filter_by(
            user_id=user.id, grade=user.grade, class_name=user.class_name
        ).first()
        if not exists:
            link = UserClassLink(
                user_id=user.id,
                grade=user.grade,
                class_name=user.class_name,
            )
            db.session.add(link)
            link_count += 1
    db.session.commit()
    print(f"✓ {link_count} 个用户-班级关联已创建")

    print("\n迁移完成！")

# StuLink v1.5.0 2026-07-01
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0
