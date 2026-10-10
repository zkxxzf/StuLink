"""Shared visibility rules for academic timetable and inspection data."""

from sqlalchemy import and_, false, or_


def visible_academic_grades(user):
    """Return the grades visible to a user, or ``None`` for an administrator.

    The grades module owns the permission-group and per-user scope rules. Reuse
    its complete resolver here so academic pages behave consistently with the
    rest of the application. An empty set means the account has no visible
    grades.
    """
    if not user or getattr(user, 'role', None) == 'admin':
        return None

    from app.modules.grades.services.scope import visible_grades

    return set(visible_grades(user) or [])


def visible_academic_class_scope(user):
    """Return per-grade class restrictions, or ``None`` when classes are open.

    A missing grade in a non-None mapping means that grade has no visible
    classes. A value of ``None`` means all classes in that grade are visible.
    """
    if not user or getattr(user, 'role', None) == 'admin':
        return None

    from app.modules.grades.services.scope import (
        SCOPE_GRADE, SCOPE_SCHOOL, get_scope, user_grade_scope,
    )
    from app.models import UserClassLink, UserDataScope
    from app.models.grades import TeacherSubjectLink

    grades = visible_academic_grades(user)
    if grades is None:
        return None

    user_scope = user_grade_scope(user)
    if user_scope is not None:
        rows = UserDataScope.query.filter_by(user_id=user.id).all()
        by_grade = {}
        for row in rows:
            by_grade.setdefault(row.grade, []).append(row.class_name or '')
        return {
            grade: (None if '' in by_grade.get(grade, [])
                    else {name for name in by_grade.get(grade, []) if name})
            for grade in grades
        }

    scope, _grade = get_scope(user)
    if scope in (SCOPE_SCHOOL, SCOPE_GRADE):
        return None

    by_grade = {grade: set() for grade in grades}
    for link in UserClassLink.query.filter_by(user_id=user.id).all():
        if link.grade in by_grade:
            by_grade[link.grade].add(link.class_name)
    for link in TeacherSubjectLink.query.filter_by(user_id=user.id, active=True).all():
        if link.grade not in by_grade:
            continue
        class_name = (getattr(link, 'class_name', None) or '').strip()
        if not class_name:
            by_grade[link.grade] = None
        elif by_grade[link.grade] is not None:
            by_grade[link.grade].add(class_name)
    return by_grade


def academic_class_is_visible(user, grade, class_name):
    """Whether a particular class is within the user's academic data scope."""
    return academic_class_authorizer(user)(grade, class_name)


def academic_class_authorizer(user):
    """Build a reusable class-scope predicate without per-row database reads."""
    grades = visible_academic_grades(user)
    scope = visible_academic_class_scope(user)

    def allowed(grade, class_name):
        if grades is not None and (grade or '') not in grades:
            return False
        if scope is None:
            return True
        classes = scope.get(grade or '', set())
        return classes is None or (class_name or '') in classes

    return allowed


def academic_has_class_restrictions(user):
    """Whether the account is limited to selected classes within a grade."""
    grades = visible_academic_grades(user)
    if grades is None:
        return False
    if not grades:
        return True
    scope = visible_academic_class_scope(user)
    return scope is not None and any(scope.get(grade, set()) is not None
                                     for grade in grades)


def apply_academic_scope(query, user, model):
    """Apply the user's grade and, where applicable, class scope to a query."""
    grades = visible_academic_grades(user)
    if grades is None:
        return query
    query = query.filter(model.grade.in_(grades))
    if not hasattr(model, 'class_name'):
        return query

    class_scope = visible_academic_class_scope(user)
    if class_scope is None:
        return query
    clauses = []
    for grade in grades:
        classes = class_scope.get(grade, set())
        if classes is None:
            clauses.append(model.grade == grade)
        elif classes:
            clauses.append(and_(model.grade == grade,
                                model.class_name.in_(classes)))
    return query.filter(or_(*clauses)) if clauses else query.filter(false())


def swap_entry_authorizer(user):
    """Build a server-side authorizer for swap requests.

    Timetable managers may request any course. Other users may request courses
    they teach or courses in a class explicitly linked to their account.

    2026-10-09：**本人任教的课一律允许申请** —— 个人调课动的本来就是自己的课，
    教师往往跨多个班任教，而"任教班级数据范围"（UserClassLink / 任课映射）未必配齐，
    此前会因为班级不在数据范围里而连自己的课都提交不了。故先判"是不是本人任教的课"，
    命中直接放行；其余情况仍按班级数据范围 + 班级关联从严把关。

    注意：放宽只作用于"能否提交调课申请"，不改变课表/学生数据的可见范围。
    """
    if not user:
        return lambda entry: False
    if getattr(user, 'role', None) == 'admin' or user.has_perm('academic.timetable'):
        return lambda entry: True

    class_visible = academic_class_authorizer(user)
    from app.models import UserClassLink
    from app.models.academic import Teacher

    class_links = {
        (grade, class_name)
        for grade, class_name in UserClassLink.query.filter_by(user_id=user.id)
        .with_entities(UserClassLink.grade, UserClassLink.class_name).all()
    }
    teacher = Teacher.query.filter_by(user_id=user.id).first()
    if teacher is None:
        # 账号还没绑定教师名单时，兜底用"唯一同名"补关联（与查课/调课的申请人解析一致）
        try:
            from app.modules.academic.services import teacher_service
            teacher = teacher_service.teacher_of_user(user)
        except Exception:  # noqa: BLE001  绑定失败就按无教师处理，退回班级范围判定
            teacher = None
    teacher_uid = teacher.teacher_uid if teacher else None

    def allowed(entry):
        # ① 本人任教的课 → 直接放行（不看班级数据范围）
        if teacher_uid and entry.teacher_uid == teacher_uid:
            return True
        # ② 其它情况（代班级申请等）仍受数据范围约束
        if not class_visible(entry.grade, entry.class_name):
            return False
        return (entry.grade, entry.class_name) in class_links

    return allowed
