# StuLink v1.18.9.1 2026-10-10
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
    FormRound, Teacher,
)


# ── 表单 CRUD ─────────────────────────────────────────────────

def create_form(title, description, category, target_type, target_scope,
                start_time, deadline, max_file_size, allow_multiple,
                questions_data, created_by, ach=None):
    """创建表单 + 题目（保存为草稿）
    questions_data: list[dict]  每项包含 question_type/title/description/
        options_json/required/file_types/max_file_size_mb
    ach: 2026-10-10 计入业绩库映射（见 _apply_achievement_mapping）
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
    db.session.flush()
    _apply_achievement_mapping(tpl, ach)
    db.session.commit()
    return tpl


def update_form(form_id, title, description, category, target_type,
                target_scope, start_time, deadline, max_file_size,
                allow_multiple, questions_data, ach=None):
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
    db.session.flush()
    _apply_achievement_mapping(tpl, ach)
    db.session.commit()
    return tpl


def _question_id_by_index(template_id, index):
    """按题目序号（0 起）取真实 question_id（业绩名称取自某题时用）。"""
    try:
        idx = int(index)
    except (TypeError, ValueError):
        return None
    q = (FormQuestion.query.filter_by(template_id=template_id)
         .order_by(FormQuestion.sort_order).offset(idx).first())
    return q.id if q else None


def _apply_achievement_mapping(tpl, ach):
    """把发起收集时配的「计入教师业绩库」规则落到模板上。

    ach: dict(to_achievement / ach_category / ach_level / ach_title_mode /
              ach_title_question_index / ach_tags)
    名称取自「第几题」时，这里在题目写库之后换算成真实 question_id。
    """
    if not ach:
        return tpl
    tpl.to_achievement = bool(ach.get('to_achievement'))
    tpl.ach_category = (ach.get('ach_category') or '').strip() or None
    tpl.ach_level = (ach.get('ach_level') or '').strip() or None
    tpl.ach_title_mode = (ach.get('ach_title_mode') or 'template').strip()
    tpl.ach_tags = (ach.get('ach_tags') or '').strip() or None
    if tpl.ach_title_mode == 'question':
        tpl.ach_title_question_id = _question_id_by_index(
            tpl.id, ach.get('ach_title_question_index'))
        if not tpl.ach_title_question_id:      # 选了但没这题，退回模板标题
            tpl.ach_title_mode = 'template'
    else:
        tpl.ach_title_question_id = None
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


# ── 收集轮次（2026-10-10）：一次收集 = 一轮，同模板可发起多轮 ─────────────────

def list_rounds(form_id):
    """该模板的轮次（按 round_no 升序）。"""
    return (FormRound.query.filter_by(template_id=form_id)
            .order_by(FormRound.round_no).all())


def ensure_default_round(tpl):
    """没有轮次时补一条第 1 轮（历史模板/直接建库场景的兜底）。"""
    if list_rounds(tpl.id):
        return None
    rnd = FormRound(
        template_id=tpl.id, round_no=1,
        start_time=tpl.start_time, deadline=tpl.deadline,
        status='closed' if tpl.status == 'closed' else 'open',
        created_by=tpl.created_by, created_at=datetime.now(),
    )
    db.session.add(rnd)
    db.session.commit()
    return rnd


def start_new_round(form_id, name=None, term=None, deadline=None,
                    created_by=None, start_time=None):
    """发起新一轮：先把上一轮收尾（否则新的提交会落进旧轮）。

    start_time 为空 → 立即开始（旧行为）。
    """
    tpl = db.session.get(FormTemplate, form_id)
    if not tpl:
        raise ValueError('表单不存在')
    if tpl.status != 'open':
        raise ValueError('仅进行中的表单可发起新一轮')
    rounds = list_rounds(form_id)
    for r in rounds:
        if r.status == 'open':
            r.status = 'closed'
    rnd = FormRound(
        template_id=form_id,
        round_no=(max(r.round_no for r in rounds) + 1) if rounds else 1,
        name=(name or '').strip() or None,
        term=(term or '').strip() or None,
        start_time=start_time or datetime.now(),
        deadline=deadline or None,
        status='open',
        created_by=created_by,
        created_at=datetime.now(),
    )
    db.session.add(rnd)
    db.session.commit()
    return rnd


def close_round(round_id):
    """结束某一轮（该轮不再接受提交，但不影响模板本身继续开放）。"""
    rnd = db.session.get(FormRound, round_id)
    if not rnd:
        raise ValueError('轮次不存在')
    if rnd.status != 'open':
        raise ValueError('该轮次已结束')
    rnd.status = 'closed'
    db.session.commit()
    return rnd


def update_round_window(round_id, start_time=None, deadline=None,
                        clear_start=False, clear_deadline=False):
    """调整进行中轮次的时间窗：None=不修改；clear_*=True 清空；已结束轮次报错。"""
    rnd = db.session.get(FormRound, round_id)
    if not rnd:
        raise ValueError('轮次不存在')
    if rnd.status != 'open':
        raise ValueError('该轮次已结束，请先重新开放本轮再调整时间')
    new_start = None if clear_start else (
        start_time if start_time is not None else rnd.start_time)
    new_deadline = None if clear_deadline else (
        deadline if deadline is not None else rnd.deadline)
    if new_start and new_deadline and new_start > new_deadline:
        raise ValueError('开始时间不能晚于截止时间')
    rnd.start_time, rnd.deadline = new_start, new_deadline
    db.session.commit()
    return rnd


def reopen_round(round_id, start_time=None, deadline=None, clear_deadline=False):
    """重新开放已结束的轮次：closed → open。

    同模板其它 open 轮自动关闭（同一时间只该有一轮在收）；模板若已 closed
    一并置 open（否则提交会被模板状态拦住）。时间参数 None=沿用原值。
    """
    rnd = db.session.get(FormRound, round_id)
    if not rnd:
        raise ValueError('轮次不存在')
    if rnd.status == 'open':
        raise ValueError('该轮次仍在进行中，无需重新开放')
    new_start = start_time if start_time is not None else rnd.start_time
    new_deadline = None if clear_deadline else (
        deadline if deadline is not None else rnd.deadline)
    if new_start and new_deadline and new_start > new_deadline:
        raise ValueError('开始时间不能晚于截止时间')
    rnd.start_time, rnd.deadline = new_start, new_deadline

    for other in FormRound.query.filter_by(template_id=rnd.template_id,
                                           status='open').all():
        if other.id != rnd.id:
            other.status = 'closed'
    rnd.status = 'open'

    tpl = db.session.get(FormTemplate, rnd.template_id)
    if tpl and tpl.status == 'closed':
        tpl.status = 'open'
    db.session.commit()
    return rnd


def reopen_form(form_id, start_time=None, deadline=None, mode='reuse',
                round_name=None, term=None, created_by=None):
    """重开收集：mode='reuse' 重开最近一轮（无轮次则补默认轮）；mode='new' 发起新一轮。

    两种模式都把模板置回 open 并应用传入时间（None=沿用原值 / 新建轮默认 now）。
    返回 rnd。
    """
    tpl = db.session.get(FormTemplate, form_id)
    if not tpl:
        raise ValueError('表单不存在')
    mode = (mode or 'reuse').strip()
    if mode not in ('reuse', 'new'):
        raise ValueError('不支持的重开方式')

    if mode == 'new':
        if tpl.status != 'open':
            tpl.status = 'open'
            db.session.flush()
        return start_new_round(form_id, name=round_name, term=term,
                               deadline=deadline, created_by=created_by,
                               start_time=start_time)

    # reuse：重开最近一轮
    if not list_rounds(form_id):
        ensure_default_round(tpl)
    rnd = list_rounds(form_id)[-1]
    new_start = start_time if start_time is not None else rnd.start_time
    new_deadline = deadline if deadline is not None else rnd.deadline
    # 原截止时间已过且没给新截止：视为"不限截止"，否则重开即仍是"已截止"，形同虚设
    if deadline is None and new_deadline and datetime.now() > new_deadline:
        new_deadline = None
    if new_start and new_deadline and new_start > new_deadline:
        raise ValueError('开始时间不能晚于截止时间')

    tpl.status = 'open'
    rnd.start_time, rnd.deadline = new_start, new_deadline
    for other in FormRound.query.filter_by(template_id=form_id, status='open').all():
        if other.id != rnd.id:
            other.status = 'closed'
    rnd.status = 'open'
    db.session.commit()
    return rnd


def publish_form(form_id):
    """draft -> open"""
    tpl = db.session.get(FormTemplate, form_id)
    if not tpl:
        raise ValueError('表单不存在')
    if tpl.status != 'draft':
        raise ValueError('仅草稿状态可发布')
    tpl.status = 'open'
    db.session.commit()
    ensure_default_round(tpl)      # 发布即开启第一轮
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

def resolve_teacher_uid(user_id, real_name=None):
    """users.id → Teacher.user_id → teacher_uid（回退：名单内唯一同名）。

    历史 bug：`form_submit` 取 `getattr(current_user,'teacher_uid')`，而 User 模型
    没有这个属性 → 教师提交恒存为 str(users.id)，与应交名单用的 teacher_uid
    对不上，导致「已交/未交/提交率」恒为 0、催交误催、业绩无法归属。
    """
    if not user_id:
        return None
    t = Teacher.query.filter_by(user_id=user_id).first()
    if t:
        return t.teacher_uid
    if real_name:
        rows = Teacher.query.filter_by(name=real_name).all()
        if len(rows) == 1:
            return rows[0].teacher_uid
    return None


def _pick_current_round(rounds):
    """在「已按 round_no 升序」的轮次列表里取当前可提交轮：open 中最大的；无 open 取最新一轮。"""
    if not rounds:
        return None
    opened = [r for r in rounds if r.status == 'open']
    return (opened or rounds[-1:])[-1]


def current_round(tpl):
    """当前可提交的轮次：open 中 round_no 最大的；没有 open 则取最新一轮。"""
    rounds = (FormRound.query.filter_by(template_id=tpl.id)
              .order_by(FormRound.round_no).all())
    return _pick_current_round(rounds)


# ── 时间窗（2026-10-10：统一口径，轮次优先、模板兜底） ─────────────────

def round_window(rnd, tpl):
    """返回 (start, deadline)：轮次优先、模板兜底。"""
    start = (rnd.start_time if rnd and rnd.start_time else None) or (
        tpl.start_time if tpl else None)
    deadline = (rnd.deadline if rnd and rnd.deadline else None) or (
        tpl.deadline if tpl else None)
    return start, deadline


def _fmt_dt(dt, short=False):
    """时间窗文案格式：short=True 形如「10-15 08:00」，否则「2026-10-20 18:00」。"""
    if not dt:
        return ''
    return dt.strftime('%m-%d %H:%M' if short else '%Y-%m-%d %H:%M')


def window_state(rnd, tpl):
    """当前轮次的时间窗状态。

    返回 dict(state, start, deadline, text)：
    state ∈ 'open'（含"未设截止"）/ 'before'（未开始）/ 'expired'（已截止）；
    text 用于列表页直接展示，如「进行中（截止 2026-10-20 18:00）」。
    """
    start, deadline = round_window(rnd, tpl)
    now = datetime.now()
    if start and now < start:
        state = 'before'
        text = f'未开始（{_fmt_dt(start, short=True)} 起）'
    elif deadline and now > deadline:
        state = 'expired'
        text = f'已截止（{_fmt_dt(deadline)}）'
    else:
        state = 'open'
        text = (f'进行中（截止 {_fmt_dt(deadline)}）' if deadline
                else '进行中（不限截止）')
    return dict(state=state, start=start, deadline=deadline, text=text)


# ── 目标范围（2026-10-10：新增按学科定向） ─────────────────────────────

def _scope_subjects(target_scope):
    """解析学科定向名单：兼容 list / {'subjects': [...]} / 逗号串。"""
    raw = target_scope
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:  # noqa: BLE001  非 JSON 就按逗号串解析
            raw = [s.strip() for s in raw.split(',') if s.strip()]
    if isinstance(raw, dict):
        raw = raw.get('subjects') or []
    if not isinstance(raw, list):
        return []
    return [str(s).strip() for s in raw if str(s).strip()]


def _matches_target(tpl, submitter_type, submitter_grade=None,
                    submitter_class=None, submitter_subject=None):
    """提交人是否落在目标范围内：all→真；teachers→教师；students→学生；
    grade*→学生且年级在 scope；subject→教师且学科在 scope['subjects']。"""
    if not submitter_type:
        # 旧调用（check_eligibility 只传 2 个参数）拿不到类型：不做定向拦截
        return True
    tt = (tpl.target_type or 'all')
    if tt == 'all':
        return True
    if tt == 'teachers':
        return submitter_type == 'teacher'
    if tt == 'students':
        return submitter_type == 'student'
    if tt.startswith('grade'):
        if submitter_type != 'student':
            return False
        try:
            raw = json.loads(tpl.target_scope or '[]')
        except Exception:  # noqa: BLE001
            return False
        # target_scope 存储年级列表 JSON，兼容 list 与 dict 两种格式
        grades = raw.get('grades') if isinstance(raw, dict) else raw
        if not isinstance(grades, list):
            return False
        return str(submitter_grade or '') in [str(g) for g in grades]
    if tt == 'subject':
        if submitter_type != 'teacher':
            return False
        return str(submitter_subject or '') in _scope_subjects(tpl.target_scope)
    return False


def _elig(eligible, already_submitted=False, submission_id=None, reason='',
          state=None, rnd=None, start=None, deadline=None, message=''):
    """统一资格结果结构（保留旧三字段，新增 reason/state/round/时间/文案）。"""
    return dict(eligible=eligible, already_submitted=already_submitted,
                submission_id=submission_id, reason=reason, state=state,
                round=rnd, start=start, deadline=deadline, message=message)


def submit_form(form_id, submitter_info, answers_dict, files):
    """提交表单
    submitter_info: dict(submitter_type/submitter_id/submitter_name/
                         submitter_uid/submitter_grade/submitter_class/round_id?)
    answers_dict: {question_id_str: answer_value}
    files: request.files MultiDict  {question_id_str: file_storage}
    """
    tpl = db.session.get(FormTemplate, form_id)
    if not tpl:
        raise ValueError('表单不存在')
    if tpl.status != 'open':
        raise ValueError('该表单当前不可提交')

    # 2026-10-10：归属轮次（不传则自动取当前开放轮次）
    rnd = None
    if submitter_info.get('round_id'):
        rnd = db.session.get(FormRound, submitter_info['round_id'])
        if rnd and rnd.template_id != form_id:
            rnd = None
    if rnd is None:
        rnd = current_round(tpl)

    now = datetime.now()
    # 2026-10-10：窗口统一走 round_window（轮次优先、模板兜底），起止都要校验
    start, deadline = round_window(rnd, tpl)
    if start and now < start:
        raise ValueError('收集尚未开始（开始时间 %s）' % _fmt_dt(start))
    if deadline and now > deadline:
        raise ValueError('已超过截止时间')

    # 定向范围校验（全体/教师/学生/年级/学科）
    if not _matches_target(tpl, submitter_info.get('submitter_type'),
                           submitter_info.get('submitter_grade'),
                           submitter_info.get('submitter_class'),
                           submitter_info.get('submitter_subject')):
        raise ValueError('您不在本次收集范围内')

    # 教师提交统一归一到教师名单的 teacher_uid（否则统计与业绩归属都会错）
    uid = submitter_info.get('submitter_uid')
    if submitter_info.get('submitter_type') == 'teacher':
        uid = resolve_teacher_uid(submitter_info.get('submitter_id'),
                                  submitter_info.get('submitter_name')) or uid

    # 检查是否已提交（不允许重复时）——同轮次内不可重复，跨轮次可以再交；
    # 被驳回（rejected）视为可重交，因此只认 submitted/approved。
    if not tpl.allow_multiple:
        existing = (FormSubmission.query.filter_by(
            template_id=form_id,
            submitter_id=submitter_info.get('submitter_id'),
            round_id=(rnd.id if rnd else None),
        ).filter(FormSubmission.status.in_(('submitted', 'approved'))).first())
        if existing:
            raise ValueError('您已提交过本轮收集，不可重复提交')

    sub = FormSubmission(
        template_id=form_id,
        round_id=(rnd.id if rnd else None),
        submitter_type=submitter_info.get('submitter_type'),
        submitter_id=submitter_info.get('submitter_id'),
        submitter_name=submitter_info.get('submitter_name'),
        submitter_uid=uid,
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

    # 2026-10-10：审核通过 → 按模板上的映射自动计入教师业绩库（幂等）。
    # 入账失败不能影响审核结果本身，因此这里只记日志（教务可在业绩库手工补录）。
    ach_ok = False
    if new_status == 'approved':
        from app.modules.academic.services import achievement_service as ach_svc
        try:
            _rec, msg = ach_svc.create_from_submission(sub, reviewer_id=reviewer_id)
            ach_ok = _rec is not None
            if _rec is None:
                current_app.logger.info('[表单入账] 提交#%s 未生成业绩：%s',
                                        sub.id, msg)
        except Exception:  # noqa: BLE001
            current_app.logger.exception('[表单入账] 提交#%s 入账失败', sub.id)

    # 2026-10-10：审核结果通知提交人（此前教师只能自己反复进来查看）
    try:
        from flask import url_for
        from app.modules.notifications.services import notification_service as notif_svc
        tpl = db.session.get(FormTemplate, sub.template_id)
        uid = sub.submitter_uid or sub.submitter_id
        if tpl and uid:
            if new_status == 'approved':
                title = f'【业绩审核】已通过：{tpl.title}'
                content = (f'你提交的「{tpl.title}」材料已通过审核。'
                           + ('已计入教师业绩库。' if ach_ok else '')
                           + '\n可在「我的提交」中查看。')
                try:
                    link = url_for('academic.form_my_submissions')
                except Exception:  # noqa: BLE001
                    link = '/academic/forms/my-submissions'
            else:
                title = f'【业绩审核】已驳回：{tpl.title}'
                content = (f'你提交的「{tpl.title}」材料被驳回。\n'
                           f'原因：{sub.review_note or "未填写"}\n'
                           f'可修改后重新提交（本轮仍可再次提交）。')
                try:
                    link = url_for('academic.form_fill', form_id=sub.template_id)
                except Exception:  # noqa: BLE001
                    link = f'/academic/forms/{sub.template_id}/fill'
            notif_svc.notify_users([uid], title=title, content=content,
                                   category='collect', biz_type='form_review',
                                   biz_id=sub.id, link_url=link,
                                   creator_id=reviewer_id)
    except Exception:  # noqa: BLE001  通知失败不影响审核结果
        current_app.logger.exception('[表单审核] 提交#%s 通知发送失败', sub.id)
    return sub


def get_submissions(form_id, status=None, page=1, per_page=20, round_id=None):
    """提交列表（分页）。round_id 为空表示全部轮次。"""
    q = FormSubmission.query.filter_by(template_id=form_id)
    if round_id:
        q = q.filter(FormSubmission.round_id == round_id)
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

def check_eligibility(form_id, submitter_id, submitter_type=None,
                      submitter_grade=None, submitter_class=None,
                      submitter_subject=None):
    """资格检查：窗口（按当前轮次）+ 定向范围 + 是否已交（按当前轮次）。

    返回 dict(eligible, already_submitted, submission_id, reason,
              state, round, start, deadline, message)
    reason: '' 可填 / 'closed' / 'before' / 'expired' / 'target' / 'already'
    （已交且 allow_multiple 的豁免由调用方处理；旧三字段保持兼容，签名可只传 2 参）
    """
    tpl = db.session.get(FormTemplate, form_id)
    if not tpl:
        return _elig(False, reason='closed', message='表单不存在')
    if tpl.status != 'open':
        return _elig(False, reason='closed', message='该表单当前不可提交')

    rnd = current_round(tpl)
    win = window_state(rnd, tpl)
    if win['state'] == 'before':
        return _elig(False, reason='before', state=win['state'], rnd=rnd,
                     start=win['start'], deadline=win['deadline'],
                     message='收集尚未开始（开始时间 %s）' % _fmt_dt(win['start']))
    if win['state'] == 'expired':
        return _elig(False, reason='expired', state=win['state'], rnd=rnd,
                     start=win['start'], deadline=win['deadline'],
                     message='已超过截止时间')
    if not _matches_target(tpl, submitter_type, submitter_grade,
                           submitter_class, submitter_subject):
        return _elig(False, reason='target', state=win['state'], rnd=rnd,
                     start=win['start'], deadline=win['deadline'],
                     message='您不在本次收集范围内')

    existing = (FormSubmission.query
                .filter_by(template_id=form_id, submitter_id=submitter_id)
                .filter(FormSubmission.round_id == (rnd.id if rnd else None))
                .filter(FormSubmission.status.in_(('submitted', 'approved')))
                .first())
    if existing:
        return _elig(False, already_submitted=True, submission_id=existing.id,
                     reason='already', state=win['state'], rnd=rnd,
                     start=win['start'], deadline=win['deadline'],
                     message='您已提交过本轮收集')
    return _elig(True, reason='', state=win['state'], rnd=rnd,
                 start=win['start'], deadline=win['deadline'])


def fill_items(submitter_id, submitter_type='teacher', submitter_grade=None,
               submitter_class=None, submitter_subject=None):
    """填写端列表数据：list[dict(tpl, rnd, state, state_text, start, deadline,
    submitted, submission_id)]。

    只列 status=open 且窗口 state ∈ ('open','before')（expired 不列）；
    已交按「当前轮 + submitted/approved」判定，查询一次成组，避免 N+1。
    """
    tpls = (FormTemplate.query.filter_by(status='open')
            .order_by(FormTemplate.deadline.asc()).all())
    if not tpls:
        return []

    # 一次取回这些模板的全部轮次，本地挑当前轮（避免每个模板一次查询）
    by_tpl = {}
    for r in (FormRound.query
              .filter(FormRound.template_id.in_([t.id for t in tpls]))
              .order_by(FormRound.round_no).all()):
        by_tpl.setdefault(r.template_id, []).append(r)

    round_map = {tpl.id: _pick_current_round(by_tpl.get(tpl.id) or [])
                 for tpl in tpls}

    # 已交（带轮次，仅 submitted/approved；被驳回可重交）
    submitted = {}
    if submitter_id:
        rnd_ids = [r.id for r in round_map.values() if r]
        no_round = [t.id for t, r in round_map.items() if not r]
        q = (FormSubmission.query
             .filter_by(submitter_id=submitter_id)
             .filter(FormSubmission.status.in_(('submitted', 'approved'))))
        if rnd_ids:
            for s in q.filter(FormSubmission.round_id.in_(rnd_ids)).all():
                submitted[(s.template_id, s.round_id)] = s.id
        if no_round:
            for s in (q.filter(FormSubmission.template_id.in_(no_round),
                               FormSubmission.round_id.is_(None)).all()):
                submitted[(s.template_id, None)] = s.id

    out = []
    for tpl in tpls:
        rnd = round_map.get(tpl.id)
        win = window_state(rnd, tpl)
        if win['state'] == 'expired':          # 已截止的不再列出
            continue
        if not _matches_target(tpl, submitter_type, submitter_grade,
                               submitter_class, submitter_subject):
            continue
        sid = submitted.get((tpl.id, rnd.id if rnd else None))
        out.append(dict(tpl=tpl, rnd=rnd, state=win['state'],
                        state_text=win['text'], start=win['start'],
                        deadline=win['deadline'], submitted=bool(sid),
                        submission_id=sid))
    return out


def get_available_forms(submitter_type, submitter_grade=None,
                        submitter_class=None, submitter_subject=None):
    """获取当前用户可填写的表单列表（fill_items 的薄封装，口径只有一套）。

    返回 list[FormTemplate]（保持旧返回类型：模板/调用方仍按模板对象用）。
    """
    return [it['tpl'] for it in fill_items(
        None, submitter_type=submitter_type, submitter_grade=submitter_grade,
        submitter_class=submitter_class, submitter_subject=submitter_subject)]


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

    # 类型校验（H-8：统一走 upload_guard。此前「未配置 file_types 就任意后缀可传」，
    # 可上传 .html/.svg 构成未登录即可触发的存储型 XSS）
    from app.utils.upload_guard import validate_upload
    allowed = None
    if question.file_types:
        allowed = [t.strip().lower().lstrip('.') for t in question.file_types.split(',')]
    ok, msg = validate_upload(file.filename, allowed_exts=allowed, stream=file.stream)
    if not ok:
        raise ValueError(msg)

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
