# StuLink v1.13.0 2026-09-14
# 成绩汇报区路由（板块三/四）：单班各科、单科各班
# 权限/缓存口径与 report.py 完全一致；缓存随 invalidate_exam_cache 全量失效
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from flask import request, jsonify
from flask_login import login_required

from app.modules.grades import bp
from app.modules.grades.services import (report_pivot_service, report_service,
                                         report_warning_service, report_teacher_service)
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
    """图5：压线提醒——层线上下浮动范围内临界生名单（各科成绩+方向排名）"""
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
    _visible_exam(exam_id)
    data = _cached(f'grades_report_nl_{exam_id}_{direction}_{layer}_{above}_{below}',
                   lambda: report_warning_service.near_line_report(
                       exam_id, direction, layer, above, below))
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
