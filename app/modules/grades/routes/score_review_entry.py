# StuLink v1.19.49.41 2026-10-19
# 成绩复核录入：搜学号/姓名 → 展示全科成绩 → 直接改分 → 批量提交
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""成绩复核（申请调分）录入接口（用户口径 2026-10-19）。

用户原话：「直接一个学号，或者姓名然后展示信息，展示全科成绩，成绩可以
先修改，然后提交，申请复核。」

流程：
  1. `GET /reviews/api/student`：按学号/姓名模糊搜学生（只在本人数据范围内），
     返回该生本场考试的**全科成绩**（无权限的科目只回名字，前端提示）；
  2. `POST /reviews/submit`：一次可提交多科 —— 前端把「修改后分数」打包成
     JSON 放进 payload，这里换算成 `delta = 新分 - 当前分` 落库。
     模型仍只存增减分，审批链与「批准并应用」完全不变。

安全：搜索与提交均走 `score_guard.ScoreScope`（与成绩分析页 / MCP 同一份
口径）——越界学生在搜索阶段就不返回，提交时再校验一次（403）。
"""
import json
import math
from datetime import datetime

from flask import request, jsonify, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from app.extensions import db
from app.models.grades import (Exam, ExamScore, ScoreReviewRequest,
                               TOTAL_SUBJECT, SUBJECTS)
from app.modules.grades import bp
from app.modules.grades.services import (scope as scope_service, score_guard)
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation

_MAX_DELTA = 100.0      # 单次增减幅度上限（防手误多打一个 0）
_MAX_STUDENTS = 20      # 搜索结果最多返回的学生数


# ==================== 内部工具 ====================

def _exam_or_abort(exam_id):
    """取考试并校验「本人可见」；不存在 404、越界 403。"""
    exam = db.session.get(Exam, exam_id)
    if exam is None:
        abort(404)
    try:
        scope_service.check_exam_visible(current_user, exam)
    except PermissionError:
        abort(403)
    return exam


def _match_rank(stu, kw):
    """精确命中（学号/姓名）排前，其余保持库内顺序。"""
    if stu['student_no'] == kw:
        return 0
    if (stu['student_name'] or '') == kw:
        return 1
    return 2


def _search_students(exam, kw, sc):
    """按学号/姓名模糊找学生，去重 + 数据范围过滤，精确命中排前。

    用 `distinct` 让 limit 按**学生**计（此前按成绩行 400 行 ≈ 44 人，
    搜单姓时只覆盖前几个班，后面班的学生搜不到）。
    """
    q = (db.session.query(ExamScore.student_no, ExamScore.student_name,
                          ExamScore.class_name, ExamScore.grade)
         .filter(ExamScore.exam_id == exam.id,
                 ExamScore.subject != TOTAL_SUBJECT,
                 db.or_(ExamScore.student_no.like(f'%{kw}%'),
                        ExamScore.student_name.like(f'%{kw}%'))))
    # 2026-10-20 审计修复（并入原 reviews_students_api 的修复）：范围条件下推到
    # SQL 再 limit。原实现先 limit 再在 Python 里按 can_view_class 过滤，而排序是
    # (class_name, student_no) —— 前 N 个班会把名额占满，班级名靠后的班主任 /
    # 任课教师搜不到本班学生。口径与 score_guard 一致：任教科目（该年级全部班级）
    # 或班级白名单，两者取或。
    _grade = exam.grade
    if not (sc.full_school or _grade in sc.grades):
        _teach_subjects = {s for g, s in sc.teach if g == _grade}
        _granted_classes = {c for g, c in sc.classes if g == _grade}
        if not _teach_subjects and not _granted_classes:
            return []                      # 无任何可见范围 → fail-closed
        _conds = []
        if _teach_subjects:
            _conds.append(ExamScore.subject.in_(list(_teach_subjects)))
        if _granted_classes:
            _conds.append(ExamScore.class_name.in_(list(_granted_classes)))
        q = q.filter(db.or_(*_conds))
    rows = (q.distinct()
            .order_by(ExamScore.class_name, ExamScore.student_no)
            .limit(_MAX_STUDENTS * 3).all())
    out = []
    for no, name, cn, grade in rows:
        g = grade or exam.grade
        if not sc.can_view_class(g, cn):
            continue
        out.append({'student_no': no, 'student_name': name,
                    'class_name': cn, 'grade': g,
                    'class_name_full': f'{g}{cn}'})
        if len(out) >= _MAX_STUDENTS:
            break
    out.sort(key=lambda s: _match_rank(s, kw))
    return out


def _total_of(subs, sc, stu):
    """总分行分数：仅对「可见总分口径」的人返回（任课教师看不到总分）。"""
    row = subs.get(TOTAL_SUBJECT)
    if row is None or not sc.class_total_visible(stu['grade'], stu['class_name']):
        return None
    return row.score


def _fill_scores(exam, students, sc):
    """给每个学生挂上**全科**成绩：可申请科目 + 无权限科目名 + 总分。"""
    nos = [s['student_no'] for s in students]
    if not nos:
        return students
    rows = (ExamScore.query
            .filter(ExamScore.exam_id == exam.id,
                    ExamScore.student_no.in_(nos))
            .all())
    marks = exam.full_marks() or {}
    by_no = {}
    for r in rows:
        by_no.setdefault(r.student_no, {})[r.subject] = r
    for stu in students:
        subs = by_no.get(stu['student_no'], {})
        items, hidden = [], []
        for subj in SUBJECTS:
            row = subs.get(subj)
            cls = row.class_name if row is not None else stu['class_name']
            g = (row.grade if row is not None else stu['grade']) or stu['grade']
            if not sc.can_view_subject(g, cls, subj):
                hidden.append(subj)
                continue
            items.append({'subject': subj,
                          'score': row.score if row is not None else None,
                          'full': marks.get(subj, 100),
                          'missing': row is None})
        stu['subjects'] = items
        stu['hidden_subjects'] = hidden
        stu['total'] = _total_of(subs, sc, stu)
    return students


def _payload_items():
    """读取前端打包的改分明细（JSON 数组）。"""
    raw = (request.form.get('payload') or '').strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [it for it in data if isinstance(it, dict)]


def _validate_one(exam, sc, by_subj, marks, item):
    """校验单科改分，返回 (条目, 错误信息)；两者只可能有一个非空。"""
    subj = (item.get('subject') or '').strip()
    if subj not in SUBJECTS:
        return None, f'未知学科：{subj or "（空）"}'
    row = by_subj.get(subj)
    if row is None:
        return None, f'该场考试没有「{subj}」的成绩行，无法提交'
    grade = row.grade or exam.grade
    if not sc.can_view_class(grade, row.class_name):
        abort(403)
    if not sc.can_view_subject(grade, row.class_name, subj):
        abort(403)
    try:
        new = round(float(item.get('new_score')), 1)
    except (TypeError, ValueError):
        return None, f'「{subj}」的修改后分数格式不正确'
    # 2026-10-20 审计修复（并入原 reviews_students_api/review_new 的修复）：
    # float('nan')/inf 能解析成功，而 nan<0 / nan>limit / nan==0 / abs(nan)>上限 全为假，
    # 会绕过下面所有校验把 NaN 写进成绩单。显式拒绝非有限值。
    if not math.isfinite(new):
        return None, f'「{subj}」的修改后分数格式不正确'
    limit = float(marks.get(subj, 100))
    if new < 0 or new > limit:
        return None, f'「{subj}」的分数 {new:g} 超出 0~{limit:g}'
    delta = round(new - (row.score or 0), 1)
    if delta == 0:
        return None, f'「{subj}」的分数没有变化'
    if abs(delta) > _MAX_DELTA:
        return None, f'「{subj}」单次增减不能超过 {_MAX_DELTA:g} 分'
    return {'row': row, 'subject': subj, 'delta': delta,
            'question_no': (item.get('question_no') or '').strip()[:40]}, None


def _build_items(exam, sc, student_no, items):
    """批量校验改分；任一科不合法就整批拒绝（不落半批）。"""
    rows = (ExamScore.query
            .filter(ExamScore.exam_id == exam.id,
                    ExamScore.student_no == student_no,
                    ExamScore.subject != TOTAL_SUBJECT)
            .all())
    if not rows:
        return None, '该场考试没有这名学生的成绩'
    by_subj = {r.subject: r for r in rows}
    marks = exam.full_marks() or {}
    out, seen = [], set()
    for item in items[:len(SUBJECTS)]:
        subj = (item.get('subject') or '').strip()
        if subj in seen:
            return None, f'「{subj}」重复提交'
        one, err = _validate_one(exam, sc, by_subj, marks, item)
        if err:
            return None, err
        if one:
            seen.add(subj)
            out.append(one)
    if not out:
        return None, '请至少修改一科成绩'
    return out, None


def _has_pending(exam_id, student_no, subject, question_no):
    """本人是否已有同考试 + 同学生 + 同科 + 同题号的待审单。"""
    return (ScoreReviewRequest.query
            .filter_by(exam_id=exam_id, student_no=student_no,
                       subject=subject, question_no=question_no,
                       status='pending', applicant_id=current_user.id)
            .first()) is not None


def _save_items(exam, student_no, reason, items):
    """写入复核单（同一批一次 commit）并记一条汇总操作日志。"""
    first = items[0]['row']
    for it in items:
        row = it['row']
        db.session.add(ScoreReviewRequest(
            exam_id=exam.id, exam_name=exam.name,
            grade=row.grade or exam.grade, class_name=row.class_name,
            student_no=student_no, student_name=row.student_name,
            subject=it['subject'], question_no=it['question_no'],
            delta=it['delta'], reason=reason, status='pending',
            applicant_id=current_user.id,
            applicant_name=current_user.real_name or current_user.username,
            created_at=datetime.now()))
    db.session.commit()
    detail = '、'.join(f"{it['subject']} {it['delta']:+g}" for it in items)
    log_operation(current_user, '提交', '成绩复核', exam.id,
                  f'{first.student_name or student_no}：{detail}',
                  module='grades')


# ==================== 路由 ====================

@bp.route('/reviews/api/student')
@login_required
@perm_required('grades.view')
def reviews_student_api():
    """按考试 + 学号/姓名取学生及其全科成绩（提交页实时搜索用）。"""
    exam_id = request.args.get('exam_id', type=int)
    kw = (request.args.get('q') or '').strip()
    if not exam_id:
        return jsonify(success=False, message='缺少 exam_id'), 400
    exam = _exam_or_abort(exam_id)
    if not kw:
        return jsonify(success=True, data=[])
    sc = score_guard.resolve(current_user)
    students = _search_students(exam, kw, sc)
    return jsonify(success=True, grade=exam.grade, limit=_MAX_STUDENTS,
                   data=_fill_scores(exam, students, sc))


@bp.route('/reviews/submit', methods=['POST'])
@login_required
@perm_required('grades.view')
def review_submit():
    """提交复核申请（同一学生可一次提交多科）。"""
    back = redirect(url_for('grades.reviews_page'))
    exam_id = request.form.get('exam_id', type=int)
    student_no = (request.form.get('student_no') or '').strip()
    reason = (request.form.get('reason') or '').strip()[:500]
    if not exam_id or not student_no:
        flash('请先搜索并选择学生', 'danger')
        return back
    if not reason:
        flash('请填写申请理由（便于审批人核对）', 'danger')
        return back
    exam = _exam_or_abort(exam_id)
    sc = score_guard.resolve(current_user)
    items, err = _build_items(exam, sc, student_no, _payload_items())
    if err:
        flash(err, 'danger')
        return back
    dup = [it['subject'] for it in items
           if _has_pending(exam.id, student_no, it['subject'],
                           it['question_no'])]
    if dup:
        flash('以下科目已有同题号的待审核申请：' + '、'.join(dup),
              'warning')
        return back
    _save_items(exam, student_no, reason, items)
    flash(f'已提交 {len(items)} 条复核申请，等待成绩管理员审核', 'success')
    return back
