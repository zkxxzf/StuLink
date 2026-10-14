# StuLink v1.19.47.30 2026-10-13
# 成绩访问权限引擎（只读）：Web 端与 MCP 服务共用同一份口径
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""成绩访问权限引擎（只读）：把"谁能看哪个年级 / 哪个班级 / 哪个学科的成绩"收敛成一个对象。

需求（成绩分析 MCP 的细粒度授权）：
  R1 任课教师：**2026-10-15 用户口径：教哪个年级的哪一科，该年级该科的
               全部学生成绩都对他开放**（不再限自己任教的那几个班）；
               另给该生的**总分年级名次**（只给名次，不给总分分数）。
  R2 班主任：在班型设置中担任班主任的账号，可查看本班成绩（全部科目 + 总分）。
  R3 年级长及以上：可查看所在年级的成绩（校级 / 管理员 = 全校）。
  R4 显式授权（可选）：权限管理页"用户级数据范围"（UserDataScope）由管理员额外授予的
               年级 / 班级，与 R3 同级；该表存在记录时按网页端口径**优先且收窄**。

授权取并集（R1/R2/R3 可叠加：既任课又当班主任则两类范围都可见）；**只读**——
本引擎不提供任何写入口，调用方不得据此放行写入类操作。

数据来源（与网页端完全同源，不另建口径）：
  · TeacherSubjectLink(active)  → R1 任课映射 (grade, subject)【年级 × 学科】
  · UserClassLink               → R2 班主任所辖班 (grade, class_name)
  · User.role / User.grade      → R3 年级长
  · PermissionGroup.scope_type  → R3 校级（school）
  · UserDataScope               → R4 显式授权（年级 / 年级+班级白名单）

2026-10-15 口径变更记录（改这三处就够了，别在别处再写一遍）：
  · R1 从「(年级, 班级, 学科) 三元组」放宽为「(年级, 学科)」→ `ScoreScope.teach` 是二元组；
  · 由此 `visible_classes()` 对有任课的年级返回 None（全部班级），**但学科与总分仍分别
    由 `class_subjects` / `class_total_visible` 收口**；
  · 凡是要按班级收"逐生总分"的调用方（成绩单 / 临界生 / 压线提醒 / AI total_hidden）
    **不能用 `visible_classes() is None → 不限班` 的旧写法**（那会把逐生总分一起放开），
    统一改用 `total_visible_classes()`。

历史：本文件 2026-10-13 由 `mcp_server/core/score_guard.py` **整体迁入主应用**
（原位置保留同名模块做薄转发）。原因：成绩汇报区的「AI 一句话分析」需要在
Web 进程内按同一套细粒度规则收窄工具结果（app 原有 `services/scope.py` 只到
考试级/年级级），若各自实现一份，权限口径迟早漂移，属安全红线。
迁入后 **MCP 与 Web 共用这一份**，改规则只需改这里。
"""
from app.models import UserClassLink  # noqa: F401  供调用方与历史行为兼容（原模块在此层可见）


class ScoreScope:
    """某个账号的成绩可见范围（只读值对象）。

    三种粒度并存：
      · grades  —— 整年级授权（年级长及以上 / 校级 / 显式授权）→ 该年级全部班、全部学科；
      · classes —— 整班授权（班主任）→ 该班全部学科（含总分）；
      · teach   —— 学科级授权（任课映射）→ (年级, 学科)：该年级**全部班级**的该科成绩
                   （2026-10-15 用户口径，此前是 (年级, 班级, 学科) 三元组）。
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
        self.teach = set(teach or ())            # {(grade, subject)}  年级 × 学科
        self.reasons = list(reasons or ())

    # ---------------- 年级 ----------------
    def visible_grades(self):
        """可见年级集合；None = 全校（不限年级）。"""
        if self.full_school:
            return None
        out = set(self.grades)
        out.update(g for g, _c in self.classes)
        out.update(g for g, _s in self.teach)
        return out

    def can_view_grade(self, grade):
        """是否可访问该年级（任意成绩）。"""
        if self.full_school:
            return True
        return grade in self.visible_grades()

    def grade_subjects(self, grade):
        """该年级可见学科集合；None = 全部学科，空集 = 无可见学科。

        R1 的"可查看其在该年级所教学科的成绩"就落在这一层：
        任课教师拿到的是自己在该年级的任课科目并集（年级级，与该班的班级无关）。
        """
        if self.full_school or grade in self.grades:
            return None
        return {s for g, s in self.teach if g == grade}

    # ---------------- 班级 ----------------
    def visible_classes(self, grade):
        """该年级可见班级名集合；None = 该年级**全部班级**。

        2026-10-15 用户口径：任课授权＝(年级, 学科)，教高一化学的老师，高一**所有班**
        的化学成绩都对他开放 → 有任课的年级在此返回 None（全部班级）。
        读到 None 只代表"班级不成约束"，**学科**看 `class_subjects`、**总分**看
        `class_total_visible`；凡是要收"逐生总分"的调用方（成绩单 / 临界生 /
        压线提醒 / AI total_hidden）请用 `total_visible_classes()`，别直接用本方法。
        """
        if self.full_school or grade in self.grades:
            return None
        if any(g == grade for g, _s in self.teach):
            return None
        return {c for g, c in self.classes if g == grade}

    def can_view_class(self, grade, class_name):
        """是否可访问该班成绩（任意学科数据）。

        R1：**任教该年级**即对该年级全部班级成立（学科由 `class_subjects` 收口）；
        R2 命中本班；R3 命中本年级。
        """
        if self.full_school or grade in self.grades:
            return True
        if (grade, class_name) in self.classes:
            return True
        return any(g == grade for g, _s in self.teach)

    def class_subjects(self, grade, class_name):
        """班内可见学科集合；None = 全部学科，空集 = 无可见学科。"""
        if (self.full_school or grade in self.grades
                or (grade, class_name) in self.classes):
            return None
        return {s for g, s in self.teach if g == grade}

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

    def total_rank_visible(self, grade, class_name):
        """是否可见该班学生的**总分年级名次**（只给名次，不给总分分数）。

        2026-10-15 用户口径："还有一个现在公开的是这个学生的年级的名次"——
        任课教师能看到该年级该科全部学生的成绩（含他班），同时给出每个学生的
        **总分年级名次**（`ExamScore.rank_dir` 的总分行），便于判断"这名学生整体
        是什么水平"；**总分分数仍不开放**（`class_total_visible` 保持 false）。
        班主任 / 年级长及以上 / 校级 / 管理员本就可见总分口径，名次自然包含在内。
        """
        return (self.class_total_visible(grade, class_name)
                or any(g == grade for g, _s in self.teach))

    def total_visible_classes(self, grade, class_names=None):
        """「总分口径」可见的班级集合 —— **逐生总分数据的唯一收口口径**。

        返回 None ＝ 该年级全部班级（年级长及以上 / 校级 / 管理员 / 年级级显式授权）；
        返回集合 ＝ 只有这些班：班主任＝本班、**纯任课教师＝空集**（fail-closed）。
        `class_names` 给出候选清单时按清单过滤（成绩单：本场考试出现过的班）。

        为什么必须有这个方法：`visible_classes()` 在 R1 放宽成"年级 × 学科"后，
        对有任课的年级也返回 None，沿用旧写法（`visible_classes() is None → 不限班`）
        的调用方会把**逐生总分**（成绩单的总分列、临界生名单）一并放开，属越权。
        """
        if self.full_school or grade in self.grades:
            return None
        if class_names is None:
            return {c for g, c in self.classes if g == grade}
        names = {str(c) for c in class_names if c}
        return {c for c in names if (grade, c) in self.classes}

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
            'subject_grants': sorted(f'{g} {s}' for g, s in self.teach),
            'reasons': self.reasons,
            'note': ('成绩可见范围＝网页端该账号可见范围；本服务仅提供读取权限。'
                     '任课教师＝任教（年级 × 学科）：该年级该科全部班级的学生成绩可见，'
                     '并附带每名学生的总分年级名次；其它学科与总分分数不开放。'),
        }


def resolve(user):
    """解析账号的成绩可见范围（R0 全校 → R4 显式授权 → R1/R2/R3 取并集）。"""
    if user is None:
        raise PermissionError('未通过身份验证：无法确定成绩可见范围')

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

    # R1 任课教师：**(年级, 学科)** 二元组（2026-10-15 用户口径）
    # 教高一化学 → 高一全部班级的化学成绩都开放；粒度只到"年级 × 学科"，
    # 具体班级不再参与判定（班内可见科目由 `class_subjects` 统一给为准）。
    tlinks = TeacherSubjectLink.query.filter_by(user_id=uid, active=True).all()
    for t in tlinks:
        teach.add((t.grade, t.subject))
    if tlinks:
        reasons.append(f'任课教师：{len(teach)} 个年级学科（{len(tlinks)} 条映射）')

    # 2026-10-13 审计修复：统一剔除已毕业年级（三条授权路径都可能含毕业年级）
    grades, dropped_g = _strip_graduated(grades)
    if dropped_g:
        reasons.append(f'已剔除毕业年级：{sorted(dropped_g)}')
    if graduated:
        classes = {(g, c) for g, c in classes if g not in graduated}
        teach = {(g, s) for g, s in teach if g not in graduated}

    return ScoreScope(uid, username, role, grades=grades, classes=classes,
                      teach=teach, reasons=reasons)


def require_exam(user, exam):
    """考试级校验：考试所在年级不在可见范围内即拒绝。"""
    scope = resolve(user)
    if not scope.can_view_grade(exam.grade):
        raise PermissionError(
            f'无权查看 {exam.grade}「{exam.name}」的成绩：'
            '该年级不在您的成绩可见范围内（任课教师需任教该年级的某一科、'
            '班主任需带该年级的班、年级长及以上可看本年级）')
    return scope


def require_class(scope, grade, class_name):
    """班级级校验：班级不在可见范围内即拒绝。"""
    if not scope.can_view_class(grade, class_name):
        raise PermissionError(
            f'无权查看 {grade} {class_name} 的成绩：'
            '任课教师可查看所教年级中本班成绩（仅限所教学科）、班主任仅能查看本班成绩')


def require_student(scope, student):
    """学生级校验：按学生当前班级判定（成绩是班级口径的数据）。"""
    return require_class(scope, student.grade, student.class_name)
