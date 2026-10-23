# StuLink v1.19.69.64 2026-10-20
# 限时练公共服务：可见范围 / 权限 / 分层模板 / 名次重算
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""限时练公共服务（只做业务规则，不碰模板渲染）。

权限口径（2026-10-21 用户定案）：
- **查看**：`grades.view` + 该年级在可见年级范围内；逐生数据再按
  `score_guard` 收口（任课教师＝所教科目，班主任＝本班全科）。
- **导入**：`grades.import` + 年级可见；无管理权的账号只能导入本人任教学科
  （`importable_subjects`）——此前只校验 `grades.view`，只读账号也能覆盖成绩。
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


def importable_subjects(grade, user=None):
    """该账号在该年级可导入的科目集合；``None`` ＝ 不限学科。

    2026-10-21 权限收口：限时练导入此前只校验 `grades.view`（读权限），
    任何能看该年级的账号都能靠 POST 覆盖整科成绩。现在路由层要求
    `grades.import`，本函数再按「年级 × 学科」收一层：
      · 年级长 / 管理岗（`can_manage`）→ 不限学科；
      · 只有导入权但无管理权的账号 → 仅本人任教的年级学科。
    """
    u = user or current_user
    if can_manage(u):
        return None
    from app.modules.grades.services import score_guard
    try:
        scope = score_guard.resolve(u)
    except PermissionError:
        return set()
    if scope.full_school or grade in scope.grades:
        return None
    return {s for g, s in scope.teach if g == grade}


def assert_subject_importable(grade, subject, user=None):
    """导入 / AI 解析前的「年级 + 学科」写校验（越界 403）"""
    allowed = importable_subjects(grade, user)
    if allowed is not None and subject not in allowed:
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


def renumber_grade(grade):
    """把某一届的场次统一改名为 `2025级20260905限时练No.17`。

    2026-10-21 用户口径（取代 10-20 的「2025级 No.1」）：
    **届 + 8 位日期 + 限时练 + No.序号**。年级学期仍只作列表标签
    （`TimedExam.semester_label()`），不进名字。
    序号按届内日期升序连续（同日按 id），跨学期不重置。
    不 commit，由调用方控制事务。
    """
    from app.modules.grades.services.timed_naming import exam_name
    rows = TimedExam.query.filter_by(grade=grade).all()
    rows.sort(key=lambda e: (e.exam_date, e.id))
    changed = []
    for idx, exam in enumerate(rows, start=1):
        name = exam_name(grade, exam.exam_date, idx)
        if (exam.name or '') != name:
            exam.name = name
            changed.append(exam.id)
    return changed


def renumber_all():
    """全库重排场次名（按届），返回改动条数。"""
    grades = {e.grade for e in TimedExam.query.all() if e.grade}
    n = 0
    for grade in sorted(grades):
        n += len(renumber_grade(grade))
    return n


def current_natural_semester():
    """当前**自然学期**（学年 + 上/下），如 `2026-2027上`。

    列表筛选按它分档：同一档里三个年级的场次一起展示，过春节自动变「下」。
    """
    from datetime import date
    from app.models.grades import semester_of, term_of_date
    today = date.today()
    half = '上' if semester_of(today) == '上学期' else '下'
    return f'{term_of_date(today)}{half}'


def natural_semester_label(key):
    """自然学期键 → 展示名（`2026-2027上` → `2026-2027 上学期`）"""
    key = (key or '').strip()
    if len(key) < 10:
        return key
    return f'{key[:9]} {"上学期" if key.endswith("上") else "下学期"}'


def report_of(exam):
    """读取导入预检草稿（无草稿 / 解析失败返回 None）"""
    import json
    if not exam.import_draft:
        return None
    try:
        return json.loads(exam.import_draft)
    except (TypeError, ValueError):
        return None
