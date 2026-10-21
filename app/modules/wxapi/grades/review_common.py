# StuLink v1.19.57.64 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序成绩复核：列表字段与本场可见考试。

录入（搜学生 / 校验改分 / 落库）完全复用网页端
`app.modules.grades.routes.score_review_entry`，这里只做取数与字段压缩。
"""
from app.extensions import db
from app.models.grades import Exam, ScoreReviewRequest
from app.modules.grades.services import scope as scope_service

STATUS_LABEL = {'pending': '待审核', 'approved': '已批准',
                'rejected': '已驳回', 'withdrawn': '已撤回'}
STATUS_ORDER = ('pending', 'approved', 'rejected', 'withdrawn')
EXAM_LIMIT = 60


def status_options():
    """筛选项（与网页端文案一致）。"""
    return [{'key': k, 'label': STATUS_LABEL[k]} for k in STATUS_ORDER]


def review_item(r, uid):
    """一条复核申请 → 小程序卡片字段。"""
    return {
        'id': r.id,
        'exam_id': r.exam_id,
        'exam_name': r.exam_name or '',
        'student_no': r.student_no or '',
        'student_name': r.student_name or '',
        'class_text': '%s%s' % (r.grade or '', r.class_name or ''),
        'subject': r.subject or '',
        'question_no': r.question_no or '',
        'delta': r.delta or 0,
        'delta_text': '%+g' % (r.delta or 0),
        'reason': r.reason or '',
        'status': r.status,
        'status_text': STATUS_LABEL.get(r.status, r.status),
        'reviewer_name': r.reviewer_name or '',
        'review_note': r.review_note or '',
        'created_at': (r.created_at.strftime('%Y-%m-%d %H:%M')
                       if r.created_at else ''),
        'reviewed_at': (r.reviewed_at.strftime('%Y-%m-%d %H:%M')
                        if r.reviewed_at else ''),
        'applied': bool(r.applied),
        'can_withdraw': bool(r.status == 'pending' and r.applicant_id == uid),
    }


def visible_exams(user):
    """本人可见的成绩考试（只列已导入的，倒序）。"""
    grades = scope_service.visible_grades(user)
    # 2026-10-19：dirty（已导入待重算）也算已导入，不能从复核下拉里消失
    q = Exam.query.filter(Exam.status != 'draft')
    if grades is not None:
        q = q.filter(Exam.grade.in_(list(grades) or ['']))
    rows = (q.order_by(Exam.exam_date.desc(), Exam.id.desc())
            .limit(EXAM_LIMIT).all())
    return [{
        'id': e.id,
        'name': e.name,
        'grade': e.grade or '',
        'date': e.exam_date.strftime('%Y-%m-%d') if e.exam_date else '',
    } for e in rows]


def exam_of(user, exam_id):
    """按 id 取考试并校验数据范围；越界 / 不存在返回 None。"""
    if not exam_id:
        return None
    # 2026-10-20 第十六轮：非数字 id 原来直接 ValueError → 500
    try:
        exam = db.session.get(Exam, int(exam_id))
    except (TypeError, ValueError):
        return None
    if exam is None:
        return None
    try:
        scope_service.check_exam_visible(user, exam)
    except PermissionError:
        return None
    return exam


def counts_of(applicant_id):
    """本人各状态申请数（列表顶部计数）。"""
    out = {k: 0 for k in STATUS_ORDER}
    rows = (db.session.query(ScoreReviewRequest.status,
                             db.func.count(ScoreReviewRequest.id))
            .filter(ScoreReviewRequest.applicant_id == applicant_id)
            .group_by(ScoreReviewRequest.status).all())
    for status, n in rows:
        if status in out:
            out[status] = n
    return out
