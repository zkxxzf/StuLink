# StuLink v1.19.49.35 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序「按学生查住宿」接口（只读）。

与宿舍列表同口径的双重范围收敛：
学生走 `student_scope_query`，房间再套 `room_scope_query`；
任一环节没有范围即空集（fail-closed），避免用姓名遍历范围外住宿信息。
"""
from flask import g, request
from sqlalchemy import or_

from app.models import BedAssignment, Room, Student, StudentAccommodation
from app.modules.wxapi import bp
from app.modules.wxapi.guards import wx_perm_required
from app.modules.wxapi.response import wx_err, wx_ok
from app.modules.wxapi.routes_dorm import PERM, _room_item
from app.utils.dormitory_scope import room_scope_query, student_scope_query

SEARCH_LIMIT = 20


@bp.route('/dorm/search')
@wx_perm_required(PERM)
def wx_dorm_search():
    """按学号 / 姓名查学生住在哪个房间。"""
    q = (request.args.get('q') or '').strip()
    if not q:
        return wx_err('请输入学号或姓名')
    like = '%' + q + '%'
    students = (student_scope_query(Student.query, g.wx_user)
                .filter(or_(Student.student_number.like(like),
                            Student.name.like(like)))
                .order_by(Student.student_number)
                .limit(SEARCH_LIMIT).all())
    if not students:
        return wx_ok([])
    return wx_ok(_lodging_rows(students, g.wx_user))


def _lodging_rows(students, user):
    """批量组装学生的住宿信息（床位一次查、房间一次查）。"""
    ids = [s.id for s in students]
    beds = BedAssignment.query.filter(
        BedAssignment.student_id.in_(ids)).all()
    bed_of = {b.student_id: b for b in beds}
    rooms = _rooms_of(user, {b.room_id for b in beds})
    accs = {a.student_id: a for a in
            StudentAccommodation.query.filter(
                StudentAccommodation.student_id.in_(ids)).all()}
    return [_lodging_item(s, bed_of.get(s.id), rooms, accs.get(s.id))
            for s in students]


def _rooms_of(user, room_ids):
    """按宿舍范围批量取房间，返回 {id: Room}。"""
    if not room_ids:
        return {}
    rows = room_scope_query(Room.query, user).filter(
        Room.id.in_(list(room_ids))).all()
    return {r.id: r for r in rows}


def _lodging_item(stu, bed, rooms, acc):
    """单个学生的住宿条目；未分配住宿时 room / bed_number 为 None。"""
    room = rooms.get(bed.room_id) if bed is not None else None
    boarding = acc.boarding_type if acc is not None else ''
    return {
        'no': stu.student_number,
        'name': stu.name,
        'grade': stu.grade or '',
        'class_name': stu.class_name or '',
        'bed_number': bed.bed_number if bed is not None else None,
        'room': _room_item(room, 0) if room is not None else None,
        'boarding_type': boarding or '',
    }
