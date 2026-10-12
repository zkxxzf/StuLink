# -*- coding: utf-8 -*-
"""通用守卫：功能权限点校验 + 取考试/学生（不存在时给中文提示）。

**成绩的数据范围（谁能看哪个年级/班级/学科）统一由 `core/score_guard.py` 裁决**，
本模块不再承载任何成绩可见性逻辑，只保留与业务无关的通用小工具：
  · perm    —— 功能权限点，对应网页端 @perm_required(...)；
  · find_student / exam_or_raise —— 按学号/考试 id 取对象，越界交给 score_guard 判定。
"""
from . import bootstrap  # noqa: F401  确保项目根已进 sys.path


def perm(user, key):
    """功能权限校验：对应网页端 @perm_required(key)。"""
    if user is None or not user.has_perm(key):
        raise PermissionError(
            f'账号缺少权限：{key}（请在权限管理中为绑定账号所在权限组勾选该权限）')
    return True


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
