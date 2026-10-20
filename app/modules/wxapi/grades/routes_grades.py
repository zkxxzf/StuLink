# StuLink v1.19.49.43 2026-10-18
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序成绩接口：考试列表 / 学生搜索 / 个人成绩。

数据全部来自 `student_service`（网页同款口径），范围过滤沿用
`_student_query_in_scope`，不在本文件重算任何统计。
"""
from flask import g, request
from sqlalchemy import or_

from app.models import Exam, Student
from app.modules.grades.routes.student_query import _student_query_in_scope
from app.modules.grades.services import scope as scope_service
from app.modules.grades.services import student_service
from app.modules.wxapi import bp
from app.modules.wxapi.core.guards import wx_perm_required
from app.modules.wxapi.core.response import wx_err, wx_ok
from app.modules.wxapi.core.serializers import (
    exam_item,
    grade_payload,
    student_item,
)

PERM = 'grades.student_query'
SEARCH_LIMIT = 50


def _visible_exams(user, grade=''):
    """按成绩可见年级列出已导入考试（倒序）。"""
    grades = scope_service.visible_grades(user)
    # 2026-10-19：dirty（已导入待重算）也算已导入
    q = Exam.query.filter(Exam.status != 'draft')
    if grades is not None:
        q = q.filter(Exam.grade.in_(list(grades)))
    if grade:
        q = q.filter(Exam.grade == grade)
    return q.order_by(Exam.exam_date.desc(), Exam.id.desc()).all()


def _filters():
    """把查询参数转成 build_data 的 filters（只传非空项）。"""
    f = {}
    exam_id = request.args.get('exam_id', type=int)
    term = (request.args.get('term') or '').strip()
    subject = (request.args.get('subject') or '').strip()
    if exam_id:
        f['exam_id'] = exam_id
    if term:
        f['term'] = term
    if subject:
        f['subject'] = subject
    return f


@bp.route('/grades/exams')
@wx_perm_required(PERM)
def wx_grades_exams():
    """可见考试列表（query: grade= 按年级过滤）。"""
    grade = (request.args.get('grade') or '').strip()
    exams = _visible_exams(g.wx_user, grade)
    return wx_ok([exam_item(e) for e in exams])


@bp.route('/grades/students')
@wx_perm_required(PERM)
def wx_grades_students():
    """学生搜索（query: q=学号或姓名模糊 / grade / class_name / limit）。"""
    q = (request.args.get('q') or '').strip()
    if not q:
        return wx_err('请输入学号或姓名')
    grade = (request.args.get('grade') or '').strip()
    class_name = (request.args.get('class_name') or '').strip()
    limit = request.args.get('limit', type=int) or 20
    limit = max(1, min(limit, SEARCH_LIMIT))
    query = _student_query_in_scope(g.wx_user)
    query = query.filter(or_(Student.student_number.like('%' + q + '%'),
                             Student.name.like('%' + q + '%')))
    if grade:
        query = query.filter(Student.grade == grade)
    if class_name:
        query = query.filter(Student.class_name == class_name)
    rows = query.order_by(Student.student_number).limit(limit).all()
    return wx_ok([student_item(s) for s in rows])


@bp.route('/grades/classes')
@wx_perm_required(PERM)
def wx_grades_classes():
    """可见年级 / 班级列表（供小程序筛选器级联）。

    只取「本人可见学生」的年级班级组合，不查全校花名册。
    """
    rows = (_student_query_in_scope(g.wx_user)
            .with_entities(Student.grade, Student.class_name)
            .distinct().all())
    buckets = {}
    for grade, cls in rows:
        if not grade:
            continue
        buckets.setdefault(grade, set())
        if cls:
            buckets[grade].add(cls)
    out = [{'grade': g, 'classes': sorted(cs)}
           for g, cs in sorted(buckets.items())]
    return wx_ok(out)


@bp.route('/grades/student/<no>')
@wx_perm_required(PERM)
def wx_grades_student(no):
    """个人成绩（query: exam_id= 指定考试，空则最近一场 + 历次趋势）。"""
    no = (no or '').strip()
    if not no:
        return wx_err('缺少学号')
    try:
        data = student_service.build_data(g.wx_user, no, _filters())
    except PermissionError:
        return wx_err('该学生不在你的可见范围内', 403)
    payload = grade_payload(data)
    if payload is None:
        return wx_err((data or {}).get('error') or '未查询到成绩数据', 404)
    return wx_ok(payload)
