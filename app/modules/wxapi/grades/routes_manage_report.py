# StuLink v1.19.50.58 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序成绩汇报区（与网页 `/grades/report` 同表同口径）。

两个板块直接调 `report_service`：
- `subject-layer`：某方向各科层上线（单/双上线、去差均分、本次 vs 上次）；
- `class-overview`：各班参考数 / 各层上线 / 去差均分 / 班主任。

缓存 key 与网页完全一致（`grades_report_sl_*` / `grades_report_co_*`，900s），
所以网页刚看过的表，小程序打开读到的是同一份；改分/划线/导入会一起失效。
"""
import hashlib

from flask import g, request

from app.modules.grades.services import (report_pivot_service, report_service,
                                        report_teacher_service,
                                        report_warning_service, score_guard)
from app.modules.wxapi import bp
from app.modules.wxapi.core.guards import wx_perm_required
from app.modules.wxapi.core.response import wx_err, wx_ok
from app.modules.wxapi.grades.routes_grades import PERM
from app.modules.wxapi.grades.routes_grades_stats import _visible_exam
from app.utils.cache import cache

CACHE_TIMEOUT = 900


@bp.route('/grades/report/class-subject')
@wx_perm_required(PERM)
def wx_report_class_subject():
    """图3：某班各科（教师/特控单双/本科单/去差均分，末行总分）。"""
    exam, err = _visible_exam(request.args.get('exam_id', type=int))
    if err:
        return err
    class_name = (request.args.get('class_name') or '').strip()
    if not class_name:
        return wx_err('缺少班级参数')
    data = _cached('grades_report_cs_%s_%s' % (exam.id, class_name),
                   lambda: report_pivot_service.class_subject_report(
                       exam.id, class_name))
    if 'error' in data:
        return wx_err(data['error'])
    return wx_ok(data)


@bp.route('/grades/report/subject-classes')
@wx_perm_required(PERM)
def wx_report_subject_classes():
    """图4：某方向某科各班（教师/特控单双/本科单/去差均分）。"""
    exam, err = _visible_exam(request.args.get('exam_id', type=int))
    if err:
        return err
    direction = (request.args.get('direction') or '').strip()
    subject = (request.args.get('subject') or '').strip()
    if not subject:
        return wx_err('缺少学科参数')
    data = _cached(
        'grades_report_sc_%s_%s_%s' % (exam.id, direction, subject),
        lambda: report_pivot_service.subject_class_report(
            exam.id, direction, subject))
    if 'error' in data:
        return wx_err(data['error'])
    return wx_ok(data)


@bp.route('/grades/report/teacher-ranks')
@wx_perm_required(PERM)
def wx_report_teacher_ranks():
    """教师×学科名次（表头勾选指标后插入名次列的数据源）。"""
    exam, err = _visible_exam(request.args.get('exam_id', type=int))
    if err:
        return err
    direction = (request.args.get('direction') or '').strip()
    data = _cached(
        'grades_report_tr_%s_%s' % (exam.id, direction),
        lambda: report_teacher_service.teacher_rank_report(exam.id, direction))
    if 'error' in data:
        return wx_err(data['error'])
    return wx_ok(data)


@bp.route('/grades/report/near-line')
@wx_perm_required(PERM)
def wx_report_near_line():
    """图5：压线提醒（层线上下浮动范围内的临界生，逐生总分口径收敛）。"""
    exam, err = _visible_exam(request.args.get('exam_id', type=int))
    if err:
        return err
    direction = (request.args.get('direction') or '').strip()
    layer = (request.args.get('layer') or '').strip()
    cfg_max = report_service.NEAR_LINE_MAX
    above = request.args.get('above', default=report_service.NEAR_LINE_ABOVE,
                             type=int)
    below = request.args.get('below', default=report_service.NEAR_LINE_BELOW,
                             type=int)
    above = max(0, min(above, cfg_max))
    below = max(0, min(below, cfg_max))
    try:
        scope = score_guard.resolve(g.wx_user)
    except PermissionError:
        return wx_err('无权查看该考试的成绩', 403)
    allowed = scope.total_visible_classes(exam.grade)
    skey = 'all' if allowed is None else hashlib.md5(
        ','.join(sorted(allowed)).encode('utf-8')).hexdigest()[:8]
    data = _cached(
        'grades_report_nl_%s_%s_%s_%s_%s_%s' % (
            exam.id, direction, layer, above, below, skey),
        lambda: report_warning_service.near_line_report(
            exam.id, direction, layer, above, below,
            allowed_classes=allowed))
    if 'error' in data:
        return wx_err(data['error'])
    return wx_ok(data)


def _cached(key, builder):
    data = cache.get(key)
    if data is None:
        data = builder()
        cache.set(key, data, timeout=CACHE_TIMEOUT)
    return data


@bp.route('/grades/report/subject-layer')
@wx_perm_required(PERM)
def wx_report_subject_layer():
    """图1：各科层上线（query: exam_id / direction / layer / compare）。"""
    exam, err = _visible_exam(request.args.get('exam_id', type=int))
    if err:
        return err
    direction = (request.args.get('direction') or '').strip()
    layer = (request.args.get('layer') or '').strip()
    compare_exam_id = request.args.get('compare_exam_id', type=int)
    no_compare = (request.args.get('compare') or '').strip() == 'none'
    key = ('grades_report_sl_%s_%s_%s_%s_%s'
           % (exam.id, direction, layer, compare_exam_id or '',
              'nc' if no_compare else 'c'))
    data = _cached(key, lambda: report_service.subject_layer_report(
        exam.id, direction, layer, compare_exam_id, no_compare))
    if 'error' in data:
        return wx_err(data['error'])
    data['no_compare'] = no_compare
    return wx_ok(data)


@bp.route('/grades/report/class-overview')
@wx_perm_required(PERM)
def wx_report_class_overview():
    """图2：班级概况（query: exam_id / direction）。"""
    exam, err = _visible_exam(request.args.get('exam_id', type=int))
    if err:
        return err
    direction = (request.args.get('direction') or '').strip()
    data = _cached(
        'grades_report_co_%s_%s' % (exam.id, direction),
        lambda: report_service.class_overview_report(exam.id, direction))
    if 'error' in data:
        return wx_err(data['error'])
    return wx_ok(data)
