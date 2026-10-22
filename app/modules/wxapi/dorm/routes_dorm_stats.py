# StuLink v1.19.62.65 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序宿舍统计（只读）：各年级床位供给 / 入住率 + 各班住校人数。

范围走 dormitory_scope 的唯一口径（fail-closed），
不返回学生姓名，只返回聚合人数。
"""
from flask import request
from sqlalchemy import func

from app.extensions import db
from app.models import BedAssignment, Room, Student, StudentAccommodation
from app.modules.wxapi import bp
from app.modules.wxapi.core.guards import wx_perm_required
from app.modules.wxapi.core.response import wx_ok
from app.utils.dormitory_scope import room_scope_query, student_scope_query

PERM = 'dormitory.view'


@bp.route('/dorm/stats')
@wx_perm_required(PERM)
def wx_dorm_stats():
    """宿舍统计（query: grade 可选，用于班级列表过滤）。"""
    grade_filter = (request.args.get('grade') or '').strip()
    rooms = room_scope_query(
        Room.query.filter(Room.is_active.is_(True))).all()
    occ = _occupancy([r.id for r in rooms])
    grades = {}
    for r in rooms:
        key = r.grade or '未分年级'
        item = grades.setdefault(key, {
            'grade': key, 'rooms': 0, 'capacity': 0, 'occupied': 0,
            'empty': 0, 'rate': 0, 'boarding': 0,
        })
        item['rooms'] += 1
        item['capacity'] += r.capacity or 0
        item['occupied'] += occ.get(r.id, 0)
    classes = _boarding_by_class()
    for item in classes:
        if item['grade'] in grades:
            grades[item['grade']]['boarding'] += item['total']
    for item in grades.values():
        item['empty'] = max(0, item['capacity'] - item['occupied'])
        item['rate'] = _rate(item['occupied'], item['capacity'])
    class_rows = [c for c in classes
                  if not grade_filter or c['grade'] == grade_filter]
    class_rows.sort(key=lambda c: (-c['total'], c['grade'], c['class_name']))
    grade_rows = sorted(grades.values(), key=lambda x: x['grade'])
    return wx_ok({
        'grades': grade_rows,
        'classes': class_rows,
        'totals': {
            'rooms': sum(g['rooms'] for g in grade_rows),
            'capacity': sum(g['capacity'] for g in grade_rows),
            'occupied': sum(g['occupied'] for g in grade_rows),
            'empty': sum(g['empty'] for g in grade_rows),
            'boarding': sum(g['boarding'] for g in grade_rows),
            'rate': _rate(sum(g['occupied'] for g in grade_rows),
                          sum(g['capacity'] for g in grade_rows)),
        },
        'grade': grade_filter,
    })


def _occupancy(room_ids):
    """{room_id: 已住人数}，一次 group_by 避免 N+1。"""
    if not room_ids:
        return {}
    rows = (db.session.query(BedAssignment.room_id,
                             func.count(BedAssignment.id))
            .filter(BedAssignment.room_id.in_(room_ids),
                    BedAssignment.student_id.isnot(None))
            .group_by(BedAssignment.room_id).all())
    return {rid: n for rid, n in rows}


def _boarding_by_class():
    """住校生按 (年级, 班级) 聚合（走宿舍学生范围）。"""
    boarding = {sa.student_id for sa in
                StudentAccommodation.query
                .filter(StudentAccommodation.boarding_type == '住校').all()}
    if not boarding:
        return []
    rows = student_scope_query(Student.query).all()
    buckets = {}
    for s in rows:
        if s.id not in boarding:
            continue
        key = (s.grade or '', s.class_name or '未分班')
        item = buckets.setdefault(key, {
            'grade': key[0], 'class_name': key[1],
            'total': 0, 'male': 0, 'female': 0,
        })
        item['total'] += 1
        if s.gender == '男':
            item['male'] += 1
        elif s.gender == '女':
            item['female'] += 1
    return list(buckets.values())


def _rate(part, total):
    """百分比（保留 1 位；分母为 0 返回 0）。"""
    if not total:
        return 0
    return round(part * 100.0 / total, 1)
