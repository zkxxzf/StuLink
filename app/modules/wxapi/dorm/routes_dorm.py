# StuLink v1.19.50.56 2026-10-18
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序宿舍接口：概览统计 / 房间列表 / 房间成员。

范围统一走 `app.utils.dormitory_scope.room_scope_query`（与网页同口径），
入住人数用一次 GROUP BY 聚合，避免逐房 COUNT 的 N+1 查询。
"""
from flask import g, request
from sqlalchemy import func

from app.extensions import db
from app.models import BedAssignment, Room, Student
from app.modules.wxapi import bp
from app.modules.wxapi.core.guards import wx_perm_required
from app.modules.wxapi.core.response import wx_err, wx_ok
from app.utils.dormitory_scope import room_scope_query

PERM = 'dormitory.view'
PAGE_SIZE = 50


def _scoped_rooms(user):
    """可见房间查询（已套范围、仅启用房间）。"""
    q = Room.query.filter(Room.is_active.is_(True))
    return room_scope_query(q, user)


def _apply_filters(q):
    """按 building / gender / grade / class_name / floor 过滤。"""
    building = (request.args.get('building') or '').strip()
    gender = (request.args.get('gender') or '').strip()
    grade = (request.args.get('grade') or '').strip()
    class_name = (request.args.get('class_name') or '').strip()
    floor = request.args.get('floor', type=int)
    if building:
        q = q.filter(Room.building == building)
    if gender:
        q = q.filter(Room.gender == gender)
    if grade:
        q = q.filter(Room.grade == grade)
    if class_name:
        q = q.filter(Room.class_name.like('%' + class_name + '%'))
    if floor:
        q = q.filter(Room.floor == floor)
    return q


def _occupancy_map(rooms):
    """一次聚合出 {room_id: 已入住人数}。"""
    ids = [r.id for r in rooms]
    if not ids:
        return {}
    rows = (db.session.query(BedAssignment.room_id,
                             func.count(BedAssignment.id))
            .filter(BedAssignment.student_id.isnot(None),
                    BedAssignment.room_id.in_(ids))
            .group_by(BedAssignment.room_id).all())
    return {rid: cnt for rid, cnt in rows}


def _room_item(room, occupied):
    """房间条目（含入住 / 容量 / 空床）。"""
    return {
        'id': room.id,
        'building': room.building or '',
        'room_number': room.room_number or '',
        'display_name': room.display_name,
        'floor': room.floor,
        'gender': room.gender or '',
        'grade': room.grade or '',
        'class_name': room.class_name or '',
        'capacity': room.capacity or 0,
        'occupied': occupied,
        'empty': max(0, (room.capacity or 0) - occupied),
    }


@bp.route('/dorm/summary')
@wx_perm_required(PERM)
def wx_dorm_summary():
    """宿舍概览：房间数 / 床位 / 已住 / 空床（按宿舍楼分组）。"""
    rooms = _scoped_rooms(g.wx_user).all()
    occ = _occupancy_map(rooms)
    buildings = {}
    for r in rooms:
        b = buildings.setdefault(r.building or '未分配', {
            'building': r.building or '未分配', 'rooms': 0,
            'capacity': 0, 'occupied': 0, 'empty': 0})
        b['rooms'] += 1
        b['capacity'] += r.capacity or 0
        b['occupied'] += occ.get(r.id, 0)
        b['empty'] = max(0, b['capacity'] - b['occupied'])
    total_cap = sum(r.capacity or 0 for r in rooms)
    total_occ = sum(occ.values())
    return wx_ok({
        'rooms': len(rooms),
        'capacity': total_cap,
        'occupied': total_occ,
        'empty': max(0, total_cap - total_occ),
        'buildings': sorted(buildings.values(), key=lambda x: x['building']),
    })


@bp.route('/dorm/rooms')
@wx_perm_required(PERM)
def wx_dorm_rooms():
    """房间列表（query: building/gender/grade/class_name/floor/page）。

    返回 {items, total, page, page_size}，total 供小程序显示"共 N 间"。
    """
    q = _apply_filters(_scoped_rooms(g.wx_user))
    total = q.count()
    page = max(1, request.args.get('page', type=int) or 1)
    q = (q.order_by(Room.building, Room.floor, Room.room_number)
         .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE))
    rooms = q.all()
    occ = _occupancy_map(rooms)
    return wx_ok({
        'items': [_room_item(r, occ.get(r.id, 0)) for r in rooms],
        'total': total,
        'page': page,
        'page_size': PAGE_SIZE,
    })


@bp.route('/dorm/filters')
@wx_perm_required(PERM)
def wx_dorm_filters():
    """筛选选项：楼栋 / 性别 / 年级 / 楼层 / 班级（按可见房间聚合）。"""
    rows = (_scoped_rooms(g.wx_user)
            .with_entities(Room.building, Room.gender, Room.grade,
                           Room.floor, Room.class_name)
            .distinct().all())
    buildings, genders, grades, floors, classes = set(), set(), set(), set(), set()
    for building, gender, grade, floor, cls in rows:
        if building:
            buildings.add(building)
        if gender:
            genders.add(gender)
        if grade:
            grades.add(grade)
        if floor:
            floors.add(int(floor))
        if cls:
            classes.add(cls)
    return wx_ok({
        'buildings': sorted(buildings),
        'genders': sorted(genders),
        'grades': sorted(grades),
        'floors': sorted(floors),
        'classes': sorted(classes),
    })


@bp.route('/dorm/room/<int:rid>')
@wx_perm_required(PERM)
def wx_dorm_room(rid):
    """房间详情：床位与成员（跨库分两次查，不做跨库 JOIN）。"""
    q = _scoped_rooms(g.wx_user).filter(Room.id == rid)
    room = q.first()
    if room is None:
        return wx_err('房间不存在或无权限查看', 404)
    beds = (BedAssignment.query.filter_by(room_id=room.id)
            .order_by(BedAssignment.bed_number).all())
    students = _students_by_ids([b.student_id for b in beds
                                 if b.student_id])
    occ = sum(1 for b in beds if b.student_id)
    return wx_ok({
        'room': _room_item(room, occ),
        'beds': [_bed_item(b, students) for b in beds],
    })


def _students_by_ids(ids):
    """按 id 批量取学生（返回 {id: {name, class_name}}）。"""
    if not ids:
        return {}
    rows = Student.query.filter(Student.id.in_(ids)).all()
    return {s.id: {'name': s.name or '', 'class_name': s.class_name or ''}
            for s in rows}


def _bed_item(bed, students):
    """床位条目：床号 + 入住学生（空床为 None）。"""
    info = students.get(bed.student_id) if bed.student_id else None
    return {
        'bed_number': bed.bed_number,
        'student_id': bed.student_id,
        'student_name': info['name'] if info else '',
        'class_name': info['class_name'] if info else '',
    }
