# StuLink v1.19.49.29 2026-10-14
# 数据范围「唯一口径」：所有模块的可见范围解析都委托到本模块
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""数据范围唯一口径（多身份改造 · P2）

背景：
改造前「当前用户能看哪些数据」在 **6 处**各写一套（student_scope / grades.scope /
workbench.scope / dormitory_scope / points / morality），口径互有漂移，是历史 bug 的温床。
本模块把「解析规则」收敛为唯一实现，其余模块只做薄封装委托。

解析优先级（与改造前完全一致）：
    1. admin                      → 全校
    2. 用户级数据范围 UserDataScope → 指定年级（专职行政/多人分管的范围值来源）
    3. 权限组 scope_type：
         school → 全校
         grade  → 本人年级（user.grade）
         class  → 本人所辖班级（UserClassLink；dormitory 侧可回落 user.grade/class_name）
         none/缺 → 空集（fail-closed，绝不回落全校）

术语：
    kind  解析后的范围类型：'school' | 'grades' | 'classes' | 'none'
    origin 结果来源：'admin' | 'user_grades' | 'school' | 'grade' | 'class' | 'none'
          （保留 origin 是为了让不同模块维持各自的返回约定，行为与改造前一致）

本模块只依赖 app.models（模型）与 flask_login，不依赖任何业务模块，避免循环依赖。
"""
import logging

from sqlalchemy import and_, or_, false

from app.models.permission_group import (
    KIND_TO_LEGACY as _KIND_TO_LEGACY,
    LEGACY_TO_KIND as _LEGACY_TO_KIND,
)

logger = logging.getLogger(__name__)

SCOPE_SCHOOL = 'school'
SCOPE_GRADE = 'grade'
SCOPE_CLASS = 'class'


def _current(user):
    if user is not None:
        return user
    from flask_login import current_user
    return current_user


class Scope:
    """解析后的数据范围（不可变视图）"""

    __slots__ = ('kind', 'grades', 'classes', 'origin')

    def __init__(self, kind, grades=None, classes=None, origin=None):
        self.kind = kind
        self.grades = set(grades or ())
        self.classes = set(classes or ())     # {(grade, class_name)}
        self.origin = origin or kind

    def __repr__(self):
        return f'<Scope {self.origin}/{self.kind} g={sorted(self.grades)} c={sorted(self.classes)}>'


# ==================== 基础原语（消除重复查询） ====================

def is_admin(user):
    return getattr(user, 'role', None) == 'admin'


def user_scope_detail(user):
    """用户级数据范围（UserDataScope）明细 → (has_scope, grade_wide, class_pairs)。

    - has_scope=False：未配置（回落组级规则）；
    - grade_wide：整年级授权的年级（class_name 为空的行）；
    - class_pairs：班级级授权 {(grade, class_name)}（class_name 有值的行）。

    2026-10-14 第四轮审计修复（多身份 B 项）：此前各处只读「年级清单」，
    把 class_name（仅授权某班）静默放大为整个年级 —— 与成绩侧
    （student_in_scope 会查班级白名单）口径分裂。统一从本函数取明细。
    admin 不受限，恒返回 (False, set(), set())。
    """
    u = _current(user)
    if is_admin(u):
        return False, set(), set()
    from app.models import UserDataScope
    rows = UserDataScope.query.filter_by(user_id=u.id).all()
    if not rows:
        return False, set(), set()
    grade_wide = {r.grade for r in rows if not r.class_name}
    pairs = {(r.grade, r.class_name) for r in rows if r.class_name}
    return True, grade_wide, pairs


def user_scope_grades(user):
    """用户级数据范围（UserDataScope）授权年级清单；None=未配置（回落组级规则）。

    admin 不受限，恒返回 None。
    """
    has_scope, grade_wide, pairs = user_scope_detail(user)
    if not has_scope:
        return None
    return sorted(grade_wide | {g for g, _ in pairs})


def _pg_of(user):
    """当前用户的身份组（优先当前身份，回落主组/旧字段）"""
    u = _current(user)
    pg = getattr(u, 'active_group', None)
    if pg is None:
        pg = getattr(u, 'permission_group', None)
    return pg


def scope_kind_of(user):
    """范围类型（新 7 值）：school / self_grade / pick_grades / self_class /
    pick_classes / self_teaching / none；admin → school。"""
    u = _current(user)
    if is_admin(u):
        return SCOPE_SCHOOL
    pg = _pg_of(u)
    if pg is None:
        return 'none'
    k = getattr(pg, 'effective_kind', None)
    if k:
        return k
    return _LEGACY_TO_KIND.get(getattr(pg, 'scope_type', 'none') or 'none', 'none')


def scope_type_of(user):
    """旧 4 值范围类型（school | grade | class | none）—— 供既有调用方兼容。

    新逻辑请用 scope_kind_of；本函数把 7 值折叠回 4 值。
    """
    return _KIND_TO_LEGACY.get(scope_kind_of(user), 'none')


def class_pairs(user):
    """用户所辖班级 {(grade, class_name)}（UserClassLink）"""
    u = _current(user)
    from app.models import UserClassLink
    return {(l.grade, l.class_name)
            for l in UserClassLink.query.filter_by(user_id=u.id).all()}


def teaching_links(user):
    """任课映射（active）列表"""
    u = _current(user)
    from app.models.grades import TeacherSubjectLink
    return TeacherSubjectLink.query.filter_by(user_id=u.id, active=True).all()


def teaching_grades(user):
    """任课映射涉及的年级集合"""
    return {l.grade for l in teaching_links(user)}


def teaching_pairs(user):
    """任课映射涉及的 (grade, class_name) 集合（class_name 为空则不计入）"""
    return {(l.grade, l.class_name) for l in teaching_links(user) if l.class_name}


# ==================== 唯一口径解析 ====================

def resolve_scope(user=None, *, class_fallback=False):
    """解析当前用户的数据范围 → Scope。

    class_fallback=True：class 范围且无 UserClassLink 时，回落
    (user.grade, user.class_name)（宿舍侧历史行为，避免老数据误伤）。
    """
    u = _current(user)
    if is_admin(u):
        return Scope('school', origin='admin')
    # 用户级数据范围优先（含班级白名单，2026-10-14 第四轮审计修复）。
    has_scope, grade_wide, pairs = user_scope_detail(u)
    if has_scope:
        if not pairs:
            # 全部是整年级授权 —— 与改造前完全一致
            return Scope('grades', grades=sorted(grade_wide), origin='user_grades')
        if not grade_wide:
            # 全部是班级级授权 —— 精确到班（此前被放大为整年级）
            return Scope('classes', classes=pairs, origin='user_grades')
        # 混合形态（部分年级整年级 + 部分年级个别班）：Scope 单 kind 无法同时表达，
        # 取舍＝数据完整优先（按整年级，宁可多不可漏），并记 warning 提示配置收敛。
        logger.warning(
            'UserDataScope 混合形态（整年级 %s + 班级级 %s）user_id=%s，'
            '按整年级口径解析；建议该用户统一按年级或统一按班级配置',
            sorted(grade_wide), sorted(pairs), u.id)
        return Scope('grades',
                     grades=sorted(grade_wide | {g for g, _ in pairs}),
                     origin='user_grades')
    kind = scope_kind_of(u)
    pg = _pg_of(u)
    if kind == SCOPE_SCHOOL:
        return Scope('school', origin='school')
    if kind in ('self_grade', 'pick_grades'):
        gr = set(pg.get_scope_grades()) if pg else set()
        if kind == 'self_grade':
            gr = {u.grade} if u.grade else set()
        elif not gr and getattr(u, 'grade', None):
            gr = {u.grade}          # 指定年级未配置 → 回落本人年级
        return Scope('grades', grades=gr, origin='grade')
    if kind in ('self_class', 'pick_classes'):
        pairs = (class_pairs(u) if kind == 'self_class' else set())
        if kind == 'pick_classes' and pg:
            pairs = {(c.get('grade'), c.get('class'))
                     for c in pg.get_scope_classes()
                     if isinstance(c, dict) and c.get('grade') and c.get('class')}
        if not pairs and class_fallback and u.grade and u.class_name:
            pairs = {(u.grade, u.class_name)}
        return Scope('classes', classes=pairs, origin='class')
    if kind == 'self_teaching':
        tps = teaching_pairs(u)
        if tps:
            return Scope('classes', classes=tps, origin='class')
        tgs = teaching_grades(u)
        if tgs:
            return Scope('grades', grades=tgs, origin='grade')
        return Scope('none', origin='none')
    return Scope('none', origin='none')


def apply_scope(query, grade_col, class_col, user=None, *, class_fallback=False):
    """把范围条件加到查询上（各模块统一调用）。

    - school  → 原样返回
    - grades  → grade_col IN (...)
    - classes → OR(grade_col=g AND class_col=c)
    - none/空 → 恒假（fail-closed）
    """
    s = resolve_scope(user, class_fallback=class_fallback)
    if s.kind == 'school':
        return query
    if s.kind == 'grades':
        if not s.grades:
            return query.filter(false())
        return query.filter(grade_col.in_(sorted(s.grades)))
    if s.kind == 'classes':
        if not s.classes:
            return query.filter(false())
        return query.filter(or_(*[and_(grade_col == g, class_col == c)
                                  for g, c in sorted(s.classes)]))
    return query.filter(false())


def in_scope(user, grade, class_name, *, class_fallback=False):
    """单条记录 (grade, class_name) 是否在范围内"""
    s = resolve_scope(user, class_fallback=class_fallback)
    if s.kind == 'school':
        return True
    if s.kind == 'grades':
        return bool(grade) and grade in s.grades
    if s.kind == 'classes':
        return (grade, class_name) in s.classes
    return False
