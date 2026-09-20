# StuLink v1.9.2 2026-09-16
# 权限组管理：功能权限表格（身份×子功能×三档）+ 用户数据范围表格（用户×年级）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""权限组管理路由

设计（v1.9.2 二维权限模型）：
- 功能权限：身份（权限组）× 子功能 → 不可见/只读/写入（三档），
  子功能定义与 key 映射见 app/utils/permission_map.py；
- 数据范围：用户 × 年级（UserDataScope），限定用户在已获权限内可见的数据；
- 身份不写死：管理员可随时新建权限组即新身份；
- 管理员组为系统保留身份：不允许删除与修改（防误操作）。
"""
from flask import (Blueprint, render_template, request, jsonify)
from flask_login import current_user

from app.extensions import db
from app.models import PermissionGroup, User, DictCategory, UserDataScope
from app.models.permission_group import invalidate_group_menu_cache
from app.utils.decorators import perm_required
from app.utils.permission_map import (MODULES, PROTECTED_GROUP_NAMES,
                                      item_levels_to_keys, keys_to_item_levels)
from app.utils.helpers import log_operation

bp = Blueprint('perm_groups', __name__, url_prefix='/perm-groups')


def _all_grades():
    """年级字典（表格列使用）"""
    cat = DictCategory.query.filter_by(code='grade').first()
    if not cat:
        return []
    return [i.value for i in cat.items.filter_by(is_active=True)
            .order_by('sort_order').all()]


@bp.route('/')
@perm_required('system.perm_groups')
def manage():
    """权限管理主页（两张表格：功能权限 + 数据范围）"""
    groups = PermissionGroup.query.order_by(PermissionGroup.id).all()
    group_rows = []
    for g in groups:
        group_rows.append({
            'g': g,
            'levels': keys_to_item_levels(g.get_menu_keys()),
            'user_count': User.query.filter_by(permission_group_id=g.id).count(),
            'protected': g.name in PROTECTED_GROUP_NAMES,
        })

    users = User.query.order_by(User.permission_group_id, User.username).all()
    scope_rows = UserDataScope.query.all()
    scope_map = {}
    for r in scope_rows:
        scope_map.setdefault(r.user_id, set()).add(r.grade)
    user_rows = []
    for u in users:
        user_rows.append({
            'u': u,
            'group_name': u.permission_group.name if u.permission_group else '未分组',
            'grades': sorted(scope_map.get(u.id, set())),
            'has_class_scope': any(
                r.user_id == u.id and r.class_name for r in scope_rows),
        })

    return render_template('system/perm_groups/manage.html',
                           group_rows=group_rows, modules=MODULES,
                           user_rows=user_rows, grades=_all_grades())


@bp.route('/save', methods=['POST'])
@perm_required('system.perm_groups')
def save():
    """创建/更新身份（权限组）基础信息"""
    data = request.get_json() or {}
    group_id = data.get('id')
    name = (data.get('name') or '').strip()
    scope_type = data.get('scope_type', 'none')
    description = (data.get('description') or '').strip()

    if not name:
        return jsonify({'success': False, 'message': '请输入身份名称'}), 400
    if scope_type not in ('class', 'grade', 'school', 'none'):
        return jsonify({'success': False, 'message': '无效的管理范围'}), 400

    if group_id:
        group = PermissionGroup.query.get(group_id)
        if not group:
            return jsonify({'success': False, 'message': '身份不存在'}), 404
        if group.name in PROTECTED_GROUP_NAMES:
            return jsonify({'success': False,
                            'message': f'「{group.name}」为系统保留身份，不允许修改'}), 400
    else:
        if PermissionGroup.query.filter_by(name=name).first():
            return jsonify({'success': False, 'message': '身份名称已存在'}), 400
        group = PermissionGroup(name=name, role='staff')
        group.set_menu_keys([])
        db.session.add(group)

    group.name = name
    group.scope_type = scope_type
    group.description = description
    db.session.commit()
    log_operation(current_user, '保存', '身份权限组',
                  group.id, f'{group.name}（{scope_type}）', module='system')
    return jsonify({'success': True, 'message': '身份已保存', 'id': group.id})


@bp.route('/save-perms', methods=['POST'])
@perm_required('system.perm_groups')
def save_perms():
    """功能权限表格批量保存：{items: [{id, levels: {子功能标识: 等级}}]}"""
    data = request.get_json() or {}
    items = data.get('items') or []
    updated = skipped = 0
    updated_ids = []
    for it in items:
        group = PermissionGroup.query.get(it.get('id'))
        if not group:
            continue
        if group.name in PROTECTED_GROUP_NAMES:
            skipped += 1
            continue
        levels = {k: str(v) for k, v in (it.get('levels') or {}).items()}
        group.set_menu_keys(item_levels_to_keys(levels))
        updated_ids.append(group.id)
        updated += 1
    db.session.commit()
    # v1.16.0：权限配置变更后主动失效菜单缓存（set_menu_keys 已逐组失效，此处再保险）
    for gid in updated_ids:
        invalidate_group_menu_cache(gid)
    msg = f'已保存 {updated} 个身份的权限'
    if skipped:
        msg += f'（{skipped} 个系统保留身份已跳过）'
    log_operation(current_user, '保存', '功能权限矩阵',
                  None, msg, module='system')
    return jsonify({'success': True, 'message': msg})


@bp.route('/data-scope/save', methods=['POST'])
@perm_required('system.perm_groups')
def save_data_scope():
    """数据范围表格保存：{scopes: {user_id: ["2025级", ...]}}（整年级授权，覆盖式）"""
    data = request.get_json() or {}
    scopes = data.get('scopes') or {}
    count = 0
    for uid, grades in scopes.items():
        try:
            uid = int(uid)
        except (TypeError, ValueError):
            continue
        entries = [{'grade': g, 'class_name': None}
                   for g in (grades or []) if str(g).strip()]
        UserDataScope.query.filter_by(user_id=uid).delete()
        for e in entries:
            db.session.add(UserDataScope(user_id=uid, grade=e['grade']))
        count += 1
    db.session.commit()
    log_operation(current_user, '保存', '数据范围授权',
                  None, f'更新 {count} 个用户', module='system')
    return jsonify({'success': True, 'message': f'已保存 {count} 个用户的数据范围'})


@bp.route('/<int:id>/delete', methods=['POST'])
@perm_required('system.perm_groups')
def delete(id):
    group = PermissionGroup.query.get_or_404(id)
    if group.name in PROTECTED_GROUP_NAMES:
        return jsonify({'success': False,
                        'message': f'「{group.name}」为系统保留身份，不允许删除'}), 400
    user_count = User.query.filter_by(permission_group_id=id).count()
    if user_count > 0:
        return jsonify({'success': False,
                        'message': f'该身份下还有 {user_count} 个用户，请先移走用户'}), 400
    db.session.delete(group)
    db.session.commit()
    # v1.16.0：删除权限组后失效其菜单缓存
    invalidate_group_menu_cache(id)
    return jsonify({'success': True, 'message': '身份已删除'})


@bp.route('/<int:id>/json')
@perm_required('system.perm_groups')
def get_json(id):
    group = PermissionGroup.query.get_or_404(id)
    return jsonify({
        'id': group.id,
        'name': group.name,
        'scope_type': group.scope_type,
        'description': group.description,
        'protected': group.name in PROTECTED_GROUP_NAMES,
    })
