# StuLink v1.19.49.49 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序自由分析（透视）：与网页 `/grades/pivot` 同一个 `pivot_service`。

网页是「行维度 / 列维度 / 指标」三组下拉 + 表格；小程序保持同一套维度与指标
定义（直接读 `pivot_service.DIM_LABELS / MEASURE_DEFS`），只把渲染换成手机表格。
"""
from flask import g, request

from app.models.grades import TOTAL_SUBJECT, SUBJECTS
from app.modules.grades.services import pivot_service as pv
from app.modules.grades.services import scope as scope_service
from app.modules.grades.services.stats_service import cached_exam_data
from app.modules.wxapi import bp
from app.modules.wxapi.core.guards import wx_perm_required
from app.modules.wxapi.core.response import wx_err, wx_ok
from app.modules.wxapi.grades.routes_grades_stats import _visible_exam

EXAM_LIMIT = 60
DIM_KEYS = ['class', 'layer', 'subject', 'direction']


@bp.route('/grades/pivot/meta')
@wx_perm_required('grades.view')   # 2026-10-19 第十四轮：与网页透视表
                                   # （grades.view）统一，不再借用「学生查询」键
def wx_pivot_meta():
    """可选考试 / 维度 / 指标 / 科目（与网页 /api/pivot/meta 同源）。"""
    from app.models.grades import Exam
    grades = [x for x in (scope_service.visible_grades(g.wx_user) or []) if x]
    exams = []
    if grades:
        rows = (Exam.query.filter(Exam.grade.in_(grades))
                .order_by(Exam.exam_date.desc(), Exam.id.desc())
                .limit(EXAM_LIMIT).all())
        exams = [{
            'id': e.id, 'name': e.name or '', 'grade': e.grade or '',
            'date': e.exam_date.strftime('%Y-%m-%d') if e.exam_date else '',
        } for e in rows]
    return wx_ok({
        'exams': exams,
        'dims': [{'key': k, 'label': pv.DIM_LABELS[k]} for k in DIM_KEYS],
        'measures': [{'key': k, 'label': n, 'need_layer': a, 'need_line': b}
                     for k, n, a, b in pv.MEASURE_DEFS],
        'subjects': [TOTAL_SUBJECT] + list(SUBJECTS),
    })


@bp.route('/grades/pivot')
@wx_perm_required('grades.view')   # 2026-10-19 第十四轮：与网页透视表
                                   # （grades.view）统一，不再借用「学生查询」键
def wx_pivot():
    """交叉表：query exam_id / row / col / subject / direction / measures。"""
    exam, err = _visible_exam(request.args.get('exam_id', type=int))
    if err:
        return err
    row_dim = (request.args.get('row') or 'class').strip()
    col_dim = (request.args.get('col') or '').strip()
    subject = (request.args.get('subject') or TOTAL_SUBJECT).strip()
    direction = (request.args.get('direction') or '').strip()
    measures = [m.strip() for m in
                (request.args.get('measures') or '').split(',') if m.strip()]
    data = cached_exam_data(exam.id)
    if not data.total_rows:
        return wx_err('该考试尚未导入成绩')
    res = pv.pivot_table(data, row_dim, col_dim, measures,
                         subject=subject, direction=direction)
    if 'error' in res:
        return wx_err(res['error'])
    res['exam'] = {
        'id': exam.id, 'name': exam.name, 'grade': exam.grade or '',
        'date': exam.exam_date.strftime('%Y-%m-%d') if exam.exam_date else '',
    }
    res['subject'] = subject
    res['direction'] = direction
    return wx_ok(res)
