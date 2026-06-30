# StuLink v1.4.6 2026-06-30
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0
from flask import Blueprint, render_template, request
from flask_login import login_required, current_user
from app.models import Student, Room, BedAssignment, UserClassLink
from app.extensions import db
from app.utils.helpers import get_dict_values

bp = Blueprint('statistics', __name__, url_prefix='/statistics')

SCOPE_CLASS = 'class'    # 班主任：只看所管班�?
SCOPE_GRADE = 'grade'    # 年级长：只看所管年�?
SCOPE_SCHOOL = 'school'  # 全校�?admin：看全部


def _get_scope():
    """获取当前用户的权限范�?""
    pg = current_user.permission_group
    if not pg:
        return SCOPE_SCHOOL, None
    return pg.scope_type, current_user.grade


def _get_user_class_links():
    """获取当前班主任的所有班级关�?""
    return UserClassLink.query.filter_by(user_id=current_user.id).all()


def _build_per_class_stats(filter_grade=None, filter_classes=None):
    """按年�?班级统计"""
    q = db.session.query(
        Student.grade, Student.class_name
    ).distinct().order_by(Student.grade, Student.class_name)
    if filter_grade:
        q = q.filter(Student.grade == filter_grade)

    results = q.all()
    if filter_classes:
        allowed = {(g, c) for g, c in filter_classes}
        results = [r for r in results if (r[0], r[1]) in allowed]

    stats = []
    for grade, class_name in results:
        qs = Student.query.filter_by(grade=grade, class_name=class_name)
        total = qs.count()
        male = qs.filter_by(gender='�?).count()
        female = qs.filter_by(gender='�?).count()
        boarding = qs.filter_by(boarding_type='住校').count()
        male_boarding = qs.filter_by(gender='�?, boarding_type='住校').count()
        female_boarding = qs.filter_by(gender='�?, boarding_type='住校').count()
        day_student = qs.filter_by(boarding_type='走读').count()
        male_day = qs.filter_by(gender='�?, boarding_type='走读').count()
        female_day = qs.filter_by(gender='�?, boarding_type='走读').count()

        stats.append({
            'grade': grade, 'class_name': class_name,
            'total': total, 'male': male, 'female': female,
            'boarding': boarding, 'male_boarding': male_boarding,
            'female_boarding': female_boarding,
            'day_student': day_student, 'male_day': male_day, 'female_day': female_day,
        })
    return stats


def _build_per_grade_stats(filter_grade=None):
    """按年级汇�?""
    q = db.session.query(Student.grade).distinct().order_by(Student.grade)
    if filter_grade:
        q = q.filter(Student.grade == filter_grade)
    grades = [r[0] for r in q.all()]

    result = []
    for grade in grades:
        qs = Student.query.filter_by(grade=grade)
        total = qs.count()
        male = qs.filter_by(gender='�?).count()
        female = qs.filter_by(gender='�?).count()
        boarding = qs.filter_by(boarding_type='住校').count()
        male_boarding = qs.filter_by(gender='�?, boarding_type='住校').count()
        female_boarding = qs.filter_by(gender='�?, boarding_type='住校').count()
        class_count = db.session.query(Student.class_name).filter_by(grade=grade).distinct().count()

        result.append({
            'grade': grade, 'class_count': class_count,
            'total': total, 'male': male, 'female': female,
            'boarding': boarding, 'male_boarding': male_boarding,
            'female_boarding': female_boarding,
        })
    return result


def _build_school_stats():
    """全校汇�?""
    qs = Student.query
    total = qs.count()
    male = qs.filter_by(gender='�?).count()
    female = qs.filter_by(gender='�?).count()
    boarding = qs.filter_by(boarding_type='住校').count()
    male_boarding = qs.filter_by(gender='�?, boarding_type='住校').count()
    female_boarding = qs.filter_by(gender='�?, boarding_type='住校').count()
    grade_count = db.session.query(Student.grade).distinct().count()
    class_count = db.session.query(Student.grade, Student.class_name).distinct().count()

    return {
        'grade_count': grade_count, 'class_count': class_count,
        'total': total, 'male': male, 'female': female,
        'boarding': boarding, 'male_boarding': male_boarding,
        'female_boarding': female_boarding,
    }


def _dorm_stats():
    total_rooms = Room.query.filter_by(is_active=True).count()
    occupied_beds = BedAssignment.query.filter(BedAssignment.student_id.isnot(None)).count()
    total_beds = BedAssignment.query.count()
    return {
        'total_rooms': total_rooms,
        'total_beds': total_beds,
        'occupied_beds': occupied_beds,
        'empty_beds': total_beds - occupied_beds,
        'occupancy_rate': round(occupied_beds / total_beds * 100, 1) if total_beds else 0,
    }


@bp.route('/')
@login_required
def index():
    scope_type, user_grade = _get_scope()
    tab = request.args.get('tab', scope_type)  # 默认选用户范围对应的tab
    if tab not in ('school', 'grade', 'class'):
        tab = 'school'
    sel_grade = request.args.get('grade', user_grade or '')

    # 班主任：只允许看 class tab
    if scope_type == SCOPE_CLASS:
        tab = 'class'
        links = _get_user_class_links()
        allowed = [(l.grade, l.class_name) for l in links]
        per_class_stats = _build_per_class_stats(filter_classes=allowed)
        per_grade_stats = []
        school_stats = {}
        grade_options = list(set(l.grade for l in links))
    # 年级长：�?grade �?class tab，限制年�?
    elif scope_type == SCOPE_GRADE:
        if tab == 'school':
            tab = 'grade'
        if not sel_grade:
            sel_grade = user_grade or ''
        per_class_stats = _build_per_class_stats(filter_grade=sel_grade)
        per_grade_stats = _build_per_grade_stats(filter_grade=user_grade)
        school_stats = _build_school_stats() if tab == 'school' else {}
        grade_options = [user_grade] if user_grade else []
    else:
        # 全校�?admin：全部数�?
        per_class_stats = _build_per_class_stats(filter_grade=sel_grade if tab == 'class' and sel_grade else None)
        per_grade_stats = _build_per_grade_stats()
        school_stats = _build_school_stats()
        grade_options = sorted(get_dict_values('grade'), reverse=True)

    return render_template('dormitory/statistics/overview.html',
                           tab=tab,
                           sel_grade=sel_grade,
                           grade_options=grade_options,
                           per_class_stats=per_class_stats,
                           per_grade_stats=per_grade_stats,
                           school_stats=school_stats,
                           dorm_stats=_dorm_stats(),
                           scope_type=scope_type)
