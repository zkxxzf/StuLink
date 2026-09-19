# StuLink v1.9.3 2026-09-18
# 班主任工作记录路由（班会 / 家访 / 谈话）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from datetime import date

from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for
from flask_login import login_required, current_user

from app.modules.academic.services import teacher_service
from app.modules.workbench.services.work_record_service import (
    add_record, get_records, update_record, delete_record, get_record_detail,
)
from app.modules.workbench.services.class_overview_service import get_managed_classes
from app.models.academic import RECORD_TYPES
from app.models import Student

bp = Blueprint('records', __name__, url_prefix='/workbench/records')


def _teacher_or_none():
    return teacher_service.teacher_of_user(current_user)


def _get_classes(user_id):
    """获取管辖班级列表（供模板下拉用）"""
    classes_info = get_managed_classes(user_id)
    return [{'class_name': c[1], 'label': f'{c[0]} {c[1]}'} for c in (classes_info or [])]


@bp.route('/')
@login_required
def records_page():
    """工作记录列表页（分页 + 类型/班级筛选）"""
    teacher = _teacher_or_none()
    if not teacher:
        flash('请先关联教务教师名单', 'warning')
        return redirect(url_for('workbench.index'))

    classes = _get_classes(current_user.id)
    current_type = request.args.get('type', '')
    current_class = request.args.get('class', '')
    page = request.args.get('page', 1, type=int)

    pag, items = get_records(
        teacher.teacher_uid,
        record_type=current_type or None,
        class_name=current_class or None,
        page=page, per_page=20,
    )
    return render_template('workbench/records.html',
                           teacher=teacher,
                           today=date.today(),
                           record_types=RECORD_TYPES,
                           classes=classes,
                           current_type=current_type,
                           current_class=current_class,
                           records=items,
                           pag=pag)


@bp.route('/add', methods=['POST'])
@login_required
def record_add():
    """新增工作记录"""
    teacher = _teacher_or_none()
    if not teacher:
        flash('请先关联教务教师名单', 'warning')
        return redirect(url_for('workbench.index'))

    record_type = request.form.get('record_type', '')
    class_name = request.form.get('class_name', '')
    rec_date = request.form.get('date', '')
    title = request.form.get('title', '')
    content = request.form.get('content', '')
    student_no = request.form.get('student_no', '')
    student_name = request.form.get('student_name', '')
    follow_up = request.form.get('follow_up', '')

    rec = add_record(
        teacher_uid=teacher.teacher_uid,
        teacher_name=teacher.name,
        record_type=record_type,
        class_name=class_name,
        date_val=rec_date,
        title=title,
        content=content,
        student_no=student_no,
        student_name=student_name,
        follow_up=follow_up,
    )
    if rec:
        flash('工作记录已保存', 'success')
    else:
        flash('保存失败，请检查必填项', 'danger')
    return redirect(url_for('records.records_page'))


@bp.route('/<int:record_id>/edit', methods=['POST'])
@login_required
def record_edit(record_id):
    """编辑工作记录"""
    teacher = _teacher_or_none()
    if not teacher:
        flash('请先关联教务教师名单', 'warning')
        return redirect(url_for('workbench.index'))

    rec = update_record(
        record_id, teacher.teacher_uid,
        record_type=request.form.get('record_type', ''),
        class_name=request.form.get('class_name', ''),
        date=request.form.get('date', ''),
        title=request.form.get('title', ''),
        content=request.form.get('content', ''),
        student_no=request.form.get('student_no', ''),
        student_name=request.form.get('student_name', ''),
        follow_up=request.form.get('follow_up', ''),
    )
    if rec:
        flash('记录已更新', 'success')
    else:
        flash('更新失败或无权操作', 'danger')
    return redirect(url_for('records.records_page'))


@bp.route('/<int:record_id>/delete', methods=['POST'])
@login_required
def record_delete(record_id):
    """删除工作记录"""
    teacher = _teacher_or_none()
    if not teacher:
        flash('请先关联教务教师名单', 'warning')
        return redirect(url_for('workbench.index'))

    ok = delete_record(record_id, teacher.teacher_uid)
    if ok:
        flash('记录已删除', 'success')
    else:
        flash('删除失败或无权操作', 'danger')
    return redirect(url_for('records.records_page'))


@bp.route('/api/<int:record_id>')
@login_required
def record_detail_api(record_id):
    """记录详情 JSON（编辑模态框加载用）"""
    teacher = _teacher_or_none()
    if not teacher:
        return jsonify({'error': '未关联教师'}), 403

    rec = get_record_detail(record_id)
    if not rec or rec.teacher_uid != teacher.teacher_uid:
        return jsonify({'error': '记录不存在或无权查看'}), 404
    return jsonify({
        'id': rec.id,
        'record_type': rec.record_type,
        'class_name': rec.class_name,
        'date': rec.date.isoformat() if rec.date else '',
        'title': rec.title,
        'content': rec.content or '',
        'student_no': rec.student_no or '',
        'student_name': rec.student_name or '',
        'follow_up': rec.follow_up or '',
    })


@bp.route('/api/student-lookup')
@login_required
def student_lookup():
    """按学号查学生姓名（家访/谈话关联学生用）"""
    no = request.args.get('no', '').strip()
    if not no:
        return jsonify({'name': ''})
    s = Student.query.filter_by(student_number=no).first()
    return jsonify({'name': s.name if s else '未找到'})
