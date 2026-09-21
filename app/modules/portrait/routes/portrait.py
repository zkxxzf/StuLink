# StuLink v1.17.0 2026-09-21
# 学生画像路由：列表页 / 详情页 / 评语管理 / 事件管理 / 数据API
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from flask import Blueprint, render_template, request, jsonify, abort, flash, redirect, url_for
from flask_login import login_required, current_user

from app.utils.decorators import perm_required
from app.utils.helpers import log_operation
from app.modules.portrait.services import portrait_service

bp = Blueprint('portrait', __name__, url_prefix='/portrait')


# ============ 页面路由 ============

@bp.route('/')
@login_required
@perm_required('portrait.view')
def index():
    """画像列表页"""
    grade = (request.args.get('grade') or '').strip()
    class_name = (request.args.get('class_name') or '').strip()
    level = (request.args.get('level') or '').strip()
    page = int(request.args.get('page') or 1)

    result = portrait_service.get_student_list(
        grade=grade or None,
        class_name=class_name or None,
        level=level or None,
        page=page,
        per_page=20,
    )
    options = portrait_service.get_filter_options()

    return render_template('portrait/index.html',
                           rows=result['rows'],
                           total=result['total'],
                           page=result['page'],
                           pages=result['pages'],
                           grade=grade,
                           class_name=class_name,
                           level=level,
                           options=options)


@bp.route('/<student_no>')
@login_required
@perm_required('portrait.view')
def detail(student_no):
    """画像详情页"""
    data = portrait_service.get_student_detail(student_no)
    if not data:
        abort(404)
    return render_template('portrait/detail.html', data=data)


# ============ 评语管理 ============

@bp.route('/comments/<student_no>')
@login_required
@perm_required('portrait.view')
def comments_list(student_no):
    """获取评语列表（JSON）"""
    from app.modules.portrait.services import portrait_aggregation as agg
    comments = agg.get_portrait_comments(student_no)
    return jsonify(success=True, data=comments)


@bp.route('/comments', methods=['POST'])
@login_required
@perm_required('portrait.edit')
def add_comment():
    """添加评语"""
    data = request.get_json(force=True, silent=True) or {}
    student_no = (data.get('student_no') or '').strip()
    comment_type = (data.get('comment_type') or '学期评语').strip()
    content = (data.get('content') or '').strip()
    term = (data.get('term') or '').strip()

    if not student_no or not content:
        return jsonify(success=False, message='学号和评语内容不能为空')

    result = portrait_service.add_comment(
        student_no=student_no,
        teacher_id=current_user.id,
        comment_type=comment_type,
        content=content,
        term=term,
    )
    log_operation(current_user, '新增', '评语', None,
                  f'学生{student_no}：{content[:30]}...', module='portrait')
    return jsonify(success=True, data=result)


@bp.route('/comments/<int:comment_id>/edit', methods=['POST'])
@login_required
@perm_required('portrait.edit')
def edit_comment(comment_id):
    """编辑评语"""
    data = request.get_json(force=True, silent=True) or {}
    content = (data.get('content') or '').strip()
    comment_type = (data.get('comment_type') or '').strip()
    term = (data.get('term') or '').strip()

    if not content:
        return jsonify(success=False, message='评语内容不能为空')

    # v1.17.0（S2）：归属校验——只能改自己撰写的评语
    ok, _comment, msg = portrait_service.can_manage_comment(comment_id, current_user.id)
    if not ok:
        return jsonify(success=False, message=msg), 403

    result = portrait_service.edit_comment(
        comment_id=comment_id,
        content=content,
        comment_type=comment_type or None,
        term=term or None,
        operator_id=current_user.id,
    )
    if not result:
        return jsonify(success=False, message='评语不存在')

    log_operation(current_user, '编辑', '评语', comment_id,
                  content[:30] + '...', module='portrait')
    return jsonify(success=True, data=result)


@bp.route('/comments/<int:comment_id>/delete', methods=['POST'])
@login_required
@perm_required('portrait.edit')
def delete_comment(comment_id):
    """删除评语"""
    # v1.17.0（S2）：归属校验——只能删自己撰写的评语
    ok, _comment, msg = portrait_service.can_manage_comment(comment_id, current_user.id)
    if not ok:
        return jsonify(success=False, message=msg), 403

    success = portrait_service.delete_comment(comment_id, operator_id=current_user.id)
    if not success:
        return jsonify(success=False, message='评语不存在')
    log_operation(current_user, '删除', '评语', comment_id, '', module='portrait')
    return jsonify(success=True)


# ============ 事件管理 ============

@bp.route('/events/<student_no>')
@login_required
@perm_required('portrait.view')
def events_list(student_no):
    """获取事件列表（JSON）"""
    from app.modules.portrait.services import portrait_aggregation as agg
    events = agg.get_portrait_events(student_no)
    return jsonify(success=True, data=events)


@bp.route('/events', methods=['POST'])
@login_required
@perm_required('portrait.edit')
def add_event():
    """添加事件"""
    data = request.get_json(force=True, silent=True) or {}
    student_no = (data.get('student_no') or '').strip()
    event_type = (data.get('event_type') or '其他').strip()
    title = (data.get('title') or '').strip()
    description = (data.get('description') or '').strip()
    event_date = (data.get('event_date') or '').strip()

    if not student_no or not title or not event_date:
        return jsonify(success=False, message='学号、标题和日期不能为空')

    result = portrait_service.add_event(
        student_no=student_no,
        event_type=event_type,
        title=title,
        description=description,
        event_date=event_date,
        evidence=data.get('evidence'),
        created_by=current_user.id,
    )
    log_operation(current_user, '新增', '事件', None,
                  f'学生{student_no}：{title}', module='portrait')
    return jsonify(success=True, data=result)


@bp.route('/events/<int:event_id>/delete', methods=['POST'])
@login_required
@perm_required('portrait.edit')
def delete_event(event_id):
    """删除事件"""
    # v1.17.0（S2）：归属校验——只能删自己创建的事件
    ok, _event, msg = portrait_service.can_manage_event(event_id, current_user.id)
    if not ok:
        return jsonify(success=False, message=msg), 403

    success = portrait_service.delete_event(event_id, operator_id=current_user.id)
    if not success:
        return jsonify(success=False, message='事件不存在')
    log_operation(current_user, '删除', '事件', event_id, '', module='portrait')
    return jsonify(success=True)


# ============ 数据API ============

@bp.route('/api/<student_no>/data')
@login_required
@perm_required('portrait.view')
def api_portrait_data(student_no):
    """画像完整数据（JSON，用于前端图表）"""
    data = portrait_service.get_student_detail(student_no)
    if not data:
        return jsonify(success=False, message='学生不存在')
    return jsonify(success=True, data=data)


@bp.route('/api/list')
@login_required
@perm_required('portrait.view')
def api_list():
    """列表数据（JSON，支持筛选分页）"""
    grade = (request.args.get('grade') or '').strip()
    class_name = (request.args.get('class_name') or '').strip()
    level = (request.args.get('level') or '').strip()
    page = int(request.args.get('page') or 1)
    per_page = int(request.args.get('per_page') or 20)

    result = portrait_service.get_student_list(
        grade=grade or None,
        class_name=class_name or None,
        level=level or None,
        page=page,
        per_page=per_page,
    )

    # 转换为JSON友好格式
    rows = []
    for r in result['rows']:
        s = r['student']
        p = r['portrait']
        rows.append({
            'student_no': s.student_number,
            'name': s.name,
            'grade': s.grade,
            'class_name': s.class_name,
            'gender': s.gender,
            'portrait': p,
        })

    return jsonify(success=True, data={
        'rows': rows,
        'total': result['total'],
        'page': result['page'],
        'per_page': result['per_page'],
        'pages': result['pages'],
    })


@bp.route('/api/<student_no>/refresh', methods=['POST'])
@login_required
@perm_required('portrait.edit')
def api_refresh(student_no):
    """重新计算画像"""
    result = portrait_service.calculate_portrait(student_no)
    if not result:
        return jsonify(success=False, message='学生不存在')
    log_operation(current_user, '刷新', '画像', None,
                  f'学生{student_no}', module='portrait')
    return jsonify(success=True, data=result)


@bp.route('/api/options')
@login_required
@perm_required('portrait.view')
def api_options():
    """获取筛选选项"""
    options = portrait_service.get_filter_options()
    return jsonify(success=True, data=options)
