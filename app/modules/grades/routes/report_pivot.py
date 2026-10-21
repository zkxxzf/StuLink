# StuLink v1.19.50.63 2026-10-10
# 成绩汇报区路由（板块三/四）：单班各科、单科各班
# 权限/缓存口径与 report.py 完全一致；缓存随 invalidate_exam_cache 全量失效
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import hashlib

from flask import request, jsonify
from flask_login import login_required, current_user

from app.modules.grades import bp
from app.modules.grades.services import (report_pivot_service, report_service,
                                         report_warning_service, report_teacher_service,
                                         score_guard)
from app.modules.grades.routes.report import _guard_report, _visible_exam, _cached
from app.utils.decorators import perm_required


@bp.route('/api/report/class-subject')
@login_required
@perm_required('grades.view')
def api_report_class_subject():
    """图3：某班各科成绩（教师/特控单双/本科单/去差均分，末行总分）"""
    _guard_report()
    exam_id = request.args.get('exam_id', type=int)
    class_name = (request.args.get('class_name') or '').strip()
    _visible_exam(exam_id)
    if not class_name:
        return jsonify(success=False, message='缺少 class_name 参数')
    data = _cached(f'grades_report_cs_{exam_id}_{class_name}',
                   lambda: report_pivot_service.class_subject_report(exam_id, class_name))
    return jsonify(success='error' not in data, data=data,
                   message=data.get('error') if 'error' in data else '')


@bp.route('/api/report/subject-classes')
@login_required
@perm_required('grades.view')
def api_report_subject_classes():
    """图4：某方向某科各班成绩（教师/特控单双/本科单/去差均分）"""
    _guard_report()
    exam_id = request.args.get('exam_id', type=int)
    direction = (request.args.get('direction') or '').strip()
    subject = (request.args.get('subject') or '').strip()
    _visible_exam(exam_id)
    if not subject:
        return jsonify(success=False, message='缺少 subject 参数')
    data = _cached(f'grades_report_sc_{exam_id}_{direction}_{subject}',
                   lambda: report_pivot_service.subject_class_report(exam_id, direction, subject))
    return jsonify(success='error' not in data, data=data,
                   message=data.get('error') if 'error' in data else '')


@bp.route('/api/report/near-line')
@login_required
@perm_required('grades.view')
def api_report_near_line():
    """图5：压线提醒——层线上下浮动范围内临界生名单（各科成绩+方向排名）

    数据范围（2026-10-15 用户口径）：名单是**逐生总分/各科明细**，按"总分口径"收敛——
    班主任只见本班、年级长及以上见整年级/全校；**任课教师虽有"年级×学科"的本科明细
    授权，但总分口径不对其开放**（2026-10-15 用户口径："只暴露名次，不暴露分数"），
    故名单为空。汇报区其它板块是班级/学科级汇总（成绩级收敛），本板块除外。
    缓存键带范围签名，避免不同账号串数据。
    """
    _guard_report()
    exam_id = request.args.get('exam_id', type=int)
    direction = (request.args.get('direction') or '').strip()
    layer = (request.args.get('layer') or '').strip()
    # n/m 缺省走配置区默认值；越界自动夹取，防止一次拖全年级
    cfg_max = report_service.NEAR_LINE_MAX
    above = request.args.get('above', default=report_service.NEAR_LINE_ABOVE, type=int)
    below = request.args.get('below', default=report_service.NEAR_LINE_BELOW, type=int)
    above = max(0, min(above, cfg_max))
    below = max(0, min(below, cfg_max))
    exam = _visible_exam(exam_id)
    try:                                   # fail-closed：解析不出范围就不放行
        scope = score_guard.resolve(current_user)
    except PermissionError:
        return jsonify(success=False, message='无权查看该考试的成绩'), 403
    # 2026-10-15：任课授权＝"年级 × 学科"，`visible_classes()` 对有任课的年级返回 None
    # （班级不成约束，但总分仍不许给）。压线提醒是**逐生总分**名单 → 必须按总分口径
    # 收班级：`total_visible_classes()` 给班主任＝本班、任课教师＝空集、校级＝None。
    allowed = scope.total_visible_classes(exam.grade)      # None = 整年级/全校
    if allowed is None:
        skey = 'all'
    else:
        skey = hashlib.md5(','.join(sorted(allowed)).encode('utf-8')).hexdigest()[:8]
    data = _cached(f'grades_report_nl_{exam_id}_{direction}_{layer}_{above}_{below}_{skey}',
                   lambda: report_warning_service.near_line_report(
                       exam_id, direction, layer, above, below,
                       allowed_classes=allowed))
    return jsonify(success='error' not in data, data=data,
                   message=data.get('error') if 'error' in data else '')


@bp.route('/api/report/teacher-ranks')
@login_required
@perm_required('grades.view')
def api_report_teacher_ranks():
    """教师×学科 排名表（各板块表头勾选指标后展示教师名次的统一数据源）"""
    _guard_report()
    exam_id = request.args.get('exam_id', type=int)
    direction = (request.args.get('direction') or '').strip()
    _visible_exam(exam_id)
    data = _cached(f'grades_report_tr_{exam_id}_{direction}',
                   lambda: report_teacher_service.teacher_rank_report(exam_id, direction))
    return jsonify(success='error' not in data, data=data,
                   message=data.get('error') if 'error' in data else '')
