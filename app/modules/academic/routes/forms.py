# StuLink v1.18.9.1 2026-10-10
# 教务 · 表单收集：管理端（创建/编辑/发布/关闭/提交列表/审核/导出）
#                 填写端（可填列表/填写/提交/我的提交）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import json
import os
from datetime import datetime

from flask import (render_template, request, redirect, url_for, flash, abort,
                   send_file, send_from_directory, current_app)
from flask_login import login_required, current_user
from sqlalchemy import func

from app.extensions import db
from app.models.academic import (
    FormTemplate, FormQuestion, FormSubmission, FormCategory, FormAnswer,
    FormRound, Teacher,
    FORM_STATUS, FORM_TARGET_TYPES, QUESTION_TYPES, SUBMISSION_STATUS,
    ROUND_STATUS, ACHIEVEMENT_CATEGORIES, ACHIEVEMENT_LEVELS,
)
from app.modules.academic import bp
from app.modules.academic.services import form_service
from app.modules.academic.services import achievement_service as ach_svc
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


def _subject_options():
    """在职教师档案里去重后的学科列表（按学科定向收集的候选）。"""
    rows = (Teacher.query.filter_by(status='active')
            .with_entities(Teacher.subject).distinct().all())
    return sorted({(r[0] or '').strip() for r in rows if (r[0] or '').strip()})


def _current_user_subject():
    """当前登录账号的任教学科（非教师/未绑定 → None，取不到不抛异常）。"""
    try:
        from app.modules.academic.services import teacher_service
        t = teacher_service.teacher_of_user(current_user)
        return (t.subject or None) if t else None
    except Exception:  # noqa: BLE001  学科取不到不阻断填写/提交
        return None


def _notify_collection_opened(tpl, rnd, only_missing=False, extra_note=None,
                              kind='open'):
    """收集开启 / 新一轮 / 重新开放 / 延期 → 通知应填人员（2026-10-10 新增）。

    - 收件人取自「应交名单」（与未交统计同一口径，不漏也不误发）；
      only_missing=True 时只通知本轮尚未提交的人（重开/延期场景）。
    - link_url 存站内相对路径（历史催交用 _external 绝对地址，换域名即失效）。
    返回 (notified, accountless, accountless_names)。
    """
    from app.modules.academic.services import form_summary_service as sum_svc
    from app.modules.notifications.services import notification_service as notif_svc
    try:
        expected = sum_svc.get_expected_submitters(tpl.id)
        uids = [e.get('uid') for e in expected if e.get('uid')]
        if only_missing:
            missing = {m.get('uid') for m in sum_svc.get_all_missing(tpl.id)}
            uids = [u for u in uids if u in missing]
        if not uids:
            return 0, 0, []
    except Exception:  # noqa: BLE001  名单取不到就不发，不影响主流程
        return 0, 0, []

    start, deadline = form_service.round_window(rnd, tpl)
    dl_txt = deadline.strftime('%Y-%m-%d %H:%M') if deadline else '不限'
    round_txt = f'第{rnd.round_no}轮' if rnd else ''
    if rnd and rnd.name:
        round_txt += f'（{rnd.name}）'

    head = {'open': '已开始', 'reopen': '已重新开放', 'extend': '截止时间已延长'}.get(
        kind, '已开始')
    title = f'【材料收集】{tpl.title}·{round_txt}{head}'
    lines = []
    if extra_note:
        lines.append(extra_note)
    lines.append(f'《{tpl.title}》{round_txt} {head}，请及时提交材料。')
    lines.append(f'截止时间：{dl_txt}')
    if tpl.description:
        lines.append(f'说明：{tpl.description[:100]}')
    lines.append('请点击通知上的按钮前往填写。')

    try:
        fill_url = url_for('academic.form_fill', form_id=tpl.id)
    except Exception:  # noqa: BLE001
        fill_url = f'/academic/forms/{tpl.id}/fill'

    ok, _msg, info = notif_svc.notify_users(
        uids, title=title, content='\n'.join(lines), category='collect',
        biz_type='form_open', biz_id=tpl.id, link_url=fill_url,
        creator_id=(current_user.id if current_user else None),
        priority='urgent' if kind in ('reopen', 'extend') else 'normal')
    if not ok:
        return 0, 0, []
    return (info.get('notified', 0), info.get('accountless', 0),
            info.get('accountless_names') or [])


def _flash_collection_notice(notified, accountless, samples):
    """把通知结果告诉教务（含"无账号收不到"的提醒，避免以为已通知到）。"""
    if not notified:
        return
    msg = f'已通知 {notified} 名应填人员（可在通知公告中查看送达情况）'
    if accountless:
        names = '、'.join([n for n in samples if n][:5])
        msg += f'；其中 {accountless} 人无登录账号收不到通知，请线下告知'
        if names:
            msg += f'：{names}'
    flash(msg, 'info')


def _parse_questions_from_form():
    """从 POST 数据解析题目 JSON"""
    raw = (request.form.get('questions_json') or '').strip()
    if not raw:
        return []
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return []


def _parse_achievement_mapping():
    """发起收集时配的「计入教师业绩库」规则（2026-10-10）。

    业绩名称取自某题时，前端提交的是题目序号（题目此刻可能还没有 id），
    由 service 在题目写库后换算成真实 question_id。
    """
    return {
        'to_achievement': request.form.get('to_achievement') == '1',
        'ach_category': (request.form.get('ach_category') or '').strip(),
        'ach_level': (request.form.get('ach_level') or '').strip(),
        'ach_title_mode': (request.form.get('ach_title_mode') or 'template').strip(),
        'ach_title_question_index': request.form.get('ach_title_question_index'),
        'ach_tags': (request.form.get('ach_tags') or '').strip(),
    }


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

    # 2026-10-10：每个表单「当前轮」的窗口状态（列表页展示进行中/未开始/已截止）
    round_info = {}
    for tpl in pagination.items:
        rnd = form_service.current_round(tpl)
        if not rnd:
            round_info[tpl.id] = {'round': None, 'state': 'none', 'text': '未发起轮次'}
            continue
        win = form_service.window_state(rnd, tpl)
        round_info[tpl.id] = {'round': rnd, 'state': win['state'],
                              'text': win['text']}

    categories = _get_categories()

    return render_template('academic/form_list.html',
                           pagination=pagination,
                           status_map=FORM_STATUS,
                           target_map=FORM_TARGET_TYPES,
                           categories=categories,
                           sub_counts=sub_counts,
                           round_info=round_info,
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
        ach = _parse_achievement_mapping()

        try:
            tpl = form_service.create_form(
                title=title, description=description, category=category,
                target_type=target_type, target_scope=target_scope,
                start_time=start_time, deadline=deadline,
                max_file_size=max_file_size, allow_multiple=allow_multiple,
                questions_data=questions_data, created_by=current_user.id,
                ach=ach)

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
                           ach_categories=ACHIEVEMENT_CATEGORIES,
                           ach_levels=ACHIEVEMENT_LEVELS,
                           tag_presets=ach_svc.TAG_PRESETS,
                           # 2026-10-10：按学科定向收集的学科候选；业绩库"发起收集"预置勾选
                           subject_options=_subject_options(),
                           preset_ach=request.args.get('to_achievement') == '1',
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
        ach = _parse_achievement_mapping()

        try:
            form_service.update_form(
                form_id=form_id, title=title, description=description,
                category=category, target_type=target_type,
                target_scope=target_scope, start_time=start_time,
                deadline=deadline, max_file_size=max_file_size,
                allow_multiple=allow_multiple, questions_data=questions_data,
                ach=ach)

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
    _sorted_q = sorted(tpl.questions, key=lambda x: x.sort_order)
    questions_list = [{
        'id': q.id,
        'question_type': q.question_type,
        'title': q.title,
        'description': q.description or '',
        'options': json.loads(q.options_json) if q.options_json else [],
        'required': q.required,
        'file_types': q.file_types or '',
        'max_file_size_mb': q.max_file_size_mb or '',
    } for q in _sorted_q]

    # 业绩名称取自某题时，前端下拉按「第几题」回显
    _qids = [q.id for q in _sorted_q]
    _ach_idx = (_qids.index(tpl.ach_title_question_id)
                if tpl.ach_title_question_id in _qids else None)

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
        # 2026-10-10：计入教师业绩库的映射
        'to_achievement': bool(tpl.to_achievement),
        'ach_category': tpl.ach_category or '',
        'ach_level': tpl.ach_level or '',
        'ach_title_mode': tpl.ach_title_mode or 'template',
        'ach_title_question_index': _ach_idx,
        'ach_tags': tpl.ach_tags or '',
    }

    return render_template('academic/form_create.html',
                           form_data=form_data,
                           categories=categories,
                           status_map=FORM_STATUS,
                           target_map=FORM_TARGET_TYPES,
                           question_types=QUESTION_TYPES,
                           ach_categories=ACHIEVEMENT_CATEGORIES,
                           ach_levels=ACHIEVEMENT_LEVELS,
                           tag_presets=ach_svc.TAG_PRESETS,
                           subject_options=_subject_options(),
                           preset_ach=False,
                           edit_mode=True)


# ── 管理端：发布/关闭/删除 ────────────────────────────────────

@bp.route('/forms/<int:form_id>/publish', methods=['POST'])
@login_required
@perm_required('academic.edit')
def form_publish(form_id):
    try:
        tpl = form_service.publish_form(form_id)
        log_operation(current_user, '发布', '表单', form_id, '', module='academic')
        flash('表单已发布', 'success')
        # 2026-10-10：发布即通知应填人员（教师收集精确到人；无账号者提示线下告知）
        _flash_collection_notice(*_notify_collection_opened(
            tpl, form_service.current_round(tpl)))
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
    round_id = request.args.get('round', type=int)
    page = request.args.get('page', 1, type=int)

    # 2026-10-10：一次收集 = 一轮；历史模板没有轮次时补一条第 1 轮
    rounds = form_service.list_rounds(form_id)
    if not rounds:
        form_service.ensure_default_round(tpl)
        rounds = form_service.list_rounds(form_id)
    cur_round = next((r for r in rounds if r.id == round_id), None)

    pagination = form_service.get_submissions(
        form_id, status=status or None, page=page, per_page=20,
        round_id=(cur_round.id if cur_round else None))

    # 统计（跟随当前轮次；未选轮次时统计全部）
    base = FormSubmission.query.filter_by(template_id=form_id)
    if cur_round:
        base = base.filter(FormSubmission.round_id == cur_round.id)
    total = base.count()
    pending = base.filter(FormSubmission.status == 'submitted').count()
    approved = base.filter(FormSubmission.status == 'approved').count()
    rejected = base.filter(FormSubmission.status == 'rejected').count()

    # 2026-10-10：各轮次统计（一次 group by 出全部轮次，避免每轮 4 次 count）
    round_stats = {}
    stat_rows = (db.session.query(FormSubmission.round_id, FormSubmission.status,
                                  func.count(FormSubmission.id))
                 .filter(FormSubmission.template_id == form_id)
                 .group_by(FormSubmission.round_id, FormSubmission.status).all())
    for rid, st, cnt in stat_rows:
        d = round_stats.setdefault(rid or 0, {'total': 0, 'pending': 0,
                                              'approved': 0, 'rejected': 0})
        d['total'] += cnt or 0
        if st == 'submitted':
            d['pending'] += cnt or 0
        elif st in ('approved', 'rejected'):
            d[st] += cnt or 0

    return render_template('academic/form_detail.html',
                           tpl=tpl,
                           pagination=pagination,
                           status_map=SUBMISSION_STATUS,
                           total=total, pending=pending,
                           approved=approved, rejected=rejected,
                           f_status=status,
                           rounds=rounds, cur_round=cur_round,
                           round_stats=round_stats,
                           round_status_map=ROUND_STATUS)


@bp.route('/forms/<int:form_id>/rounds/new', methods=['POST'])
@login_required
@perm_required('academic.edit')
def form_round_new(form_id):
    """发起新一轮收集（上一轮自动结束，新提交落进新轮）"""
    try:
        rnd = form_service.start_new_round(
            form_id,
            name=(request.form.get('name') or '').strip(),
            term=(request.form.get('term') or '').strip(),
            start_time=_parse_datetime(request.form.get('start_time')),
            deadline=_parse_datetime(request.form.get('deadline')),
            created_by=current_user.id)
        log_operation(current_user, '发起', '表单轮次', rnd.id,
                      rnd.label(), module='academic')
        flash(f'已发起第 {rnd.round_no} 轮收集（上一轮已自动结束）', 'success')
        # 2026-10-10：新一轮开启即通知应填人员（含本轮时间窗与截止时间）
        tpl = form_service.get_form_detail(form_id)
        if tpl:
            _flash_collection_notice(*_notify_collection_opened(tpl, rnd))
        return redirect(url_for('academic.form_submissions', form_id=form_id,
                                round=rnd.id))
    except ValueError as e:
        flash(str(e), 'danger')
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        flash(f'发起失败：{e}', 'danger')
    return redirect(url_for('academic.form_submissions', form_id=form_id))


@bp.route('/forms/round/<int:round_id>/close', methods=['POST'])
@login_required
@perm_required('academic.edit')
def form_round_close(round_id):
    """结束某一轮收集（不影响模板继续开放，可再发起新一轮）"""
    rnd = db.session.get(FormRound, round_id)
    if not rnd:
        abort(404)
    try:
        form_service.close_round(round_id)
        log_operation(current_user, '结束', '表单轮次', round_id,
                      rnd.label(), module='academic')
        flash(f'已结束本轮：{rnd.label()}', 'success')
    except ValueError as e:
        flash(str(e), 'danger')
    return redirect(url_for('academic.form_submissions',
                            form_id=rnd.template_id, round=round_id))


@bp.route('/forms/round/<int:round_id>/reopen', methods=['POST'])
@login_required
@perm_required('academic.edit')
def form_round_reopen(round_id):
    """重新开放已结束的轮次（同模板其它进行中轮次自动结束）"""
    rnd = db.session.get(FormRound, round_id)
    if not rnd:
        abort(404)
    form_id = rnd.template_id
    try:
        form_service.reopen_round(
            round_id,
            start_time=_parse_datetime(request.form.get('start_time')),
            deadline=_parse_datetime(request.form.get('deadline')),
            clear_deadline=request.form.get('clear_deadline') == '1')
        log_operation(current_user, '重开', '表单轮次', round_id, rnd.label(),
                      module='academic')
        flash(f'已重新开放：{rnd.label()}', 'success')
        # 2026-10-10：重开后只提醒本轮尚未提交的人（已交者不打扰）
        tpl = form_service.get_form_detail(form_id)
        if tpl:
            _flash_collection_notice(*_notify_collection_opened(
                tpl, rnd, only_missing=True, kind='reopen'))
    except ValueError as e:
        db.session.rollback()
        flash(str(e), 'danger')
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        flash(f'重新开放失败：{e}', 'danger')
    return redirect(url_for('academic.form_submissions', form_id=form_id,
                            round=round_id))


@bp.route('/forms/round/<int:round_id>/window', methods=['POST'])
@login_required
@perm_required('academic.edit')
def form_round_window(round_id):
    """调整进行中轮次的时间窗（延长截止 / 设置开始时间 / 清除）"""
    rnd = db.session.get(FormRound, round_id)
    if not rnd:
        abort(404)
    form_id = rnd.template_id
    old_deadline = rnd.deadline
    try:
        form_service.update_round_window(
            round_id,
            start_time=_parse_datetime(request.form.get('start_time')),
            deadline=_parse_datetime(request.form.get('deadline')),
            clear_start=request.form.get('clear_start') == '1',
            clear_deadline=request.form.get('clear_deadline') == '1')
        log_operation(current_user, '调整', '表单轮次', round_id,
                      f'调整时间窗：{rnd.label()}', module='academic')
        flash('时间窗已更新', 'success')
        # 2026-10-10：截止时间被延长时，只提醒本轮尚未提交的人（无变化则不发）
        new_deadline = rnd.deadline
        if new_deadline and (old_deadline is None or new_deadline > old_deadline):
            tpl = form_service.get_form_detail(form_id)
            if tpl:
                _flash_collection_notice(*_notify_collection_opened(
                    tpl, rnd, only_missing=True, kind='extend'))
    except ValueError as e:
        db.session.rollback()
        flash(str(e), 'danger')
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        flash(f'调整失败：{e}', 'danger')
    return redirect(url_for('academic.form_submissions', form_id=form_id,
                            round=round_id))


@bp.route('/forms/<int:form_id>/reopen', methods=['POST'])
@login_required
@perm_required('academic.edit')
def form_reopen(form_id):
    """重开收集：mode=reuse 重开最近一轮 / mode=new 发起新一轮"""
    tpl = form_service.get_form_detail(form_id)
    if not tpl:
        abort(404)
    mode = (request.form.get('mode') or 'reuse').strip()
    try:
        rnd = form_service.reopen_form(
            form_id,
            start_time=_parse_datetime(request.form.get('start_time')),
            deadline=_parse_datetime(request.form.get('deadline')),
            mode=mode,
            round_name=(request.form.get('name') or '').strip(),
            created_by=current_user.id)
        log_operation(current_user, '重开', '表单', form_id, rnd.label(),
                      module='academic')
        flash(f'已重新开放收集（第 {rnd.round_no} 轮）', 'success')
        # 2026-10-10：沿用原轮次 → 只提醒未交者；作为新一轮 → 通知全体应填
        tpl = form_service.get_form_detail(form_id)
        if tpl:
            _flash_collection_notice(*_notify_collection_opened(
                tpl, rnd, only_missing=(mode != 'new'), kind='reopen'))
        return redirect(url_for('academic.form_submissions', form_id=form_id,
                                round=rnd.id))
    except ValueError as e:
        db.session.rollback()
        flash(str(e), 'danger')
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        flash(f'重开失败：{e}', 'danger')
    return redirect(url_for('academic.form_submissions', form_id=form_id))


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
    """获取当前用户可填写的表单列表（按当前轮时间窗 + 定向范围）"""
    submitter_type = 'teacher' if current_user.role in ('teacher', 'admin') else 'student'

    items = form_service.fill_items(
        current_user.id,
        submitter_type=submitter_type,
        submitter_grade=getattr(current_user, 'grade', None),
        submitter_class=getattr(current_user, 'class_name', None),
        submitter_subject=_current_user_subject())

    # 旧变量保留（兼容外部引用）；已交判定按「当前轮」
    templates = [it['tpl'] for it in items]
    submitted_ids = {it['tpl'].id for it in items
                     if it['submitted'] and not it['tpl'].allow_multiple}

    return render_template('academic/form_fill_list.html',
                           templates=templates,
                           submitted_ids=submitted_ids,
                           fill_items=items,
                           status_map=FORM_STATUS,
                           now=datetime.now())


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

    # 资格检查（窗口按当前轮次判定：未开始/已截止/不在定向范围/本轮已交）
    submitter_type = 'teacher' if current_user.role in ('teacher', 'admin') else 'student'
    eligibility = form_service.check_eligibility(
        form_id, current_user.id,
        submitter_type=submitter_type,
        submitter_grade=getattr(current_user, 'grade', None),
        submitter_class=getattr(current_user, 'class_name', None),
        submitter_subject=_current_user_subject())
    if not eligibility['eligible']:
        if eligibility['reason'] == 'already' and tpl.allow_multiple:
            pass                        # 允许多次提交 → 放行（不拦已交）
        else:
            flash(eligibility['message'] or '当前不可填写', 'warning')
            if eligibility['already_submitted']:
                return redirect(url_for('academic.form_my_submissions'))
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
        'submitter_subject': _current_user_subject(),
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


# ── 附件下载（带鉴权） ────────────────────────────────────────

@bp.route('/forms/<int:form_id>/file/<path:rel_path>')
@login_required
def form_file(form_id, rel_path):
    """表单附件下载（替代 /static/uploads/... 无鉴权直链）。

    原来 FormAnswer.file_path 直接拼 /static/ 对外暴露：任何未登录用户拿到链接
    都能下载学生上传的材料。改为受控路由后，仅以下两类人可下载：
      1) 有 academic.edit（教务管理端）权限者；
      2) 该条提交的提交者本人（FormSubmission.submitter_id）。
    rel_path 格式固定为 uploads/forms/<form_id>/<submission_id>/<file>，
    取出 submission_id 后再用 basename 定位，天然阻断了 ../ 目录穿越。
    """
    tpl = db.session.get(FormTemplate, form_id)
    if not tpl:
        abort(404)

    parts = (rel_path or '').split('/')
    if not (len(parts) >= 5 and parts[0] == 'uploads' and parts[1] == 'forms'
            and parts[2] == str(form_id)):
        abort(404)
    sub_id = parts[3]
    if not sub_id.isdigit():
        abort(404)
    sub = db.session.get(FormSubmission, int(sub_id))
    if sub is None or sub.template_id != form_id:
        abort(404)

    if not (current_user.has_perm('academic.edit') or sub.submitter_id == current_user.id):
        abort(403)

    root = os.path.abspath(os.path.join(current_app.static_folder, 'uploads',
                                        'forms', str(form_id), sub_id))
    fname = os.path.basename(parts[-1])
    target = os.path.abspath(os.path.join(root, fname))
    if os.path.commonpath([root, target]) != root or not os.path.isfile(target):
        abort(404)

    ans = (FormAnswer.query.filter_by(submission_id=sub.id, file_path=rel_path)
           .first())
    download_name = (ans.file_name if ans and ans.file_name else fname)
    return send_from_directory(root, fname, as_attachment=True,
                               download_name=download_name)


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
