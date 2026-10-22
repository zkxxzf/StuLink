# StuLink v1.19.62.65 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序考务扩展：我的考场名单 + 我的监考工作量（只读）。

可见性仍按「本人在该批次有监考记录」收敛；不返回别人的排班。
"""
from flask import g
from sqlalchemy import func

from app.extensions import db
from app.models import AffairRoom, AffairStudent
from app.modules.wxapi import bp
from app.modules.wxapi.core.guards import wx_login_required
from app.modules.wxapi.core.response import wx_err, wx_ok
from app.modules.wxapi.exam.routes_exam import _date_str, _invig_rows


@bp.route('/exam/affair/<int:aid>/rooms')
@wx_login_required
def wx_exam_affair_rooms(aid):
    """批次考场列表；只对「我监考的考场」返回考生名单。"""
    rows = _invig_rows(g.wx_user.id, aid, real_name=g.wx_user.real_name or '')
    if not rows:
        return wx_err('考务批次不存在或你没有参与', 404)
    rooms = (AffairRoom.query.filter_by(affair_id=aid)
             .order_by(AffairRoom.room_no).all())
    counts = dict(
        db.session.query(AffairStudent.room_no, func.count(AffairStudent.id))
        .filter(AffairStudent.affair_id == aid,
                AffairStudent.is_attend.is_(True))
        .group_by(AffairStudent.room_no).all())
    my_rooms = {inv.room_no for inv, _s, _a in rows if inv.room_no}
    out = []
    for room in rooms:
        item = {
            'room_no': room.room_no or '',
            'location': room.location or '',
            'subject': room.subject or '',
            'capacity': room.capacity or 0,
            'count': counts.get(room.room_no, 0),
            'is_mine': room.room_no in my_rooms,
        }
        if item['is_mine']:
            item['students'] = [_student_row(s) for s in (
                AffairStudent.query
                .filter_by(affair_id=aid, room_no=room.room_no,
                           is_attend=True)
                .order_by(AffairStudent.seat_no,
                          AffairStudent.student_no)
                .limit(120).all())]
        out.append(item)
    return wx_ok({
        'rooms': out,
        'my_rooms': sorted(my_rooms),
        'total_students': sum(counts.values()),
    })


def _student_row(s):
    """考场考生行：学号 / 姓名 / 班级 / 座号 / 考号 / 选科。"""
    return {
        'no': s.student_no or '',
        'name': s.name or '',
        'class_name': s.class_name or '',
        'seat_no': s.seat_no,
        'exam_number': s.exam_number or '',
        'selection': s.subject_selection or '',
    }


@bp.route('/exam/affair/<int:aid>/workload')
@wx_login_required
def wx_exam_affair_workload(aid):
    """我在该批次的监考工作量（场次数 / 分钟数 / 缺时间提示）。"""
    rows = _invig_rows(g.wx_user.id, aid, real_name=g.wx_user.real_name or '')
    if not rows:
        return wx_err('考务批次不存在或你没有参与', 404)
    detail = []
    minutes = 0
    missing = 0
    for inv, sch, _affair in rows:
        dur = _minutes_between(sch.start_time, sch.end_time)
        if dur <= 0:
            missing += 1
        minutes += dur
        detail.append({
            'date': _date_str(sch.exam_date),
            'start': sch.start_time or '',
            'end': sch.end_time or '',
            'subject': sch.subject or '',
            'room_no': inv.room_no or '',
            'slot': inv.slot or 1,
            'minutes': dur,
        })
    detail.sort(key=lambda x: (x['date'], x['start'], x['room_no']))
    return wx_ok({
        'cells': len(rows),
        'minutes': minutes,
        'missing': missing,
        'detail': detail,
    })


def _minutes_between(start, end):
    """'HH:MM' 起止时间 → 分钟数（解析失败返回 0）。"""
    try:
        sh, sm = [int(x) for x in (start or '').split(':')[:2]]
        eh, em = [int(x) for x in (end or '').split(':')[:2]]
        return max(0, (eh * 60 + em) - (sh * 60 + sm))
    except (TypeError, ValueError):
        return 0
