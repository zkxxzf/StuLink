# -*- coding: utf-8 -*-
"""数据范围与功能权限守卫：复用系统既有判定，越界统一抛 PermissionError。

MCP 侧不重新实现一套权限逻辑——直接调用成绩模块 / 工作台模块已有的
范围函数与 User.has_perm，保证"AI 能看到的范围"与"网页上该账号能看到的范围"一致：
  · 数据范围：student_in_scope / class_allowed / check_exam_visible；
  · 功能权限：网页端 @perm_required(...) 对应的权限点（has_perm）；
  · 分析维度：网页端 _guard_tab 的"角色 → 分析 tab"可见矩阵。
"""
from . import bootstrap  # noqa: F401  确保项目根已进 sys.path

_ALL_TABS = {'grade', 'class', 'subject', 'teacher', 'compare'}


def perm(user, key):
    """功能权限校验：对应网页端 @perm_required(key)。"""
    if user is None or not user.has_perm(key):
        raise PermissionError(
            f'账号缺少权限：{key}（请在权限管理中为绑定账号所在权限组勾选该权限）')
    return True


def student_scope(user, student_no):
    """校验学号在账号可见范围内（越界抛 PermissionError）。"""
    from app.modules.grades.services.scope import student_in_scope
    return student_in_scope(user, student_no)


def class_scope(user, grade, class_name):
    """校验班级在账号可见范围内（越界抛 PermissionError）。"""
    from app.modules.workbench.services.scope import assert_class_allowed
    return assert_class_allowed(user, grade, class_name)


def tab_scope(user, tab):
    """成绩分析维度校验：移植网页端 analysis.py 的 _guard_tab 可见矩阵。

    网页端口径：管理员 / 校级范围 / 用户级数据范围 / 年级长（或年级范围）可用
    全部五个分析维度；班主任仅 class；任课教师仅 teacher；其余一律拒绝。
    """
    from app.modules.grades.services import scope as scope_service
    if user.role == 'admin':
        allowed = set(_ALL_TABS)
    else:
        scope_type, _grade = scope_service.get_scope(user)
        if scope_type == 'school' or scope_service.has_user_scope(user):
            allowed = set(_ALL_TABS)
        elif user.role == 'grade_leader' or scope_type == 'grade':
            allowed = set(_ALL_TABS)
        elif user.has_role('homeroom_teacher'):
            allowed = {'class'}
        elif user.has_role('teacher'):
            allowed = {'teacher'}
        else:
            allowed = set()
    if tab not in allowed:
        raise PermissionError(f'当前账号无权访问该分析维度（{tab}）')
    return True


def visible_class_names(user, grade):
    """受限用户在该年级可见的班级名集合；None = 不限班（与 visible_classes 同源）。

    网页端可见性过滤写的是 `if limited:`（空列表会被当作"不过滤"），这里用
    `is None` 判定：空列表代表"没有任何可见班级"，放行会越权，故按空集合处理。
    """
    from app.modules.grades.services import scope as scope_service
    limited = scope_service.visible_classes(user)
    if limited is None:
        return None
    return {c for g, c in limited if g == grade}


def exam_scope(user, exam):
    """校验考试在账号可见范围内（越界抛 PermissionError）。"""
    from app.modules.grades.services.scope import check_exam_visible
    return check_exam_visible(user, exam)


def find_student(student_no):
    """按学号取学生（不存在抛 ValueError，便于工具返回明确提示）。"""
    from app.models.student import Student
    student = Student.query.filter_by(student_number=student_no).first()
    if student is None:
        raise ValueError(f'学号不存在：{student_no}')
    return student


def exam_or_raise(exam_id):
    """按 id 取考试（不存在抛 ValueError）。"""
    from app.extensions import db
    from app.models.grades import Exam
    exam = db.session.get(Exam, int(exam_id)) if exam_id else None
    if exam is None:
        raise ValueError(f'考试不存在：exam_id={exam_id}（可先用 list_exams 查询可用考试）')
    return exam