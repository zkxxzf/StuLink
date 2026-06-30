# StuLink v1.4.6 2026-06-30
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0
from flask import Blueprint, render_template
from flask_login import login_required
from app.models import Student, Room, BedAssignment, User
from app.extensions import db
from sqlalchemy import func, case

bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')


@bp.route('/')
@login_required
def index():
    # 使用单次聚合查询优化统计性能
    student_stats = db.session.query(
        func.count(Student.id).label('total'),
        func.sum(case((Student.gender == '�?, 1), else_=0)).label('male'),
        func.sum(case((Student.gender == '�?, 1), else_=0)).label('female'),
        func.sum(case((Student.boarding_type == '住校', 1), else_=0)).label('boarding'),
        func.sum(case((Student.boarding_type == '走读', 1), else_=0)).label('day')
    ).one()

    # 房间和床位统�?
    total_rooms = db.session.query(func.count(Room.id)).filter(Room.is_active == True).scalar()
    
    # 获取活跃房间的ID列表
    active_room_ids = db.session.query(Room.id).filter(Room.is_active == True).all()
    active_room_ids = [r[0] for r in active_room_ids]
    
    # 只统计活跃房间的床位
    if active_room_ids:
        total_beds = db.session.query(func.count(BedAssignment.id)).filter(
            BedAssignment.room_id.in_(active_room_ids)
        ).scalar()
        assigned_beds = db.session.query(func.count(BedAssignment.id)).filter(
            BedAssignment.room_id.in_(active_room_ids),
            BedAssignment.student_id.isnot(None)
        ).scalar()
    else:
        total_beds = 0
        assigned_beds = 0
    
    total_users = db.session.query(func.count(User.id)).filter(User.is_active == True).scalar()

    stats = {
        'total_students': student_stats.total or 0,
        'male_students': student_stats.male or 0,
        'female_students': student_stats.female or 0,
        'boarding_students': student_stats.boarding or 0,
        'day_students': student_stats.day or 0,
        'total_rooms': total_rooms or 0,
        'assigned_beds': assigned_beds or 0,
        'total_beds': total_beds or 0,
        'total_users': total_users or 0,
    }

    # 按年级统�?- 使用单次查询
    grade_query = db.session.query(
        Student.grade,
        func.count(Student.id).label('total'),
        func.sum(case((Student.gender == '�?, 1), else_=0)).label('male'),
        func.sum(case((Student.gender == '�?, 1), else_=0)).label('female'),
        func.sum(case((Student.boarding_type == '住校', 1), else_=0)).label('boarding'),
        func.sum(case((Student.boarding_type == '走读', 1), else_=0)).label('day')
    ).group_by(Student.grade).order_by(Student.grade).all()

    grade_stats = [{
        'grade': g.grade,
        'total': g.total,
        'male': g.male,
        'female': g.female,
        'boarding': g.boarding,
        'day': g.day,
    } for g in grade_query]

    return render_template('dormitory/dashboard/index.html', stats=stats, grade_stats=grade_stats)
