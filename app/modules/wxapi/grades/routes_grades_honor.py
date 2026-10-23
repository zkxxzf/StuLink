# StuLink v1.19.69.64 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序成绩进阶（一）：表彰名单 / 临界生（只读）。

口径全部复用网页端 service，这里只做参数校验、范围收敛与裁剪。
"""
from flask import g, request

from app.modules.grades.services import (
    honor_service,
    report_service,
    report_warning_service,
    score_guard,
    stats_service,
)
from app.modules.wxapi import bp
from app.modules.wxapi.core.guards import wx_perm_required
from app.modules.wxapi.core.response import wx_err, wx_ok
from app.modules.wxapi.grades.routes_grades import PERM
from app.modules.wxapi.grades.routes_grades_stats import _visible_exam

NEAR_MAX = 100
NEAR_LIMIT = 300


def _int_arg(name, default, low, high):
    """读取整型 query 参数并夹在 [low, high]。"""
    val = request.args.get(name, type=int)
    if val is None:
        return default
    return max(low, min(high, val))


@bp.route('/grades/honor')
@wx_perm_required(PERM)
def wx_grades_honor():
    """表彰名单（query: exam_id / mode）。"""
    exam, err = _visible_exam(request.args.get('exam_id', type=int))
    if err:
        return err
    data = honor_service.build(exam.id, (request.args.get('mode') or '').strip())
    return wx_ok({
        'exam': data['exam'],
        'mode': data['mode'],
        'mode_options': data['mode_options'],
        'groups': [{
            'key': gp.get('key') or '',
            'label': gp.get('label') or '',
            'note': gp.get('note') or '',
            'top_n': gp.get('top_n'),
            'size': gp.get('size') or 0,
            'rows': [_honor_row(r) for r in (gp.get('rows') or [])],
        } for gp in data['groups']],
        'elite': {
            'top_n': data['elite']['top_n'],
            'subject_limit': data['elite']['subject_limit'],
            'classes': [{
                'class_name': c.get('class_name') or '',
                'students': c.get('students') or 0,
                'total': [_honor_row(r) for r in (c.get('total') or [])],
                'subjects': [_subject_board(s) for s in (c.get('subjects') or [])],
            } for c in data['elite']['classes']],
        },
        'subject_board': [_subject_board(s) for s in data['subject_board']],
        'stats': data['stats'],
        'gap': data['gap'],
    })


def _honor_row(row):
    """榜单行：名次 / 学号 / 姓名 / 班级 / 班型 / 总分 / 单科分。"""
    return {
        'rank': row.get('rank'),
        'no': row.get('no') or '',
        'name': row.get('name') or '',
        'class_name': row.get('class_name') or '',
        'class_type': row.get('class_type') or '',
        'direction': row.get('direction') or '',
        'selection': row.get('selection') or '',
        'total': row.get('total'),
        'score': row.get('score'),
        'scores': row.get('scores') or {},
    }


def _subject_board(board):
    """单科榜：科目 / 最高分 / 并列第一人数 / 榜单行。"""
    return {
        'subject': board.get('subject') or '',
        'top': board.get('top'),
        'count': board.get('count') or 0,
        'tied': board.get('tied') or 0,
        'top_n': board.get('top_n'),
        'rows': [_honor_row(r) for r in (board.get('rows') or [])],
    }


@bp.route('/grades/near-line')
@wx_perm_required(PERM)
def wx_grades_near_line():
    """临界生名单（query: exam_id / direction / layer / above / below / cls）。"""
    exam, err = _visible_exam(request.args.get('exam_id', type=int))
    if err:
        return err
    direction = (request.args.get('direction') or '').strip()
    layer = (request.args.get('layer') or '').strip()
    cls = (request.args.get('cls') or '').strip()
    above = _int_arg('above', report_service.NEAR_LINE_ABOVE, 0, NEAR_MAX)
    below = _int_arg('below', report_service.NEAR_LINE_BELOW, 0, NEAR_MAX)
    allowed = score_guard.resolve(g.wx_user).total_visible_classes(exam.grade)
    report = report_warning_service.near_line_report(
        exam.id, direction, layer, above, below,
        class_name=cls or None, allowed_classes=allowed)
    if report.get('error'):
        return wx_err(report['error'])
    data = stats_service.cached_exam_data(exam.id)
    directions = list(data.directions or [])
    layer_map = {d: [n for n, _line in report_service.layers_desc(data, d)]
                 for d in directions}
    layer_map[''] = [n for n, _line in report_service.layers_desc(data, '')]
    rows = report.get('rows') or []
    # 班级分布用全量 rows 统计后再截断，否则超过上限时前端算出的分布会偏小
    class_stats = {}
    for r in rows:
        key = r.get('class_name') or '—'
        item = class_stats.setdefault(key, {
            'class_name': key, 'below': 0, 'above': 0, 'total': 0,
        })
        item['total'] += 1
        if r.get('status') == '线下':
            item['below'] += 1
        else:
            item['above'] += 1
    class_stats = sorted(class_stats.values(),
                         key=lambda x: (-x['total'], x['class_name']))
    return wx_ok({
        'exam': report.get('exam'),
        'direction': report.get('direction'),
        'layer': report.get('layer'),
        'line': report.get('line'),
        'lines': report.get('lines') or {},
        'above': above,
        'below': below,
        'above_n': report.get('above_n') or 0,
        'below_n': report.get('below_n') or 0,
        'classes': report.get('classes') or [],
        'class_name': report.get('class_name') or '',
        'subjects': report.get('subjects') or [],
        'directions': directions,
        'layer_map': layer_map,
        'class_stats': class_stats,
        'rows': rows[:NEAR_LIMIT],
        'truncated': len(rows) > NEAR_LIMIT,
    })
