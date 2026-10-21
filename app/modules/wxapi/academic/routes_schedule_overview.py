# StuLink v1.19.50.55 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序全校总课表（只读）：行＝节次、列＝班级，按天切换。

复用网页端 `schedule_service.get_overview_view` 的竖版矩阵（ovv），
只裁剪成手机需要的字段；范围与班级/教师课表同口径。
"""
from datetime import date

from flask import request

from app.modules.academic.services import schedule_service as svc
from app.modules.wxapi import bp
from app.modules.wxapi.core.guards import wx_perm_required
from app.modules.wxapi.core.response import wx_err, wx_ok
from app.modules.wxapi.academic.routes_schedule import (
    _term,
    _view_scope,
    _week,
)

CELL_ENTRIES = 2


@bp.route('/schedule/overview')
@wx_perm_required('academic.view')
def wx_schedule_overview():
    """全校总课表（query: sid / week / day / grade）。"""
    ts = _term(request.args.get('sid', type=int))
    if ts is None:
        return wx_err('暂无可用学期课表', 404)
    week = _week(ts)
    day = request.args.get('day', type=int) or date.today().isoweekday()
    day = max(1, min(7, day))
    grade = (request.args.get('grade') or '').strip()
    allowed_grades, allowed_classes = _view_scope()
    view = svc.get_overview_view(
        ts.id, week=week,
        grades=[grade] if grade else None,
        weekday=day,
        allowed_grades=allowed_grades,
        allowed_classes=allowed_classes,
    )
    ovv = view.get('ovv') or {}
    return wx_ok({
        'term': {'id': ts.id, 'name': ts.name},
        'week': week,
        'day': day,
        'max_weekday': view.get('max_weekday') or 5,
        'all_grades': view.get('all_grades') or [],
        'labels': view.get('labels') or {},
        'grade_groups': ovv.get('grade_groups') or [],
        'columns': [_column(c) for c in (ovv.get('columns') or [])],
        'rows': [_row(r) for r in (ovv.get('rows') or [])],
        'total': view.get('total') or 0,
    })


def _column(c):
    """表头列：年级 / 班级 / 班型方向 / 节数。"""
    meta = c.get('meta') or {}
    return {
        'grade': c.get('grade') or '',
        'grade_label': c.get('grade_label') or '',
        'grade_short': c.get('grade_short') or '',
        'class_name': c.get('class_name') or '',
        'direction': meta.get('direction') or '',
        'class_type': meta.get('class_type') or '',
        'total': c.get('total') or 0,
    }


def _row(r):
    """一行节次：节次信息 + 每班格子的学科/教师。"""
    period = r.get('period') or {}
    cells = []
    for entries in (r.get('cells') or []):
        items = [_entry(e) for e in (entries or [])[:CELL_ENTRIES]]
        cells.append({
            'items': items,
            'more': max(0, len(entries or []) - CELL_ENTRIES),
        })
    return {
        'period': {
            'number': period.get('period_number'),
            'name': period.get('period_name') or '',
            'start': period.get('start_time') or '',
            'end': period.get('end_time') or '',
            'type': period.get('period_type') or '',
        },
        'group_label': r.get('group_label') or '',
        'group_start': bool(r.get('group_start')),
        'group_span': r.get('group_span') or 1,
        'cells': cells,
    }


def _entry(e):
    """格内课程：学科 / 教师 / 教室 / 班级。"""
    return {
        'subject': e.get('subject') or '',
        'teacher_name': e.get('teacher_name') or '',
        'room': e.get('room') or '',
        'class_name': e.get('class_name') or '',
        'grade': e.get('grade') or '',
    }
