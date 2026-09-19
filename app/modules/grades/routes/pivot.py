# StuLink v1.9.3 2026-09-19
# 自由表：行维度 / 列维度 / 指标 自由组合的交叉分析
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from flask import render_template, request, jsonify, abort
from flask_login import login_required, current_user

from app.models.grades import Exam, TOTAL_SUBJECT
from app.models import DictCategory
from app.modules.grades import bp
from app.modules.grades.services import pivot_service as pv
from app.modules.grades.services import scope as scope_service
from app.modules.grades.services.stats_service import cached_exam_data
from app.utils.decorators import perm_required


@bp.route('/pivot')
@login_required
@perm_required('grades.view')
def pivot_page():
    """自由表页面"""
    return render_template('grades/pivot.html')


@bp.route('/api/pivot/meta')
@login_required
@perm_required('grades.view')
def api_pivot_meta():
    """可选考试 / 维度 / 指标定义"""
    grades = scope_service.visible_grades(current_user)
    exams = (Exam.query.filter(Exam.grade.in_(grades))
             .order_by(Exam.exam_date.desc(), Exam.id.desc()).all()) if grades else []
    subjects = []
    cat = DictCategory.query.filter_by(code='exam_subject').first()
    if cat:
        subjects = [i.value for i in cat.items.filter_by(is_active=True)
                    .order_by('sort_order').all()]
    return jsonify(success=True, data={
        'grades': grades,
        'exams': [{'id': e.id, 'name': e.name, 'grade': e.grade,
                   'date': e.exam_date.strftime('%Y-%m-%d')} for e in exams],
        'subjects': subjects,
        'dims': [{'key': k, 'label': v} for k, v in pv.DIM_LABELS.items()],
        'measures': [{'key': k, 'label': n, 'need_layer': a, 'need_line': b}
                     for k, n, a, b in pv.MEASURE_DEFS],
    })


@bp.route('/api/pivot')
@login_required
@perm_required('grades.view')
def api_pivot():
    """交叉统计；参数 exam_id / row / col / subject / direction / measures(逗号分隔)"""
    exam_id = request.args.get('exam_id', type=int)
    if not exam_id:
        return jsonify(success=False, message='请先选择考试')
    row_dim = (request.args.get('row') or 'class').strip()
    col_dim = (request.args.get('col') or '').strip()
    subject = (request.args.get('subject') or TOTAL_SUBJECT).strip()
    direction = (request.args.get('direction') or '').strip()
    measures = [m.strip() for m in (request.args.get('measures') or '').split(',') if m.strip()]

    exam = Exam.query.get_or_404(exam_id)
    try:
        scope_service.check_exam_visible(current_user, exam)
    except PermissionError:
        abort(403)

    data = cached_exam_data(exam_id)
    if not data.total_rows:
        return jsonify(success=False, message='该考试尚未导入成绩')
    res = pv.pivot_table(data, row_dim, col_dim, measures,
                         subject=subject, direction=direction)
    if 'error' in res:
        return jsonify(success=False, message=res['error'])
    res['exam'] = {'id': exam.id, 'name': exam.name, 'grade': exam.grade,
                   'date': exam.exam_date.strftime('%Y-%m-%d')}
    res['subject'] = subject
    res['direction'] = direction
    return jsonify(success=True, data=res)
