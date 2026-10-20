# StuLink v1.7.0 2026-08-02
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import secrets
from flask import Blueprint, render_template, redirect, url_for, flash, request, send_file
from flask_login import login_required, current_user
from app.extensions import db
from app.models import User, PermissionGroup, WeChatBinding
from app.forms.user_forms import UserForm
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation
from app.modules.academic.services import teacher_sync
from app.modules.academic.services import teacher_archive_service as archive_svc
from app.models.academic import Teacher
# 2026-10-14：教师名单子页复用教务侧视图函数（挂到 /users/teachers，见文件末尾）
from app.modules.academic.routes import teachers as _ac_teachers
import io

bp = Blueprint('users', __name__, url_prefix='/users')


def _normalize_user_phone(raw):
    """账号手机号规范化 + 格式校验。

    返回 (规范值 或 None, 错误信息 或 None)；空值合法（= 不填手机号）。
    带分隔符的写法（138-0000-8000 / +86…）由 normalize_phone 统一成 11 位数字。
    """
    from app.utils.crypto import normalize_phone
    raw = (raw or '').strip()
    if not raw:
        return None, None
    norm = normalize_phone(raw)
    if not norm or len(norm) != 11 or not norm.startswith('1'):
        return None, '手机号格式不正确（应为 11 位手机号，可含 - 或 +86）'
    return norm, None


def _phone_taken(norm, exclude_id=None):
    """手机号是否已被其他账号占用（盲索引等值查询，不依赖明文）"""
    if not norm:
        return None
    q = User.query.filter(User.phone_idx == User.phone_index(norm))
    if exclude_id:
        q = q.filter(User.id != exclude_id)
    return q.first()


def _generate_password():
    """生成随机一次性口令（12 位字母数字，保证同时含字母与数字以符合
    app/utils/password_policy.py 的策略）"""
    import string
    alphabet = string.ascii_letters + string.digits
    while True:
        pwd = ''.join(secrets.choice(alphabet) for _ in range(12))
        if any(c.isdigit() for c in pwd) and any(c.isalpha() for c in pwd):
            return pwd


def _generate_initial_password():
    """批量导出的初始口令：2 位字母 + 6 位数字（2026-10-20 用户口径）。

    仍满足密码策略（≥8 位、含字母与数字），首次登录强制改密。
    """
    import string
    letters = string.ascii_letters
    return (''.join(secrets.choice(letters) for _ in range(2))
            + ''.join(secrets.choice(string.digits) for _ in range(6)))


def _store_one_time_password(username, real_name, password):
    """L-7：初始/重置口令不再写进 flash（会进响应 HTML 与签名 cookie），
    改为放进 session 的一次性展示区，在用户列表页显示一次即失效。"""
    from flask import session
    items = session.get('_pwd_once', [])
    items.append({'username': username, 'real_name': real_name,
                  'password': password})
    session['_pwd_once'] = items[-50:]
    session.modified = True


# ===== 2026-10-19 审计修复（H-01 权限组提权）=====
# create() 原来只拦 role=='admin'，edit() 只拦「非超管编辑其他管理员 / 把账号设为
# admin」；但两条路径最终都通过 _set_user_groups() 写入任意权限组。管理员组
# （role=='admin' 或 name=='管理员组'）的 menu_keys 覆盖全部权限，非超管把账号
# （含自己）挂进去即等同提权。口径：管理员组的授予/编辑只允许内置 admin 账号。
ADMIN_GROUP_NAME = '管理员组'


def _actor_is_super(actor=None):
    """actor 是否超管：唯一口径 = 内置 admin 账号。"""
    actor = current_user if actor is None else actor
    return (getattr(actor, 'username', '') or '') == 'admin'


def _is_admin_group(group):
    """是否管理员组：role=='admin' 或名称为「管理员组」。"""
    if group is None:
        return False
    return ((getattr(group, 'role', '') or '') == 'admin'
            or (getattr(group, 'name', '') or '') == ADMIN_GROUP_NAME)


def _admin_group_ids():
    """全库管理员组 id 集合。"""
    return {g.id for g in PermissionGroup.query.all() if _is_admin_group(g)}


def _group_grantable(group, actor=None):
    """非超管只能授予「权限点 ⊆ 自己」的组（2026-10-20 第十七轮审计）。

    H-01 只封了管理员组；其它高权组（带 system.grade_mgmt / system.users /
    students.export 等）仍可被持 system.users 的账号授予新账号，再拿页面显示的
    一次性口令登录，等于纵向提权。这里按权限点子集判定；空组（无任何权限）放行。
    管理员组成员 has_perm 恒真，不受影响。
    """
    actor = current_user if actor is None else actor
    if _actor_is_super(actor):
        return True
    if _is_admin_group(group):
        return False
    keys = set(group.get_menu_keys() or [])
    if not keys:
        return True
    return all(actor.has_perm(k) for k in keys)


def _submitted_admin_group_ids(group_ids):
    """提交的 group_id 中落在管理员组的部分。"""
    ids = {g for g in (group_ids or []) if g}
    if not ids:
        return set()
    return ids & _admin_group_ids()


def _user_in_admin_group(user):
    """用户当前是否属于管理员组（按多身份列表判定）。"""
    return any(_is_admin_group(g) for g in (user.permission_groups or []))


def _set_user_groups(user, group_ids, allow_admin_group=False, actor=None):
    """设置用户的权限组（多身份，P5）：列表首个为主组。

    同步旧字段 permission_group_id 与多身份关联表 user_permission_groups，
    并移除未选中的关联（cascade delete-orphan）。

    2026-10-14 第四轮审计修复（多身份 B 项）：组 id 先与 permission_groups 实际
    存在的 id 求交 —— SQLite 不强制外键，原实现可写入指向不存在组的悬空关联
    （group_links 里 group=None，该"身份"静默消失）。

    2026-10-19（H-01）：allow_admin_group=False 时**过滤掉管理员组**，
    防止其它调用点绕过路由层校验直接授予。
    """
    from app.models import UserPermissionGroup, PermissionGroup
    valid_ids = {g.id for g in PermissionGroup.query.all()}
    admin_ids = _admin_group_ids()
    gids = []
    groups_by_id = {g.id: g for g in PermissionGroup.query.all()}
    for g in (group_ids or []):
        if g and g not in gids and g in valid_ids:
            if g in admin_ids and not allow_admin_group:
                continue
            # 2026-10-20 第十七轮：非超管不得授予超出自身权限的组
            if not _group_grantable(groups_by_id.get(g), actor):
                continue
            gids.append(g)
    user.permission_group_id = gids[0] if gids else None
    keep = set(gids)
    for l in list(user.group_links):
        if l.group_id not in keep:
            user.group_links.remove(l)
    for i, gid in enumerate(gids):
        link = next((l for l in user.group_links if l.group_id == gid), None)
        if link is None:
            link = UserPermissionGroup(group_id=gid)
            user.group_links.append(link)
        link.is_primary = (i == 0)


@bp.route('/')
@perm_required('system.users')
def list_users():
    from flask import session
    # 2026-10-18 审计（N+1）：此前每个用户访问 u.primary_group 都懒加载一次，
    # 97 个账号 = 97 条 SQL（实测整页 102 条）。改成 selectinload 预加载，
    # 一次查回 group_links + group + permission_group，SQL 降到个位数。
    from sqlalchemy.orm import selectinload
    from app.models.user_permission_group import UserPermissionGroup
    users = (User.query
             .options(selectinload(User.group_links)
                      .selectinload(UserPermissionGroup.group),
                      selectinload(User.permission_group))
             .order_by(User.role, User.username).all())
    # L-7：取出一次性初始口令（显示后立即失效）
    one_time_passwords = session.pop('_pwd_once', []) if users is not None else []
    # 2026-10-20：取消按权限组分组 —— 一次列出全部用户，角色由表头下拉筛选；
    # 页面不再展示系统 UID（id / uuid 均不展示）。
    # 角色筛选选项 = 所有身份组名（一人多组时每个组都算一个可筛角色）。
    role_names = set()
    for u in users:
        gs = [g.name for g in u.permission_groups if g and g.name]
        role_names.update(gs or [u.display_role])
    roles = sorted(role_names)
    # 2026-10-19：教师管理展示微信绑定状态 / openid / 绑定 UID。
    # 一次查回，避免每个用户 N+1。
    wx_bindings = {}
    user_ids = [u.id for u in users]
    if user_ids:
        rows = (WeChatBinding.query
                .filter(WeChatBinding.user_id.in_(user_ids))
                .order_by(WeChatBinding.user_id, WeChatBinding.id).all())
        for row in rows:
            wx_bindings.setdefault(row.user_id, []).append(row)
    # 2026-10-20：教师删除/归档 —— 行内按钮需要知道「账号是否关联教师档案」；
    # 离职/退休存档名单只在本页展示（其他模块已按 status/archive_kind 排除）
    teacher_map = archive_svc.teacher_map(user_ids)
    archived = archive_svc.archived_teachers()
    # 2026-10-20：批量导出初始密码 —— 按权限组选择（分组导出）
    perm_groups = PermissionGroup.query.order_by(PermissionGroup.id).all()
    # 可勾选导出的账号（与后端排除口径一致：启用 / 非管理员 / 非本人 / 非管理员组）
    selectable_ids = {u.id for u in users
                      if u.is_active and u.role != 'admin'
                      and u.id != current_user.id
                      and not _user_in_admin_group(u)}
    return render_template('system/users/list.html', users=users, roles=roles,
                           one_time_passwords=one_time_passwords,
                           wx_bindings=wx_bindings, teacher_map=teacher_map,
                           archived=archived, perm_groups=perm_groups,
                           selectable_ids=selectable_ids)


@bp.route('/create', methods=['GET', 'POST'])
@perm_required('system.users')
def create():
    form = UserForm()
    if form.validate_on_submit():
        if User.query.filter_by(username=form.username.data).first():
            flash('用户名已存在', 'danger')
            return render_template('system/users/form.html', form=form, title='新建用户')

        # 校验：班主任最多3人/班，且年级班级必填
        role = form.role.data
        # 2026-10-19：管理年级多选（年级长可多个）；班主任强制 1 个。
        grades = [g for g in (form.grade.data or []) if g]
        grade = grades[0] if grades else None
        class_name = form.class_name.data or None

        # 2026-10-18 审计修复（P1 横向提权）：非超管不得新建管理员账号
        # （否则任何有 system.users 的账号都能造一个 admin 给自己用）。
        if role == 'admin' and (current_user.username or '') != 'admin':
            flash('只有超管（admin 账号）能新建管理员账号', 'danger')
            return render_template('system/users/form.html', form=form, title='新建用户')

        # H-01：权限组同样不能绕过 —— 非超管即使 role 填普通教师，
        # 也不能把新账号挂进「管理员组」（否则等于造了一个超管）。
        if _submitted_admin_group_ids(form.permission_group_ids.data) \
                and not _actor_is_super():
            log_operation(current_user, '新建被拒', '用户', None,
                          '非超管不得授予管理员组',
                          module='system', severity='WARNING')
            flash('只有超管（admin 账号）能把账号加入「管理员组」', 'danger')
            return render_template('system/users/form.html', form=form, title='新建用户')

        if role == 'homeroom_teacher':
            if len(grades) != 1 or not class_name:
                flash('班主任必须指定 1 个管理年级和班级（一个班只属于一个年级）', 'danger')
                return render_template('system/users/form.html', form=form, title='新建用户')
            existing_count = User.query.filter_by(
                role='homeroom_teacher', grade=grade, class_name=class_name, is_active=True
            ).count()
            if existing_count >= 3:
                flash(f'{grade}{class_name} 已有 {existing_count} 名班主任，最多设置3名', 'danger')
                return render_template('system/users/form.html', form=form, title='新建用户')

        if role == 'grade_leader' and not grades:
            flash('年级长必须指定管理年级（可多选）', 'danger')
            return render_template('system/users/form.html', form=form, title='新建用户')

        # 2026-10-20：手机号（选填）——填了就能用手机号登录，要求格式正确且不被占用
        phone_norm, phone_err = _normalize_user_phone(form.phone.data)
        if phone_err:
            flash(phone_err, 'danger')
            return render_template('system/users/form.html', form=form, title='新建用户')
        dup = _phone_taken(phone_norm)
        if dup:
            flash(f'手机号已被「{dup.real_name}」使用，请换一个', 'danger')
            return render_template('system/users/form.html', form=form, title='新建用户')

        user = User(
            username=form.username.data,
            real_name=form.real_name.data,
            role=role,
            grade=','.join(grades) or None,
            class_name=class_name,
            must_change_pwd=True,
        )
        user.phone = phone_norm
        # P5：多身份（权限组），列表首个为主组
        _set_user_groups(user, form.permission_group_ids.data,
                         allow_admin_group=_actor_is_super())
        generated_pwd = form.password.data or _generate_password()
        user.set_password(generated_pwd)
        db.session.add(user)
        db.session.commit()
        # v1.19.49.49：非管理员自动同步创建教师档案（admin 不写入）
        _sync_state, _ = teacher_sync.sync_from_user(user)
        db.session.commit()
        log_operation(current_user, '创建', '用户', user.id, f'{user.real_name} ({user.role_display})')
        # L-7：初始口令不再经 flash 明文回显（会进响应 HTML 与签名 cookie）
        auto_pwd = not form.password.data
        if auto_pwd:
            _store_one_time_password(user.username, user.real_name, generated_pwd)
        flash(f'用户 {user.real_name} 已创建'
              + ('，初始口令已在本页一次性显示（仅此一次，请立即转交本人）' if auto_pwd else ''),
              'success')
        return redirect(url_for('users.list_users'))
    return render_template('system/users/form.html', form=form, title='新建用户')


@bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@perm_required('system.users')
def edit(id):
    user = User.query.get_or_404(id)
    form = UserForm(obj=user)
    # P5：多身份多选的回显（GET 时按当前所属组预选；首个为主组）
    if not form.is_submitted():
        form.permission_group_ids.data = [g.id for g in user.permission_groups]
        form.grade.data = user.grades     # 管理年级多选回显（库内是逗号串）
        form.phone.data = user.phone or ''   # 手机号回显（仅本页管理员可见完整值）
    if form.validate_on_submit():
        existing = User.query.filter(
            User.username == form.username.data, User.id != user.id).first()
        if existing:
            flash('用户名已存在', 'danger')
            return render_template('system/users/form.html', form=form, title='编辑用户')

        # 校验
        role = form.role.data
        # 2026-10-19：管理年级多选（年级长可多个）；班主任强制 1 个。
        grades = [g for g in (form.grade.data or []) if g]
        grade = grades[0] if grades else None
        class_name = form.class_name.data or None

        # v1.9.2 防误操作：管理员账号的角色不可修改
        if user.role == 'admin' and role != 'admin':
            flash('管理员账号的角色不可修改（防止系统失去管理入口）', 'danger')
            return render_template('system/users/form.html', form=form, title='编辑用户')

        if role == 'homeroom_teacher':
            if len(grades) != 1 or not class_name:
                flash('班主任必须指定 1 个管理年级和班级（一个班只属于一个年级）', 'danger')
                return render_template('system/users/form.html', form=form, title='编辑用户')
            existing_count = User.query.filter_by(
                role='homeroom_teacher', grade=grade, class_name=class_name, is_active=True
            ).filter(User.id != user.id).count()
            if existing_count >= 3:
                flash(f'{grade}{class_name} 已有 {existing_count} 名班主任，最多设置3名', 'danger')
                return render_template('system/users/form.html', form=form, title='编辑用户')

        if role == 'grade_leader' and not grades:
            flash('年级长必须指定管理年级（可多选）', 'danger')
            return render_template('system/users/form.html', form=form, title='编辑用户')

        # 2026-10-20：手机号（留空 = 清空，改用用户名登录）
        phone_norm, phone_err = _normalize_user_phone(form.phone.data)
        if phone_err:
            flash(phone_err, 'danger')
            return render_template('system/users/form.html', form=form, title='编辑用户')
        dup = _phone_taken(phone_norm, exclude_id=user.id)
        if dup:
            flash(f'手机号已被「{dup.real_name}」使用，请换一个', 'danger')
            return render_template('system/users/form.html', form=form, title='编辑用户')

        # 2026-10-18 审计修复（P1 横向提权）：此页可以改 role / 权限组 / **密码**，
        # 原来没有任何授权上限 —— 非超管管理员能编辑其他管理员（含超管），
        # 也能借 password 字段直接改他人密码，绕过 reset_password 的口径。
        # 现在：① 非超管不得编辑其他管理员；② 非超管不得把账号设为 admin。
        is_super = (current_user.username or '') == 'admin'
        # 2026-10-20：自己编辑自己 —— 允许改手机号 / 密码等自我维护字段，
        # 但权限组一律不动（见下方 _set_user_groups），避免自提权或自降权。
        own_edit = (user.id == current_user.id)
        if not is_super:
            if user.role == 'admin' and user.id != current_user.id:
                log_operation(current_user, '编辑被拒', '用户', user.id,
                              f'{user.real_name}（非超管不得编辑管理员）',
                              module='system', severity='WARNING')
                flash('只有超管（admin 账号）能编辑其他管理员', 'danger')
                return render_template('system/users/form.html', form=form,
                                       title='编辑用户')
            if role == 'admin' and user.role != 'admin':
                flash('只有超管能把账号设为管理员', 'danger')
                return render_template('system/users/form.html', form=form,
                                       title='编辑用户')
            # H-01：权限组是第二条提权路径 —— 非超管不得改**别人**的管理员组
            # 账号；自己编辑自己放行（否则管理员组账号连自己的手机号都存不了）。
            if _user_in_admin_group(user) and not own_edit:
                log_operation(current_user, '编辑被拒', '用户', user.id,
                              f'{user.real_name}（非超管不得编辑管理员组用户）',
                              module='system', severity='WARNING')
                flash('只有超管（admin 账号）能编辑其他管理员组用户', 'danger')
                return render_template('system/users/form.html', form=form,
                                       title='编辑用户')
            # 非超管不得把账号**新加**进管理员组；自己本来就在管理员组时，
            # 表单回显里自然带着它，不算"新加"。
            if _submitted_admin_group_ids(form.permission_group_ids.data) \
                    and not own_edit:
                log_operation(current_user, '编辑被拒', '用户', user.id,
                              f'{user.real_name}（非超管不得授予管理员组）',
                              module='system', severity='WARNING')
                flash('只有超管（admin 账号）能把账号加入「管理员组」', 'danger')
                return render_template('system/users/form.html', form=form,
                                       title='编辑用户')
        user.username = form.username.data
        # 姓名锁定：真实姓名与任课教师映射/成绩归属绑定，一经创建不可修改（成绩模块约定）
        new_name = (form.real_name.data or '').strip()
        if new_name and new_name != user.real_name:
            flash('真实姓名不可修改（与任课教师映射/成绩归属绑定），已保留原姓名', 'warning')
        user.role = role
        user.grade = ','.join(grades) or None
        user.class_name = class_name
        user.phone = phone_norm
        # P5：多身份（权限组），列表首个为主组
        # L-05：编辑页若请求里完全没有权限组字段（旧表单/脚本/半截请求），
        # 不能把用户的组静默清空；只有显式提交过才覆盖。
        _groups_submitted = (
            request.form.get('permission_group_ids_present') == '1'
            or 'permission_group_ids' in request.form)
        # 2026-10-20：非超管编辑自己时**不动权限组** —— 表单里的组可能是旧回显，
        # 而 _set_user_groups(allow_admin_group=False) 会把管理员组过滤掉，
        # 一保存就把自己的管理员身份弄丢（自降权）；其它字段照常保存。
        if _groups_submitted and not (own_edit and not is_super):
            _old_groups = {g.name for g in user.permission_groups}
            _set_user_groups(user, form.permission_group_ids.data,
                             allow_admin_group=is_super)
            _new_groups = {g.name for g in user.permission_groups}
            if _old_groups != _new_groups:
                log_operation(
                    current_user, '权限组变更', '用户', user.id,
                    f'{user.real_name}: {sorted(_old_groups)} -> {sorted(_new_groups)}',
                    module='system', severity='WARNING')
        else:
            log_operation(current_user, '更新', '用户', user.id,
                          '未提交权限组字段，保留原权限组', module='system',
                          severity='WARNING')
        if form.password.data:
            user.set_password(form.password.data)
        db.session.commit()
        # v1.19.49.49：教师角色变更 → 同步到 academic.teachers（admin 自动 skip）
        teacher_sync.sync_from_user(user)
        db.session.commit()
        log_operation(current_user, '更新', '用户', user.id, f'{user.real_name} 信息已更新')
        flash('用户信息已更新', 'success')
        return redirect(url_for('users.list_users'))
    return render_template('system/users/form.html', form=form, title='编辑用户')


@bp.route('/<int:id>/teacher/remove', methods=['POST'])
@perm_required('system.users')
def remove_teacher(id):
    """删除教师：错误录入=彻底删除；离职/退休=存档（2026-10-20 用户口径）。

    - 必填删除原因；
    - 只有「错误录入」允许彻底删除，且账号已有任何业务数据时拒绝（只能存档）；
    - 离职/退休：教师置 left + 归档字段，账号停用并吊销小程序令牌，
      任课映射置失效，数据保留、仅本页可查阅；
    - 全程写操作日志。
    """
    user = User.query.get_or_404(id)
    kind = (request.form.get('kind') or '').strip()
    reason = (request.form.get('reason') or '').strip()
    back = redirect(url_for('users.list_users'))

    if user.role == 'admin' or _user_in_admin_group(user):
        flash('管理员账号不能删除或归档', 'danger')
        return back
    if user.id == current_user.id:
        flash('不能删除当前登录的账号', 'danger')
        return back
    if kind not in archive_svc.KIND_LABELS:
        flash('请选择处理方式：错误录入 / 离职 / 退休', 'danger')
        return back
    if not reason:
        flash('必须填写删除原因', 'danger')
        return back
    if len(reason) > 200:
        flash('删除原因不能超过 200 字', 'danger')
        return back

    teacher = Teacher.query.filter_by(user_id=user.id).first()
    if teacher is None:
        flash('该账号未关联教师档案，无法执行教师删除/归档', 'danger')
        return back
    if teacher.archive_kind:
        flash(f'{teacher.name} 已是「{teacher.archive_kind}」存档状态', 'warning')
        return back

    tid, tname = teacher.id, teacher.name
    try:
        if kind == archive_svc.DELETE_KIND:
            detail = archive_svc.hard_delete_teacher(teacher, reason, current_user)
            action = '删除教师'
        else:
            detail = archive_svc.archive_teacher(teacher, kind, reason, current_user)
            action = '归档教师'
    except ValueError as exc:
        flash(str(exc), 'danger')
        return back

    log_operation(current_user, action, '教师', tid,
                  f'{tname}：{detail}', module='system', severity='WARNING')
    flash(detail, 'success')
    return back


@bp.route('/<int:id>/toggle', methods=['POST'])
@perm_required('system.users')
def toggle(id):
    user = User.query.get_or_404(id)
    # 2026-10-19 第十三轮审计：与 edit / reset-password 同源补超管闸 ——
    # 原实现只拦"停用当前活跃 admin"，非超管可停用管理员组其他成员，
    # 也能把已停用的 admin 原地重新启用。
    if _user_in_admin_group(user) and not _actor_is_super():
        flash('管理员组成员只有 admin 本人可以启用/禁用', 'danger')
        return redirect(url_for('users.list_users'))
    # v1.9.2 防误操作：管理员账号不可禁用；不能禁用当前登录账号
    if user.role == 'admin' and user.is_active:
        flash('管理员账号不允许禁用（防止系统无法登录）', 'danger')
        return redirect(url_for('users.list_users'))
    if user.id == current_user.id and user.is_active:
        flash('不能禁用当前登录的账号', 'danger')
        return redirect(url_for('users.list_users'))
    # 2026-10-20：已归档（离职/退休）的教师不允许原地重新启用 —— 否则会绕过
    # 归档口径重新进入课表/监考/任课映射等模块；返聘请重新建档。
    _t = Teacher.query.filter_by(user_id=user.id).first()
    if _t is not None and _t.archive_kind and not user.is_active:
        flash(f'{_t.name} 已按「{_t.archive_kind}」归档，不能重新启用；'
              f'如返聘请重新建立教师档案', 'danger')
        return redirect(url_for('users.list_users'))
    was_active = user.is_active
    user.is_active = not user.is_active
    # P1 M-03：停用即吊销小程序旧令牌（网页端由 session_guard 的 is_active 检查兜住）
    if was_active and not user.is_active:
        user.wx_token_ver = int(user.wx_token_ver or 0) + 1
    db.session.commit()
    # v1.19.49.49：启禁变更 → 同步 Teacher.status (active/left)
    teacher_sync.sync_from_user(user)
    db.session.commit()
    status = '启用' if user.is_active else '禁用'
    log_operation(current_user, '更新', '用户', user.id, f'{user.real_name} {status}')
    flash(f'用户 {user.real_name} 已{status}', 'success')
    return redirect(url_for('users.list_users'))


@bp.route('/<int:id>/reset-password', methods=['POST'])
@perm_required('system.users')
def reset_password(id):
    user = User.query.get_or_404(id)
    # 2026-10-18 审计修复（P1 横向提权）：此前只要拥有 system.users 权限，就能重置
    # **任何账号**的口令（含其他管理员），被授权者可以接管同级账号。用户口径：
    #   ① 只有超管（内置 admin 账号）能重置其他管理员；
    #   ② 管理员可以重置普通教师/班主任等非管理员；
    #   ③ 任何人都可以重置自己（相当于改密，仍需登录态）。
    is_super = (current_user.username or '') == 'admin'
    if (user.role == 'admin' and not is_super
            and user.id != current_user.id):
        log_operation(current_user, '重置被拒', '用户', user.id,
                      f'{user.real_name}（非超管不得重置管理员）', module='system',
                      severity='WARNING')
        flash('只有超管（admin 账号）能重置其他管理员的口令', 'danger')
        return redirect(url_for('users.list_users'))
    # 2026-10-20：与批量导出统一为「2 字母 + 6 数字」的初始口令
    new_pwd = _generate_initial_password()
    user.set_password(new_pwd)
    user.must_change_pwd = True
    db.session.commit()
    log_operation(current_user, '更新', '用户', user.id, f'{user.real_name} 密码已重置')
    # L-7：重置后的口令一次性展示（此前生成后直接丢弃，管理员无法交付）
    _store_one_time_password(user.username, user.real_name, new_pwd)
    flash(f'{user.real_name} 的密码已重置，新口令已在本页一次性显示（仅此一次）', 'success')
    return redirect(url_for('users.list_users'))


@bp.route('/export-passwords', methods=['POST'])
@perm_required('system.users')
def export_passwords():
    """批量重置并导出初始密码（可按权限组分组导出）。

    2026-10-20 用户需求。系统只存密码摘要、**无法导出原密码**，因此本功能是
    「为所选账号生成新的初始密码 → 下载清单」，旧密码立即失效、首次登录强制改密。

    - 范围：优先按「勾选用户」；未勾选时按权限组多选或「全部用户」；
     - 口令：2 位字母 + 6 位数字，首次登录强制改密；
     - 排除：管理员账号、管理员组成员、当前登录本人（防批量外发管理入口口令）；
     - 仅启用账号（禁用账号不分配新口令）；
     - 全程写操作日志（WARNING 级）。
    """
    from datetime import datetime

    from sqlalchemy import or_

    from app.models.user_permission_group import UserPermissionGroup

    uids = [int(v) for v in request.form.getlist('user_ids') if str(v).isdigit()]
    all_flag = (request.form.get('all') or '') == '1'
    gids = [int(v) for v in request.form.getlist('group_ids') if str(v).isdigit()]
    if not uids and not all_flag and not gids:
        flash('请勾选用户，或选择权限组 / 全部用户', 'danger')
        return redirect(url_for('users.list_users'))

    if uids:
        q = User.query.filter(User.id.in_(uids), User.is_active.is_(True))
    else:
        q = User.query.filter(User.is_active.is_(True))
        if not all_flag:
            sub = db.session.query(UserPermissionGroup.user_id).filter(
                UserPermissionGroup.group_id.in_(gids))
            q = q.filter(or_(User.id.in_(sub),
                             User.permission_group_id.in_(gids)))
    users = q.order_by(User.role, User.username).all()

    rows = []
    for u in users:
        if u.id == current_user.id or u.role == 'admin' or _user_in_admin_group(u):
            continue
        pwd = _generate_initial_password()
        u.set_password(pwd)          # 同时递增 wx_token_ver，吊销小程序旧令牌
        u.must_change_pwd = True
        gnames = ('、'.join(g.name for g in u.permission_groups if g and g.name)
                  or u.role_display)
        rows.append([u.username, u.real_name or '', gnames, pwd])
    if not rows:
        flash('所选范围内没有可导出的账号（管理员与本人已排除）', 'warning')
        return redirect(url_for('users.list_users'))
    db.session.commit()

    group_names = [g.name for g in PermissionGroup.query
                   .filter(PermissionGroup.id.in_(gids)).all()] if gids else []
    if uids:
        scope_label = f'勾选用户 {len(rows)} 人'
    elif all_flag:
        scope_label = '全部用户'
    else:
        scope_label = '、'.join(group_names)
    log_operation(current_user, '导出初始密码', '用户', None,
                  f'{len(rows)} 个账号；范围：{scope_label}', module='system',
                  severity='WARNING')

    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '初始密码'
    ws.append(['登录用户名', '姓名', '权限组', '初始密码'])
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill('solid', fgColor='D9E2F3')
        cell.alignment = Alignment(horizontal='center')
    for r in rows:
        ws.append(r)
    for i, width in enumerate([18, 14, 28, 16], start=1):
        ws.column_dimensions[chr(64 + i)].width = width
    ws.freeze_panes = 'A2'

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    name = f'初始密码_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
    return send_file(
        buf, as_attachment=True, download_name=name,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@bp.route('/download-teacher-template')
@perm_required('system.users')
def download_teacher_template():
    """下载教师批量导入模板"""
    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '教师导入模板'

    headers = ['手机号', '真实姓名', '权限组']
    header_fill = PatternFill(start_color='ED7D31', end_color='ED7D31', fill_type='solid')
    header_font = Font(color='FFFFFF', bold=True, size=11)
    thin_border = Border(left=Side(style='thin'), right=Side(style='thin'),
                         top=Side(style='thin'), bottom=Side(style='thin'))

    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center')
        cell.border = thin_border

    ws.column_dimensions['A'].width = 16
    ws.column_dimensions['B'].width = 14
    ws.column_dimensions['C'].width = 14

    # example row
    ws.append(['13800138000', '张老师', '管理员'])

    # instruction row
    ws.merge_cells('A4:C4')
    instr = ws.cell(row=4, column=1, value='说明：手机号即登录名，初始口令由系统随机生成（导入后在本页一次性展示），首次登录需改密 | 权限组填写系统已有权限组名称 | 第 2 行起填数据，删除本行和示例')
    instr.font = Font(color='FF0000', bold=True, size=10)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True,
                     download_name='教师导入模板.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@bp.route('/import-teachers', methods=['POST'])
@perm_required('system.users')
def import_teachers():
    """批量导入教师：手机号=登录名，初始口令为随机一次性口令（H-1③），
    首次登录强制改密（由 app/__init__.py 的 _force_password_change 兜底）"""
    file = request.files.get('file')
    # L-8：统一上传校验（白名单 + 危险类型 + magic），不再只看扩展名
    from app.utils.upload_guard import validate_upload
    if not file:
        flash('请上传 .xlsx 格式的Excel文件', 'danger')
        return redirect(url_for('users.list_users'))
    ok, msg = validate_upload(file.filename, allowed_exts=['xlsx', 'xls'],
                              stream=file.stream)
    if not ok:
        flash(msg, 'danger')
        return redirect(url_for('users.list_users'))

    try:
        # 2026-10-10：改用公共 excel 读取层（含 openpyxl 兼容性 patch，幂等）
        from app.utils.excel_patch import load_sheet, read_header_map
        ws = load_sheet(file)
        header_map = read_header_map(ws)

        phone_col = header_map.get('手机号')
        name_col = header_map.get('真实姓名')
        group_col = header_map.get('权限组')

        if not phone_col or not name_col or not group_col:
            flash('Excel缺少必填列：手机号、真实姓名、权限组', 'danger')
            return redirect(url_for('users.list_users'))

        # load existing phones and permission groups
        existing_phones = set(r[0] for r in db.session.query(User.username).all() if r[0])
        pg_map = {pg.name: pg for pg in PermissionGroup.query.all()}

        created = 0
        errors = []
        _new_users = []                          # v1.19.49.49：导入后同步至 teachers

        for row_idx in range(2, ws.max_row + 1):
            phone = str(ws.cell(row=row_idx, column=phone_col).value or '').strip()
            name = str(ws.cell(row=row_idx, column=name_col).value or '').strip()
            group_name = str(ws.cell(row=row_idx, column=group_col).value or '').strip()

            if not phone or not name or not group_name:
                continue

            if phone in existing_phones:
                errors.append(f'第{row_idx}行（{name}）：手机号{phone}已存在')
                continue

            pg = pg_map.get(group_name)
            if not pg:
                errors.append(f'第{row_idx}行（{name}）：权限组"{group_name}"不存在')
                continue
            # 2026-10-19 第十四轮：管理员组只能由内置 admin 授予 —— create()/edit()
            # 早已有这道闸（H-01），批量导入口漏了：往 Excel 的「权限组」列写
            # 「管理员组」就能批量把人挂成管理员（has_perm 即全权限）。
            # 2026-10-20 第十七轮：不仅管理员组，任何超出操作者权限的组都拒绝
            if not _group_grantable(pg):
                errors.append(f'第{row_idx}行（{name}）：无权导入到「{pg.name}」'
                              '（超出你的权限范围）')
                continue

            user = User(username=phone, real_name=name, role='teacher',
                        permission_group_id=pg.id,
                        must_change_pwd=True, is_active=True)
            # H-1③：不再用手机号当口令（半公开信息 = 批量可登录），改随机一次性口令
            one_time_pwd = _generate_password()
            user.set_password(one_time_pwd)
            db.session.add(user)
            db.session.flush()
            user.sync_primary_link()   # P5：同步多身份关联表
            _store_one_time_password(phone, name, one_time_pwd)
            _new_users.append(user)
            existing_phones.add(phone)
            created += 1

        db.session.commit()
        # v1.19.49.49：批量导入后同步到 academic.teachers
        for u in _new_users:
            teacher_sync.sync_from_user(u)
        db.session.commit()

        if created:
            log_operation(current_user, '导入', '教师', None, f'批量导入 {created} 名教师')
        msg = f'成功导入 {created} 名教师，初始口令已在本页一次性显示（仅此一次）'
        if errors:
            msg += f'，{len(errors)} 条失败：' + '；'.join(errors[:20])
            if len(errors) > 20:
                msg += f'...还有{len(errors)-20}条'
            flash(msg, 'warning')
        else:
            flash(msg, 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'导入失败：{str(e)}', 'danger')

    return redirect(url_for('users.list_users'))


# ==================== 教师名单子页（2026-10-14） ====================
# 用户要求：系统管理「教师管理」拆成两个子页 —— 教师名单 / 教师权限管理（本页）。
# 教师名单与教务管理的 /academic/teachers 是**同一份数据、同一批视图函数**
# （档案在 academic.db.teachers，权限 academic.view），故这里用 add_url_rule 把**整条链路**
# 挂到 /users 前缀下（零逻辑复制，与 academic/routes/teacher_links.py 复用 grades 视图同款；
# 视图内的重定向/表单 action 由 academic/routes/teachers.py 的 _ep() 按蓝图自适应，
# 页面顶部 tab 栏见 app/templates/system/_user_tabs.html）。
#
# 为什么两边都要有入口：教务主任 / 教务员 / 校长 / 副校长 / 备课组长 / 教研组长都有
# academic.view 但**没有 system.users**（见 permission_map 与权限组实测），
# 只留系统管理一侧会让这些身份看不到教师名单。
bp.add_url_rule('/teachers', 'teachers_page', _ac_teachers.teachers_page)
bp.add_url_rule('/teachers/export', 'teachers_export', _ac_teachers.teachers_export)
bp.add_url_rule('/teachers/template.xlsx', 'teachers_template',
                _ac_teachers.teachers_template)
bp.add_url_rule('/teachers/import/upload', 'teachers_import_upload',
                _ac_teachers.teachers_import_upload, methods=['POST'])
bp.add_url_rule('/teachers/import/confirm', 'teachers_import_confirm',
                _ac_teachers.teachers_import_confirm, methods=['POST'])
bp.add_url_rule('/teachers/import/passwords/<token>.csv', 'teachers_import_passwords',
                _ac_teachers.teachers_import_passwords)
bp.add_url_rule('/teachers/<int:tid>/edit', 'teacher_edit', _ac_teachers.teacher_edit,
                methods=['GET', 'POST'])


