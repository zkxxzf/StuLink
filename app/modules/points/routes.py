# StuLink v1.9.1 2026-09-14
# 积分管理：单页记录（独立库 points.db）+ 范围权限过滤
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from datetime import date, datetime

from flask import Blueprint, render_template, request, jsonify, abort
from flask_login import login_required, current_user
from sqlalchemy import func

from app.extensions import db
from app.models import Student, UserClassLink, PointRecord
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation, get_graduated_grades

bp = Blueprint('points', __name__, url_prefix='/points')

CATEGORIES = ['纪律', '学习', '卫生', '活动', '其他']


# ==================== 数据范围（复用权限组 scope_type 体系） ====================

def _scope():
    """返回 (scope_type, grade, 班级白名单 or None)"""
    pg = current_user.permission_group
    scope = pg.scope_type if pg else 'none'
    grade = current_user.grade or ''
    classes = None
    if scope == 'class':
        links = UserClassLink.query.filter_by(user_id=current_user.id).all()
        classes = [(l.grade, l.class_name) for l in links]
    return scope, grade, classes


def _apply_scope(q, scope, grade, classes):
    """把范围条件应用到 PointRecord 查询"""
    if scope == 'school':
        return q
    if scope == 'grade' and grade:
        return q.filter_by(grade=grade)
    if scope == 'class' and classes:
        from sqlalchemy import or_, and_
        conds = [and_(PointRecord.grade == g, PointRecord.class_name == c)
                 for g, c in classes]
        return q.filter(or_(*conds))
    # none / 无范围：什么都看不到
    return q.filter(PointRecord.id == -1)


def _in_scope(student, scope, grade, classes):
    if scope == 'school':
        return True
    if scope == 'grade':
        return student.grade == grade
    if scope == 'class':
        return (student.grade, student.class_name) in (classes or [])
    return False


# ==================== 页面 ====================

@bp.route('/')
@login_required
@perm_required('points.view')
def index():
    return render_template('points/index.html')


@bp.route('/api/options')
@login_required
@perm_required('points.view')
def api_options():
    scope, grade, classes = _scope()
    gds = set(get_graduated_grades())
    if scope == 'school':
        rows = (Student.query.with_entities(Student.grade).distinct().all())
        grades = sorted({r[0] for r in rows if r[0] and r[0] not in gds},
                        key=lambda g: int(''.join(filter(str.isdigit, g)) or 0), reverse=True)
        class_map = {}
        for g in grades:
            cls = Student.query.filter_by(grade=g).with_entities(Student.class_name).distinct().all()
            class_map[g] = sorted([r[0] for r in cls if r[0] and r[0].endswith('班')
                                   and r[0][:-1].isdigit()],
                                  key=lambda c: int(c[:-1]))
    elif scope == 'grade' and grade:
        grades = [grade]
        cls = Student.query.filter_by(grade=grade).with_entities(Student.class_name).distinct().all()
        class_map = {grade: sorted([r[0] for r in cls if r[0] and r[0].endswith('班')
                                    and r[0][:-1].isdigit()], key=lambda c: int(c[:-1]))}
    elif scope == 'class':
        grades = sorted({g for g, _c in (classes or [])}, reverse=True)
        class_map = {}
        for g, c in (classes or []):
            class_map.setdefault(g, [])
            if c not in class_map[g]:
                class_map[g].append(c)
        for g in class_map:
            class_map[g].sort(key=lambda c: int(c[:-1]) if c[:-1].isdigit() else 99)
    else:
        grades, class_map = [], {}
    return jsonify(success=True, data={
        'scope': scope, 'grades': grades, 'classes': class_map,
        'categories': CATEGORIES,
        'can_edit': current_user.has_perm('points.edit'),
        'today': date.today().strftime('%Y-%m-%d'),
    })


# ==================== 列表与汇总 ====================

def _filter_query():
    scope, grade, classes = _scope()
    q = PointRecord.query
    q = _apply_scope(q, scope, grade, classes)
    g = request.args.get('grade', '').strip()
    c = request.args.get('class_name', '').strip()
    kw = request.args.get('q', '').strip()
    d1 = request.args.get('date_from', '').strip()
    d2 = request.args.get('date_to', '').strip()
    if g:
        q = q.filter_by(grade=g)
    if c:
        q = q.filter_by(class_name=c)
    if kw:
        q = q.filter(db.or_(PointRecord.student_no.contains(kw),
                            PointRecord.student_name.contains(kw)))
    if d1:
        q = q.filter(PointRecord.recorded_at >= date.fromisoformat(d1))
    if d2:
        q = q.filter(PointRecord.recorded_at <= date.fromisoformat(d2))
    return q


@bp.route('/api/list')
@login_required
@perm_required('points.view')
def api_list():
    q = _filter_query()
    rows = q.order_by(PointRecord.recorded_at.desc(), PointRecord.id.desc()) \
            .limit(500).all()
    total_points = sum(r.points for r in rows)
    students = len({r.student_no for r in rows})
    return jsonify(success=True, data={
        'records': [r.to_dict() for r in rows],
        'stat': {'count': len(rows), 'students': students, 'total_points': total_points},
    })


@bp.route('/api/summary')
@login_required
@perm_required('points.view')
def api_summary():
    q = _filter_query()
    rows = (q.with_entities(PointRecord.student_no, PointRecord.student_name,
                            PointRecord.grade, PointRecord.class_name,
                            func.sum(PointRecord.points).label('pts'),
                            func.count(PointRecord.id).label('cnt'))
            .group_by(PointRecord.student_no)
            .order_by(func.sum(PointRecord.points).desc())
            .limit(500).all())
    return jsonify(success=True, data=[{
        'student_no': r[0], 'student_name': r[1], 'grade': r[2],
        'class_name': r[3], 'points': int(r[4] or 0), 'count': int(r[5] or 0),
    } for r in rows])


@bp.route('/api/students')
@login_required
@perm_required('points.view')
def api_students():
    """录入时按学号/姓名搜索学生（限当前用户范围）"""
    kw = request.args.get('q', '').strip()
    if len(kw) < 1:
        return jsonify(success=True, data=[])
    scope, grade, classes = _scope()
    q = Student.query
    if scope == 'school':
        pass
    elif scope == 'grade' and grade:
        q = q.filter_by(grade=grade)
    elif scope == 'class' and classes:
        from sqlalchemy import or_, and_
        conds = [and_(Student.grade == g, Student.class_name == c) for g, c in classes]
        q = q.filter(or_(*conds))
    else:
        return jsonify(success=True, data=[])
    rows = q.filter(db.or_(Student.student_number.contains(kw),
                           Student.name.contains(kw))) \
            .order_by(Student.grade, Student.class_name, Student.student_number) \
            .limit(20).all()
    return jsonify(success=True, data=[{
        'student_no': str(s.student_number), 'name': s.name,
        'grade': s.grade, 'class_name': s.class_name,
    } for s in rows])


# ==================== 录入 / 编辑 / 删除 ====================

def _parse_payload():
    data = request.get_json(silent=True) or {}
    student_no = str(data.get('student_no') or '').strip()
    try:
        points = int(data.get('points'))
    except (TypeError, ValueError):
        return None, '分值必须是整数'
    reason = (data.get('reason') or '').strip()
    category = (data.get('category') or '').strip()[:20]
    remark = (data.get('remark') or '').strip()[:200]
    rd = (data.get('recorded_at') or '').strip() or date.today().isoformat()
    if not student_no:
        return None, '请选择学生'
    if points == 0 or abs(points) > 100:
        return None, '分值需在 -100 ~ 100 之间且不为 0'
    if not reason:
        return None, '请填写事由'
    try:
        recorded_at = date.fromisoformat(rd)
    except ValueError:
        return None, '日期格式不正确'
    return {'student_no': student_no, 'points': points, 'reason': reason,
            'category': category, 'remark': remark, 'recorded_at': recorded_at}, None


@bp.route('/api/record', methods=['POST'])
@login_required
@perm_required('points.edit')
def api_record_create():
    payload, err = _parse_payload()
    if err:
        return jsonify(success=False, message=err), 400
    scope, grade, classes = _scope()
    student = Student.query.filter_by(student_number=payload['student_no']).first()
    if not student:
        return jsonify(success=False, message='学号未匹配到学生'), 400
    if not _in_scope(student, scope, grade, classes):
        return jsonify(success=False, message='该学生不在您的管理范围内'), 403
    rec = PointRecord(
        student_no=str(student.student_number), student_name=student.name,
        grade=student.grade, class_name=student.class_name,
        points=payload['points'], category=payload['category'],
        reason=payload['reason'], remark=payload['remark'],
        recorded_at=payload['recorded_at'],
        operator_id=current_user.id, operator_name=current_user.real_name)
    db.session.add(rec)
    db.session.commit()
    log_operation(current_user, '录入', '积分', rec.id,
                  f"{student.name} {payload['points']:+d}分 {payload['reason']}",
                  module='points')
    return jsonify(success=True, message='已记录', data=rec.to_dict())


@bp.route('/api/record/<int:rid>', methods=['POST'])
@login_required
@perm_required('points.edit')
def api_record_update(rid):
    rec = PointRecord.query.get_or_404(rid)
    scope, grade, classes = _scope()
    student = Student.query.filter_by(student_number=rec.student_no).first()
    if student and not _in_scope(student, scope, grade, classes):
        return jsonify(success=False, message='该记录不在您的管理范围内'), 403
    payload, err = _parse_payload()
    if err:
        return jsonify(success=False, message=err), 400
    rec.points = payload['points']
    rec.category = payload['category']
    rec.reason = payload['reason']
    rec.remark = payload['remark']
    rec.recorded_at = payload['recorded_at']
    db.session.commit()
    return jsonify(success=True, message='已更新', data=rec.to_dict())


@bp.route('/api/record/<int:rid>/delete', methods=['POST'])
@login_required
@perm_required('points.edit')
def api_record_delete(rid):
    rec = PointRecord.query.get_or_404(rid)
    scope, grade, classes = _scope()
    student = Student.query.filter_by(student_number=rec.student_no).first()
    if student and not _in_scope(student, scope, grade, classes):
        return jsonify(success=False, message='该记录不在您的管理范围内'), 403
    db.session.delete(rec)
    db.session.commit()
    log_operation(current_user, '删除', '积分', rid, f'{rec.student_name} {rec.points:+d}', module='points')
    return jsonify(success=True, message='已删除')
