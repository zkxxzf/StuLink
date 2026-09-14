# StuLink v1.9.0 2026-09-03
# 成绩模块数据范围解析（复用现有角色/权限组体系，参照宿舍统计模块范式）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from app.models import UserClassLink, Student
from app.utils.helpers import get_graduated_grades
from app.models.grades import TeacherSubjectLink

SCOPE_SCHOOL = 'school'
SCOPE_GRADE = 'grade'
SCOPE_CLASS = 'class'


def get_scope(user):
    """返回 (scope_type, grade)，与宿舍统计模块一致"""
    pg = user.permission_group
    if not pg:
        return 'none', None
    return pg.scope_type, (user.grade or None)


def visible_grades(user):
    """成绩分析可见年级列表（管理/分析页下拉用）"""
    scope, grade = get_scope(user)
    if scope == SCOPE_SCHOOL:
        return _all_active_grades()
    if scope == SCOPE_GRADE:
        return [grade] if grade else []
    # 班主任/任课教师：所辖班级所在年级
    # 任课教师通常没有 UserClassLink，必须并入本人任课映射的年级，
    # 否则其可见年级为空 → 分析页考试下拉选不到任何考试
    links = UserClassLink.query.filter_by(user_id=user.id).all()
    grades = {l.grade for l in links}
    for l in TeacherSubjectLink.query.filter_by(user_id=user.id, active=True).all():
        grades.add(l.grade)
    return sorted(grades)


def visible_classes(user):
    """可见 (grade, class_name) 列表"""
    scope, grade = get_scope(user)
    links = UserClassLink.query.filter_by(user_id=user.id).all()
    if scope == SCOPE_SCHOOL or scope == SCOPE_GRADE:
        return None  # 全校/全年级（不限班）
    return [(l.grade, l.class_name) for l in links]


def teacher_links(user):
    """任课教师映射（active）列表：[(grade, class_name, subject, user_id)]"""
    return TeacherSubjectLink.query.filter_by(user_id=user.id, active=True).all()


def check_exam_visible(user, exam):
    """考试是否在用户可见范围（越界抛 PermissionError）"""
    if user.role == 'admin':
        return
    # 任课教师：按本人任课映射所在年级
    if user.has_role('teacher'):
        grades = {l.grade for l in
                  TeacherSubjectLink.query.filter_by(user_id=user.id, active=True).all()}
        if exam.grade not in grades:
            raise PermissionError
        return
    scope, grade = get_scope(user)
    gds = get_graduated_grades()
    if exam.grade in gds and scope != SCOPE_SCHOOL:
        raise PermissionError
    if scope == SCOPE_GRADE and exam.grade != grade:
        raise PermissionError
    if scope in (SCOPE_CLASS,):
        grades = {l.grade for l in UserClassLink.query.filter_by(user_id=user.id).all()}
        if exam.grade not in grades:
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
    if user.has_role('teacher'):
        grades = {l.grade for l in
                  TeacherSubjectLink.query.filter_by(user_id=user.id, active=True).all()}
        if st_obj.grade not in grades:
            raise PermissionError
        return
    scope, grade = get_scope(user)
    if scope == SCOPE_SCHOOL:
        return
    if scope == SCOPE_GRADE:
        if st_obj.grade != grade:
            raise PermissionError
        return
    if scope == SCOPE_CLASS:
        links = UserClassLink.query.filter_by(user_id=user.id).all()
        allowed = {(l.grade, l.class_name) for l in links}
        if (st_obj.grade, st_obj.class_name) not in allowed:
            raise PermissionError
        return
    raise PermissionError
