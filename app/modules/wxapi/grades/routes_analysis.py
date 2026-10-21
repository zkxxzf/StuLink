# StuLink v1.19.50.53 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序成绩分析：对齐网页 /grades/analysis 的五个页签。

复用网页的 tab_service / compare_service，不复制计算逻辑；
页签可见性与数据范围按网页端同一口径收敛。
"""
from flask import g, request

from app.extensions import db
from app.models.grades import Exam, ExamScore, TOTAL_SUBJECT, TeacherSubjectLink
from app.modules.grades.services import compare_service
from app.modules.grades.services import scope as scope_service
from app.modules.grades.services import score_guard
from app.modules.grades.services import tab_service
from app.modules.grades.utils import numeric_classes
from app.modules.wxapi import bp
from app.modules.wxapi.core.guards import wx_perm_required
from app.modules.wxapi.core.response import wx_err, wx_ok

TABS = ('grade', 'class', 'subject', 'teacher', 'compare')
MAX_ROWS = 120


def _allowed_tabs(user):
    """与网页 analysis._guard_tab 同口径。"""
    if user.role == 'admin':
        return set(TABS)
    scope_type, _ = scope_service.get_scope(user)
    if (scope_type == 'school' or scope_service.has_user_scope(user)
            or user.role == 'grade_leader' or scope_type == 'grade'):
        return set(TABS)
    allowed = set()
    if user.has_role('homeroom_teacher'):
        allowed |= {'grade', 'class'}
    if user.has_role('teacher'):
        allowed |= {'grade', 'subject', 'teacher'}
    return allowed


def _exam(exam_id):
    exam = Exam.query.get(exam_id)
    if exam is None:
        raise LookupError('考试不存在')
    scope_service.check_exam_visible(g.wx_user, exam)
    return exam


def _subjects(exam_id):
    rows = (db.session.query(ExamScore.subject)
            .filter(ExamScore.exam_id == exam_id,
                    ExamScore.subject != '总分')
            .distinct().all())
    return sorted({r[0] for r in rows if r[0]})


def _classes(exam_id):
    """本场教学班（总分行快照 + 排除 不分班/已转出/离校）。

    与网页 analysis._classes_by_exam 同一口径；此前按全部成绩行去重，
    会把科目行上的旧班级和非教学班一并带进来。
    """
    rows = (db.session.query(ExamScore.class_name)
            .filter(ExamScore.exam_id == exam_id,
                    ExamScore.subject == TOTAL_SUBJECT)
            .distinct().all())
    return numeric_classes(sorted({r[0] for r in rows if r[0]}))


def _compact(data):
    """裁剪为前端表格需要的字段，行数设上限。"""
    tables = {}
    for key, t in (data.get('tables') or {}).items():
        rows = (t.get('rows') or [])[:MAX_ROWS]
        tables[key] = {
            'title': t.get('title') or key,
            'columns': [{k: c.get(k) for k in
                         ('key', 'label', 'type')}
                        for c in (t.get('columns') or [])],
            'rows': rows,
            'truncated': len(t.get('rows') or []) > MAX_ROWS,
            'link': t.get('link') or '',
        }
    meta = data.get('meta') or {}
    charts = {}
    for key, c in (data.get('charts') or {}).items():
        charts[key] = {k: c.get(k) for k in
                       ('type', 'title', 'xAxis', 'series', 'markLine')}
    return {
        'meta': {
            'classes': meta.get('classes') or [],
            'directions': meta.get('directions') or [],
            'layers': meta.get('layers') or [],
            'denom': meta.get('denom') or 'ref',
        },
        'tables': tables,
        'charts': charts,
    }


def _teacher_data(user, exam, subject, denom):
    scope_type, grade = scope_service.get_scope(user)
    if user.has_role('teacher'):
        links = scope_service.teacher_links(user)
        if not links:
            return tab_service._empty(exam)
        return tab_service.teacher_tab(
            exam.id, subject=subject, links=links,
            grade=exam.grade, denom=denom)
    if (user.role == 'admin' or scope_type == 'school'
            or scope_service.has_user_scope(user)):
        return tab_service.teacher_tab(
            exam.id, subject=subject, links=None,
            grade=exam.grade, denom=denom)
    if scope_type == 'grade':
        # 2026-10-19：管理年级可多选，取本场考试年级（已校验可见）
        links = TeacherSubjectLink.query.filter_by(
            grade=exam.grade, active=True).all()
        return tab_service.teacher_tab(
            exam.id, subject=subject, links=links,
            grade=exam.grade, denom=denom)
    raise PermissionError('无权查看教师分析')


@bp.route('/grades/analysis/<tab>')
# 2026-10-19 第十四轮：与网页分析页统一用 grades.view（原借 student_query）
@wx_perm_required('grades.view')     # 内含 wx_login_required，勿再叠一层（会双倍限流）
def wx_grades_analysis(tab):
    """query: exam_id / class_name / subject / classes / denom。"""
    if tab not in TABS:
        return wx_err('未知的分析页签', 404)
    user = g.wx_user
    if tab not in _allowed_tabs(user):
        return wx_err('当前账号无权查看该分析页签', 403)
    exam_id = request.args.get('exam_id', type=int)
    if not exam_id:
        return wx_err('缺少考试 id')
    try:
        exam = _exam(exam_id)
    except LookupError as exc:
        return wx_err(str(exc), 404)
    except PermissionError:
        return wx_err('该考试不在你的可见范围内', 403)

    denom = (request.args.get('denom') or 'ref').strip()
    denom = denom if denom in ('ref', 'enrolled') else 'ref'
    direction = (request.args.get('direction') or '').strip()
    selection = (request.args.get('selection') or '').strip()

    try:
        if tab == 'grade':
            data = tab_service.grade_tab(
                exam_id, direction, selection, denom)
        elif tab == 'class':
            class_name = (request.args.get('class_name') or '').strip()
            if not class_name:
                return wx_err('请先选择班级')
            guard = score_guard.resolve(user)
            if not guard.class_total_visible(exam.grade, class_name):
                return wx_err('无权查看该班逐生总分', 403)
            data = tab_service.class_tab(
                exam_id, class_name, direction, selection, denom)
        elif tab == 'subject':
            subjects = _subjects(exam_id)
            subject = (request.args.get('subject') or '').strip()
            subject = subject or (subjects[0] if subjects else '')
            if not subject:
                return wx_err('该考试暂无学科数据', 404)
            data = tab_service.subject_tab(
                exam_id, subject, direction, selection, denom)
        elif tab == 'teacher':
            subject = (request.args.get('subject') or '').strip() or None
            data = _teacher_data(user, exam, subject, denom)
        else:
            raw = (request.args.get('classes') or '').strip()
            classes = [c.strip() for c in raw.split(',') if c.strip()]
            limited = scope_service.visible_classes(user)
            if limited is not None:
                allowed = {c for gr, c in limited if gr == exam.grade}
                classes = [c for c in classes if c in allowed]
            classes = classes[:20]
            if len(classes) < 2:
                return wx_err('请至少选择 2 个班级进行对比')
            data = compare_service.compare_tab(
                exam_id, classes, direction, denom)
    except PermissionError:
        return wx_err('当前账号无权查看该分析数据', 403)

    payload = _compact(data)
    classes = payload['meta'].get('classes') or _classes(exam_id)
    limited = scope_service.visible_classes(user)
    if limited is not None:
        allowed = {c for gr, c in limited if gr == exam.grade}
        classes = [c for c in classes if c in allowed]
    from app.modules.grades.routes.analysis import _combos_by_exam
    payload.update({
        'tab': tab,
        'exam': {
            'id': exam.id,
            'name': exam.name,
            'date': exam.exam_date.strftime('%Y-%m-%d')
            if exam.exam_date else '',
            'grade': exam.grade or '',
        },
        'options': {
            'classes': classes,
            'subjects': _subjects(exam_id),
            # 与网页分析页同一份：本场方向 + 出现过的选科组合
            'directions': payload['meta'].get('directions') or [],
            'combos': _combos_by_exam().get(str(exam.id)) or [],
        },
        'denom': denom,
        'direction': direction,
        'selection': selection,
    })
    return wx_ok(payload)
