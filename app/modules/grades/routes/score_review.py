# StuLink v1.19.50.35 2026-10-15
# 成绩复核（申请调分）：教师提交 → 有改分权限者审批 → 可选应用到成绩单
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""成绩复核申请（用户口径 2026-10-15）

用户原话：「然后申请调分可以。因为他有一个成绩复核的，这个的话就需要学生的信息、
学科、题号，然后增减分数。」

设计：
  · 提交：任课教师 / 班主任在**本人可见范围内**提交 —— 学生 + 学科 + 题号 + 增减分数 + 理由；
    范围判定统一走 `score_guard.ScoreScope`（与成绩分析页 / MCP / AI 同一份口径）：
    任课教师只能对本班本科提，班主任可对本班全部科目提；
  · 审批：有 `grades.score_edit`（"成绩修改（改分）"权限）的人批准 / 驳回；
  · 批准时可选 `apply=1` —— 把增减分**真正写进成绩单**，走与手动改分同一条链路：
    校验满分 → 改 score（原始分同步）→ 重算该生总分 → 重算本场排名 →
    写 `ScoreChangeLog`（留痕，note 标出复核单号）→ 清缓存 → 写操作日志；
  · 题号是说明性字段（`exam_scores` 粒度是「考试 × 学生 × 学科」，无题号列）。

安全：
  · 提交/查看本人申请 = `grades.view` + 数据范围收敛（越界 403）；
  · 审批 = `grades.score_edit`（与"改分"同一把钥匙，不新增权限 key）；
  · 已审过的单子不可再审；同考试同学生同科同题号的待审单不重复提交。
"""
import math
from datetime import datetime

from flask import (render_template, request, jsonify, redirect, url_for,
                   flash, abort)
from flask_login import login_required, current_user

from app.extensions import db
from app.models.grades import (Exam, ExamScore, ScoreReviewRequest, ScoreChangeLog,
                               TOTAL_SUBJECT, SUBJECTS)
from app.modules.grades import bp
from app.modules.grades.services import (ranking, scope as scope_service,
                                         score_guard, store_service, tab_service)
from app.modules.grades.utils import invalidate_exam_cache
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation

STATUS_LABEL = {'pending': '待审核', 'approved': '已批准',
                'rejected': '已驳回', 'withdrawn': '已撤回'}
STATUS_BADGE = {'pending': 'bg-warning text-dark', 'approved': 'bg-success',
                'rejected': 'bg-danger', 'withdrawn': 'bg-secondary'}

_MAX_DELTA = 100.0      # 单次增减幅度上限（防手误多打一个 0）


def _review_or_404(rid):
    r = db.session.get(ScoreReviewRequest, rid)
    if r is None:
        abort(404)
    return r


def _require_own(r):
    """只有申请人本人（或管理员）能操作自己的单子"""
    if current_user.role == 'admin':
        return
    if r.applicant_id != current_user.id:
        abort(403)


def _my_combos():
    """本人可提交复核的范围：**任教年级的学科（该年级全部班）** ∪ 班主任整班。

    2026-10-15：`ScoreScope.teach` 已变成 (年级, 学科) 二元组，不再带班级 ——
    任课组合就按"年级 × 学科"给出（`class_name` 留空＝该年级全部班），具体班级由
    提交页"选考试 → 搜学生"决定，放行仍由 `reviews_students_api` 的
    `can_view_class` / `can_view_subject` 双重收口（不在范围内的人搜不到）。
    """
    sc = score_guard.resolve(current_user)
    out = []
    seen = set()
    for (g, s) in sorted(sc.teach):
        if s == TOTAL_SUBJECT:
            continue
        out.append({'grade': g, 'class_name': '', 'subject': s, 'source': 'teach'})
        seen.add((g, '', s))
    for (g, c) in sorted(sc.classes):
        subs = sc.class_subjects(g, c)
        if subs is None:
            subs = set(SUBJECTS)          # 成绩链路只认 9 科（综合组六科不进成绩）
        for s in sorted(subs):
            if s == TOTAL_SUBJECT or (g, c, s) in seen:
                continue
            out.append({'grade': g, 'class_name': c, 'subject': s, 'source': 'head'})
            seen.add((g, '', s))
            seen.add((g, c, s))
    return out


# ==================== 我的申请（教师） ====================

@bp.route('/reviews')
@login_required
@perm_required('grades.view')
def reviews_page():
    """我的成绩复核申请：提交表单 + 本人申请列表"""
    grades = scope_service.visible_grades(current_user)
    exams = (Exam.query.filter(Exam.grade.in_(grades))
             .order_by(Exam.exam_date.desc(), Exam.id.desc()).limit(60).all()
             if grades else [])
    rows = (ScoreReviewRequest.query
            .filter_by(applicant_id=current_user.id)
            .order_by(ScoreReviewRequest.id.desc()).limit(200).all())
    return render_template('grades/score_review.html', rows=rows, exams=exams,
                           combos=_my_combos(), status_label=STATUS_LABEL,
                           status_badge=STATUS_BADGE,
                           can_audit=current_user.has_perm('grades.score_edit'))


@bp.route('/reviews/api/students')
@login_required
@perm_required('grades.view')
def reviews_students_api():
    """按考试 + 关键词取"本人可见范围内"的学生×科目（提交表单的下拉/搜索用）。

    天然做范围收敛：只返回 `can_view_class` 且 `can_view_subject` 命中的行，
    越权学生不会出现在候选里。
    """
    exam_id = request.args.get('exam_id', type=int)
    kw = (request.args.get('q') or '').strip()
    if not exam_id:
        return jsonify(success=False, message='缺少 exam_id'), 400
    exam = db.session.get(Exam, exam_id)
    if exam is None:
        return jsonify(success=False, message='考试不存在'), 404
    try:
        scope_service.check_exam_visible(current_user, exam)
    except PermissionError:
        return jsonify(success=False, message='无权查看该考试'), 403

    sc = score_guard.resolve(current_user)
    q = (db.session.query(ExamScore.student_no, ExamScore.student_name,
                          ExamScore.class_name, ExamScore.subject, ExamScore.score)
         .filter(ExamScore.exam_id == exam_id,
                 ExamScore.subject != TOTAL_SUBJECT))
    # 2026-10-16 审计修复：原实现先 limit(300) 再在 Python 里按范围过滤 —— 排序为
    # (班名, 学号, 科目)，前 300 行往往被前几个班占满，导致班级名靠后的班主任/任课
    # 教师在「空关键词」浏览态（前端固定以 q='' 调用）取不到本班学生。改为把范围条件
    # 下推到 SQL 后再 limit：班级授权 → class_name 白名单；学科授权 → subject 白名单
    # （两者取或，与 can_view_class / can_view_subject 的判定一致）。
    _grade = exam.grade
    if not (sc.full_school or _grade in sc.grades):
        _teach_subjects = {s for g, s in sc.teach if g == _grade}
        _granted_classes = {c for g, c in sc.classes if g == _grade}
        if not _teach_subjects and not _granted_classes:
            return jsonify(success=True, grade=_grade, data=[])
        _conds = []
        if _teach_subjects:
            _conds.append(ExamScore.subject.in_(list(_teach_subjects)))
        if _granted_classes:
            _conds.append(ExamScore.class_name.in_(list(_granted_classes)))
        q = q.filter(db.or_(*_conds))
    if kw:
        q = q.filter(db.or_(ExamScore.student_no.like(f'%{kw}%'),
                            ExamScore.student_name.like(f'%{kw}%')))
    rows = q.order_by(ExamScore.class_name, ExamScore.student_no,
                      ExamScore.subject).limit(300).all()
    out = []
    for no, name, cn, subj, score in rows:
        if not sc.can_view_class(exam.grade, cn):
            continue
        if not sc.can_view_subject(exam.grade, cn, subj):
            continue
        out.append({'student_no': no, 'student_name': name, 'class_name': cn,
                    'class_name_full': f'{exam.grade}{cn}', 'subject': subj,
                    'score': score})
    return jsonify(success=True, grade=exam.grade, data=out)


@bp.route('/reviews/new', methods=['POST'])
@login_required
@perm_required('grades.view')
def review_new():
    """提交一条成绩复核申请"""
    back = redirect(url_for('grades.reviews_page'))
    exam_id = request.form.get('exam_id', type=int)
    student_no = (request.form.get('student_no') or '').strip()
    subject = (request.form.get('subject') or '').strip()
    question_no = (request.form.get('question_no') or '').strip()[:40]
    reason = (request.form.get('reason') or '').strip()[:500]
    try:
        delta = round(float(request.form.get('delta') or 0), 1)
    except (TypeError, ValueError):
        flash('增减分数格式不正确', 'danger')
        return back
    # 2026-10-16 审计修复：float('nan') 能解析成功，而 nan==0 / abs(nan)>上限 全为假，
    # 会绕过下面所有校验并写进成绩单。显式拒绝非有限值。
    if not math.isfinite(delta):
        flash('增减分数格式不正确', 'danger')
        return back

    if not exam_id or not student_no or not subject:
        flash('请选择考试、学生与学科', 'danger')
        return back
    if subject == TOTAL_SUBJECT:
        flash('总分由各科自动汇总，不能申请修改总分，请按单科提交', 'danger')
        return back
    if delta == 0:
        flash('增减分数不能为 0', 'danger')
        return back
    if abs(delta) > _MAX_DELTA:
        flash(f'单次增减幅度不能超过 {_MAX_DELTA:g} 分，请核对是否多打了一位', 'danger')
        return back
    if not reason:
        flash('请填写申请理由（便于审批人核对）', 'danger')
        return back

    exam = db.session.get(Exam, exam_id)
    if exam is None:
        abort(404)
    try:
        scope_service.check_exam_visible(current_user, exam)
    except PermissionError:
        abort(403)

    row = (ExamScore.query.filter_by(exam_id=exam_id, student_no=student_no,
                                     subject=subject).first())
    if row is None:
        flash('该场考试没有这名学生该科的成绩', 'danger')
        return back

    # 数据范围（唯一口径）：班级 + 学科双重校验
    sc = score_guard.resolve(current_user)
    grade = row.grade or exam.grade
    if not sc.can_view_class(grade, row.class_name):
        abort(403)
    if not sc.can_view_subject(grade, row.class_name, subject):
        abort(403)

    dup = (ScoreReviewRequest.query
           .filter_by(exam_id=exam_id, student_no=student_no, subject=subject,
                      question_no=question_no, status='pending',
                      applicant_id=current_user.id).first())
    if dup:
        flash('同一学生 + 同一学科 + 同一题号已有一条待审核申请，请勿重复提交', 'warning')
        return back

    db.session.add(ScoreReviewRequest(
        exam_id=exam_id, exam_name=exam.name, grade=grade, class_name=row.class_name,
        student_no=student_no, student_name=row.student_name, subject=subject,
        question_no=question_no, delta=delta, reason=reason, status='pending',
        applicant_id=current_user.id,
        applicant_name=current_user.real_name or current_user.username,
        created_at=datetime.now()))
    db.session.commit()
    log_operation(current_user, '提交', '成绩复核', exam_id,
                  f'{row.student_name or student_no} {subject} {delta:+g}'
                  f'{("（题号 " + question_no + "）") if question_no else "（整卷）"}',
                  module='grades')
    flash('申请已提交，等待成绩管理员审核', 'success')
    return back


@bp.route('/reviews/<int:rid>/withdraw', methods=['POST'])
@login_required
@perm_required('grades.view')
def review_withdraw(rid):
    """撤回本人待审核的申请"""
    r = _review_or_404(rid)
    _require_own(r)
    if r.status != 'pending':
        flash('只有待审核的申请可以撤回', 'warning')
        return redirect(url_for('grades.reviews_page'))
    r.status = 'withdrawn'
    r.reviewed_at = datetime.now()
    db.session.commit()
    log_operation(current_user, '撤回', '成绩复核', r.exam_id,
                  f'复核单 #{r.id}', module='grades')
    flash('已撤回该申请', 'success')
    return redirect(url_for('grades.reviews_page'))


# ==================== 审批（有改分权限者） ====================

@bp.route('/reviews/manage')
@login_required
@perm_required('grades.score_edit')
def reviews_manage_page():
    """成绩复核审批列表（按状态筛选，默认待审核）"""
    status = (request.args.get('status') or 'pending').strip()
    if status not in STATUS_LABEL:
        status = 'pending'
    grade = (request.args.get('grade') or '').strip()
    # 2026-10-15 审计修复（P0）：审批列表必须按**数据范围**收敛 ——
    # 此前只按 ?grade= 手选过滤，持 grades.score_edit 的年级长/教务员能列出
    # （并在下一屏审批、甚至直接改分）**别的年级**的复核单（含学生姓名/科目/分数）。
    vis = scope_service.visible_grades(current_user)
    q = ScoreReviewRequest.query.filter_by(status=status)
    if vis is not None:
        q = q.filter(ScoreReviewRequest.grade.in_(list(vis) or ['']))
    if grade:
        q = q.filter_by(grade=grade)
    rows = q.order_by(ScoreReviewRequest.id.desc()).limit(300).all()
    grades = list(vis) if vis is not None else None
    counts = {}
    for st in STATUS_LABEL:
        cq = ScoreReviewRequest.query.filter_by(status=st)
        if vis is not None:
            cq = cq.filter(ScoreReviewRequest.grade.in_(list(vis) or ['']))
        counts[st] = cq.count()
    return render_template('grades/score_review_manage.html', rows=rows,
                           status=status, counts=counts, grades=grades,
                           grade=grade, status_label=STATUS_LABEL,
                           status_badge=STATUS_BADGE)


@bp.route('/reviews/<int:rid>/review', methods=['POST'])
@login_required
@perm_required('grades.score_edit')
def review_audit(rid):
    """批准 / 驳回。

    approve 时可带 `apply=1`：把增减分写进成绩单（走与手动改分同一条链路）。
    另外支持 `apply=1` 但分数越界时拒绝落库（不静默截断）。
    """
    r = _review_or_404(rid)
    back = redirect(url_for('grades.reviews_manage_page'))
    # 2026-10-15 审计修复（P0）：审批（「批准并应用」会直接改分并重算排名）必须先过
    # **数据范围**校验。此前只有 grades.score_edit 功能权限 + status 检查，
    # 持该权限的年级长/教务员可以对别的年级的复核单直接改分（越权写）。
    _r_exam = db.session.get(Exam, r.exam_id)
    if _r_exam is not None:
        try:
            scope_service.check_exam_visible(current_user, _r_exam)
        except PermissionError:
            abort(403)
    _sc = score_guard.resolve(current_user)
    if not _sc.can_view_class(r.grade or (_r_exam.grade if _r_exam else ''), r.class_name):
        abort(403)
    if r.status != 'pending':
        flash('该申请已处理过，不能重复审核', 'warning')
        return back
    action = (request.form.get('action') or '').strip()
    note = (request.form.get('review_note') or '').strip()[:200]
    if action not in ('approve', 'reject'):
        flash('未知的审核动作', 'danger')
        return back

    r.reviewer_id = current_user.id
    r.reviewer_name = current_user.real_name or current_user.username
    r.reviewed_at = datetime.now()
    r.review_note = note

    if action == 'reject':
        r.status = 'rejected'
        db.session.commit()
        log_operation(current_user, '驳回', '成绩复核', r.exam_id,
                      f'复核单 #{r.id}：{note or "无意见"}', module='grades')
        flash(f'已驳回复核单 #{r.id}', 'success')
        return back

    # ---- 批准 ----
    apply_now = (request.form.get('apply') or '') in ('1', 'on', 'true')
    if apply_now:
        exam = db.session.get(Exam, r.exam_id)
        if exam is None:
            flash('该申请对应的考试已不存在，无法应用改分', 'danger')
            return back
        srow = (ExamScore.query.filter_by(exam_id=r.exam_id, student_no=r.student_no,
                                          subject=r.subject).first())
        if srow is None:
            flash('该场考试没有这名学生该科的成绩，请先导入成绩后再应用', 'danger')
            return back
        limit = float((exam.full_marks() or {}).get(r.subject, 100))
        old_score, old_raw = srow.score, srow.raw_score
        new_score = round((old_score or 0) + r.delta, 1)
        if new_score < 0 or new_score > limit + 0.5:
            flash(f'应用后分数 {new_score} 超出 0~{limit:g} 的范围，请核对增减分数'
                  f'（未写库，可先驳回或修改后重提）', 'danger')
            return back
        srow.score = new_score
        if old_raw is not None:
            srow.raw_score = round(old_raw + r.delta, 1)
        # 与手动改分同一条链路：留痕 → 重算总分 → 重算排名 → 清缓存 → 操作日志
        db.session.add(ScoreChangeLog(
            exam_id=exam.id, exam_name=exam.name, score_id=srow.id,
            student_no=srow.student_no, student_name=srow.student_name,
            class_name=srow.class_name, subject=srow.subject, action='update',
            old_score=old_score, new_score=srow.score,
            old_raw=old_raw, new_raw=srow.raw_score,
            operator_id=current_user.id,
            operator_name=current_user.real_name or current_user.username,
            note=f'成绩复核单 #{r.id}'))
        store_service.refresh_student_total(exam, srow.student_no)
        n = ranking.recalc_exam(exam.id)
        if exam.status != 'imported':
            exam.status = 'imported'
        r.applied = True
        r.applied_at = datetime.now()
        r.status = 'approved'
        db.session.commit()
        invalidate_exam_cache(exam.id)
        # 第七轮审计（P-L1）：原此处紧跟一次 tab_service 的缓存清理调用，
        # 而它内部就是 invalidate_exam_cache —— 同一次提交后清两遍（纯重复），删。
        log_operation(current_user, '批准并应用', '成绩复核', r.exam_id,
                      f'复核单 #{r.id}：{srow.student_name or srow.student_no} '
                      f'{r.subject} {r.delta:+g}（新分 {srow.score}，排名重算 {n} 条）',
                      module='grades')
        flash(f'已批准并将 {r.delta:+g} 分应用到成绩单（总分与排名已重算，已写入分数修改记录）',
              'success')
        return back

    # 仅批准记录（不写成绩单）
    r.status = 'approved'
    db.session.commit()
    log_operation(current_user, '批准', '成绩复核', r.exam_id,
                  f'复核单 #{r.id}（仅批准，未应用改分）', module='grades')
    flash(f'已批准复核单 #{r.id}（未写入成绩单；如需改分请用「批准并应用」）', 'success')
    return back
