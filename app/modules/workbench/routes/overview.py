# StuLink v1.18.2.0 2026-09-24
# 班级概览 / 学生查看 / 成绩整合 / 积分概览 路由
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from flask import Blueprint, render_template, request, jsonify, abort
from flask_login import login_required, current_user

from app.utils.decorators import perm_required
from app.modules.academic.services import teacher_service
from app.modules.workbench.services import class_overview_service as svc
from app.modules.workbench.services import scope as wb_scope

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
    # 可见班级按数据范围解析（管理员/年级长/班主任/任课教师口径统一）
    classes_info = wb_scope.managed_class_pairs(current_user)
    if not classes_info:
        return render_template('workbench/students.html',
                               classes_info=[], selected_grade='', selected_class='',
                               result=None, search='', teacher=teacher)

    # 默认选中第一个班级（或所选年级的第一个班）
    grade = request.args.get('grade') or classes_info[0][0]
    class_name = request.args.get('class_name')
    if not class_name:
        class_name = next((c for g, c in classes_info if g == grade), classes_info[0][1])
    # 年级列表（去重排序，供模板渲染年级按钮行）
    grades = sorted(set(g for g, _ in classes_info))
    # 越权防护：URL 参数指向非管辖班级时拒绝（原先可拉任意班名单）
    if not wb_scope.class_allowed(current_user, grade, class_name):
        abort(403)
    search = request.args.get('search', '').strip()
    page = int(request.args.get('page', 1))

    result = svc.get_students_by_class(grade, class_name, search=search or None, page=page)
    return render_template('workbench/students.html',
                           classes_info=classes_info, grades=grades,
                           selected_grade=grade, selected_class=class_name,
                           result=result, search=search, teacher=teacher)


@bp.route('/api/students')
@login_required
@perm_required('workbench.class_view')
def students_api():
    """学生数据 JSON（供 AJAX 或外部调用）"""
    grade = request.args.get('grade', '')
    class_name = request.args.get('class_name', '')
    if not grade or not class_name:
        return jsonify({'error': '缺少 grade 或 class_name 参数'}), 400
    if not wb_scope.class_allowed(current_user, grade, class_name):
        return jsonify({'error': '无权访问该班级数据'}), 403

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
