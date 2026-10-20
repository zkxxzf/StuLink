# StuLink v1.19.49.49 2026-10-20
# 限时练公共服务：可见范围 / 权限 / 分层模板 / 名次重算
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""限时练公共服务（只做业务规则，不碰模板渲染）。

权限口径（2026-10-20 用户明确）：
- **查看 / 导入**：所有能看到该年级的老师（`grades.view` + 年级可见性）；
  普通任课老师即可执行单科成绩导入，不要求教学管理权限。
- **改分 / 删成绩 / 删场次 / 改分层模板**：仅年级长与管理员
  （admin、`grades.edit`、数据范围为年级/全校的身份）。
"""
from flask import abort
from flask_login import current_user

from app.extensions import db
from app.models.timed import (BUILTIN_TIMED_TEMPLATE, DEFAULT_TIMED_LAYERS,
                              TimedBandTemplate, TimedExam, TimedScore)
from app.modules.grades.services import scope as scope_service


def can_manage(user=None):
    """是否具备限时练的改分 / 删除 / 模板维护权限。

    口径（2026-10-20 用户明确）：仅年级长与管理员。
    实现上按现有权限键收口，**不再用数据范围兜底** —— 否则任何
    `scope=school/grade` 但只有只读权限的组（校长、教务员等）会意外
    获得改分权，与「只读身份不改分」的既有安全口径冲突。
    """
    u = user or current_user
    if getattr(u, 'role', '') == 'admin':
        return True
    return u.has_perm('grades.edit') or u.has_perm('grades.score_edit')


def visible_grades_of(user=None):
    """限时练可见年级（与成绩模块其它页同口径，fail-closed）"""
    try:
        return [g for g in (scope_service.visible_grades(user or current_user)
                            or []) if g]
    except Exception:                                   # noqa: BLE001
        return []


def query_visible(user=None):
    """限时练场次查询（按可见年级收敛；空范围返回空集）"""
    grades = visible_grades_of(user)
    if not grades:
        return TimedExam.query.filter(False)
    return TimedExam.query.filter(TimedExam.grade.in_(grades))


def assert_visible(timed_id, user=None):
    """取场次并校验年级范围：不存在 → 404，越界 → 403"""
    exam = db.session.get(TimedExam, int(timed_id))
    if not exam:
        abort(404)
    if (user or current_user).role != 'admin':
        if exam.grade not in visible_grades_of(user):
            abort(403)
    return exam


def assert_grade_writable(grade):
    """导入 / 改分前的年级校验（越界 403）"""
    if current_user.role == 'admin':
        return
    if grade not in visible_grades_of(current_user):
        abort(403)


def assert_grade_readable(grade):
    """只读页面的年级筛选校验（与写校验分开命名，避免语义反转）"""
    if current_user.role == 'admin':
        return
    if grade not in visible_grades_of(current_user):
        abort(403)


# ==================== 分层模板 ====================

def ensure_builtin_templates():
    """幂等创建内置分层模板（首次进入限时练页面时调用）。

    并发安全：两个请求同时首访会撞 name 唯一约束 —— 捕获后回查即可，
    不能让只读页面的渲染因建模板失败而 500。
    """
    from sqlalchemy.exc import IntegrityError
    exists = TimedBandTemplate.query.filter_by(name=BUILTIN_TIMED_TEMPLATE).first()
    if exists:
        return exists
    tpl = TimedBandTemplate(name=BUILTIN_TIMED_TEMPLATE, is_builtin=True,
                            sort_order=0)
    tpl.set_layers([dict(x) for x in DEFAULT_TIMED_LAYERS])
    db.session.add(tpl)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return TimedBandTemplate.query.filter_by(
            name=BUILTIN_TIMED_TEMPLATE).first()
    return tpl


def current_template(exam):
    """本场当前使用的分层模板。

    2026-10-20 用户口径：限时练固定用内置「名次分层」模板，
    不再提供选模板 / 自定义模板（模板维护接口与弹窗已一并删除）。
    """
    if exam.template_id:
        tpl = db.session.get(TimedBandTemplate, int(exam.template_id))
        if tpl:
            return tpl
    return ensure_builtin_templates()


# ==================== 科目 / 名次 ====================

def subjects_of(exam_id):
    """本场已导入的科目（去重排序，供分析页切换）"""
    rows = (db.session.query(TimedScore.subject)
            .filter(TimedScore.exam_id == int(exam_id))
            .distinct().all())
    return sorted({r[0] for r in rows if r[0]})


def recalc_subject_ranks(exam_id, subject):
    """重算本场某科全年级名次：降序、同分同名次、后延跳号（1,1,3）

    不 commit，由调用方控制事务。
    """
    rows = (TimedScore.query
            .filter_by(exam_id=int(exam_id), subject=subject)
            .order_by(TimedScore.score.desc(), TimedScore.id.asc()).all())
    prev_score, prev_rank = None, 0
    for idx, row in enumerate(rows, start=1):
        rank = prev_rank if row.score == prev_score else idx
        row.rank_grade = rank
        prev_score, prev_rank = row.score, rank


def recalc_all(exam_id):
    """重算本场全部科目的名次（改分 / 删除后调用）"""
    for subject in subjects_of(exam_id):
        recalc_subject_ranks(exam_id, subject)


def delete_scores_of(exam_id, subject=None):
    """删除本场成绩（整场或某一科），不 commit"""
    q = TimedScore.query.filter_by(exam_id=int(exam_id))
    if subject:
        q = q.filter_by(subject=subject)
    return q.delete(synchronize_session=False)


def student_counts(exam_id):
    """{科目: 人数}（列表页展示用）"""
    rows = (db.session.query(TimedScore.subject, db.func.count(TimedScore.id))
            .filter(TimedScore.exam_id == int(exam_id))
            .group_by(TimedScore.subject).all())
    return {s: int(n) for s, n in rows}


def student_total(exam_id):
    """本场**去重学生数**。

    注意：不能用成绩行数（一人 9 科就是 9 行，曾把 2946 当成人数）；
    一场里各科人数可能略有差异，这里取"本场出现过的学生总数"。
    """
    n = (db.session.query(
        db.func.count(db.func.distinct(TimedScore.student_no)))
        .filter(TimedScore.exam_id == int(exam_id)).scalar())
    return int(n or 0)


def current_semesters(grades):
    """当前学期的「年级学期」标签集合（按今天与各年级入学年推算）"""
    from datetime import date
    from app.models.grades import exam_semester
    today = date.today()
    return {exam_semester(g, today) for g in grades if g}


def report_of(exam):
    """读取导入预检草稿（无草稿 / 解析失败返回 None）"""
    import json
    if not exam.import_draft:
        return None
    try:
        return json.loads(exam.import_draft)
    except (TypeError, ValueError):
        return None
