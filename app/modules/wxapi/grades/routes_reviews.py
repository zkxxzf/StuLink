# StuLink v1.19.67.64 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序成绩复核（申请调分）：我的申请 / 搜学生 / 提交 / 撤回。

审批（grades.score_edit）仍在网页端；小程序负责教师侧「发现分数有误 →
搜学号/姓名 → 改分 → 提交 → 看进度」。
"""
from datetime import datetime

from flask import g, request

from app.extensions import db
from app.models.grades import ScoreReviewRequest
from app.modules.grades.routes import score_review_entry as entry_svc
from app.modules.grades.services import score_guard
from app.modules.wxapi import bp
from app.modules.wxapi.core.guards import wx_perm_required
from app.modules.wxapi.core.response import wx_err, wx_ok
from app.modules.wxapi.grades import review_common as common
from app.utils.helpers import log_operation

PERM = 'grades.view'
LIST_LIMIT = 200


@bp.route('/grades/reviews')
@wx_perm_required(PERM)
def wx_reviews():
    """我的复核申请（query: status=）。"""
    status = (request.args.get('status') or '').strip()
    q = ScoreReviewRequest.query.filter_by(applicant_id=g.wx_user.id)
    if status in common.STATUS_LABEL:
        q = q.filter_by(status=status)
    rows = q.order_by(ScoreReviewRequest.id.desc()).limit(LIST_LIMIT).all()
    uid = g.wx_user.id
    return wx_ok({
        'items': [common.review_item(r, uid) for r in rows],
        'status': status if status in common.STATUS_LABEL else '',
        'statuses': common.status_options(),
        'counts': common.counts_of(uid),
    })


@bp.route('/grades/reviews/exams')
@wx_perm_required(PERM)
def wx_review_exams():
    """可提交复核的考试（按本人成绩可见范围收敛）。"""
    return wx_ok(common.visible_exams(g.wx_user))


@bp.route('/grades/reviews/search')
@wx_perm_required(PERM)
def wx_review_search():
    """按考试 + 学号/姓名搜学生，返回其全科成绩（query: exam_id / q）。"""
    exam = common.exam_of(g.wx_user, request.args.get('exam_id', type=int))
    if exam is None:
        return wx_err('考试不存在或不在你的可见范围', 403)
    kw = (request.args.get('q') or '').strip()
    if not kw:
        return wx_ok([])
    sc = score_guard.resolve(g.wx_user)
    students = entry_svc._search_students(exam, kw, sc)
    return wx_ok(entry_svc._fill_scores(exam, students, sc))


def _clean_items(raw):
    """前端提交的改分明细（去掉非 dict 项）。"""
    if not isinstance(raw, list):
        return []
    return [x for x in raw if isinstance(x, dict)]


@bp.route('/grades/reviews', methods=['POST'])
@wx_perm_required(PERM)
def wx_review_submit():
    """提交复核（同一学生可多科）：body {exam_id, student_no, reason, items}。"""
    data = request.get_json(silent=True) or {}
    exam = common.exam_of(g.wx_user, data.get('exam_id'))
    if exam is None:
        return wx_err('考试不存在或不在你的可见范围', 403)
    student_no = (data.get('student_no') or '').strip()
    reason = (data.get('reason') or '').strip()[:500]
    if not student_no:
        return wx_err('请先搜索并选择学生')
    if not reason:
        return wx_err('请填写申请理由')
    sc = score_guard.resolve(g.wx_user)
    items, err = entry_svc._build_items(
        exam, sc, student_no, _clean_items(data.get('items')))
    if err:
        return wx_err(err)
    dup = [it['subject'] for it in items
           if entry_svc._has_pending(exam.id, student_no, it['subject'],
                                     it['question_no'])]
    if dup:
        return wx_err('以下科目已有待审核申请：' + '、'.join(dup))
    entry_svc._save_items(exam, student_no, reason, items)
    return wx_ok({'created': len(items)}, '已提交复核申请')


@bp.route('/grades/reviews/<int:rid>/withdraw', methods=['POST'])
@wx_perm_required(PERM)
def wx_review_withdraw(rid):
    """撤回本人待审核的复核申请。"""
    r = db.session.get(ScoreReviewRequest, rid)
    if r is None:
        return wx_err('复核单不存在', 404)
    # 2026-10-19 审计：小程序只允许本人撤回，管理员也不能代撤回
    # （网页端审批链路另有留痕入口，避免这里横向扩大撤回权）。
    if r.applicant_id != g.wx_user.id:
        return wx_err('只能撤回本人的申请', 403)
    if r.status != 'pending':
        return wx_err('只有待审核的申请可以撤回')
    r.status = 'withdrawn'
    r.reviewed_at = datetime.now()
    db.session.commit()
    log_operation(g.wx_user, '撤回', '成绩复核', r.exam_id,
                  '复核单 #%d' % r.id, module='grades')
    return wx_ok({'id': rid}, '已撤回该申请')
