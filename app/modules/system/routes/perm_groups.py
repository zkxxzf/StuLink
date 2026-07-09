"""权限组管理路由"""
from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for
from flask_login import login_required
from app.extensions import db
from app.models import PermissionGroup, User
from app.utils.decorators import perm_required

bp = Blueprint('perm_groups', __name__, url_prefix='/perm-groups')

PERMISSION_MODULES = [
    {
        'name': '学生管理',
        'icon': 'bi-person',
        'permissions': [
            ('students.view', '查看学生'),
            ('students.edit', '编辑学生'),
            ('students.import', '批量导入学生'),
            ('students.export', '导出学生'),
            ('students.export_id_card', '导出身份证号'),
            ('students.export_phone', '导出手机号'),
            ('students.export_graduation_school', '导出毕业学校'),
            ('students.export_enrollment', '导出学籍信息'),
            ('students.transfer', '批量调班'),
        ]
    },
    {
        'name': '宿舍管理',
        'icon': 'bi-building',
        'permissions': [
            ('dormitory.view', '查看宿舍'),
            ('dormitory.manage', '管理宿舍'),
            ('dormitory.assign', '分配宿舍'),
            ('dormitory.beds', '床位分配'),
            ('dormitory.import', '宿舍数据导入'),
        ]
    },
    {
        'name': '统计报表',
        'icon': 'bi-bar-chart',
        'permissions': [
            ('statistics.view', '查看统计报表'),
        ]
    },
    {
        'name': '系统管理',
        'icon': 'bi-gear',
        'permissions': [
            ('system.users', '教师管理'),
            ('system.dictionary', '字典管理'),
            ('system.class_profile', '班型设置'),
            ('system.perm_groups', '权限组管理'),
            ('system.grade_mgmt', '年级管理'),
        ]
    },
    {
        'name': '积分管理',
        'icon': 'bi-star',
        'permissions': [
            ('points.view', '查看积分'),
            ('points.edit', '积分管理'),
        ]
    },
    {
        'name': '成绩管理',
        'icon': 'bi-book',
        'permissions': [
            ('grades.view', '查看成绩'),
            ('grades.edit', '成绩管理'),
        ]
    },
]

ALL_MENU_KEYS = []
for module in PERMISSION_MODULES:
    ALL_MENU_KEYS.extend(module['permissions'])


@bp.route('/')
@perm_required('system.perm_groups')
def manage():
    """权限组管理主页"""
# StuLink v1.6.1 2026-07-09
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0
    groups = PermissionGroup.query.order_by(PermissionGroup.id).all()
    for g in groups:
        g._user_count = User.query.filter_by(permission_group_id=g.id).count()
    return render_template('system/perm_groups/manage.html',
                           groups=groups,
                           all_menu_keys=ALL_MENU_KEYS,
                           permission_modules=PERMISSION_MODULES)


@bp.route('/save', methods=['POST'])
@perm_required('system.perm_groups')
def save():
    """创建或更新权限组"""
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': '数据格式错误'}), 400

    group_id = data.get('id')
    name = data.get('name', '').strip()
    scope_type = data.get('scope_type', 'none')
    menu_keys = data.get('menu_keys', [])
    description = data.get('description', '').strip()

    if not name:
        return jsonify({'success': False, 'message': '请输入组名'}), 400
    if scope_type not in ('class', 'grade', 'school', 'none'):
        return jsonify({'success': False, 'message': '无效的管理范围'}), 400

    if group_id:
        group = PermissionGroup.query.get(group_id)
        if not group:
            return jsonify({'success': False, 'message': '权限组不存在'}), 404
    else:
        existing = PermissionGroup.query.filter_by(name=name).first()
        if existing:
            return jsonify({'success': False, 'message': '组名已存在'}), 400
        group = PermissionGroup(name=name)

    group.name = name
    group.scope_type = scope_type
    group.description = description
    group.set_menu_keys(menu_keys)

    if not group_id:
        db.session.add(group)
    db.session.commit()

    return jsonify({'success': True, 'message': '权限组已保存', 'id': group.id})


@bp.route('/<int:id>/delete', methods=['POST'])
@perm_required('system.perm_groups')
def delete(id):
    group = PermissionGroup.query.get_or_404(id)
    user_count = User.query.filter_by(permission_group_id=id).count()
    if user_count > 0:
        return jsonify({'success': False,
                        'message': f'该组下还有 {user_count} 个用户，请先移走用户'}), 400
    db.session.delete(group)
    db.session.commit()
    return jsonify({'success': True, 'message': '权限组已删除'})


@bp.route('/<int:id>/json')
@perm_required('system.perm_groups')
def get_json(id):
    group = PermissionGroup.query.get_or_404(id)
    return jsonify({
        'id': group.id,
        'name': group.name,
        'scope_type': group.scope_type,
        'description': group.description,
        'menu_keys': group.get_menu_keys(),
    })


