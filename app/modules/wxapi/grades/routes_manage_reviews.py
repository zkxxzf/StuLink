# StuLink v1.19.49.48 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序成绩复核审批（grades.score_edit）。

列表与审批都按**数据范围**收敛（与网页 `/grades/reviews/manage` 同一口径）：
- 列表只出本人可见年级的复核单；
- 审批前再校验考试可见 + 班级可写，越权 403；
- 「批准并应用」调用 `services/review_service.audit_review`，与网页同一条
  改分链路（满分校验 → 改分 → 留痕 → 重算总分与排名 → 清缓存）。
"""
from flask import g, request

from app.extensions import db
from app.models.grades import Exam, ScoreReviewRequest
from app.models.user import User
from app.modules.grades.services import review_service
from app.modules.grades.services import scope as scope_service
from app.modules.wxapi import bp
from app.modules.wxapi.core.guards import wx_perm_required
from app.modules.wxapi.core.response import wx_err, wx_ok
from app.modules.wxapi.grades import review_common as common

PERM = 'grades.score_edit'
LIST_LIMIT = 300


def _visible_grades_where(user, q):
    """按可见年级收敛查询（None = 全校）。"""
    vis = scope_service.visible_grades(user)
    if vis is not None:
        q = q.filter(ScoreReviewRequest.grade.in_(list(vis) or ['']))
    return q


def _names(ids):
    if not ids:
        return {}
    rows = User.query.filter(User.id.in_(ids)).all()
    return {u.id: (u.real_name or u.username) for u in rows}


@bp.route('/grades/reviews/manage')
@wx_perm_required(PERM)
def wx_reviews_manage():
    """复核审批列表（query: status= / grade=）。"""
    user = g.wx_user
    status = (request.args.get('status') or 'pending').strip()
    if status not in common.STATUS_LABEL:
        status = 'pending'
    grade = (request.args.get('grade') or '').strip()
    q = _visible_grades_where(
        user, ScoreReviewRequest.query.filter_by(status=status))
    if grade:
        q = q.filter_by(grade=grade)
    rows = q.order_by(ScoreReviewRequest.id.desc()).limit(LIST_LIMIT).all()
    applicants = _names({r.applicant_id for r in rows if r.applicant_id})
    items = []
    for r in rows:
        item = common.review_item(r, user.id)
        item['grade'] = r.grade or ''
        item['applicant_name'] = applicants.get(r.applicant_id, '')
        item['can_audit'] = True
        items.append(item)
    counts = {}
    for st in common.STATUS_LABEL:
        cq = _visible_grades_where(
            user, ScoreReviewRequest.query.filter_by(status=st))
        counts[st] = cq.count()
    vis = scope_service.visible_grades(user)
    return wx_ok({
        'items': items,
        'status': status,
        'statuses': common.status_options(),
        'counts': counts,
        'grades': list(vis) if vis is not None else [],
        'grade': grade,
    })


@bp.route('/grades/reviews/<int:rid>/audit', methods=['POST'])
@wx_perm_required(PERM)
def wx_review_audit(rid):
    """审批：body {action: approve|reject, note, apply}。"""
    r = db.session.get(ScoreReviewRequest, rid)
    if r is None:
        return wx_err('复核单不存在', 404)
    # H-02：申请人不得审批本人单据（服务层还有一道，这里给小程序明确的 403）。
    # 2026-10-20 用户口径：管理员 / 校长例外 —— 最高审批身份可审批自己的提交。
    if r.applicant_id == g.wx_user.id \
            and not getattr(g.wx_user, 'is_top_reviewer', False):
        return wx_err('不得审批本人提交的复核申请', 403)
    exam = db.session.get(Exam, r.exam_id)
    if exam is not None:
        try:
            scope_service.check_exam_visible(g.wx_user, exam)
        except PermissionError:
            return wx_err('无权审批该考试的成绩', 403)
    from app.modules.grades.services import score_guard
    sc = score_guard.resolve(g.wx_user)
    if not sc.can_view_class(
            r.grade or (exam.grade if exam else ''), r.class_name):
        return wx_err('无权审批该班级的成绩', 403)
    data = request.get_json(silent=True) or {}
    action = (data.get('action') or '').strip()
    note = (data.get('note') or '').strip()[:200]
    apply_now = bool(data.get('apply'))
    ok, msg = review_service.audit_review(
        r, g.wx_user, action, note, apply_now)
    if not ok:
        return wx_err(msg)
    return wx_ok({
        'id': rid,
        'status': r.status,
        'applied': bool(r.applied),
    }, msg)
