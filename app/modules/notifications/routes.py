# StuLink v1.9.3 2026-09-18
# 通知公告路由（独立蓝图，url_prefix=/notifications）
# v2.0 收件人表改造：列表筛选/已读进度/用户搜索/分类字典
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import json

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user

from app.extensions import db
from app.models.notification import Notification, CATEGORY_LABELS
from app.models.user import User
from app.utils.decorators import perm_required
from app.modules.notifications.services.notification_service import (
    create_notification,
    get_notifications_for_user,
    mark_read,
    mark_all_read,
    get_unread_count_accurate,
    delete_notification,
    get_notification_detail,
    get_read_progress,
    resolve_recipients,
    _user_matches_target,
)

bp = Blueprint('notifications', __name__, url_prefix='/notifications')

# 目标群体选项
TARGET_TYPES = [
    ('all', '全体人员'),
    ('grade', '指定年级'),
    ('class', '指定班级'),
    ('role', '指定角色'),
    ('users', '指定人员'),
]

# 角色选项（用于创建通知时的下拉列表）
ROLE_OPTIONS = [
    ('admin', '管理员'),
    ('dorm_manager', '宿管教师'),
    ('homeroom_teacher', '班主任'),
    ('grade_leader', '年级长'),
    ('teacher', '普通教师'),
    ('staff', '教职人员'),
]

# 年级选项（与 DICT_DATA['grade'] 保持一致）
GRADE_OPTIONS = ['2025级', '2024级', '2023级']

# 班级选项（常用班级）
CLASS_OPTIONS = [f'{i:02d}班' for i in range(1, 11)]


@bp.route('/')
@login_required
def notifications_page():
    """通知列表页"""
    page = request.args.get('page', 1, type=int)
    category = request.args.get('category', '').strip() or None
    only_unread = request.args.get('only_unread', '') in ('1', 'true', 'on')
    result = get_notifications_for_user(
        current_user, page=page, per_page=20,
        category=category, only_unread=only_unread)
    return render_template(
        'notifications/list.html',
        notifications=result['items'],
        page=result['page'],
        total_pages=result['total_pages'],
        total=result['total'],
        target_types=TARGET_TYPES,
        role_options=ROLE_OPTIONS,
        grade_options=GRADE_OPTIONS,
        class_options=CLASS_OPTIONS,
        category_labels=CATEGORY_LABELS,
        current_category=category or '',
        only_unread=only_unread,
        can_create=current_user.has_perm('system.settings'),
    )


@bp.route('/create', methods=['POST'])
@login_required
@perm_required('system.settings')
def notification_create():
    """创建通知（仅管理员/有 system.settings 权限的用户）"""
    title = (request.form.get('title') or '').strip()
    content = (request.form.get('content') or '').strip()
    target_type = (request.form.get('target_type') or 'all').strip()
    priority = (request.form.get('priority') or 'normal').strip()
    category = (request.form.get('category') or 'system').strip()
    link_url = (request.form.get('link_url') or '').strip() or None

    if not title or not content:
        flash('标题和内容不能为空', 'danger')
        return redirect(url_for('notifications.notifications_page'))

    # 构建 target_scope JSON
    scope = {}
    target_uids = []
    if target_type == 'grade':
        grades = request.form.getlist('target_grades')
        scope['grades'] = grades
    elif target_type == 'class':
        classes = request.form.getlist('target_classes')
        scope['classes'] = classes
    elif target_type == 'role':
        roles = request.form.getlist('target_roles')
        scope['roles'] = roles
    elif target_type == 'users':
        target_uids = request.form.getlist('target_uids')

    target_scope = json.dumps(scope, ensure_ascii=False)

    create_notification(
        title=title,
        content=content,
        target_type=target_type,
        target_scope=target_scope,
        priority=priority,
        published_by=current_user.id,
        target_uids=target_uids if target_type == 'users' else None,
        category=category,
        link_url=link_url,
    )
    flash('通知已发布', 'success')
    return redirect(url_for('notifications.notifications_page'))


@bp.route('/<int:notification_id>/read', methods=['POST'])
@login_required
def notification_read(notification_id):
    """标记单条通知为已读"""
    mark_read(notification_id, current_user.id)
    return jsonify({'ok': True})


@bp.route('/read-all', methods=['POST'])
@login_required
def notification_read_all():
    """全部标记已读"""
    count = mark_all_read(current_user.id)
    flash(f'已将 {count} 条通知标记为已读', 'success')
    return redirect(url_for('notifications.notifications_page'))


@bp.route('/<int:notification_id>/delete', methods=['POST'])
@login_required
@perm_required('system.settings')
def notification_delete(notification_id):
    """删除通知（仅管理员）"""
    delete_notification(notification_id)
    flash('通知已删除', 'success')
    return redirect(url_for('notifications.notifications_page'))


@bp.route('/api/unread-count')
@login_required
def unread_count_api():
    """获取当前用户未读通知数（JSON，供铃铛轮询）"""
    count = get_unread_count_accurate(current_user)
    return jsonify({'count': count})


@bp.route('/<int:notification_id>')
@login_required
def notification_detail(notification_id):
    """通知详情页"""
    result = get_notification_detail(notification_id, user=current_user)
    if result is None or (isinstance(result, tuple) and result[0] is None):
        flash('通知不存在或已删除', 'warning')
        return redirect(url_for('notifications.notifications_page'))
    if isinstance(result, tuple):
        notif, meta = result
        if meta is None:
            flash('您没有权限查看此通知', 'warning')
            return redirect(url_for('notifications.notifications_page'))
    else:
        # 旧签名返回仅 notif（user=None），走旧可见性检查
        notif = result
        if not notif or not notif.is_active:
            flash('通知不存在或已删除', 'warning')
            return redirect(url_for('notifications.notifications_page'))
        if not _user_matches_target(current_user, notif):
            flash('您没有权限查看此通知', 'warning')
            return redirect(url_for('notifications.notifications_page'))
        meta = {'can_manage': False, 'progress': None}
    return render_template('notifications/detail.html', notification=notif, meta=meta)


@bp.route('/<int:nid>/progress')
@login_required
def notification_progress(nid):
    """已读进度页（发布者/管理员可见）"""
    notif = db.session.get(Notification, nid)
    if not notif or not notif.is_active:
        flash('通知不存在', 'warning')
        return redirect(url_for('notifications.notifications_page'))
    can_manage = bool(current_user.role == 'admin'
                      or current_user.has_perm('system.settings')
                      or notif.published_by == current_user.id)
    if not can_manage:
        flash('您没有权限查看此通知的已读进度', 'warning')
        return redirect(url_for('notifications.notification_detail', notification_id=nid))
    progress = get_read_progress(nid)
    return render_template('notifications/progress.html',
                           notification=notif, progress=progress)


@bp.route('/api/search-users')
@login_required
def api_search_users():
    """用户搜索（供「指定人员」多选用）
    返回 JSON: [{uid, name, role, grade, class_name}, ...]
    """
    keyword = (request.args.get('keyword') or '').strip()
    if not keyword or len(keyword) < 1:
        return jsonify([])
    kw = f'%{keyword}%'
    users = (User.query
             .filter(User.is_active.is_(True),
                     db.or_(User.real_name.like(kw),
                            User.username.like(kw)))
             .limit(20).all())
    results = [{'uid': u.username, 'name': u.real_name or u.username,
                'role': u.role or '', 'grade': u.grade or '',
                'class_name': u.class_name or ''} for u in users]
    return jsonify(results)


@bp.route('/api/categories')
@login_required
def api_categories():
    """分类字典 JSON"""
    return jsonify(CATEGORY_LABELS)
