# StuLink v1.7.0 2026-08-02
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import json
import logging

from flask import Blueprint, render_template, request
from flask_login import login_required, current_user
from app.models import OperationLog, User
from app.extensions import db
from app.utils.decorators import perm_required

logger = logging.getLogger(__name__)
# 2026-10-10 架构审计修复：审计日志 detail 解析原为**裸 except**（PEP 8 严禁，
# 会连 KeyboardInterrupt/SystemExit 一起吞掉），且失败不可观测。
# 现改为精确异常类型 + logger 留痕；降级行为不变（解析失败仍按 None 处理）。

bp = Blueprint('operation_logs', __name__, url_prefix='/operation-logs')

# PERF-07：操作日志页的三个筛选下拉（action / module / target_type）原先每次翻页
# 都各做一次全表 DISTINCT 扫描。这三个值域极小，且只有新增模块或新操作类型时才会变，
# 缓存 600s 完全够；缓存的是纯字符串列表，不涉及 ORM 对象（避免 detach/expire 问题）。
_LOG_ENUMS_KEY = 'oplog_filter_enums_v1'
_LOG_ENUMS_TTL = 600


def _cached_log_enums():
    """日志筛选下拉的取值枚举（带进程内缓存，返回 3 个 sorted list）"""
    from app.utils.cache import cache
    cached_value = cache.get(_LOG_ENUMS_KEY)
    if cached_value is not None:
        return cached_value
    actions = sorted({a[0] for a in
                      db.session.query(OperationLog.action).distinct().all() if a[0]})
    modules = sorted({m[0] for m in
                      db.session.query(OperationLog.module).distinct().all() if m[0]})
    target_types = sorted({t[0] for t in
                           db.session.query(OperationLog.target_type).distinct().all()
                           if t[0]})
    enums = (actions, modules, target_types)
    cache.set(_LOG_ENUMS_KEY, enums, timeout=_LOG_ENUMS_TTL)
    return enums


@bp.route('/')
@perm_required('system.users')   # M-1：审计日志不再对全体登录用户开放
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

    # 审计加固：module 下拉不再硬编码 4 个值（academic/workbench/portrait/auth
    # 等模块的日志此前在页面上筛不出来），改为按库中实际值动态列出。
    # PERF-07：三个值域的 DISTINCT 都是全表扫描，原先每次翻页都重算一遍；
    # 值域极小且只有新增模块/操作类型才会变，缓存 600s 足够（见 _cached_log_enums）。
    actions, modules, target_types = _cached_log_enums()

    logs_with_detail = []
    for log in pagination.items:
        detail_data = None
        if log.detail:
            try:
                detail_data = json.loads(log.detail)
            except (ValueError, TypeError) as _e:
                # detail 非 JSON（历史明文记录）属预期情况，debug 级即可，不刷屏
                logger.debug('审计日志 detail 非 JSON（log id=%s）: %s', log.id, _e)
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
