# StuLink v1.19.49.52 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序成绩进阶（二）：班级对比 / 全局对比（只读）。"""
from flask import g, request

from app.modules.grades.services import (
    compare_service,
    global_compare_service,
    score_guard,
)
from app.modules.wxapi import bp
from app.modules.wxapi.core.guards import wx_perm_required
from app.modules.wxapi.core.response import wx_err, wx_ok
from app.modules.wxapi.grades.routes_grades import PERM
from app.modules.wxapi.grades.routes_grades_stats import _visible_exam
from app.utils.cache import cache

COMPARE_MIN = 2
COMPARE_MAX = 20
# 与网页端共用 grades_report_ld_ 前缀 → 改分/划线/导入时被
# invalidate_exam_cache 一并清掉（2026-10-19 审计：原来用模块内 dict，
# 不在任何失效前缀内，网页改完分小程序仍能看 10 分钟旧数）。
GC_CACHE_PREFIX = 'grades_report_ld_wxgc_'
GC_TTL = 600


@bp.route('/grades/compare')
@wx_perm_required(PERM)
def wx_grades_compare():
    """多班横向对比（query: exam_id / classes=01班,02班 / direction / denom）。"""
    exam, err = _visible_exam(request.args.get('exam_id', type=int))
    if err:
        return err
    raw = (request.args.get('classes') or '').strip()
    classes = [c.strip() for c in raw.split(',') if c.strip()]
    classes = list(dict.fromkeys(classes))
    if len(classes) < COMPARE_MIN:
        return wx_err('至少选择 2 个班级')
    classes = classes[:COMPARE_MAX]
    visible = score_guard.resolve(g.wx_user).visible_classes(exam.grade)
    if visible is not None:
        classes = [c for c in classes if c in visible]
    if len(classes) < COMPARE_MIN:
        return wx_err('所选班级不在你的可见范围内', 403)
    direction = (request.args.get('direction') or '').strip()
    denom = (request.args.get('denom') or 'ref').strip()
    result = compare_service.compare_tab(exam.id, classes, direction, denom)
    tables = result.get('tables') or {}
    keep = {k: tables[k] for k in
            ('cmp_overview', 'cmp_subject', 'cmp_pass', 'cmp_good')
            if k in tables}
    return wx_ok({
        'exam': result.get('exam'),
        'classes': result.get('classes') or [],
        'tables': keep,
        'meta': result.get('meta') or {},
    })


@bp.route('/grades/global-compare')
@wx_perm_required(PERM)
def wx_grades_global_compare():
    """全局对比（query: exam_id / direction）：班级总分 + 各层上线率。"""
    exam, err = _visible_exam(request.args.get('exam_id', type=int))
    if err:
        return err
    direction = (request.args.get('direction') or '').strip()
    report = _gc_cached(exam.id, direction)
    if report.get('error'):
        return wx_err(report['error'])
    layers = report.get('layers') or []
    groups = []
    for gp in report.get('groups') or []:
        groups.append({
            'key': gp.get('key') or '',
            'label': gp.get('label') or '',
            'rows': [_gc_row(r, layers) for r in (gp.get('rows') or [])],
            'subtotal': _gc_row(gp.get('subtotal'), layers)
            if gp.get('subtotal') else None,
        })
    return wx_ok({
        'exam': report.get('exam'),
        'mode': report.get('mode'),
        'layers': layers,
        'default_lines': report.get('default_lines') or [],
        'directions': report.get('directions') or [],
        'has_direction': report.get('has_direction'),
        'groups': groups,
        'grand': _gc_row(report.get('grand'), layers)
        if report.get('grand') else None,
    })


def _gc_cached(exam_id, direction):
    """全局对比结果缓存（避免手机反复进页面重算）。

    键必须落在 ``grades_report_ld_`` 前缀下：``invalidate_exam_cache``
    用该前缀做整体失效，否则改分/划线后两端数字会不一致。
    """
    key = '%s%s_%s' % (GC_CACHE_PREFIX, exam_id, direction or '')
    hit = cache.get(key)
    if hit is not None:
        return hit
    val = global_compare_service.global_compare_report(exam_id, direction)
    cache.set(key, val, timeout=GC_TTL)
    return val


def _gc_row(row, layers):
    """全局对比的一行：班级 + 总分去差均分 + 各层上线率。"""
    if not row:
        return None
    cell = (row.get('cells') or {}).get('总分') or {}
    lines = cell.get('lines') or {}
    return {
        'class_name': row.get('class_name') or '',
        'count': row.get('count') or 0,
        'direction': row.get('direction') or '',
        'class_type': row.get('class_type') or '',
        'headteacher': row.get('headteacher') or '',
        'is_combo': bool(row.get('is_combo')),
        'trim_avg': cell.get('trim_avg'),
        'rates': {name: (lines.get(name) or {}).get('rate') for name in layers},
    }
