# StuLink v1.19.49.44 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序成绩管理：写操作。

目前只开放「新建考试」——它与网页 `/grades/exams/new` 同一套落库字段
（年级 / 名称 / 日期 / 性质 / 阶段 / 学期），并补同名阻断与操作日志。
"""
from datetime import date

from flask import g, request

from app.extensions import db
from app.models.grades import Exam
from app.modules.grades.services import scope as scope_service
from app.modules.grades.utils import term_of_date
from app.modules.wxapi import bp
from app.modules.wxapi.core.guards import wx_perm_required
from app.modules.wxapi.core.response import wx_err, wx_ok
from app.utils.helpers import log_operation


@bp.route('/grades/manage/exam-new-options')
@wx_perm_required('grades.edit')
def wx_manage_exam_new_options():
    """新建考试的表单选项（年级 / 性质 / 阶段 / 今天）。"""
    from app.models.grades import EXAM_KINDS, EXAM_STAGES
    from app.modules.grades.routes.exams import _dict_values
    return wx_ok({
        'grades': [x for x in (scope_service.visible_grades(g.wx_user) or [])
                   if x],
        'kinds': _dict_values('exam_kind', EXAM_KINDS),
        'stages': _dict_values('exam_type', EXAM_STAGES),
        'today': date.today().isoformat(),
    })


@bp.route('/grades/manage/exam-new', methods=['POST'])
@wx_perm_required('grades.edit')
def wx_manage_exam_new():
    """新建考试（json: grade / name / exam_date / exam_kind / exam_type）。"""
    data = request.get_json(silent=True) or {}
    grade = (data.get('grade') or '').strip()
    name = (data.get('name') or '').strip()
    raw_date = (data.get('exam_date') or '').strip()
    exam_kind = (data.get('exam_kind') or '').strip()
    exam_type = (data.get('exam_type') or '').strip()

    grades = scope_service.visible_grades(g.wx_user) or []
    if not grade or grade not in grades:
        return wx_err('无权为该年级创建考试', 403)
    try:
        exam_date = date.fromisoformat(raw_date)
    except (TypeError, ValueError):
        exam_date = date.today()
    if not name:
        name = '%s%s' % (grade, exam_date.isoformat())
    if Exam.query.filter_by(grade=grade, name=name).first():
        return wx_err('同年级已存在同名考试「%s」，请修改名称' % name, 409)

    exam = Exam(grade=grade, name=name, exam_date=exam_date,
                exam_kind=exam_kind, exam_type=exam_type or '其他',
                term=term_of_date(exam_date), operator_id=g.wx_user.id)
    db.session.add(exam)
    db.session.commit()
    log_operation(g.wx_user, '新建', '考试', exam.id,
                  '%s（小程序）' % name, module='grades')
    return wx_ok({'id': exam.id, 'name': exam.name})
