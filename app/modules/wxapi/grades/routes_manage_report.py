# StuLink v1.19.49.35 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序成绩汇报区（与网页 `/grades/report` 同表同口径）。

两个板块直接调 `report_service`：
- `subject-layer`：某方向各科层上线（单/双上线、去差均分、本次 vs 上次）；
- `class-overview`：各班参考数 / 各层上线 / 去差均分 / 班主任。

缓存 key 与网页完全一致（`grades_report_sl_*` / `grades_report_co_*`，900s），
所以网页刚看过的表，小程序打开读到的是同一份；改分/划线/导入会一起失效。
"""
from flask import request

from app.modules.grades.services import report_service
from app.modules.wxapi import bp
from app.modules.wxapi.core.guards import wx_perm_required
from app.modules.wxapi.core.response import wx_err, wx_ok
from app.modules.wxapi.grades.routes_grades import PERM
from app.modules.wxapi.grades.routes_grades_stats import _visible_exam
from app.utils.cache import cache

CACHE_TIMEOUT = 900


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
