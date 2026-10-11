# -*- coding: utf-8 -*-
"""数据范围守卫：复用系统既有的范围判定，越界统一抛 PermissionError。

MCP 侧不重新实现一套权限逻辑——直接调用成绩模块 / 工作台模块已有的
范围函数，保证"AI 能看到的范围"与"网页上该账号能看到的范围"完全一致。
"""
from . import bootstrap  # noqa: F401  确保项目根已进 sys.path


def student_scope(user, student_no):
    """校验学号在账号可见范围内（越界抛 PermissionError）。"""
    from app.modules.grades.services.scope import student_in_scope
    return student_in_scope(user, student_no)


def class_scope(user, grade, class_name):
    """校验班级在账号可见范围内（越界抛 PermissionError）。"""
    from app.modules.workbench.services.scope import assert_class_allowed
    return assert_class_allowed(user, grade, class_name)


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