"""初始化预设权限组（模块化权限系统）"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import create_app
from app.extensions import db
from app.models import PermissionGroup, User

PRESETS = [
    {
        'name': '管理员组',
        'role': 'admin',
        'scope_type': 'school',
        'description': '系统管理员，拥有全部权限',
        'menu_keys': [
            'students.view', 'students.edit', 'students.import',
            'students.export', 'students.transfer',
            'dormitory.view', 'dormitory.manage', 'dormitory.assign',
            'dormitory.beds', 'dormitory.import',
            'statistics.view',
            'system.users', 'system.dictionary', 'system.class_profile',
            'system.perm_groups', 'system.grade_mgmt',
            'points.view', 'points.edit',
            'grades.view', 'grades.edit',
        ],
    },
    {
        'name': '年级长组',
        'role': 'grade_leader',
        'scope_type': 'grade',
        'description': '年级长，管理本年级学生数据和统计',
        'menu_keys': [
            'students.view', 'students.edit', 'students.import',
            'students.export', 'students.transfer',
            'dormitory.view', 'dormitory.beds',
            'statistics.view',
            'points.view', 'grades.view',
        ],
    },
    {
        'name': '班主任组',
        'role': 'homeroom_teacher',
        'scope_type': 'class',
        'description': '班主任，管理本班学生和床位',
        'menu_keys': [
            'students.view', 'students.edit', 'students.import',
            'students.export',
            'dormitory.view', 'dormitory.beds',
            'statistics.view',
            'points.view', 'grades.view',
        ],
    },
    {
        'name': '宿管组',
        'role': 'dorm_manager',
        'scope_type': 'school',
        'description': '宿管教师，管理全校宿舍分配和床位',
        'menu_keys': [
            'students.view',
            'dormitory.view', 'dormitory.manage', 'dormitory.assign',
            'dormitory.beds', 'dormitory.import',
            'statistics.view',
        ],
    },
    {
        'name': '任课教师组',
        'role': 'teacher',
        'scope_type': 'class',
        'description': '任课教师，查看所教班级学生',
        'menu_keys': [
            'students.view',
            'points.view', 'points.edit',
            'grades.view', 'grades.edit',
        ],
    },
]

app = create_app()
with app.app_context():
    for p in PRESETS:
        existing = PermissionGroup.query.filter_by(name=p['name']).first()
        if existing:
            existing.scope_type = p['scope_type']
            existing.role = p['role']
            existing.description = p['description']
            existing.set_menu_keys(p['menu_keys'])
            print(f'Updated: {p["name"]}')
        else:
            g = PermissionGroup(
                name=p['name'],
                role=p['role'],
                scope_type=p['scope_type'],
                description=p['description'],
            )
            g.set_menu_keys(p['menu_keys'])
            db.session.add(g)
            print(f'Created: {p["name"]}')
    db.session.commit()

    # 确保 admin 用户有权限组
    admin = User.query.filter_by(username='admin').first()
    if admin and not admin.permission_group_id:
        admin_group = PermissionGroup.query.filter_by(name='管理员组').first()
        if admin_group:
            admin.permission_group_id = admin_group.id
            db.session.commit()
            print('Admin user linked to 管理员组')

    print('Done.')
