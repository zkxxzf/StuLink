# -*- coding: utf-8 -*-
"""表彰名单页（2026-10-18）：年级总分榜 / 卓越班榜 / 单科榜。

用户口径：**每个人都可以看** —— 只校验成绩模块查看权限，不做班级/年级范围
收敛（表彰名单本来就是公开宣读的名单）。只读，不写库；计算复用 ExamData
进程内缓存，切换考试不重复读表。
"""
from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import login_required

from app.models.grades import Exam
from app.modules.grades import bp
from app.modules.grades.services import honor_service
from app.utils.decorators import perm_required


def _exam_list():
    """可选考试：只列已导入成绩的场次（draft/dirty 不出现）。"""
    return (Exam.query.filter(Exam.status == 'imported')
            .order_by(Exam.exam_date.desc(), Exam.id.desc()).all())


def _pick_exam(exams):
    """?exam=<id> 优先，缺省=最近一场；指定了不可用的场次则提示并回落。"""
    raw = (request.args.get('exam') or '').strip()
    if not raw:
        return exams[0], None
    if not raw.isdigit():
        abort(404)
    exam = next((e for e in exams if e.id == int(raw)), None)
    if exam is None:
        flash('该考试不存在或尚未导入成绩，已显示最近一场', 'warning')
        return exams[0], 'fallback'
    return exam, None


@bp.route('/honor')
@login_required
@perm_required('grades.view')
def honor():
    """表彰名单（?exam=<id>&group=combo|direction|whole）。"""
    exams = _exam_list()
    if not exams:
        return render_template('grades/honor.html', exams=[], by_grade={},
                               data=None, exam=None, title='表彰名单')
    exam, fallback = _pick_exam(exams)
    if fallback:
        return redirect(url_for('grades.honor', exam=exam.id))
    data = honor_service.build(exam.id, (request.args.get('group') or '').strip())
    by_grade = {}
    for e in exams:
        by_grade.setdefault(e.grade, []).append(e)
    return render_template('grades/honor.html', exams=exams, by_grade=by_grade,
                           data=data, exam=exam, title='表彰名单')
