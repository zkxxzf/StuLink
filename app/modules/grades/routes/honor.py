# -*- coding: utf-8 -*-
"""表彰名单页（2026-10-18）：年级总分榜 / 卓越班榜 / 单科榜。

用户口径：**每个人都可以看** —— 只校验成绩模块查看权限，不做班级/年级范围
收敛（表彰名单本来就是公开宣读的名单）。只读，不写库；计算复用 ExamData
进程内缓存，切换考试不重复读表。
"""
import io

from flask import (abort, flash, redirect, render_template, request, send_file,
                   url_for)
from flask_login import current_user, login_required

from app.models.grades import Exam
from app.modules.grades import bp
from app.modules.grades.services import honor_service
from app.modules.grades.services import honor_xlsx
from app.modules.grades.services import xlsx_kit
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation

_XLSX_MIME = ('application/vnd.openxmlformats-officedocument'
              '.spreadsheetml.sheet')


def _exam_list():
    """可选考试：只列已导入成绩的场次（draft 不出现）。

    2026-10-19 第十三轮审计：dirty（已导入、改过分待重算）同样有成绩，
    原来被一起排除 → 刚改过分的那场考试在表彰名单里"消失"。
    """
    return (Exam.query.filter(Exam.status != 'draft')
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


@bp.route('/honor/export')
@login_required
@perm_required('grades.view')
def honor_export():
    """导出表彰名单 xlsx（全部榜单各一个工作表）。

    与页面同一份 `honor_service.build` 结果；`cls` / `kw` 为页面当前的
    班级与关键词筛选（不传＝全部）。并列规则不在导出端重算。
    """
    exams = _exam_list()
    if not exams:                        # 无成绩可导：回空态页并提示，不给 404
        flash('暂无已导入成绩的考试，无法导出', 'warning')
        return redirect(url_for('grades.honor'))
    exam, _fallback = _pick_exam(exams)
    data = honor_service.build(exam.id,
                               (request.args.get('group') or '').strip())
    cls = (request.args.get('cls') or '').strip()
    kw = (request.args.get('kw') or '').strip()
    buf = io.BytesIO()
    honor_xlsx.build(data, cls, kw).save(buf)
    buf.seek(0)
    log_operation(current_user, '导出', '表彰名单', exam.id,
                  f'{exam.name} 分组={data["mode"]} '
                  f'班级={cls or "全部"} 关键词={kw or "-"}', module='grades')
    fname = f'表彰名单_{xlsx_kit.safe_fname(exam.name)}.xlsx'
    return send_file(buf, as_attachment=True, download_name=fname,
                     mimetype=_XLSX_MIME)
