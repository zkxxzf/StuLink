# StuLink v1.19.60.64 2026-10-14
# 成绩模块数据范围解析（P2：委托 app.utils.scope 唯一口径，保留本模块公开 API）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""成绩模块数据范围（唯一口径的薄封装）

P2（多身份改造）：解析规则已收敛到 ``app.utils.scope``；本模块保留原有公开函数
（get_scope / visible_grades / visible_classes / check_exam_visible / student_in_scope /
user_grade_scope / has_user_scope / teacher_links），供各业务模块继续 import，
行为与改造前一致。
"""
from app.models import UserClassLink, Student
from app.models.user_data_scope import get_user_scope_classes
from app.utils.helpers import get_graduated_grades
from app.utils.scope import (
    SCOPE_SCHOOL, SCOPE_GRADE, SCOPE_CLASS,
    user_scope_grades as user_grade_scope,     # 兼容旧名（多处 import 旧名）
    class_pairs, teaching_links, teaching_grades, teaching_pairs,
)


def get_scope_grades(user):
    """年级范围的**完整列表**（2026-10-19：管理年级可多选）。

    入参 user；返回去重保序的年级列表（admin / 无配置返回 []）。
    凡"限定到年级"的判定与查询都应使用本函数，不要再取 user.grade 单值。
    """
    if user.role == 'admin':
        return []
    return list(getattr(user, 'grades', []) or [])


def get_scope(user):
    """返回 (scope_type, grade)，与宿舍统计模块一致

    2026-10-12 审计修复（P0-2）：admin 恒为全校（不再落 ('none', None)）。
    2026-10-19：grade 为**首个**管理年级（兼容旧调用方）；需要全部年级请用
    ``get_scope_grades``。
    """
    if user.role == 'admin':
        return SCOPE_SCHOOL, None
    pg = getattr(user, 'active_group', None) or getattr(user, 'permission_group', None)
    if not pg:
        return 'none', None
    from app.utils.scope import scope_type_of
    grades = get_scope_grades(user)
    return scope_type_of(user), (grades[0] if grades else None)


def has_user_scope(user):
    return user_grade_scope(user) is not None


def visible_grades(user):
    """成绩分析可见年级列表（管理/分析页下拉用）"""
    if user.role == 'admin':
        return _all_active_grades()  # 管理员=全部未毕业年级（2026-10-12 P0-2）
    ug = user_grade_scope(user)
    if ug is not None:
        # v1.9.2 用户级数据范围：仅授权年级（剔除已毕业）
        gds = set(get_graduated_grades())
        return [g for g in ug if g not in gds]
    scope, grade = get_scope(user)
    if scope == SCOPE_SCHOOL:
        return _all_active_grades()
    if scope == SCOPE_GRADE:
        return get_scope_grades(user)
    # 班主任/任课教师：所辖班级所在年级
    # 任课教师通常没有 UserClassLink，必须并入本人任课映射的年级，
    # 否则其可见年级为空 → 分析页考试下拉选不到任何考试
    grades = {g for g, _c in class_pairs(user)}
    grades |= teaching_grades(user)
    return sorted(grades)


def visible_classes(user):
    """可见 (grade, class_name) 列表；None = 不受班级约束（全校 / 整年级授权）"""
    if user.role == 'admin':
        return None  # 管理员不限班（2026-10-12 P0-2，与 get_scope 特判同口径）
    # 2026-10-15 审计修复（P0）：用户级数据范围（UserDataScope）**可能带班级白名单**
    # （user_data_scopes.class_name 有值 = 仅该班）。此前一律返回 None（=不限班）→
    # 「只授权 01 班」的账号在分析/导出/对比里能读别的班逐生数据，与
    # student_in_scope / MCP score_guard（R4 按班收窄）相矛盾。
    from app.utils.scope import user_scope_detail
    _has, _grade_wide, _pairs = user_scope_detail(user)
    if _has:
        if not _pairs:
            return None                     # 全部整年级授权（行为与改造前一致）
        if not _grade_wide:
            return sorted(_pairs)           # 全部班级级授权：精确到班（fail-closed）
        # 混合形态：与 utils.scope.resolve_scope 同一取舍（数据完整优先＝按整年级）
        return None
    scope, grade = get_scope(user)
    if scope == SCOPE_SCHOOL or scope == SCOPE_GRADE:
        return None  # 全校/全年级（不限班）
    return [(l.grade, l.class_name)
            for l in UserClassLink.query.filter_by(user_id=user.id).all()]


def teacher_links(user):
    """任课教师映射（active）列表：[(grade, class_name, subject, user_id)]"""
    return teaching_links(user)


def check_exam_visible(user, exam):
    """考试是否在用户可见范围（越界抛 PermissionError）"""
    if user.role == 'admin':
        return
    ug = user_grade_scope(user)
    if ug is not None:
        # v1.9.2 用户级数据范围优先：仅授权年级
        if exam.grade not in ug:
            raise PermissionError
        return
    # 任课教师：按本人任课映射所在年级；并上「班主任所带班」的年级
    # （2026-10-15 用户口径：班主任带某班、但不在该班任课时，也应当能看该班成绩。
    #  纯班主任账号本就走下面的 SCOPE_CLASS 分支，这里补的是"既任教又带班"的并集。）
    if user.has_role('teacher'):
        grades = teaching_grades(user) | {g for g, _c in class_pairs(user)}
        if exam.grade not in grades:
            raise PermissionError
        return
    scope, grade = get_scope(user)
    gds = get_graduated_grades()
    if exam.grade in gds and scope != SCOPE_SCHOOL:
        raise PermissionError
    if scope == SCOPE_GRADE and exam.grade not in get_scope_grades(user):
        raise PermissionError
    if scope in (SCOPE_CLASS,):
        grades = {g for g, _c in class_pairs(user)}
        if exam.grade not in grades:
            raise PermissionError
    if scope not in (SCOPE_SCHOOL, SCOPE_GRADE, SCOPE_CLASS):
        # v1.9.2 兜底：无有效数据范围（如未配置范围的身份）一律拒绝，避免越权读取
        raise PermissionError


def _all_active_grades():
    """未毕业年级（按年份降序）"""
    import re
    from app.models import DictCategory
    gds = set(get_graduated_grades())
    cat = DictCategory.query.filter_by(code='grade').first()
    grades = []
    if cat:
        grades = [i.value for i in cat.items.filter_by(is_active=True).order_by('sort_order').all()]
    grades = [g for g in grades if g not in gds]
    grades.sort(key=lambda g: int(''.join(filter(str.isdigit, g)) or '0'), reverse=True)
    return grades


def student_in_scope(user, student_no):
    """判断 student_no（= Student.student_number）是否在当前用户成绩可见范围内。

    越界抛 PermissionError。沿用现有角色/权限组体系（不新增角色）：
    admin/校级 → 全校；年级长 → 本年级；班主任 → 所辖班；任课教师 → 任教年级内学生。
    """
    if user.role == 'admin':
        return
    st_obj = Student.query.filter_by(student_number=student_no).first()
    if not st_obj:
        raise PermissionError
    ug = user_grade_scope(user)
    if ug is not None:
        # v1.9.2 用户级数据范围优先：按授权年级（含可选班级白名单）
        if st_obj.grade not in ug:
            raise PermissionError
        classes = get_user_scope_classes(user.id, st_obj.grade)
        if classes is not None and st_obj.class_name not in classes:
            raise PermissionError
        return
    if user.has_role('teacher'):
        # 2026-10-18 审计修复（P1）：此前只校验年级，任课教师能按学号逐个读取
        # **同年级任意班**学生的成绩（实测 licancan 只教 01/04 班，可读 02 班）。
        # 改为精确到任教班级；只有"整年级任教"（class_name 为空）才按年级兜底。
        # 2026-10-19 审计修复：并入「班主任所带班」（class_pairs）—— 与 check_exam_visible
        # 同一口径；否则"既任教又带班"的班主任会读不到自己班的学生（口径分裂）。
        tps = teaching_pairs(user)
        own_classes = class_pairs(user)
        if tps or own_classes:
            if ((st_obj.grade, st_obj.class_name) not in tps
                    and (st_obj.grade, st_obj.class_name) not in own_classes):
                raise PermissionError
            return
        if st_obj.grade not in teaching_grades(user):
            raise PermissionError
        return
    scope, grade = get_scope(user)
    if scope == SCOPE_SCHOOL:
        return
    if scope == SCOPE_GRADE:
        if st_obj.grade not in get_scope_grades(user):
            raise PermissionError
        return
    if scope == SCOPE_CLASS:
        if (st_obj.grade, st_obj.class_name) not in class_pairs(user):
            raise PermissionError
        return
    raise PermissionError
