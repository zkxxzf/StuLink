# StuLink v1.9.2 2026-09-18
# 教务 · 表单收集：管理端（创建/编辑/发布/关闭/提交列表/审核/导出）
#                 填写端（可填列表/填写/提交/我的提交）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import json
from datetime import datetime

from flask import render_template, request, redirect, url_for, flash, abort, send_file
from flask_login import login_required, current_user

from app.extensions import db
from app.models.academic import (
    FormTemplate, FormQuestion, FormSubmission, FormCategory,
    FORM_STATUS, FORM_TARGET_TYPES, QUESTION_TYPES, SUBMISSION_STATUS,
)
from app.modules.academic import bp
from app.modules.academic.services import form_service
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation


# ── 工具 ─────────────────────────────────────────────────────

def _parse_datetime(value):
    """解析前端 datetime-local 输入"""
    value = (value or '').strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _get_categories():
    """获取分类列表（含默认分类）"""
    cats = FormCategory.query.order_by(FormCategory.sort_order).all()
    return cats


def _parse_questions_from_form():
    """从 POST 数据解析题目 JSON"""
    raw = (request.form.get('questions_json') or '').strip()
    if not raw:
        return []
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return []


# ── 管理端：表单列表 ──────────────────────────────────────────

@bp.route('/forms')
@login_required
@perm_required('academic.edit')
def form_list():
    """表单管理列表"""
    status = (request.args.get('status') or '').strip()
    category = (request.args.get('category') or '').strip()
    page = request.args.get('page', 1, type=int)

    pagination = form_service.get_forms_list(
        status=status or None,
        category=category or None,
        page=page, per_page=20)

    # 每个表单的提交数
    sub_counts = {}
    for tpl in pagination.items:
        sub_counts[tpl.id] = FormSubmission.query.filter_by(template_id=tpl.id).count()

    categories = _get_categories()

    return render_template('academic/form_list.html',
                           pagination=pagination,
                           status_map=FORM_STATUS,
                           target_map=FORM_TARGET_TYPES,
                           categories=categories,
                           sub_counts=sub_counts,
                           f_status=status, f_category=category)


# ── 管理端：创建表单 ──────────────────────────────────────────

@bp.route('/forms/create', methods=['GET', 'POST'])
@login_required
@perm_required('academic.edit')
def form_create():
    """创建新表单"""
    if request.method == 'POST':
        title = (request.form.get('title') or '').strip()
        if not title:
            flash('请填写表单标题', 'danger')
            return redirect(url_for('academic.form_create'))

        description = (request.form.get('description') or '').strip()
        category = (request.form.get('category') or '').strip()
        target_type = (request.form.get('target_type') or 'all').strip()
        target_scope = (request.form.get('target_scope') or '').strip()
        start_time = _parse_datetime(request.form.get('start_time'))
        deadline = _parse_datetime(request.form.get('deadline'))
        max_file_size = request.form.get('max_file_size', 10, type=int)
        allow_multiple = request.form.get('allow_multiple') == '1'

        questions_data = _parse_questions_from_form()

        try:
            tpl = form_service.create_form(
                title=title, description=description, category=category,
                target_type=target_type, target_scope=target_scope,
                start_time=start_time, deadline=deadline,
                max_file_size=max_file_size, allow_multiple=allow_multiple,
                questions_data=questions_data, created_by=current_user.id)

            log_operation(current_user, '新增', '表单', tpl.id,
                          f'创建表单：{title}', module='academic')

            # 保存并发布
            if request.form.get('action') == 'publish':
                form_service.publish_form(tpl.id)
                flash('表单已发布', 'success')
            else:
                flash('表单已保存为草稿', 'success')

            return redirect(url_for('academic.form_list'))

        except Exception as e:
            db.session.rollback()
            flash(f'创建失败：{e}', 'danger')

    categories = _get_categories()
    return render_template('academic/form_create.html',
                           form_data=None,
                           categories=categories,
                           status_map=FORM_STATUS,
                           target_map=FORM_TARGET_TYPES,
                           question_types=QUESTION_TYPES,
                           edit_mode=False)


# ── 管理端：编辑表单 ──────────────────────────────────────────

@bp.route('/forms/<int:form_id>/edit', methods=['GET', 'POST'])
@login_required
@perm_required('academic.edit')
def form_edit(form_id):
    """编辑表单（仅 draft）"""
    tpl = form_service.get_form_detail(form_id)
    if not tpl:
        abort(404)
    if tpl.status != 'draft':
        flash('仅草稿状态可编辑', 'warning')
        return redirect(url_for('academic.form_list'))

    if request.method == 'POST':
        title = (request.form.get('title') or '').strip()
        if not title:
            flash('请填写表单标题', 'danger')
            return redirect(url_for('academic.form_edit', form_id=form_id))

        description = (request.form.get('description') or '').strip()
        category = (request.form.get('category') or '').strip()
        target_type = (request.form.get('target_type') or 'all').strip()
        target_scope = (request.form.get('target_scope') or '').strip()
        start_time = _parse_datetime(request.form.get('start_time'))
        deadline = _parse_datetime(request.form.get('deadline'))
        max_file_size = request.form.get('max_file_size', 10, type=int)
        allow_multiple = request.form.get('allow_multiple') == '1'
        questions_data = _parse_questions_from_form()

        try:
            form_service.update_form(
                form_id=form_id, title=title, description=description,
                category=category, target_type=target_type,
                target_scope=target_scope, start_time=start_time,
                deadline=deadline, max_file_size=max_file_size,
                allow_multiple=allow_multiple, questions_data=questions_data)

            log_operation(current_user, '编辑', '表单', form_id,
                          f'编辑表单：{title}', module='academic')

            if request.form.get('action') == 'publish':
                form_service.publish_form(form_id)
                flash('表单已发布', 'success')
            else:
                flash('表单已更新', 'success')

            return redirect(url_for('academic.form_list'))

        except Exception as e:
            db.session.rollback()
            flash(f'更新失败：{e}', 'danger')

    # GET：填充已有数据
    categories = _get_categories()
    questions_list = [{
        'question_type': q.question_type,
        'title': q.title,
        'description': q.description or '',
        'options': json.loads(q.options_json) if q.options_json else [],
        'required': q.required,
        'file_types': q.file_types or '',
        'max_file_size_mb': q.max_file_size_mb or '',
    } for q in sorted(tpl.questions, key=lambda x: x.sort_order)]

    form_data = {
        'id': tpl.id,
        'title': tpl.title,
        'description': tpl.description or '',
        'category': tpl.category or '',
        'target_type': tpl.target_type or 'all',
        'target_scope': tpl.target_scope or '',
        'start_time': tpl.start_time.strftime('%Y-%m-%dT%H:%M') if tpl.start_time else '',
        'deadline': tpl.deadline.strftime('%Y-%m-%dT%H:%M') if tpl.deadline else '',
        'max_file_size_mb': tpl.max_file_size_mb or 10,
        'allow_multiple': tpl.allow_multiple,
        'questions': questions_list,
    }

    return render_template('academic/form_create.html',
                           form_data=form_data,
                           categories=categories,
                           status_map=FORM_STATUS,
                           target_map=FORM_TARGET_TYPES,
                           question_types=QUESTION_TYPES,
                           edit_mode=True)


# ── 管理端：发布/关闭/删除 ────────────────────────────────────

@bp.route('/forms/<int:form_id>/publish', methods=['POST'])
@login_required
@perm_required('academic.edit')
def form_publish(form_id):
    try:
        form_service.publish_form(form_id)
        log_operation(current_user, '发布', '表单', form_id, '', module='academic')
        flash('表单已发布', 'success')
    except ValueError as e:
        flash(str(e), 'danger')
    return redirect(url_for('academic.form_list'))


@bp.route('/forms/<int:form_id>/close', methods=['POST'])
@login_required
@perm_required('academic.edit')
def form_close(form_id):
    try:
        form_service.close_form(form_id)
        log_operation(current_user, '关闭', '表单', form_id, '', module='academic')
        flash('表单已关闭', 'success')
    except ValueError as e:
        flash(str(e), 'danger')
    return redirect(url_for('academic.form_list'))


@bp.route('/forms/<int:form_id>/delete', methods=['POST'])
@login_required
@perm_required('academic.edit')
def form_delete(form_id):
    try:
        form_service.delete_form(form_id)
        log_operation(current_user, '删除', '表单', form_id, '', module='academic')
        flash('表单已删除', 'success')
    except ValueError as e:
        flash(str(e), 'danger')
    return redirect(url_for('academic.form_list'))


# ── 管理端：提交列表 ──────────────────────────────────────────

@bp.route('/forms/<int:form_id>/submissions')
@login_required
@perm_required('academic.edit')
def form_submissions(form_id):
    """提交列表页"""
    tpl = form_service.get_form_detail(form_id)
    if not tpl:
        abort(404)

    status = (request.args.get('status') or '').strip()
    page = request.args.get('page', 1, type=int)
    pagination = form_service.get_submissions(form_id, status=status or None,
                                              page=page, per_page=20)

    # 统计
    total = FormSubmission.query.filter_by(template_id=form_id).count()
    pending = FormSubmission.query.filter_by(template_id=form_id, status='submitted').count()
    approved = FormSubmission.query.filter_by(template_id=form_id, status='approved').count()
    rejected = FormSubmission.query.filter_by(template_id=form_id, status='rejected').count()

    return render_template('academic/form_detail.html',
                           tpl=tpl,
                           pagination=pagination,
                           status_map=SUBMISSION_STATUS,
                           total=total, pending=pending,
                           approved=approved, rejected=rejected,
                           f_status=status)


# ── 管理端：导出 ──────────────────────────────────────────────

@bp.route('/forms/<int:form_id>/export')
@login_required
@perm_required('academic.edit')
def form_export(form_id):
    try:
        out, title = form_service.export_submissions(form_id)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'{title}_{timestamp}.xlsx'
        log_operation(current_user, '导出', '表单提交', form_id,
                      f'导出：{title}', module='academic')
        return send_file(out, as_attachment=True, download_name=filename,
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    except ValueError as e:
        flash(str(e), 'danger')
        return redirect(url_for('academic.form_submissions', form_id=form_id))


# ── 管理端：审核 ──────────────────────────────────────────────

@bp.route('/forms/submission/<int:sid>/review', methods=['POST'])
@login_required
@perm_required('academic.edit')
def form_review(sid):
    """审核提交（通过/驳回）"""
    new_status = (request.form.get('status') or '').strip()
    note = (request.form.get('note') or '').strip()

    if new_status not in ('approved', 'rejected'):
        flash('无效审核状态', 'danger')
        return redirect(url_for('academic.form_list'))

    try:
        sub = form_service.review_submission(sid, new_status, current_user.id, note)
        log_operation(current_user, '审核', '表单提交', sid,
                      f'{"通过" if new_status == "approved" else "驳回"} #{sub.id}',
                      module='academic')
        flash('审核完成', 'success')
    except ValueError as e:
        flash(str(e), 'danger')

    # 返回来源页
    sub = db.session.get(FormSubmission, sid)
    if sub:
        return redirect(url_for('academic.form_submissions', form_id=sub.template_id))
    return redirect(url_for('academic.form_list'))


# ── 填写端：可填写表单列表 ────────────────────────────────────

@bp.route('/forms/fill')
@login_required
@perm_required('academic.view')
def form_fill_list():
    """获取当前用户可填写的表单列表"""
    submitter_type = 'teacher' if current_user.role in ('teacher', 'admin') else 'student'
    submitter_grade = getattr(current_user, 'grade', None)

    templates = form_service.get_available_forms(
        submitter_type=submitter_type,
        submitter_grade=submitter_grade)

    # 检查每个表单是否已提交
    submitted_ids = set()
    for tpl in templates:
        existing = FormSubmission.query.filter_by(
            template_id=tpl.id, submitter_id=current_user.id).first()
        if existing and not tpl.allow_multiple:
            submitted_ids.add(tpl.id)

    return render_template('academic/form_fill_list.html',
                           templates=templates,
                           submitted_ids=submitted_ids,
                           status_map=FORM_STATUS)


# ── 填写端：填写表单 ──────────────────────────────────────────

@bp.route('/forms/<int:form_id>/fill')
@login_required
@perm_required('academic.view')
def form_fill(form_id):
    """填写页面"""
    tpl = form_service.get_form_detail(form_id)
    if not tpl:
        abort(404)
    if tpl.status != 'open':
        flash('该表单当前不可填写', 'warning')
        return redirect(url_for('academic.form_fill_list'))

    # 资格检查
    eligibility = form_service.check_eligibility(form_id, current_user.id)
    if not eligibility['eligible'] and not tpl.allow_multiple:
        if eligibility['already_submitted']:
            flash('您已提交过此表单', 'info')
            return redirect(url_for('academic.form_my_submissions'))
        flash('当前不在填写时间内', 'warning')
        return redirect(url_for('academic.form_fill_list'))

    questions = sorted(tpl.questions, key=lambda q: q.sort_order)

    return render_template('academic/form_fill.html',
                           tpl=tpl,
                           questions=questions,
                           eligibility=eligibility)


# ── 填写端：提交答案 ──────────────────────────────────────────

@bp.route('/forms/<int:form_id>/submit', methods=['POST'])
@login_required
@perm_required('academic.view')
def form_submit(form_id):
    """提交答案"""
    tpl = form_service.get_form_detail(form_id)
    if not tpl:
        abort(404)

    # 构建提交者信息
    submitter_type = 'teacher' if current_user.role in ('teacher', 'admin') else 'student'
    submitter_info = {
        'submitter_type': submitter_type,
        'submitter_id': current_user.id,
        'submitter_name': current_user.name or current_user.username,
        'submitter_uid': getattr(current_user, 'teacher_uid', None) or
                         getattr(current_user, 'student_number', None) or str(current_user.id),
        'submitter_grade': getattr(current_user, 'grade', None),
        'submitter_class': getattr(current_user, 'class_name', None),
    }

    # 收集答案
    answers_dict = {}
    for q in tpl.questions:
        qid_str = str(q.id)
        if q.question_type == 'multi_choice':
            answers_dict[qid_str] = request.form.getlist(f'q_{qid_str}')
        elif q.question_type == 'file':
            pass  # 文件在 files 中处理
        else:
            answers_dict[qid_str] = request.form.get(f'q_{qid_str}', '')

    try:
        sub = form_service.submit_form(
            form_id=form_id,
            submitter_info=submitter_info,
            answers_dict=answers_dict,
            files=request.files)

        log_operation(current_user, '提交', '表单', form_id,
                      f'提交表单：{tpl.title}', module='academic')
        flash('提交成功！', 'success')
        return redirect(url_for('academic.form_my_submissions'))

    except ValueError as e:
        flash(str(e), 'danger')
        return redirect(url_for('academic.form_fill', form_id=form_id))


# ── 填写端：我的提交记录 ──────────────────────────────────────

@bp.route('/forms/my-submissions')
@login_required
@perm_required('academic.view')
def form_my_submissions():
    """我的提交记录"""
    submissions = form_service.get_my_submissions(current_user.id)

    # 附加表单标题
    sub_data = []
    for sub in submissions:
        tpl = db.session.get(FormTemplate, sub.template_id)
        sub_data.append({
            'submission': sub,
            'form_title': tpl.title if tpl else '(已删除)',
            'form_status': tpl.status if tpl else None,
        })

    return render_template('academic/form_my_submissions.html',
                           sub_data=sub_data,
                           status_map=SUBMISSION_STATUS)
