# StuLink v1.18.2.0 2026-09-24
# 班主任工作台：班级/学生归属校验（越权防护，PR#5 安全审查 S1）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""工作台数据范围判定。

工作台路由此前仅 @login_required：任意登录账号改 URL 参数即可拉取全校任意班级
名单、导出任意班级考勤、为任意学生写入考勤记录。本模块沿用成绩模块的
「角色 → 权限组范围 → 用户级数据范围 → 班主任/任课教师映射」同一套口径，
对外只暴露三个判定函数，供路由层做越权拦截。
"""
from app.models import Student, UserClassLink
from app.models.user_data_scope import get_user_scope_classes
from app.models.grades import TeacherSubjectLink
from app.modules.grades.services.scope import (
    user_grade_scope, get_scope, SCOPE_SCHOOL, SCOPE_GRADE, SCOPE_CLASS,
)


def _all_pairs():
    """全校班级 [(grade, class_name)]"""
    rows = Student.query.with_entities(Student.grade, Student.class_name).distinct().all()
    return [(g, c) for g, c in rows if g and c]


def _grade_pairs(grades):
    grades = set(grades or [])
    return [(g, c) for g, c in _all_pairs() if g in grades]


def managed_class_pairs(user):
    """该用户可见班级 [(grade, class_name)]（下拉/默认选中用）"""
    if not user or not getattr(user, 'role', None):
        return []
    if user.role == 'admin':
        return _all_pairs()
    ug = user_grade_scope(user)
    if ug is not None:
        return _grade_pairs(ug)
    scope, grade = get_scope(user)
    if scope == SCOPE_SCHOOL:
        return _all_pairs()
    if scope == SCOPE_GRADE:
        return _grade_pairs([grade] if grade else [])
    # 班主任（UserClassLink）；任课教师并入任教映射（与成绩模块 visible_grades 口径一致：
    # scope_type=class 的任课教师若只看 UserClassLink 会拿到空列表，页面无班可选）
    pairs = {(l.grade, l.class_name) for l in
             UserClassLink.query.filter_by(user_id=user.id).all()}
    for l in TeacherSubjectLink.query.filter_by(user_id=user.id, active=True).all():
        cn = getattr(l, 'class_name', None)
        if cn:
            pairs.add((l.grade, cn))
        else:
            pairs.update(_grade_pairs([l.grade]))
    return sorted(pairs)


def class_allowed(user, grade, class_name):
    """(grade, class_name) 是否在该用户可见/可操作范围内"""
    if not user or not grade or not class_name:
        return False
    if user.role == 'admin':
        return True
    ug = user_grade_scope(user)
    if ug is not None:
        # 用户级数据范围优先（含可选班级白名单）
        if grade not in ug:
            return False
        classes = get_user_scope_classes(user.id, grade)
        return True if classes is None else (class_name in classes)
    scope, g = get_scope(user)
    if scope == SCOPE_GRADE:
        return grade == g
    # 班主任（UserClassLink）或任课教师映射（任教年级/班级）
    if UserClassLink.query.filter_by(user_id=user.id, grade=grade,
                                     class_name=class_name).first():
        return True
    for l in TeacherSubjectLink.query.filter_by(user_id=user.id, grade=grade,
                                                active=True).all():
        cn = getattr(l, 'class_name', None)
        if not cn or cn == class_name:
            return True
    if scope == SCOPE_SCHOOL:
        return True
    # 其余（含 scope_type=class 但无任何班级映射）一律拒绝
    return False


def student_no_allowed(user, student_no):
    """学号是否在该用户范围内（学生不存在 → False）"""
    if not user or not student_no:
        return False
    s = Student.query.filter_by(student_number=student_no).first()
    if not s:
        return False
    return class_allowed(user, s.grade, s.class_name)


def assert_class_allowed(user, grade, class_name, what='该班级'):
    """越界抛 PermissionError（与成绩模块 scope 模块一致）"""
    if not class_allowed(user, grade, class_name):
        raise PermissionError(f'无权访问{what}：{grade}{class_name or ""}')
    return True
