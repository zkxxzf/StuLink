<<<<<<< HEAD
# StuLink v1.13.0 2026-09-14
=======
# StuLink v1.9.3 2026-09-19
>>>>>>> 4934b4a0230dda5c541daf86b4b3dcc06612141c
# 成绩汇报区：对标年级汇报 PPT 的网页表格（可框选复制粘贴进 PPT）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from flask import render_template, request, jsonify, abort
from flask_login import login_required, current_user

from app.models.grades import Exam
from app.modules.grades import bp
from app.modules.grades.services import (report_service, report_export_service,
                                         scope as scope_service)
from app.utils.decorators import perm_required
from app.utils.cache import cache


@bp.route('/report')
@login_required
@perm_required('grades.view')
def report_page():
    """汇报区主页（从考试分析页携 exam_id 跳入）"""
    return render_template('grades/report.html')


def _visible_exam(exam_id):
    exam = Exam.query.get(exam_id)
    if not exam:
        abort(404)
    try:
        scope_service.check_exam_visible(current_user, exam)
    except PermissionError:
        abort(403)
    return exam


def _guard_report():
    """班主任/任课教师不开放年级汇报；管理员/校领导/年级长可用"""
    if current_user.role == 'admin':
        return
    if scope_service.has_user_scope(current_user):
        # v1.9.2 用户级数据范围（如教务员按年级）：数据已限授权年级
        return
    scope_type, _ = scope_service.get_scope(current_user)
    if scope_type in ('school', 'grade'):
        return
    abort(403)


# 汇报数据随导入/划线/单条改分变化，相关入口统一调 invalidate_exam_cache：
# 先清该考试 grades_tab_ 缓存，再全清 grades_report_ 汇报缓存（跨考试对比也随之刷新）
CACHE_TIMEOUT = 900


def _cached(key, builder):
    data = cache.get(key)
    if data is None:
        data = builder()
        cache.set(key, data, timeout=CACHE_TIMEOUT)
    return data


@bp.route('/api/report/subject-layer')
@login_required
@perm_required('grades.view')
def api_report_subject_layer():
    """图1：某方向各科层上线（单/双上线、本次 vs 上次、去差均分）"""
    _guard_report()
    exam_id = request.args.get('exam_id', type=int)
    direction = (request.args.get('direction') or '').strip()
    layer = (request.args.get('layer') or '').strip()
    compare_exam_id = request.args.get('compare_exam_id', type=int)
    _visible_exam(exam_id)
    key = f'grades_report_sl_{exam_id}_{direction}_{layer}_{compare_exam_id or ""}'
    data = _cached(key, lambda: report_service.subject_layer_report(
        exam_id, direction, layer, compare_exam_id))
    return jsonify(success='error' not in data, data=data,
                   message=data.get('error') if 'error' in data else '')


@bp.route('/api/report/class-overview')
@login_required
@perm_required('grades.view')
def api_report_class_overview():
    """图2：班级概况（班主任/参考数/各层上线/去差均分）"""
    _guard_report()
    exam_id = request.args.get('exam_id', type=int)
    direction = (request.args.get('direction') or '').strip()
    _visible_exam(exam_id)
    data = _cached(f'grades_report_co_{exam_id}_{direction}',
                   lambda: report_service.class_overview_report(exam_id, direction))
    return jsonify(success='error' not in data, data=data,
                   message=data.get('error') if 'error' in data else '')


@bp.route('/api/report/export-pptx', methods=['POST'])
@login_required
@perm_required('grades.view')
def api_report_export_pptx():
    """v1.13.1 交付物：前端把当前渲染的表格（含名次列/红绿字颜色）序列化上传，
    后端用 python-pptx 一表一页生成 PowerPoint 下载。仅渲染无业务计算，不缓存。"""
    _guard_report()
    payload = request.get_json(silent=True) or {}
    tables = payload.get('tables') or []
    if not tables:
        return jsonify(success=False, message='没有可导出的表格'), 400
    # 防御：限制规模，避免超大 body 拖垮进程（五表×几十行远小于该上限）
    if len(tables) > 12 or sum(len(t.get('rows') or []) for t in tables) > 4000:
        return jsonify(success=False, message='导出内容过大'), 400
    data = report_export_service.build_pptx(tables)
    from urllib.parse import quote
    from flask import Response
    fname = quote('成绩汇报.pptx')
    resp = Response(data, mimetype='application/vnd.openxmlformats-officedocument'
                                '.presentationml.presentation')
    resp.headers['Content-Disposition'] = f"attachment; filename=report.pptx; filename*=UTF-8''{fname}"
    return resp
