# StuLink v1.6.1 2026-07-09
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0
from flask import Blueprint, render_template, request
from flask_login import login_required, current_user
from app.models import OperationLog, User
from app.extensions import db

bp = Blueprint('operation_logs', __name__, url_prefix='/operation-logs')


@bp.route('/')
@login_required
def list_logs():
    page = request.args.get('page', 1, type=int)
    per_page = 20

    query = OperationLog.query

    action_filter = request.args.get('action')
    if action_filter:
        query = query.filter_by(action=action_filter)

    module_filter = request.args.get('module')
    if module_filter:
        query = query.filter_by(module=module_filter)

    target_type_filter = request.args.get('target_type')
    if target_type_filter:
        query = query.filter_by(target_type=target_type_filter)

    user_id_filter = request.args.get('user_id', type=int)
    if user_id_filter:
        query = query.filter_by(user_id=user_id_filter)

    date_start = request.args.get('date_start')
    date_end = request.args.get('date_end')
    if date_start:
        query = query.filter(OperationLog.created_at >= date_start)
    if date_end:
        query = query.filter(OperationLog.created_at <= date_end + ' 23:59:59')

    search_keyword = request.args.get('keyword', '').strip()
    if search_keyword:
        query = query.filter(OperationLog.detail.like(f'%{search_keyword}%'))

    pagination = query.order_by(OperationLog.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    users = User.query.all()

    actions = ['创建', '更新', '删除', '导入', '导出', '登录']
    modules = ['system', 'dormitory', 'points', 'grades']

    target_types = db.session.query(OperationLog.target_type).distinct().all()
    target_types = [t[0] for t in target_types if t[0]]

    logs_with_detail = []
    for log in pagination.items:
        detail_data = None
        if log.detail:
            try:
                import json
                detail_data = json.loads(log.detail)
            except:
                pass
        logs_with_detail.append({
            'log': log,
            'detail_data': detail_data
        })

    return render_template('system/operation_logs/list.html',
                           logs=logs_with_detail,
                           pagination=pagination,
                           users=users,
                           actions=actions,
                           modules=modules,
                           target_types=target_types,
                           current_action=action_filter,
                           current_module=module_filter,
                           current_target_type=target_type_filter,
                           current_user_id=user_id_filter,
                           date_start=date_start,
                           date_end=date_end,
                           keyword=search_keyword)
