# StuLink v1.5.0 2026-07-01
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0
import secrets
from flask import Blueprint, render_template, redirect, url_for, flash
from flask_login import login_required, current_user
from app.extensions import db
from app.models import User
from app.forms.user_forms import UserForm
from app.utils.decorators import role_required
from app.utils.helpers import log_operation

bp = Blueprint('users', __name__, url_prefix='/users')


def _generate_password():
    """生成随机安全密码（10位字母数字）"""
    import string
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(10))


@bp.route('/')
@role_required('admin')
def list_users():
    users = User.query.order_by(User.role, User.username).all()
    return render_template('system/users/list.html', users=users)


@bp.route('/create', methods=['GET', 'POST'])
@role_required('admin')
def create():
    form = UserForm()
    if form.validate_on_submit():
        if User.query.filter_by(username=form.username.data).first():
            flash('用户名已存在', 'danger')
            return render_template('system/users/form.html', form=form, title='新建用户')

        # 校验：班主任最多3人/班，且年级班级必填
        role = form.role.data
        grade = form.grade.data or None
        class_name = form.class_name.data or None

        if role == 'homeroom_teacher':
            if not grade or not class_name:
                flash('班主任必须指定年级和班级', 'danger')
                return render_template('system/users/form.html', form=form, title='新建用户')
            existing_count = User.query.filter_by(
                role='homeroom_teacher', grade=grade, class_name=class_name, is_active=True
            ).count()
            if existing_count >= 3:
                flash(f'{grade}{class_name} 已有 {existing_count} 名班主任，最多设置3名', 'danger')
                return render_template('system/users/form.html', form=form, title='新建用户')

        if role == 'grade_leader' and not grade:
            flash('年级长必须指定管理年级', 'danger')
            return render_template('system/users/form.html', form=form, title='新建用户')

        user = User(
            username=form.username.data,
            real_name=form.real_name.data,
            role=role,
            grade=grade,
            class_name=class_name,
            permission_group_id=form.permission_group_id.data if form.permission_group_id.data else None,
            must_change_pwd=True,
        )
        generated_pwd = form.password.data or _generate_password()
        user.set_password(generated_pwd)
        db.session.add(user)
        db.session.commit()
        log_operation(current_user, '创建', '用户', user.id, f'{user.real_name} ({user.role_display})')
        pwd_hint = f'，初始密码：{generated_pwd}' if not form.password.data else ''
        flash(f'用户 {user.real_name} 已创建{pwd_hint}', 'success')
        return redirect(url_for('users.list_users'))
    return render_template('system/users/form.html', form=form, title='新建用户')


@bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@role_required('admin')
def edit(id):
    user = User.query.get_or_404(id)
    form = UserForm(obj=user)
    if form.validate_on_submit():
        existing = User.query.filter(
            User.username == form.username.data, User.id != user.id).first()
        if existing:
            flash('用户名已存在', 'danger')
            return render_template('system/users/form.html', form=form, title='编辑用户')

        # 校验
        role = form.role.data
        grade = form.grade.data or None
        class_name = form.class_name.data or None

        if role == 'homeroom_teacher':
            if not grade or not class_name:
                flash('班主任必须指定年级和班级', 'danger')
                return render_template('system/users/form.html', form=form, title='编辑用户')
            existing_count = User.query.filter_by(
                role='homeroom_teacher', grade=grade, class_name=class_name, is_active=True
            ).filter(User.id != user.id).count()
            if existing_count >= 3:
                flash(f'{grade}{class_name} 已有 {existing_count} 名班主任，最多设置3名', 'danger')
                return render_template('system/users/form.html', form=form, title='编辑用户')

        if role == 'grade_leader' and not grade:
            flash('年级长必须指定管理年级', 'danger')
            return render_template('system/users/form.html', form=form, title='编辑用户')

        user.username = form.username.data
        user.real_name = form.real_name.data
        user.role = role
        user.grade = grade
        user.class_name = class_name
        user.permission_group_id = form.permission_group_id.data if form.permission_group_id.data else None
        if form.password.data:
            user.set_password(form.password.data)
        db.session.commit()
        log_operation(current_user, '更新', '用户', user.id, f'{user.real_name} 信息已更新')
        flash('用户信息已更新', 'success')
        return redirect(url_for('users.list_users'))
    return render_template('system/users/form.html', form=form, title='编辑用户')


@bp.route('/<int:id>/toggle', methods=['POST'])
@role_required('admin')
def toggle(id):
    user = User.query.get_or_404(id)
    user.is_active = not user.is_active
    db.session.commit()
    status = '启用' if user.is_active else '禁用'
    log_operation(current_user, '更新', '用户', user.id, f'{user.real_name} {status}')
    flash(f'用户 {user.real_name} 已{status}', 'success')
    return redirect(url_for('users.list_users'))


@bp.route('/<int:id>/reset-password', methods=['POST'])
@role_required('admin')
def reset_password(id):
    user = User.query.get_or_404(id)
    user.set_password(_generate_password())
    user.must_change_pwd = True
    db.session.commit()
    log_operation(current_user, '更新', '用户', user.id, f'{user.real_name} 密码已重置')
    flash(f'{user.real_name} 的密码已重置', 'success')
    return redirect(url_for('users.list_users'))
