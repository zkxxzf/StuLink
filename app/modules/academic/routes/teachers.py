# StuLink v1.17.0 2026-09-20
# 教务 · 教师名单：列表 / Excel 导入（模板-预览-确认-密码清单）/ 编辑
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import io
import re
import time
import uuid

from flask import (render_template, request, redirect, url_for,
                   flash, send_file, abort)
from flask_login import login_required, current_user
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import User
from app.models.academic import Teacher
from app.modules.academic import bp
from app.modules.academic.services import teacher_import_service
from app.utils import id_card as id_card_util
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation

_PHONE_RE = re.compile(r'^1\d{10}$')

# 导入草稿（内存级，服务重启后自动失效，与成绩模块教师导入同模式）
_DRAFT = {}
_DRAFT_TTL = 1800


def _purge_expired_drafts():
    now = time.time()
    for key in [k for k, v in _DRAFT.items() if now - v.get('_ts', 0) > _DRAFT_TTL]:
        _DRAFT.pop(key, None)


@bp.route('/teachers')
@login_required
@perm_required('academic.view')
def teachers_page():
    """教师名单（教务基础数据）"""
    from app.models.grades import TeacherSubjectLink

    kw = (request.args.get('kw') or '').strip()
    q = Teacher.query
    if kw:
        like = f'%{kw}%'
        q = q.filter(or_(Teacher.name.like(like), Teacher.teacher_uid.like(like),
                         Teacher.phone.like(like), Teacher.subject.like(like)))
    teachers = q.order_by(Teacher.teacher_uid).all()

    user_ids = [t.user_id for t in teachers if t.user_id]
    users = ({u.id: u for u in User.query.filter(User.id.in_(user_ids)).all()}
             if user_ids else {})
    counts = {}
    if user_ids:
        counts = dict(TeacherSubjectLink.query.with_entities(
            TeacherSubjectLink.user_id, func.count())
            .filter(TeacherSubjectLink.user_id.in_(user_ids),
                    TeacherSubjectLink.active.is_(True))
            .group_by(TeacherSubjectLink.user_id).all())

    rows = []
    for t in teachers:
        rows.append({
            't': t,
            'account': users.get(t.user_id) if t.user_id else None,
            'lesson_count': counts.get(t.user_id, 0),
            'id_masked': id_card_util.masked_from_cipher(t.id_card_enc),
        })
    return render_template('academic/teachers.html', rows=rows, kw=kw)


@bp.route('/teachers/template.xlsx')
@login_required
@perm_required('academic.view')
def teachers_template():
    """下载教师名单导入模板"""
    buf = teacher_import_service.build_template()
    return send_file(buf, as_attachment=True,
                     download_name='教师名单导入模板.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument'
                              '.spreadsheetml.sheet')


@bp.route('/teachers/import/upload', methods=['POST'])
@login_required
@perm_required('academic.edit')
def teachers_import_upload():
    """上传教师名单 → 解析并生成导入预览"""
    file = request.files.get('file')
    if not file or not file.filename:
        flash('请选择教师名单 Excel 文件', 'danger')
        return redirect(url_for('academic.teachers_page'))
    try:
        rows = teacher_import_service.parse_excel(io.BytesIO(file.read()))
    except ValueError as e:
        flash(str(e), 'danger')
        return redirect(url_for('academic.teachers_page'))
    except Exception:
        flash('文件解析失败：请使用下载的标准模板填写后重试', 'danger')
        return redirect(url_for('academic.teachers_page'))

    plan = teacher_import_service.build_plan(rows)
    token = uuid.uuid4().hex
    _purge_expired_drafts()
    _DRAFT[token] = {'plan': plan, 'fname': file.filename, '_ts': time.time()}
    return render_template('academic/teacher_import_preview.html',
                           token=token, plan=plan, fname=file.filename)


@bp.route('/teachers/import/confirm', methods=['POST'])
@login_required
@perm_required('academic.edit')
def teachers_import_confirm():
    """确认导入：落库 + 自动建号"""
    token = (request.form.get('token') or '').strip()
    _purge_expired_drafts()
    draft = _DRAFT.get(token)
    if not draft:
        flash('导入批次已失效，请重新上传文件', 'danger')
        return redirect(url_for('academic.teachers_page'))

    plan = draft['plan']
    overrides = {}
    for it in plan['items']:
        if (it.get('account') or {}).get('status') == 'to_create':
            val = (request.form.get(f'un_{it["idx"]}') or '').strip()
            if val:
                overrides[str(it['idx'])] = val
    try:
        result = teacher_import_service.apply_plan(plan['items'], overrides)
    except IntegrityError:
        db.session.rollback()
        flash('导入失败：身份证号或用户名与现有数据冲突，请检查后重新导入', 'danger')
        return redirect(url_for('academic.teachers_page'))
    except Exception as e:
        db.session.rollback()
        flash(f'导入失败：{e}', 'danger')
        return redirect(url_for('academic.teachers_page'))

    draft['result'] = result
    draft['_ts'] = time.time()
    log_operation(current_user, '导入', '教师名单', None,
                  f'新增 {result["created_teachers"]}、更新 {result["updated_teachers"]}、'
                  f'新建账号 {len(result["accounts"])}', module='academic')
    return render_template('academic/teacher_import_result.html',
                           token=token, result=result, plan=plan)


@bp.route('/teachers/import/passwords/<token>.csv')
@login_required
@perm_required('academic.edit')
def teachers_import_passwords(token):
    """下载本次导入的初始账号密码清单"""
    _purge_expired_drafts()
    draft = _DRAFT.get(token)
    if not draft or not draft.get('result'):
        flash('密码清单已失效，请在教师管理页重置相应账号密码', 'warning')
        return redirect(url_for('academic.teachers_page'))
    data = teacher_import_service.accounts_csv(draft['result']['accounts'])
    return send_file(io.BytesIO(data), as_attachment=True,
                     download_name='教师初始账号密码.csv', mimetype='text/csv')


@bp.route('/teachers/<int:tid>/edit', methods=['GET', 'POST'])
@login_required
@perm_required('academic.edit')
def teacher_edit(tid):
    """编辑教师：身份证一经录入仅管理员可更新（本页即管理入口）"""
    t = db.session.get(Teacher, tid)
    if not t:
        abort(404)

    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        subject = (request.form.get('subject') or '').strip()
        phone = (request.form.get('phone') or '').strip()
        note = (request.form.get('note') or '').strip()
        status = request.form.get('status') or 'active'
        id_card_raw = id_card_util.normalize(request.form.get('id_card'))

        back = redirect(url_for('academic.teacher_edit', tid=tid))
        if not name:
            flash('姓名不能为空', 'danger')
            return back
        if phone and not _PHONE_RE.match(phone):
            flash('手机号格式不正确（11 位，1 开头）', 'danger')
            return back
        if id_card_raw:
            if not id_card_util.validate(id_card_raw):
                flash('身份证号无效（需为 18 位有效号码，含校验码）', 'danger')
                return back
            new_cipher = id_card_util.encrypt_id_card(id_card_raw)
            old_cipher = t.id_card_enc or ''
            if new_cipher != old_cipher:
                if old_cipher:
                    flash('身份证号已录入，如需更正请确认无误后重试', 'warning')
                exist = Teacher.query.filter_by(id_card_enc=new_cipher).first()
                if exist and exist.id != t.id:
                    flash(f'该身份证号已属于教师「{exist.name}」，无法重复使用', 'danger')
                    return back
                t.id_card_enc = new_cipher

        changes = []
        if name != t.name:
            changes.append(f'姓名 {t.name}→{name}')
            t.name = name
        if phone != (t.phone or ''):
            changes.append(f'手机号 {(t.phone or "空")}→{(phone or "空")}')
            t.phone = phone or None
        if subject != (t.subject or ''):
            t.subject = subject or None
        if note != (t.note or ''):
            t.note = note or None
        if status in ('active', 'left') and status != t.status:
            t.status = status
            changes.append('状态变更')
        db.session.commit()
        log_operation(current_user, '更新', '教师', t.id,
                      f'{t.name}：' + ('；'.join(changes) or '无字段变化'),
                      module='academic')
        flash(f'教师 {t.name} 已更新', 'success')
        return redirect(url_for('academic.teachers_page'))

    return render_template('academic/teacher_edit.html', t=t,
                           id_masked=id_card_util.masked_from_cipher(t.id_card_enc))
