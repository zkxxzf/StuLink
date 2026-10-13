# -*- coding: utf-8 -*-
"""成绩访问权限引擎（只读）：把"谁能看哪个年级 / 哪个班级 / 哪个学科的成绩"收敛成一个对象。

需求（成绩分析 MCP 的细粒度授权）：
  R1 任课教师：仅当该教师**同时**任教某班级所在年级**及**该班级时，才可查看该班成绩；
               并且可查看其在该年级所教学科的成绩；否则拒绝访问。
  R2 班主任：在班型设置中担任班主任的账号，可查看本班成绩。
  R3 年级长及以上：可查看所在年级的成绩（校级 / 管理员 = 全校）。
  R4 显式授权（可选）：权限管理页"用户级数据范围"（UserDataScope）由管理员额外授予的
               年级 / 班级，与 R3 同级；该表存在记录时按网页端口径**优先且收窄**。

授权取并集（R1/R2/R3 可叠加：既任课又当班主任则两类范围都可见）；**只读**——
本引擎不提供任何写入口，调用方不得据此放行写入类操作。

数据来源（与网页端完全同源，不另建口径）：
  · TeacherSubjectLink(active)  → R1 任课映射 (grade, class_name, subject)
  · UserClassLink               → R2 班主任所辖班 (grade, class_name)
  · User.role / User.grade      → R3 年级长
  · PermissionGroup.scope_type  → R3 校级（school）
  · UserDataScope               → R4 显式授权（年级 / 年级+班级白名单）
"""
from . import bootstrap  # noqa: F401  确保项目根已进 sys.path


class ScoreScope:
    """某个账号的成绩可见范围（只读值对象）。

    三种粒度并存：
      · grades  —— 整年级授权（年级长及以上 / 校级 / 显式授权）→ 该年级全部班、全部学科；
      · classes —— 整班授权（班主任）→ 该班全部学科；
      · teach   —— 学科级授权（任课映射）→ 仅 (年级, 班级, 学科) 三元组。
    """

    __slots__ = ('user_id', 'username', 'role', 'full_school',
                 'grades', 'classes', 'teach', 'reasons')

    def __init__(self, user_id=None, username=None, role=None, full_school=False,
                 grades=None, classes=None, teach=None, reasons=None):
        self.user_id = user_id
        self.username = username
        self.role = role
        self.full_school = bool(full_school)
        self.grades = set(grades or ())          # {grade}
        self.classes = set(classes or ())        # {(grade, class_name)}
        self.teach = set(teach or ())            # {(grade, class_name, subject)}
        self.reasons = list(reasons or ())

    # ---------------- 年级 ----------------
    def visible_grades(self):
        """可见年级集合；None = 全校（不限年级）。"""
        if self.full_school:
            return None
        out = set(self.grades)
        out.update(g for g, _c in self.classes)
        out.update(g for g, _c, _s in self.teach)
        return out

    def can_view_grade(self, grade):
        """是否可访问该年级（任意成绩）。"""
        if self.full_school:
            return True
        return grade in self.visible_grades()

    def grade_subjects(self, grade):
        """该年级可见学科集合；None = 全部学科，空集 = 无可见学科。

        R1 的"可查看其在该年级所教学科的成绩"就落在这一层：
        任课教师拿到的是自己在该年级的任课科目并集。
        """
        if self.full_school or grade in self.grades:
            return None
        return {s for g, _c, s in self.teach if g == grade}

    # ---------------- 班级 ----------------
    def visible_classes(self, grade):
        """该年级可见班级名集合；None = 该年级全部班级。"""
        if self.full_school or grade in self.grades:
            return None
        out = {c for g, c in self.classes if g == grade}
        out.update(c for g, c, _s in self.teach if g == grade)
        return out

    def can_view_class(self, grade, class_name):
        """是否可访问该班成绩（R1 需年级+班级同时命中，R2 命中本班，R3 命中本年级）。"""
        if self.full_school or grade in self.grades:
            return True
        if (grade, class_name) in self.classes:
            return True
        return any(g == grade and c == class_name for g, c, _s in self.teach)

    def class_subjects(self, grade, class_name):
        """班内可见学科集合；None = 全部学科，空集 = 无可见学科。"""
        if (self.full_school or grade in self.grades
                or (grade, class_name) in self.classes):
            return None
        return {s for g, c, s in self.teach if g == grade and c == class_name}

    def can_view_subject(self, grade, class_name, subject):
        subjects = self.class_subjects(grade, class_name)
        return True if subjects is None else subject in subjects

    def class_total_visible(self, grade, class_name):
        """是否可见该班的"总分"口径（总分、班均分、排名等）。

        任课教师只被授予"所教学科"，总分不属于任何单科 → 不对其开放；
        班主任 / 年级长及以上 / 校级 / 管理员可见。
        """
        return (self.full_school or grade in self.grades
                or (grade, class_name) in self.classes)

    # ---------------- 展示 ----------------
    def describe(self):
        """给 AI / 用户核对的范围说明（不含任何成绩数据）。"""
        grades = self.visible_grades()
        return {
            'user': self.username,
            'role': self.role,
            'full_school': self.full_school,
            'visible_grades': sorted(grades) if grades is not None else '全部年级',
            'class_grants': sorted(f'{g} {c}' for g, c in self.classes),
            'subject_grants': sorted(f'{g} {c} {s}' for g, c, s in self.teach),
            'reasons': self.reasons,
            'note': ('成绩可见范围＝网页端该账号可见范围；本服务仅提供读取权限，'
                     '任课教师只能看到所教班级中自己所教学科的成绩。'),
        }


def resolve(user):
    """解析账号的成绩可见范围（R0 全校 → R4 显式授权 → R1/R2/R3 取并集）。"""
    if user is None:
        raise PermissionError('未通过身份验证：无法确定成绩可见范围')

    from app.models import UserClassLink
    from app.models.grades import TeacherSubjectLink
    from app.models.user_data_scope import UserDataScope
    from app.modules.grades.services import scope as scope_service

    # 2026-10-13 审计修复：网页端 scope.check_exam_visible / visible_grades 都会
    # 剔除「已毕业年级」（get_graduated_grades），本引擎此前漏了这一层，导致
    # 年级长 / 权限组 grade 范围 / 班级授权账号可通过 MCP 读到已毕业年级成绩，
    # 违反「AI 可见范围 = 网页端可见范围」。
    # 管理员与校级范围本就全校可见，不受毕业标记影响（与网页端口径一致）。
    from app.utils.helpers import get_graduated_grades
    graduated = set(get_graduated_grades() or ())

    def _strip_graduated(grade_set):
        """从年级集合中剔除已毕业年级；返回 (剩余集合, 被剔除集合)。"""
        if not grade_set or not graduated:
            return grade_set, set()
        dropped = grade_set & graduated
        return grade_set - graduated, dropped

    uid = getattr(user, 'id', None)
    username = getattr(user, 'username', None)
    role = getattr(user, 'role', None)

    # R0 管理员：全校
    if role == 'admin':
        return ScoreScope(uid, username, role, full_school=True,
                          reasons=['管理员：全校成绩'])

    scope_type, _scope_grade = scope_service.get_scope(user)
    user_grade = getattr(user, 'grade', None)
    # R0 校级（权限组数据范围为 school）：全校
    if scope_type == 'school':
        return ScoreScope(uid, username, role, full_school=True,
                          reasons=['校级数据范围：全校成绩'])

    # R4 用户级数据范围（权限管理页显式授权）：按网页端口径优先且收窄。
    #    某年级若存在"指定班级"记录，则该年级按班级白名单处理（与
    #    scope.get_user_scope_classes 一致）。
    rows = UserDataScope.query.filter_by(user_id=uid).all()
    if rows:
        grades, classes = set(), set()
        by_grade = {}
        for r in rows:
            by_grade.setdefault(r.grade, []).append(r.class_name)
        for grade, names in by_grade.items():
            whitelist = [n for n in names if n]
            if whitelist:
                classes.update((grade, n) for n in whitelist)
            else:
                grades.add(grade)
        # 已毕业年级不可见（与网页端口径一致）
        grades, dropped_g = _strip_graduated(grades)
        classes = {(g, c) for g, c in classes if g not in graduated}
        if not grades and not classes:
            raise PermissionError('无权查看任何成绩：授权年级均已毕业')
        reason = f'用户级数据范围授权（{len(rows)} 条）'
        if dropped_g:
            reason += f'；已剔除毕业年级 {sorted(dropped_g)}'
        return ScoreScope(uid, username, role, grades=grades, classes=classes,
                          reasons=[reason])

    grades, classes, teach, reasons = set(), set(), set(), []

    # R3 年级长及以上：本年级（校级/管理员已在上面返回）
    if role == 'grade_leader' and user_grade:
        grades.add(user_grade)
        reasons.append(f'年级长：{user_grade}')
    elif scope_type == 'grade' and user_grade:
        grades.add(user_grade)
        reasons.append(f'年级范围（权限组）：{user_grade}')

    # R2 班主任：所辖班级（UserClassLink 为准，一班最多 3 名班主任，无主副之分）
    links = UserClassLink.query.filter_by(user_id=uid).all()
    for link in links:
        classes.add((link.grade, link.class_name))
    if links:
        reasons.append(f'班主任：{len(links)} 个班')
    # 兼容网页端口径：homereoom_teacher 角色还会按 User.grade/class_name 兜底
    if role == 'homeroom_teacher' and getattr(user, 'grade', None) and getattr(user, 'class_name', None):
        classes.add((user.grade, user.class_name))

    # R1 任课教师：仅 (年级, 班级, 学科) 三元组
    tlinks = TeacherSubjectLink.query.filter_by(user_id=uid, active=True).all()
    for t in tlinks:
        teach.add((t.grade, t.class_name, t.subject))
    if tlinks:
        reasons.append(f'任课教师：{len(tlinks)} 条映射')

    # 2026-10-13 审计修复：统一剔除已毕业年级（三条授权路径都可能含毕业年级）
    grades, dropped_g = _strip_graduated(grades)
    if dropped_g:
        reasons.append(f'已剔除毕业年级：{sorted(dropped_g)}')
    if graduated:
        classes = {(g, c) for g, c in classes if g not in graduated}
        teach = {(g, c, s) for g, c, s in teach if g not in graduated}

    return ScoreScope(uid, username, role, grades=grades, classes=classes,
                      teach=teach, reasons=reasons)


def require_exam(user, exam):
    """考试级校验：考试所在年级不在可见范围内即拒绝。"""
    scope = resolve(user)
    if not scope.can_view_grade(exam.grade):
        raise PermissionError(
            f'无权查看 {exam.grade}「{exam.name}」的成绩：'
            '该年级不在您的成绩可见范围内（任课教师需任教该年级、'
            '班主任需带该年级的班、年级长及以上可看本年级）')
    return scope


def require_class(scope, grade, class_name):
    """班级级校验：班级不在可见范围内即拒绝。"""
    if not scope.can_view_class(grade, class_name):
        raise PermissionError(
            f'无权查看 {grade} {class_name} 的成绩：'
            '任课教师仅能查看同时任教年级与班级的成绩，班主任仅能查看本班成绩')


def require_student(scope, student):
    """学生级校验：按学生当前班级判定（成绩是班级口径的数据）。"""
    return require_class(scope, student.grade, student.class_name)
