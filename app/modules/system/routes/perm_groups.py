# StuLink v1.19.49.41 2026-10-10
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

# 2026-10-10：按权限组批量统计用户数需要 func.count（消除 N+1）
from sqlalchemy import func

from app.extensions import db
from app.models import (PermissionGroup, User, DictCategory, UserDataScope,
                        UserPermissionGroup)
from app.models.permission_group import (invalidate_group_menu_cache,
                                         KIND_LABELS, LEGACY_TO_KIND)
from app.utils.decorators import perm_required
from app.utils.permission_map import (MODULES, PROTECTED_GROUP_NAMES,
                                      item_levels_to_keys, keys_to_item_levels)
from app.utils.helpers import log_operation
import json

bp = Blueprint('perm_groups', __name__, url_prefix='/perm-groups')

# 范围类型下拉（用户可见文案见 KIND_LABELS）
KIND_OPTIONS = [('school', '全校'), ('self_grade', '本人年级'),
                ('pick_grades', '指定年级'), ('self_class', '本人班级'),
                ('pick_classes', '指定班级'), ('self_teaching', '本人任课'),
                ('none', '无')]


def _is_super():
    """超管：内置 admin 账号。"""
    return (getattr(current_user, 'username', '') or '') == 'admin'


def _is_admin_group_member():
    """是否管理员组成员（内置 admin / role=admin / 名为「管理员组」的身份）。"""
    if _is_super():
        return True
    if (getattr(current_user, 'role', '') or '') == 'admin':
        return True
    for g in (current_user.permission_groups or []):
        if (g.name or '') in PROTECTED_GROUP_NAMES:
            return True
    return False


def _my_group_ids():
    """当前用户所在的身份组 id 集合。"""
    ids = {g.id for g in (current_user.permission_groups or []) if g is not None}
    if getattr(current_user, 'permission_group_id', None):
        ids.add(current_user.permission_group_id)
    return ids


def _data_grade_allowed(want):
    """非超管授权年级时，只能授予自己可见的年级（school 范围 = 全校）。"""
    if not want:
        return True
    from app.utils.scope import resolve_scope
    s = resolve_scope(current_user)
    if s.kind == 'school':
        return True
    if s.kind == 'grades':
        return want <= set(s.grades or [])
    if s.kind == 'classes':
        return want <= {g for g, _c in (s.classes or set())}
    return False


def _require_super():
    """权限组 / 数据范围属系统级配置：只有管理员组成员可写。

    2026-10-19 第十三轮审计：原实现只要求 `system.perm_groups` 权限点，
    而该权限点可以被任意身份组勾上 —— 拿到它的人就能：
    ① 给自己所在组写入任意权限点（含 system.users / system.perm_groups，横向提权）；
    ② 给自己或任意用户覆盖式写 UserDataScope（年级范围直接变全校）。
    现在收紧为「管理员组成员」才可进；`save_perms` / `save_data_scope`
    另有"不能改自己所在组、不能授予自己没有的权限"的第二道闸。
    """
    from flask import abort
    if not _is_admin_group_member():
        abort(403, description='权限组与数据范围仅管理员组成员可修改')


def _clean_str_list(v):
    """清洗字符串列表（去空、去重、保序）"""
    out = []
    for x in (v or []):
        s = str(x or '').strip()
        if s and s not in out:
            out.append(s)
    return out


def _clean_pairs(v):
    """清洗 [{grade,class}] 列表"""
    out = []
    seen = set()
    for c in (v or []):
        if not isinstance(c, dict):
            continue
        g = str(c.get('grade') or '').strip()
        cl = str(c.get('class') or '').strip()
        if g and cl and (g, cl) not in seen:
            seen.add((g, cl))
            out.append({'grade': g, 'class': cl})
    return out


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
    # 2026-10-10 架构审计修复：原实现每个权限组都 count 一次用户（N+1）。
    # 改为一次性 group_by 统计；user_count 语义完全不变。
    _gid_list = [g.id for g in groups]
    _ucount = {}
    if _gid_list:
        # P5：用户数按多身份关联表统计（一人多组则各组都计）
        _ucount = {
            row[0]: row[1] for row in db.session.query(
                UserPermissionGroup.group_id, func.count(UserPermissionGroup.user_id)
            ).filter(
                UserPermissionGroup.group_id.in_(_gid_list)
            ).group_by(UserPermissionGroup.group_id).all()
        }
    group_rows = []
    for g in groups:
        group_rows.append({
            'g': g,
            'levels': keys_to_item_levels(g.get_menu_keys()),
            'user_count': _ucount.get(g.id, 0),
            'protected': g.name in PROTECTED_GROUP_NAMES,
            'kind': g.effective_kind,
            'kind_label': g.kind_label,
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
                           user_rows=user_rows, grades=_all_grades(),
                           kind_options=KIND_OPTIONS)


@bp.route('/save', methods=['POST'])
@perm_required('system.perm_groups')
def save():
    """创建/更新身份（权限组）基础信息（P5：范围类型 7 档 + 指定年级/班级）"""
    _require_super()
    data = request.get_json() or {}
    group_id = data.get('id')
    name = (data.get('name') or '').strip()
    description = (data.get('description') or '').strip()
    # 范围类型：优先 scope_kind（7 值），兼容旧 scope_type（4 值）
    scope_kind = (data.get('scope_kind') or '').strip()
    if not scope_kind:
        scope_kind = LEGACY_TO_KIND.get((data.get('scope_type') or 'none').strip(), 'none')
    if scope_kind not in KIND_LABELS:
        return jsonify({'success': False, 'message': '无效的范围类型'}), 400
    scope_grades = _clean_str_list(data.get('scope_grades'))
    scope_classes = _clean_pairs(data.get('scope_classes'))

    if not name:
        return jsonify({'success': False, 'message': '请输入身份名称'}), 400

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
    group.scope_kind = scope_kind
    group.scope_grades = json.dumps(scope_grades, ensure_ascii=False)
    group.scope_classes = json.dumps(scope_classes, ensure_ascii=False)
    group.sync_scope_type()
    group.description = description
    db.session.commit()
    log_operation(current_user, '保存', '身份权限组',
                  group.id, f'{group.name}（{group.kind_label}）', module='system')
    return jsonify({'success': True, 'message': '身份已保存', 'id': group.id})


@bp.route('/save-perms', methods=['POST'])
@perm_required('system.perm_groups')
def save_perms():
    """功能权限表格批量保存：{items: [{id, levels: {子功能标识: 等级}}]}"""
    _require_super()
    data = request.get_json() or {}
    items = data.get('items') or []
    updated = skipped = 0
    updated_ids = []
    my_groups = set() if _is_super() else _my_group_ids()
    for it in items:
        group = PermissionGroup.query.get(it.get('id'))
        if not group:
            continue
        if group.name in PROTECTED_GROUP_NAMES:
            skipped += 1
            continue
        levels = {k: str(v) for k, v in (it.get('levels') or {}).items()}
        new_keys = set(item_levels_to_keys(levels))
        # 2026-10-19 第十三轮审计（第二道闸）：不允许把「自己原本没有的权限点」
        # 写进任何组（含自己所在组）——否则可从别的组横向拿权；
        # 降权（去掉权限）仍允许。管理员组成员 has_perm 恒真，不受影响。
        if not _is_admin_group_member() and any(
                not current_user.has_perm(k) for k in new_keys):
            skipped += 1
            continue
        group.set_menu_keys(list(new_keys))   # set 不能直接 json.dumps
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
    _require_super()
    data = request.get_json() or {}
    scopes = data.get('scopes') or {}
    count = 0
    for uid, grades in scopes.items():
        try:
            uid = int(uid)
        except (TypeError, ValueError):
            continue
        want = {str(g) for g in (grades or []) if str(g).strip()}
        # 2026-10-19 第十三轮审计（第二道闸）：非内置 admin 不能给自己写范围，
        # 也不能授予超出自己可见范围的年级。
        if not _is_super():
            if uid == current_user.id:
                continue
            if want and not _data_grade_allowed(want):
                continue
        entries = [{'grade': g, 'class_name': None}
                   for g in (grades or []) if str(g).strip()]
        # 2026-10-20 第十七轮审计：只覆盖「年级级」行（class_name IS NULL），
        # 保留精细到班的授权 —— 原实现全删后只写回 grade，会把「仅某班」
        # 静默放大成「整个年级」（成绩/学生/宿舍/德育/积分全线放大）。
        UserDataScope.query.filter_by(user_id=uid).filter(
            UserDataScope.class_name.is_(None)).delete()
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
    _require_super()
    group = PermissionGroup.query.get_or_404(id)
    if group.name in PROTECTED_GROUP_NAMES:
        return jsonify({'success': False,
                        'message': f'「{group.name}」为系统保留身份，不允许删除'}), 400
    user_count = UserPermissionGroup.query.filter_by(group_id=id).count()
    if user_count > 0:
        return jsonify({'success': False,
                        'message': f'该身份下还有 {user_count} 个用户，请先移走用户'}), 400
    db.session.delete(group)
    db.session.commit()
    # 2026-10-18 审计：删除身份（权限组）此前**没有埋点**，同文件的
    # save / save_perms / save_data_scope 都有，唯独 delete 漏了。
    log_operation(current_user, '删除', '权限组', id,
                  f'{group.name}（身份及其权限配置）',
                  module='system', severity='WARNING')
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
        'scope_kind': group.effective_kind,
        'scope_grades': group.get_scope_grades(),
        'scope_classes': group.get_scope_classes(),
        'description': group.description,
        'protected': group.name in PROTECTED_GROUP_NAMES,
    })
