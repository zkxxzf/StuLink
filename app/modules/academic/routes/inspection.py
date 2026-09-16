# StuLink v1.9.2 2026-09-16
# 教务 · 查课统计：记录录入 / 列表筛选 / 月度统计
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from datetime import date

from flask import render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from sqlalchemy import func

from app.extensions import db
from app.models.academic import (InspectionRecord, Teacher, INSPECTION_RESULTS)
from app.modules.academic import bp
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation

_RESULT_KEYS = {k for k, _ in INSPECTION_RESULTS}


def _active_teachers():
    return Teacher.query.filter_by(status='active').order_by(Teacher.teacher_uid).all()


@bp.route('/inspection')
@login_required
@perm_required('academic.view')
def inspection_page():
    """查课记录与统计"""
    teacher_uid = (request.args.get('teacher_uid') or '').strip()
    result_f = (request.args.get('result') or '').strip()
    d_from = (request.args.get('date_from') or '').strip()
    d_to = (request.args.get('date_to') or '').strip()

    q = InspectionRecord.query
    if teacher_uid:
        q = q.filter_by(teacher_uid=teacher_uid)
    if result_f in _RESULT_KEYS:
        q = q.filter_by(result=result_f)
    if d_from:
        try:
            q = q.filter(InspectionRecord.inspect_date >= date.fromisoformat(d_from))
        except ValueError:
            pass
    if d_to:
        try:
            q = q.filter(InspectionRecord.inspect_date <= date.fromisoformat(d_to))
        except ValueError:
            pass
    records = (q.order_by(InspectionRecord.inspect_date.desc(),
                          InspectionRecord.id.desc()).limit(300).all())

    # 本月统计
    today = date.today()
    month_start = today.replace(day=1)
    month_q = InspectionRecord.query.filter(
        InspectionRecord.inspect_date >= month_start)
    month_total = month_q.count()
    month_abnormal = month_q.filter(
        InspectionRecord.result != 'normal').count()
    by_teacher = (InspectionRecord.query.with_entities(
        InspectionRecord.teacher_uid, InspectionRecord.teacher_name,
        func.count(InspectionRecord.id))
        .filter(InspectionRecord.inspect_date >= month_start)
        .group_by(InspectionRecord.teacher_uid, InspectionRecord.teacher_name)
        .order_by(func.count(InspectionRecord.id).desc()).limit(10).all())

    return render_template('academic/inspection.html',
                           records=records, teachers=_active_teachers(),
                           results=INSPECTION_RESULTS,
                           f_teacher=teacher_uid, f_result=result_f,
                           f_from=d_from, f_to=d_to,
                           month_total=month_total, month_abnormal=month_abnormal,
                           by_teacher=by_teacher, today=today.isoformat())


@bp.route('/inspection/add', methods=['POST'])
@login_required
@perm_required('academic.view')
def inspection_add():
    """录入一条查课记录"""
    back = redirect(url_for('academic.inspection_page'))
    date_str = (request.form.get('inspect_date') or '').strip()
    teacher_uid = (request.form.get('teacher_uid') or '').strip()
    result = (request.form.get('result') or 'normal').strip()
    try:
        inspect_date = date.fromisoformat(date_str)
    except ValueError:
        flash('请选择正确的查课日期', 'danger')
        return back
    t = Teacher.query.filter_by(teacher_uid=teacher_uid).first()
    if not t:
        flash('请选择被查课的教师', 'danger')
        return back
    if result not in _RESULT_KEYS:
        result = 'normal'
    period = request.form.get('period', type=int)

    rec = InspectionRecord(
        inspect_date=inspect_date,
        period=period,
        teacher_uid=t.teacher_uid,
        teacher_name=t.name,
        class_name=(request.form.get('class_name') or '').strip() or None,
        subject=(request.form.get('subject') or '').strip() or t.subject,
        result=result,
        inspector_id=current_user.id,
        note=(request.form.get('note') or '').strip() or None,
    )
    db.session.add(rec)
    db.session.commit()
    log_operation(current_user, '新增', '查课记录', rec.id,
                  f'{inspect_date} 第{period or "?"}节 {t.name} {rec.class_name or ""}',
                  module='academic')
    flash(f'已记录 {t.name} 的查课情况', 'success')
    return back


@bp.route('/inspection/<int:rid>/delete', methods=['POST'])
@login_required
@perm_required('academic.view')
def inspection_delete(rid):
    rec = db.session.get(InspectionRecord, rid)
    if not rec:
        abort(404)
    db.session.delete(rec)
    db.session.commit()
    log_operation(current_user, '删除', '查课记录', rid,
                  f'{rec.inspect_date} {rec.teacher_name}', module='academic')
    flash('查课记录已删除', 'success')
    return redirect(url_for('academic.inspection_page'))
