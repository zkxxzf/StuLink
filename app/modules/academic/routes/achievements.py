# StuLink v1.18.2.0 2026-09-24
# 教务 · 教师业绩库：录入 / 列表筛选 / 审核（教师工作台提交的待审核业绩）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from datetime import date, datetime

from flask import render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from app.extensions import db
from app.models.academic import (TeacherAchievement, Teacher,
                                 ACHIEVEMENT_CATEGORIES, ACHIEVEMENT_LEVELS,
                                 ACHIEVEMENT_STATUS)
from app.modules.academic import bp
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation

_CATEGORY_KEYS = {k for k, _ in ACHIEVEMENT_CATEGORIES}


@bp.route('/achievements')
@login_required
@perm_required('academic.view')
def achievements_page():
    """教师业绩库"""
    teacher_uid = (request.args.get('teacher_uid') or '').strip()
    category = (request.args.get('category') or '').strip()
    status = (request.args.get('status') or '').strip()

    q = TeacherAchievement.query
    if teacher_uid:
        q = q.filter_by(teacher_uid=teacher_uid)
    if category in _CATEGORY_KEYS:
        q = q.filter_by(category=category)
    if status in ACHIEVEMENT_STATUS:
        q = q.filter_by(status=status)
    items = (q.order_by(TeacherAchievement.created_at.desc(),
                        TeacherAchievement.id.desc()).limit(300).all())

    pending = TeacherAchievement.query.filter_by(status='pending').count()
    teachers = Teacher.query.filter_by(status='active').order_by(
        Teacher.teacher_uid).all()
    return render_template('academic/achievements.html',
                           items=items, teachers=teachers,
                           categories=ACHIEVEMENT_CATEGORIES,
                           levels=ACHIEVEMENT_LEVELS,
                           status_map=ACHIEVEMENT_STATUS, pending=pending,
                           f_teacher=teacher_uid, f_category=category,
                           f_status=status, today=date.today().isoformat())


def _parse_date(value):
    try:
        return date.fromisoformat((value or '').strip())
    except ValueError:
        return None


@bp.route('/achievements/add', methods=['POST'])
@login_required
@perm_required('academic.edit')
def achievements_add():
    """教务录入业绩（直接生效）"""
    back = redirect(url_for('academic.achievements_page'))
    teacher_uid = (request.form.get('teacher_uid') or '').strip()
    category = (request.form.get('category') or '').strip()
    title = (request.form.get('title') or '').strip()
    t = Teacher.query.filter_by(teacher_uid=teacher_uid).first()
    if not t:
        flash('请选择教师', 'danger')
        return back
    if category not in _CATEGORY_KEYS:
        flash('请选择有效的业绩类别', 'danger')
        return back
    if not title:
        flash('请填写业绩名称', 'danger')
        return back

    rec = TeacherAchievement(
        teacher_uid=t.teacher_uid, teacher_name=t.name,
        category=category, title=title,
        level=(request.form.get('level') or '').strip() or None,
        obtain_date=_parse_date(request.form.get('obtain_date')),
        issuer=(request.form.get('issuer') or '').strip() or None,
        note=(request.form.get('note') or '').strip() or None,
        status='approved', submitted_by=current_user.id,
    )
    db.session.add(rec)
    db.session.commit()
    log_operation(current_user, '新增', '教师业绩', rec.id,
                  f'{t.name} {title}', module='academic')
    flash(f'已录入 {t.name} 的业绩：{title}', 'success')
    return back


@bp.route('/achievements/<int:aid>/review', methods=['POST'])
@login_required
@perm_required('academic.edit')
def achievements_review(aid):
    """审核教师工作台提交的业绩"""
    rec = db.session.get(TeacherAchievement, aid)
    if not rec:
        abort(404)
    action = (request.form.get('action') or '').strip()
    if action == 'approve':
        rec.status = 'approved'
    elif action == 'reject':
        rec.status = 'rejected'
    else:
        flash('无效的审核操作', 'danger')
        return redirect(url_for('academic.achievements_page'))
    rec.reviewed_by = current_user.id
    rec.reviewed_at = datetime.now()
    db.session.commit()
    log_operation(current_user, '审核', '教师业绩', rec.id,
                  f'{rec.teacher_name} {rec.title} → {ACHIEVEMENT_STATUS[rec.status]}',
                  module='academic')
    flash(f'已{ACHIEVEMENT_STATUS[rec.status]}：{rec.title}', 'success')
    return redirect(url_for('academic.achievements_page'))


@bp.route('/achievements/<int:aid>/delete', methods=['POST'])
@login_required
@perm_required('academic.edit')
def achievements_delete(aid):
    rec = db.session.get(TeacherAchievement, aid)
    if not rec:
        abort(404)
    db.session.delete(rec)
    db.session.commit()
    log_operation(current_user, '删除', '教师业绩', aid,
                  f'{rec.teacher_name} {rec.title}', module='academic')
    flash('业绩记录已删除', 'success')
    return redirect(url_for('academic.achievements_page'))
