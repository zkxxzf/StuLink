# -*- coding: utf-8 -*-
# StuLink v1.18.2.0 2026-09-24
# users ↔ academic.teachers 双向同步钩子（保证两页数据一致）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""
- sync_from_user(user, renamed_from=None):
    在 users.create/edit/toggle 中调用；admin 角色跳过；教师类角色确保 teachers 有对应记录
- sync_from_teacher(teacher, renamed_from=None):
    在 academic.teacher_edit 中调用；把 teachers 的姓名/手机号/状态回写到 users

角色口径与 scripts/sync_users_to_teachers.py 保持一致：
    TEACHER_ROLES = ('teacher','homeroom_teacher','grade_leader',
                     'dorm_manager','school_viewer','staff')
"""
import re

from app.extensions import db
from app.models import User
from app.models.academic import Teacher
from app.modules.academic.services import teacher_service

PHONE_RE = re.compile(r'^1[3-9]\d{9}$')
TEACHER_ROLES = ('teacher', 'homeroom_teacher', 'grade_leader',
                 'dorm_manager', 'school_viewer', 'staff')


def is_teacher_role(role):
    return role in TEACHER_ROLES


def sync_from_user(user, renamed_from=None):
    """用户创建/编辑/启禁后调用；返回 (created_or_updated: str, teacher: Teacher|None)

    - created='new'      : 新建了一条 Teacher
    - created='updated'  : 更新已有 Teacher 的姓名/手机/状态
    - created='skip'     : 角色非教师类（含 admin），或已完全一致
    """
    if user.role not in TEACHER_ROLES:
        return 'skip', None
    phone_like = user.username if PHONE_RE.match(user.username or '') else None

    t = Teacher.query.filter_by(user_id=user.id).first()
    if t:
        changed = False
        if user.real_name and t.name != user.real_name:
            t.name = user.real_name
            changed = True
        if phone_like and (not t.phone):
            t.phone = phone_like
            changed = True
        want_status = 'active' if user.is_active else 'left'
        if t.status != want_status:
            t.status = want_status
            changed = True
        if renamed_from:
            # 老名字与新名字不同（改名场景已并入上面的 t.name = ...）
            pass
        return ('updated' if changed else 'skip'), t

    # 尝试按 phone 或 name 关联已存在但未关联 user_id 的 Teacher
    m = None
    if phone_like:
        m = Teacher.query.filter_by(phone=phone_like, user_id=None).first()
    if not m and user.real_name:
        m = Teacher.query.filter_by(name=user.real_name, user_id=None).first()
    if m:
        m.user_id = user.id
        if phone_like and not m.phone:
            m.phone = phone_like
        want_status = 'active' if user.is_active else 'left'
        if m.status != want_status:
            m.status = want_status
        return 'updated', m

    # 全新
    uid = teacher_service.gen_teacher_uid()
    t = Teacher(teacher_uid=uid, name=user.real_name, phone=phone_like,
                subject=None, status='active' if user.is_active else 'left',
                user_id=user.id,
                note='由 users 页自动同步')
    db.session.add(t)
    return 'new', t


def sync_from_teacher(teacher, renamed_from=None):
    """教务端教师档案变更后调用；把姓名/手机号/状态回写到关联的 user

    返回变更摘要 list[str]，无变更返回 []。
    """
    if not teacher or not teacher.user_id:
        return []
    u = User.query.get(teacher.user_id)
    if not u or u.role == 'admin':
        return []
    changes = []
    if teacher.name and u.real_name != teacher.name:
        # 与 users.edit 保持一致策略：真实姓名与任课/成绩归属绑定，谨慎同步
        # 这里教务端主动改名视为权威修改，同步 users.real_name
        old = u.real_name
        u.real_name = teacher.name
        changes.append(f'账号姓名 {old}→{teacher.name}')
    if teacher.phone and PHONE_RE.match(teacher.phone) and u.username != teacher.phone:
        # username 是唯一登录名，历史可能不是手机号；不改登录名（会破坏登录）
        # 仅在 username 与 teacher.phone 不同但都是手机号时才提示（不自动改）
        changes.append(f'手机号已改但登录名不变（登录名={u.username}）')
    want_active = (teacher.status == 'active')
    if bool(u.is_active) != want_active:
        u.is_active = want_active
        changes.append('账号启用状态已同步')
    if changes:
        db.session.flush()
    return changes
