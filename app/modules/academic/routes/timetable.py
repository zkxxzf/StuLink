# StuLink v1.9.2 2026-09-16
# 教务 · 课表：查看页（导入功能后续版本开放）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from flask import render_template, request
from flask_login import login_required

from app.extensions import db
from app.models.academic import Timetable, TimetableEntry
from app.modules.academic import bp
from app.utils.decorators import perm_required

_WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']


@bp.route('/timetable')
@login_required
@perm_required('academic.view')
def timetable_page():
    """课表查看（按学期切换；数据待后续导入功能接入）"""
    tid = request.args.get('id', type=int)
    timetables = Timetable.query.order_by(Timetable.id.desc()).all()
    current = None
    if tid:
        current = db.session.get(Timetable, tid)
    elif timetables:
        current = timetables[0]

    entries = []
    if current:
        entries = (TimetableEntry.query.filter_by(timetable_id=current.id)
                   .order_by(TimetableEntry.weekday, TimetableEntry.period).all())

    # 按 星期 × 节次 组织成网格，便于展示
    grid = {}
    periods = set()
    for e in entries:
        grid.setdefault((e.weekday or 0, e.period or 0), []).append(e)
        periods.add(e.period or 0)
    return render_template('academic/timetable.html', timetables=timetables,
                           current=current, entries=entries,
                           weekday_names=_WEEKDAYS,
                           periods=sorted(periods), grid=grid)
