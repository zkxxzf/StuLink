# StuLink v1.9.0 2026-09-03
# 成绩分析：主页（四 tab）+ options/analysis API + AI 预留
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from flask import render_template, request, jsonify, abort
from flask_login import login_required, current_user
from app.models.grades import Exam, TeacherSubjectLink
from app.models import Student
from app.modules.grades import bp
from app.modules.grades.services import tab_service, scope as scope_service
from app.modules.grades.utils import numeric_classes
from app.utils.decorators import perm_required
from app.utils.cache import cache


@bp.route('/')
@login_required
@perm_required('grades.view')
def index():
    return render_template('grades/index.html')


@bp.route('/history')
@login_required
@perm_required('grades.view')
def history():
    """毕业生（往届学生）成绩查询（二期，预留占位）"""
    return render_template('grades/history.html')


@bp.route('/alumni')
@login_required
@perm_required('grades.view')
def alumni():
    """往届成绩查询（二期，预留占位）"""
    return render_template('grades/alumni.html')


# ==================== 筛选选项 ====================

@bp.route('/api/options')
@login_required
@perm_required('grades.view')
def api_options():
    """顶部筛选器选项（按用户范围过滤）"""
    scope_type, grade = scope_service.get_scope(current_user)
    if current_user.role == 'admin':
        scope_type = 'school'
    grades = scope_service.visible_grades(current_user)
    # 用户可见考试（未毕业年级 + 范围过滤）
    exams = Exam.query.order_by(Exam.exam_date.desc(), Exam.id.desc()).all()
    exams = [e for e in exams if e.grade in grades]
    by_grade = {}
    for e in exams:
        by_grade.setdefault(e.grade, []).append({
            'id': e.id, 'name': e.name, 'date': e.exam_date.strftime('%Y-%m-%d'),
            'type': e.exam_type or '', 'status': e.status,
        })
    # 班级（年级下的数字教学班 + 锁定范围）
    classes_by_grade = {}
    for g in grades:
        st_rows = Student.query.filter_by(grade=g).with_entities(Student.class_name).distinct().all()
        classes_by_grade[g] = numeric_classes([r[0] for r in st_rows])
    # tab 可见性（恒返回 4 键，前端据此显隐）
    tabs = {'grade': False, 'class': False, 'subject': False, 'teacher': False}
    if current_user.role == 'admin' or scope_type == 'school':
        tabs = {'grade': True, 'class': True, 'subject': True, 'teacher': True}
    elif scope_type == 'grade':
        tabs = {'grade': True, 'class': True, 'subject': True, 'teacher': True}
    elif current_user.has_role('homeroom_teacher'):
        tabs = {'grade': False, 'class': True, 'subject': False, 'teacher': False}
    elif current_user.has_role('teacher'):
        tabs = {'grade': False, 'class': False, 'subject': False, 'teacher': True}
    # 班主任/教师锁定范围
    locked = {'grade': '', 'classes': []}
    if scope_type == 'grade':
        locked['grade'] = grade or ''
    if current_user.has_role('homeroom_teacher'):
        links = scope_service.visible_classes(current_user) or []
        locked['classes'] = [{'grade': g, 'class_name': c} for g, c in links]
        locked['grade'] = links[0][0] if links else ''
    teacher_combos = []
    if current_user.has_role('teacher'):
        for l in scope_service.teacher_links(current_user):
            teacher_combos.append({'grade': l.grade, 'class_name': l.class_name,
                                   'subject': l.subject})
        if teacher_combos:
            locked['grade'] = teacher_combos[0]['grade']
    subjects = []
    cat = None
    from app.models import DictCategory
    cat = DictCategory.query.filter_by(code='exam_subject').first()
    if cat:
        subjects = [i.value for i in cat.items.filter_by(is_active=True)
                    .order_by('sort_order').all()]
    return jsonify(success=True, data={
        'grades': grades,
        'exams': by_grade,
        'classes': classes_by_grade,
        'subjects': subjects,
        'tabs': tabs,
        'locked': locked,
        'teacher_combos': teacher_combos,
        'scope': scope_type,
        'role': current_user.role,
    })


# ==================== 分析 API ====================

TAB_BY_ROLE = {
    'admin': {'grade', 'class', 'subject', 'teacher'},
    'grade_leader': {'grade', 'class', 'subject', 'teacher'},
    'homeroom_teacher': {'class'},
    'teacher': {'teacher'},
}


def _guard_tab(tab):
    """按角色限制可访问的分析 tab（9.2 可见矩阵的后端强制）"""
    allowed = TAB_BY_ROLE.get(current_user.role, set())
    if tab not in allowed:
        abort(403)


def _get_exam(exam_id):
    exam = Exam.query.get(exam_id)
    if not exam:
        abort(404)
    try:
        scope_service.check_exam_visible(current_user, exam)
    except PermissionError:
        abort(403)
    return exam


def _fetch(key, builder):
    data = cache.get(key)
    if data is None:
        data = builder()
        cache.set(key, data, timeout=60)
    return data


@bp.route('/api/analysis/grade')
@login_required
@perm_required('grades.view')
def api_grade_tab():
    _guard_tab('grade')
    exam_id = request.args.get('exam_id', type=int)
    direction = (request.args.get('direction') or '').strip()
    exam = _get_exam(exam_id)
    return jsonify(success=True, data=_fetch(
        f'grades_tab_{exam_id}_grade_{direction}',
        lambda: tab_service.grade_tab(exam_id, direction)))


@bp.route('/api/analysis/class')
@login_required
@perm_required('grades.view')
def api_class_tab():
    _guard_tab('class')
    exam_id = request.args.get('exam_id', type=int)
    class_name = request.args.get('class_name', '').strip()
    exam = _get_exam(exam_id)
    scope_type, _ = scope_service.get_scope(current_user)
    if current_user.has_role('homeroom_teacher'):
        allowed = {(g, c) for g, c in (scope_service.visible_classes(current_user) or [])}
        if (exam.grade, class_name) not in allowed:
            abort(403)
    return jsonify(success=True, data=_fetch(
        f'grades_tab_{exam_id}_class_{class_name}',
        lambda: tab_service.class_tab(exam_id, class_name)))


@bp.route('/api/analysis/subject')
@login_required
@perm_required('grades.view')
def api_subject_tab():
    _guard_tab('subject')
    exam_id = request.args.get('exam_id', type=int)
    subject = request.args.get('subject', '').strip()
    direction = (request.args.get('direction') or '').strip()
    exam = _get_exam(exam_id)
    return jsonify(success=True, data=_fetch(
        f'grades_tab_{exam_id}_subject_{subject}_{direction}',
        lambda: tab_service.subject_tab(exam_id, subject, direction)))


@bp.route('/api/analysis/teacher')
@login_required
@perm_required('grades.view')
def api_teacher_tab():
    _guard_tab('teacher')
    exam_id = request.args.get('exam_id', type=int)
    subject = request.args.get('subject', '').strip() or None
    exam = _get_exam(exam_id)
    scope_type, _ = scope_service.get_scope(current_user)
    if current_user.has_role('teacher'):
        # 任课教师：仅本人映射
        links = scope_service.teacher_links(current_user)
        if not links:
            return jsonify(success=True, data=tab_service._empty(exam))
        data = tab_service.teacher_tab(exam_id, subject=subject, links=links,
                                       grade=exam.grade)
    elif current_user.role == 'admin':
        data = tab_service.teacher_tab(exam_id, subject=subject,
                                       links=None, grade=exam.grade)
    elif scope_type == 'grade':
        # 年级长：本年级
        grade = scope_service.get_scope(current_user)[1]
        links = TeacherSubjectLink.query.filter_by(grade=grade, active=True).all()
        data = tab_service.teacher_tab(exam_id, subject=subject, links=links,
                                       grade=grade)
    else:
        abort(403)
    cache_key = f'grades_tab_{exam_id}_teacher_{subject or "*"}_{current_user.id}'
    return jsonify(success=True, data=_fetch(cache_key, lambda: data))
