# StuLink v1.19.49.35 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""成绩复核审批（网页与小程序共用）。

`audit_review()` 把原来内联在 `/grades/reviews/<id>/review` 里的审批逻辑搬出来：
- 驳回：改状态 + 留痕；
- 仅批准：改状态 + 留痕，不写成绩单；
- 批准并应用：改 score（原始分同步）→ 写 `ScoreChangeLog` → 重算该生总分 →
  重算本场排名 → 清缓存 → 操作日志。

满分校验失败时**不写库**并返回 (False, 原因)，由调用方提示；未提交的
reviewer 字段随请求结束回滚，不会污染复核单。
"""
from datetime import datetime

from app.extensions import db
from app.models.grades import Exam, ExamScore, ScoreChangeLog
from app.modules.grades.services import ranking, store_service
from app.modules.grades.utils import invalidate_exam_cache
from app.utils.helpers import log_operation


def _reject(r, user, note):
    r.status = 'rejected'
    r.review_note = note
    db.session.commit()
    log_operation(user, '驳回', '成绩复核', r.exam_id,
                  '复核单 #%d：%s' % (r.id, note or '无意见'), module='grades')
    return True, '已驳回复核单 #%d' % r.id


def _approve_only(r, user, note):
    r.status = 'approved'
    r.review_note = note
    db.session.commit()
    log_operation(user, '批准', '成绩复核', r.exam_id,
                  '复核单 #%d（仅批准，未应用改分）' % r.id, module='grades')
    return True, '已批准复核单 #%d（未写入成绩单；如需改分请用「批准并应用」）' % r.id


def _apply(r, user, note):
    exam = db.session.get(Exam, r.exam_id)
    if exam is None:
        return False, '该申请对应的考试已不存在，无法应用改分'
    srow = (ExamScore.query
            .filter_by(exam_id=r.exam_id, student_no=r.student_no,
                       subject=r.subject)
            .first())
    if srow is None:
        return False, '该场考试没有这名学生该科的成绩，请先导入成绩后再应用'
    limit = float((exam.full_marks() or {}).get(r.subject, 100))
    old_score, old_raw = srow.score, srow.raw_score
    new_score = round((old_score or 0) + r.delta, 1)
    if new_score < 0 or new_score > limit + 0.5:
        return False, ('应用后分数 %g 超出 0~%g 的范围，请核对增减分数'
                       '（未写库，可先驳回或修改后重提）'
                       % (new_score, limit))
    srow.score = new_score
    if old_raw is not None:
        srow.raw_score = round(old_raw + r.delta, 1)
    db.session.add(ScoreChangeLog(
        exam_id=exam.id, exam_name=exam.name, score_id=srow.id,
        student_no=srow.student_no, student_name=srow.student_name,
        class_name=srow.class_name, subject=srow.subject, action='update',
        old_score=old_score, new_score=srow.score,
        old_raw=old_raw, new_raw=srow.raw_score,
        operator_id=user.id,
        operator_name=user.real_name or user.username,
        note='成绩复核单 #%d' % r.id))
    store_service.refresh_student_total(exam, srow.student_no)
    n = ranking.recalc_exam(exam.id)
    if exam.status != 'imported':
        exam.status = 'imported'
    r.applied = True
    r.applied_at = datetime.now()
    r.status = 'approved'
    r.review_note = note
    db.session.commit()
    invalidate_exam_cache(exam.id)
    log_operation(
        user, '批准并应用', '成绩复核', r.exam_id,
        '复核单 #%d：%s %s %+g（新分 %s，排名重算 %d 条）'
        % (r.id, srow.student_name or srow.student_no, r.subject,
           r.delta, srow.score, n), module='grades')
    return True, ('已批准并将 %+g 分应用到成绩单'
                  '（总分与排名已重算，已写入分数修改记录）' % r.delta)


def audit_review(r, user, action, note='', apply_now=False):
    """审批一张复核单，返回 (ok, message)。

    r 必须已通过调用方的数据范围校验（考试可见 + 班级可写）。
    """
    if r.status != 'pending':
        return False, '该申请已处理过，不能重复审核'
    if action not in ('approve', 'reject'):
        return False, '未知的审核动作'
    note = (note or '')[:200]
    r.reviewer_id = user.id
    r.reviewer_name = user.real_name or user.username
    r.reviewed_at = datetime.now()
    if action == 'reject':
        return _reject(r, user, note)
    if not apply_now:
        return _approve_only(r, user, note)
    ok, msg = _apply(r, user, note)
    if not ok:
        db.session.rollback()
        return False, msg
    return True, msg
