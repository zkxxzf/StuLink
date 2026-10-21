# StuLink v1.19.50.57 2026-10-15
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
  · 2026-10-19 用户口径：录入改成「搜学号/姓名 → 看全科成绩 → 直接改分 →
    提交」，一次可提交多科；搜索与批量提交在 `score_review_entry.py`。

安全：
  · 提交/查看本人申请 = `grades.view` + 数据范围收敛（越界 403）；
  · 审批 = `grades.score_edit`（与"改分"同一把钥匙，不新增权限 key）；
  · 已审过的单子不可再审；同考试同学生同科同题号的待审单不重复提交。
"""
from datetime import datetime

from flask import (render_template, request, redirect, url_for, flash, abort)
from flask_login import login_required, current_user

from app.extensions import db
from app.models.grades import (Exam, ScoreReviewRequest,
                               TOTAL_SUBJECT, SUBJECTS)
from app.modules.grades import bp
from app.modules.grades.services import (review_service,
                                        scope as scope_service,
                                        score_guard)
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation

STATUS_LABEL = {'pending': '待审核', 'approved': '已批准',
                'rejected': '已驳回', 'withdrawn': '已撤回'}
STATUS_BADGE = {'pending': 'bg-warning text-dark', 'approved': 'bg-success',
                'rejected': 'bg-danger', 'withdrawn': 'bg-secondary'}


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
    提交页"选考试 → 搜学号/姓名"决定，放行仍由 `score_review_entry` 的
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
    """批准 / 驳回（审批与「批准并应用」逻辑见 review_service.audit_review）。"""
    r = _review_or_404(rid)
    back = redirect(url_for('grades.reviews_manage_page'))
    # H-02：申请人不得审批本人单据（服务层还有一道）。2026-10-20 用户口径：
    # **管理员 / 校长例外** —— 最高审批身份不存在"上级"，可审批自己的提交。
    if r.applicant_id == current_user.id and not current_user.is_top_reviewer:
        log_operation(current_user, '审批被拒', '成绩复核', r.exam_id,
                      '复核单 #%d：不得审批本人申请' % r.id,
                      module='grades', severity='WARNING')
        abort(403, description='不得审批本人提交的复核申请')
    # 审批（「批准并应用」会直接改分并重算排名）必须先过**数据范围**校验：
    # 只有 grades.score_edit 功能权限、但不在本年级范围内的账号不得越权写分。
    _r_exam = db.session.get(Exam, r.exam_id)
    if _r_exam is not None:
        try:
            scope_service.check_exam_visible(current_user, _r_exam)
        except PermissionError:
            abort(403)
    _sc = score_guard.resolve(current_user)
    if not _sc.can_view_class(
            r.grade or (_r_exam.grade if _r_exam else ''), r.class_name):
        abort(403)
    action = (request.form.get('action') or '').strip()
    note = (request.form.get('review_note') or '').strip()[:200]
    apply_now = (request.form.get('apply') or '') in ('1', 'on', 'true')
    ok, msg = review_service.audit_review(
        r, current_user, action, note, apply_now)
    flash(msg, 'success' if ok else 'danger')
    return back
