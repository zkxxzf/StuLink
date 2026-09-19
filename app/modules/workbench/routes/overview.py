# StuLink v1.9.2 2026-09-18
# 班级概览 / 学生查看 / 成绩整合 / 积分概览 路由
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user

from app.modules.academic.services import teacher_service
from app.modules.workbench.services import class_overview_service as svc
from app.modules.workbench.services import scope_service
from app.utils.decorators import perm_required

bp = Blueprint('overview', __name__, url_prefix='/workbench')


def _teacher_or_none():
    """获取当前用户关联的教师记录"""
    return teacher_service.teacher_of_user(current_user)


# ── 班级概览页 ──────────────────────────────────────────────

@bp.route('/class-overview')
@login_required
@perm_required('workbench.class_view')
def class_overview_page():
    """班级概览页：管辖班级卡片网格"""
    teacher = _teacher_or_none()
    user_id = current_user.id
    month = request.args.get('month', '')
    classes = svc.get_class_overview(user_id, month_str=month or None)
    # JSON 格式（工作台首页 AJAX 加载用）
    if request.args.get('format') == 'json':
        return jsonify(classes)
    return render_template('workbench/class_overview.html',
                           classes=classes, teacher=teacher,
                           is_head=(current_user.role == 'homeroom_teacher'),
                           current_month=month)


# ── 学生信息页 ──────────────────────────────────────────────

@bp.route('/students')
@login_required
@perm_required('workbench.class_view')
def students_page():
    """学生信息页：班级切换 + 搜索 + 表格"""
    teacher = _teacher_or_none()
    user_id = current_user.id
    classes_info = svc.get_managed_classes(user_id)
    if not classes_info:
        return render_template('workbench/students.html',
                               classes_info=[], selected_grade='', selected_class='',
                               result=None, search='', teacher=teacher)

    # 默认选中第一个班级
    grade = request.args.get('grade', classes_info[0][0])
    class_name = request.args.get('class_name', classes_info[0][1])
    # 越界（非管辖班级）回退到默认班级
    if not scope_service.is_class_in_scope(current_user, grade, class_name):
        grade, class_name = classes_info[0][0], classes_info[0][1]
    search = request.args.get('search', '').strip()
    page = int(request.args.get('page', 1))

    result = svc.get_students_by_class(grade, class_name, search=search or None, page=page)
    return render_template('workbench/students.html',
                           classes_info=classes_info,
                           selected_grade=grade, selected_class=class_name,
                           result=result, search=search, teacher=teacher)


@bp.route('/api/students')
@login_required
@perm_required('workbench.class_view')
def students_api():
    """学生数据 JSON（供 AJAX 或外部调用）"""
    user_id = current_user.id
    grade = request.args.get('grade', '')
    class_name = request.args.get('class_name', '')
    if not grade or not class_name:
        return jsonify({'error': '缺少 grade 或 class_name 参数'}), 400
    if not scope_service.is_class_in_scope(current_user, grade, class_name):
        return jsonify({'error': '无该班级的访问权限（超出管辖范围）'}), 403

    search = request.args.get('search', '').strip()
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 30))

    result = svc.get_students_by_class(grade, class_name, search=search or None,
                                       page=page, per_page=per_page)
    students_data = []
    for s in result['students']:
        students_data.append({
            'id': s.id,
            'student_number': s.student_number,
            'name': s.name,
            'gender': s.gender,
            'grade': s.grade,
            'class_name': s.class_name,
            'enrollment_status': s.enrollment_status or '',
            'subject_selection': s.subject_selection or '',
        })
    return jsonify({
        'students': students_data,
        'total': result['total'],
        'page': result['page'],
        'pages': result['pages'],
    })


# ── 成绩概览页 ──────────────────────────────────────────────

@bp.route('/grades')
@login_required
@perm_required('workbench.class_view')
def grades_page():
    """成绩概览页：按考试展示任课班级成绩摘要"""
    teacher = _teacher_or_none()
    user_id = current_user.id
    if not teacher or not teacher.user_id:
        return render_template('workbench/grades.html', exam_data=[], teacher=None)

    grade = request.args.get('grade', '')
    exam_data = svc.get_grade_summary(teacher.user_id, grade=grade or None)
    return render_template('workbench/grades.html',
                           exam_data=exam_data, teacher=teacher)


@bp.route('/api/grade-summary')
@login_required
@perm_required('workbench.class_view')
def grade_summary_api():
    """成绩摘要 JSON"""
    teacher = _teacher_or_none()
    if not teacher or not teacher.user_id:
        return jsonify({'data': []})

    grade = request.args.get('grade', '')
    exam_data = svc.get_grade_summary(teacher.user_id, grade=grade or None)
    return jsonify({'data': exam_data})


# ── 积分概览页 ──────────────────────────────────────────────

@bp.route('/points')
@login_required
@perm_required('workbench.class_view')
def points_page():
    """积分概览页：管辖班级积分统计"""
    teacher = _teacher_or_none()
    user_id = current_user.id
    month = request.args.get('month', '')
    summary = svc.get_points_summary(user_id, month_str=month or None)
    return render_template('workbench/points.html',
                           summary=summary, teacher=teacher,
                           current_month=month)


@bp.route('/api/points-summary')
@login_required
@perm_required('workbench.class_view')
def points_summary_api():
    """积分汇总 JSON"""
    user_id = current_user.id
    month = request.args.get('month', '')
    summary = svc.get_points_summary(user_id, month_str=month or None)
    return jsonify(summary)
