"""权限组管理路�?""
from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for
from flask_login import login_required
from app.extensions import db
from app.models import PermissionGroup, User
from app.utils.decorators import role_required

bp = Blueprint('perm_groups', __name__, url_prefix='/perm-groups')

ALL_MENU_KEYS = [
    ('dashboard', '首页概览'),
    ('search', '学生查询'),
    ('students', '学生列表'),
    ('rooms', '宿舍列表'),
    ('assign', '分配宿舍'),
    ('beds', '床位分配'),
    ('overview', '宿舍总览'),
    ('statistics', '统计报表'),
    ('users', '用户管理'),
    ('dictionary', '字典管理'),
    ('class_profile', '班型设置'),
    ('perm_groups', '权限组管�?),
]


@bp.route('/')
@role_required('admin')
def manage():
    """权限组管理主�?""
# StuLink v1.4.6 2026-06-30
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0
    groups = PermissionGroup.query.order_by(PermissionGroup.id).all()
    for g in groups:
        g._user_count = User.query.filter_by(permission_group_id=g.id).count()
    return render_template('system/perm_groups/manage.html',
                           groups=groups,
                           all_menu_keys=ALL_MENU_KEYS)


@bp.route('/save', methods=['POST'])
@role_required('admin')
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
        return jsonify({'success': False, 'message': '请输入组�?}), 400
    if scope_type not in ('class', 'grade', 'school', 'none'):
        return jsonify({'success': False, 'message': '无效的管理范�?}), 400

    if group_id:
        group = PermissionGroup.query.get(group_id)
        if not group:
            return jsonify({'success': False, 'message': '权限组不存在'}), 404
    else:
        existing = PermissionGroup.query.filter_by(name=name).first()
        if existing:
            return jsonify({'success': False, 'message': '组名已存�?}), 400
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
@role_required('admin')
def delete(id):
    group = PermissionGroup.query.get_or_404(id)
    user_count = User.query.filter_by(permission_group_id=id).count()
    if user_count > 0:
        return jsonify({'success': False,
                        'message': f'该组下还�?{user_count} 个用户，请先移走用户'}), 400
    db.session.delete(group)
    db.session.commit()
    return jsonify({'success': True, 'message': '权限组已删除'})


@bp.route('/<int:id>/json')
@role_required('admin')
def get_json(id):
    group = PermissionGroup.query.get_or_404(id)
    return jsonify({
        'id': group.id,
        'name': group.name,
        'scope_type': group.scope_type,
        'description': group.description,
        'menu_keys': group.get_menu_keys(),
    })
