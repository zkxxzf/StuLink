# StuLink v1.18.2.0 2026-09-24
# 表单收集服务：创建/编辑/发布/提交/审核/导出/文件上传
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import json
import os
import uuid
from datetime import datetime

from werkzeug.utils import secure_filename

from flask import current_app

from app.extensions import db
from app.models.academic import (
    FormTemplate, FormQuestion, FormSubmission, FormAnswer, FormCategory,
)


# ── 表单 CRUD ─────────────────────────────────────────────────

def create_form(title, description, category, target_type, target_scope,
                start_time, deadline, max_file_size, allow_multiple,
                questions_data, created_by):
    """创建表单 + 题目（保存为草稿）
    questions_data: list[dict]  每项包含 question_type/title/description/
        options_json/required/file_types/max_file_size_mb
    """
    tpl = FormTemplate(
        title=title,
        description=description or None,
        category=category or None,
        target_type=target_type or 'all',
        target_scope=target_scope or None,
        start_time=start_time,
        deadline=deadline,
        max_file_size_mb=max_file_size or 10,
        allow_multiple=bool(allow_multiple),
        created_by=created_by,
        status='draft',
    )
    db.session.add(tpl)
    db.session.flush()          # 获取 tpl.id

    _save_questions(tpl.id, questions_data)
    db.session.commit()
    return tpl


def update_form(form_id, title, description, category, target_type,
                target_scope, start_time, deadline, max_file_size,
                allow_multiple, questions_data):
    """编辑表单（仅 draft 状态）"""
    tpl = db.session.get(FormTemplate, form_id)
    if not tpl:
        raise ValueError('表单不存在')
    if tpl.status != 'draft':
        raise ValueError('仅草稿状态可编辑')

    tpl.title = title
    tpl.description = description or None
    tpl.category = category or None
    tpl.target_type = target_type or 'all'
    tpl.target_scope = target_scope or None
    tpl.start_time = start_time
    tpl.deadline = deadline
    tpl.max_file_size_mb = max_file_size or 10
    tpl.allow_multiple = bool(allow_multiple)

    # 重建题目
    FormQuestion.query.filter_by(template_id=form_id).delete()
    _save_questions(form_id, questions_data)
    db.session.commit()
    return tpl


def _save_questions(template_id, questions_data):
    """批量写入题目"""
    for idx, qd in enumerate(questions_data):
        q = FormQuestion(
            template_id=template_id,
            question_type=qd.get('question_type', 'text'),
            title=(qd.get('title') or '').strip() or f'题目{idx + 1}',
            description=(qd.get('description') or '').strip() or None,
            options_json=json.dumps(qd['options'], ensure_ascii=False) if qd.get('options') else None,
            required=bool(qd.get('required')),
            file_types=(qd.get('file_types') or '').strip() or None,
            max_file_size_mb=qd.get('max_file_size_mb') or None,
            sort_order=idx,
        )
        db.session.add(q)


def publish_form(form_id):
    """draft -> open"""
    tpl = db.session.get(FormTemplate, form_id)
    if not tpl:
        raise ValueError('表单不存在')
    if tpl.status != 'draft':
        raise ValueError('仅草稿状态可发布')
    tpl.status = 'open'
    db.session.commit()
    return tpl


def close_form(form_id):
    """open -> closed"""
    tpl = db.session.get(FormTemplate, form_id)
    if not tpl:
        raise ValueError('表单不存在')
    if tpl.status != 'open':
        raise ValueError('仅进行中状态可关闭')
    tpl.status = 'closed'
    db.session.commit()
    return tpl


def delete_form(form_id):
    """删除表单（仅 draft）"""
    tpl = db.session.get(FormTemplate, form_id)
    if not tpl:
        raise ValueError('表单不存在')
    if tpl.status != 'draft':
        raise ValueError('仅草稿状态可删除')
    db.session.delete(tpl)
    db.session.commit()


def get_form_detail(form_id):
    """返回表单完整结构（含题目列表）"""
    return db.session.get(FormTemplate, form_id)


def get_forms_list(status=None, category=None, page=1, per_page=20):
    """表单列表（分页）"""
    q = FormTemplate.query
    if status and status in ('draft', 'open', 'closed', 'archived'):
        q = q.filter_by(status=status)
    if category:
        q = q.filter_by(category=category)
    return q.order_by(FormTemplate.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False)


# ── 提交 ─────────────────────────────────────────────────────

def submit_form(form_id, submitter_info, answers_dict, files):
    """提交表单
    submitter_info: dict(submitter_type/submitter_id/submitter_name/
                         submitter_uid/submitter_grade/submitter_class)
    answers_dict: {question_id_str: answer_value}
    files: request.files MultiDict  {question_id_str: file_storage}
    """
    tpl = db.session.get(FormTemplate, form_id)
    if not tpl:
        raise ValueError('表单不存在')
    if tpl.status != 'open':
        raise ValueError('该表单当前不可提交')

    now = datetime.now()
    if tpl.deadline and now > tpl.deadline:
        raise ValueError('已超过截止时间')

    # 检查是否已提交（不允许重复时）
    if not tpl.allow_multiple:
        existing = FormSubmission.query.filter_by(
            template_id=form_id,
            submitter_id=submitter_info.get('submitter_id'),
        ).first()
        if existing:
            raise ValueError('您已提交过此表单，不可重复提交')

    sub = FormSubmission(
        template_id=form_id,
        submitter_type=submitter_info.get('submitter_type'),
        submitter_id=submitter_info.get('submitter_id'),
        submitter_name=submitter_info.get('submitter_name'),
        submitter_uid=submitter_info.get('submitter_uid'),
        submitter_grade=submitter_info.get('submitter_grade'),
        submitter_class=submitter_info.get('submitter_class'),
        status='submitted',
    )
    db.session.add(sub)
    db.session.flush()

    # 写入答案
    questions_map = {q.id: q for q in tpl.questions}
    for qid_str, value in answers_dict.items():
        try:
            qid = int(qid_str)
        except ValueError:
            continue
        question = questions_map.get(qid)
        if not question:
            continue

        answer = FormAnswer(submission_id=sub.id, question_id=qid)

        if question.question_type == 'file':
            f = files.get(qid_str)
            if f and f.filename:
                saved = upload_file(f, form_id, sub.id, question)
                answer.file_path = saved['file_path']
                answer.file_name = saved['file_name']
                answer.file_size = saved['file_size']
        elif question.question_type == 'multi_choice':
            # value 可能是 list
            if isinstance(value, list):
                answer.answer_json = json.dumps(value, ensure_ascii=False)
            else:
                answer.answer_text = str(value) if value else None
        else:
            answer.answer_text = str(value).strip() if value else None

        db.session.add(answer)

    db.session.commit()
    return sub


def review_submission(submission_id, new_status, reviewer_id, note=None):
    """审核提交（approved / rejected）"""
    sub = db.session.get(FormSubmission, submission_id)
    if not sub:
        raise ValueError('提交记录不存在')
    if sub.status != 'submitted':
        raise ValueError('该记录已审核，不可重复操作')
    sub.status = new_status
    sub.reviewed_by = reviewer_id
    sub.reviewed_at = datetime.now()
    sub.review_note = (note or '').strip() or None
    db.session.commit()
    return sub


def get_submissions(form_id, status=None, page=1, per_page=20):
    """提交列表（分页）"""
    q = FormSubmission.query.filter_by(template_id=form_id)
    if status and status in ('submitted', 'approved', 'rejected'):
        q = q.filter_by(status=status)
    return q.order_by(FormSubmission.submitted_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False)


def export_submissions(form_id):
    """导出提交数据为 Excel（答案汇总）
    返回 openpyxl BytesIO 对象，由路由层 send_file
    """
    import io
    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from app.utils.export_helpers import xl_safe

    tpl = db.session.get(FormTemplate, form_id)
    if not tpl:
        raise ValueError('表单不存在')

    questions = sorted(tpl.questions, key=lambda q: q.sort_order)
    submissions = (FormSubmission.query.filter_by(template_id=form_id)
                   .order_by(FormSubmission.submitted_at.desc()).all())

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = tpl.title[:30]

    # 表头
    headers = ['提交人', '类型', '年级/班级', '提交时间', '状态']
    for q in questions:
        headers.append(q.title[:30])

    hf = Font(bold=True, color='FFFFFF')
    hfl = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
    tb = Border(left=Side(style='thin'), right=Side(style='thin'),
                top=Side(style='thin'), bottom=Side(style='thin'))

    for ci, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=ci, value=h)
        c.font = hf
        c.fill = hfl
        c.alignment = Alignment(horizontal='center')
        c.border = tb

    # 数据行
    for ri, sub in enumerate(submissions, 2):
        answers_map = {}
        for a in sub.answers:
            answers_map[a.question_id] = _format_answer(a)

        row = [
            sub.submitter_name or '-',
            '教师' if sub.submitter_type == 'teacher' else '学生',
            f"{sub.submitter_grade or ''}{sub.submitter_class or ''}" or '-',
            sub.submitted_at.strftime('%Y-%m-%d %H:%M') if sub.submitted_at else '-',
            {'submitted': '已提交', 'approved': '已通过', 'rejected': '已驳回'}.get(sub.status, sub.status),
        ]
        for q in questions:
            row.append(answers_map.get(q.id, '-'))

        for ci, v in enumerate(row, 1):
            c = ws.cell(row=ri, column=ci, value=xl_safe(v))
            c.border = tb

    # 列宽
    for ci in range(1, len(headers) + 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(ci)].width = 18

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return out, tpl.title


def _format_answer(answer):
    """格式化答案用于导出"""
    if answer.file_name:
        return f'[文件] {answer.file_name}'
    if answer.answer_json:
        try:
            items = json.loads(answer.answer_json)
            if isinstance(items, list):
                return '、'.join(str(i) for i in items)
        except Exception:
            pass
        return answer.answer_json
    return answer.answer_text or '-'


# ── 资格检查 ──────────────────────────────────────────────────

def check_eligibility(form_id, submitter_id):
    """检查是否有资格 / 是否已提交
    返回 dict: dict(eligible=bool, already_submitted=bool, submission_id=int|None)
    """
    tpl = db.session.get(FormTemplate, form_id)
    if not tpl:
        return dict(eligible=False, already_submitted=False, submission_id=None)
    if tpl.status != 'open':
        return dict(eligible=False, already_submitted=False, submission_id=None)

    now = datetime.now()
    if tpl.start_time and now < tpl.start_time:
        return dict(eligible=False, already_submitted=False, submission_id=None)
    if tpl.deadline and now > tpl.deadline:
        return dict(eligible=False, already_submitted=False, submission_id=None)

    existing = FormSubmission.query.filter_by(
        template_id=form_id, submitter_id=submitter_id).first()
    if existing and not tpl.allow_multiple:
        return dict(eligible=False, already_submitted=True, submission_id=existing.id)

    return dict(eligible=True, already_submitted=False, submission_id=None)


def get_available_forms(submitter_type, submitter_grade=None, submitter_class=None):
    """获取当前用户可填写的表单列表"""
    now = datetime.now()
    q = FormTemplate.query.filter_by(status='open')
    q = q.filter(
        db.or_(
            FormTemplate.start_time.is_(None),
            FormTemplate.start_time <= now,
        ),
        db.or_(
            FormTemplate.deadline.is_(None),
            FormTemplate.deadline > now,
        ),
    )
    templates = q.order_by(FormTemplate.deadline.asc()).all()

    result = []
    for tpl in templates:
        tt = tpl.target_type or 'all'
        if tt == 'all':
            result.append(tpl)
        elif tt == 'teachers' and submitter_type == 'teacher':
            result.append(tpl)
        elif tt == 'students' and submitter_type == 'student':
            result.append(tpl)
        elif tt.startswith('grade') and submitter_type == 'student':
            # target_scope 存储年级列表 JSON，兼容 list 与 dict 两种格式
            try:
                raw = json.loads(tpl.target_scope or '[]')
                if isinstance(raw, list):
                    grades = raw
                elif isinstance(raw, dict):
                    grades = raw.get('grades') or []
                else:
                    grades = []
                if submitter_grade in [str(g) for g in grades]:
                    result.append(tpl)
            except Exception:
                pass
    return result


def get_my_submissions(submitter_id):
    """获取我的提交记录"""
    return (FormSubmission.query.filter_by(submitter_id=submitter_id)
            .order_by(FormSubmission.submitted_at.desc()).all())


# ── 文件上传 ──────────────────────────────────────────────────

def upload_file(file, form_id, submission_id, question):
    """处理文件上传
    返回 dict(file_path, file_name, file_size)
    """
    # 大小限制
    max_mb = question.max_file_size_mb or 10
    max_bytes = max_mb * 1024 * 1024
    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)
    if size > max_bytes:
        raise ValueError(f'文件大小超过限制（最大 {max_mb}MB）')

    # 类型校验
    if question.file_types:
        allowed = [t.strip().lower().lstrip('.') for t in question.file_types.split(',')]
        ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
        if ext not in allowed:
            raise ValueError(f'不允许的文件类型（允许：{", ".join(allowed)}）')

    # 安全文件名
    original = secure_filename(file.filename or 'upload')
    unique = f'{uuid.uuid4().hex[:8]}_{original}'

    # 保存目录
    upload_dir = os.path.join(
        current_app.static_folder, 'uploads', 'forms',
        str(form_id), str(submission_id))
    os.makedirs(upload_dir, exist_ok=True)

    save_path = os.path.join(upload_dir, unique)
    file.save(save_path)

    # 存储相对路径（相对于 static 目录）
    rel_path = f'uploads/forms/{form_id}/{submission_id}/{unique}'

    return dict(file_path=rel_path, file_name=original, file_size=size)
