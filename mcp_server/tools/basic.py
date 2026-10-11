# -*- coding: utf-8 -*-
"""基础信息与学生工具：年级/班级清单、学生检索、学生详情、系统概览。"""
from sqlalchemy import func, or_

from ..core import guards, respond, toolkit


def _scope_filters(user, query):
    """按账号数据范围收窄学生查询（与网页端 visible_grades/visible_classes 同口径）。"""
    from app.models.student import Student
    from app.modules.grades.services import scope
    grades = scope.visible_grades(user)
    query = query.filter(Student.grade.in_(grades)) if grades is not None else query
    classes = scope.visible_classes(user)
    if classes:
        pairs = set(classes)
        query = query.filter(or_(*[
            (Student.grade == g) & (Student.class_name == c) for g, c in sorted(pairs)
        ]))
    return query


def _list_grades(user):
    from app.models.student import Student
    rows = (_scope_filters(user, Student.query)
            .with_entities(Student.grade, func.count(Student.id).label('cnt'))
            .group_by(Student.grade).order_by(Student.grade.desc()).all())
    return [{'grade': g, 'student_count': c} for g, c in rows]


def _list_classes(user, grade):
    from app.models.student import Student
    query = _scope_filters(user, Student.query)
    if grade:
        query = query.filter(Student.grade == grade)
    rows = (query.with_entities(Student.grade, Student.class_name,
                                func.count(Student.id).label('cnt'))
            .group_by(Student.grade, Student.class_name)
            .order_by(Student.grade.desc(), Student.class_name).all())
    return [{'grade': g, 'class_name': c, 'student_count': n} for g, c, n in rows]


def _search_students(user, keyword, grade, class_name, limit):
    from app.models.student import Student
    query = _scope_filters(user, Student.query)
    if keyword:
        like = f'%{keyword.strip()}%'
        query = query.filter(or_(Student.name.like(like),
                                 Student.student_number.like(like)))
    if grade:
        query = query.filter(Student.grade == grade)
    if class_name:
        query = query.filter(Student.class_name == class_name)
    rows = query.order_by(Student.grade.desc(), Student.class_name,
                          Student.student_number).limit(limit).all()
    fields = ('student_number', 'name', 'gender', 'grade', 'class_name',
              'subject_selection', 'enrollment_status')
    return {'count': len(rows), 'students': [respond.pick(s, fields) for s in rows]}


def _get_student(user, student_no):
    from app.models.student import Student
    student = guards.find_student(student_no)
    guards.student_scope(user, student_no)
    data = respond.pick(student, (
        'student_number', 'name', 'gender', 'grade', 'class_name',
        'subject_selection', 'enrollment_status', 'enrollment_notes',
        'ethnicity', 'graduation_school', 'phone1', 'phone2', 'id_card_number'))
    # 补充住宿与床位（跨库 viewonly 关联，取不到时静默置空）
    bed = student.bed_assignment
    if bed and bed.room:
        data['dormitory'] = {'building': bed.room.building,
                             'room_number': bed.room.room_number,
                             'bed_number': bed.bed_number}
    return data


def _system_overview(user):
    from app.models.student import Student
    from app.models.user import User
    from app.models.grades import Exam
    from app.modules.grades.services import scope
    student_q = _scope_filters(user, Student.query)
    total_students = student_q.count()
    grades = [_list_grades(user)]
    # 账号总数仅管理员可见；考试总数按账号可见年级统计（与 list_exams 同口径）
    exam_q = Exam.query
    visible = scope.visible_grades(user)
    if visible is not None:
        exam_q = exam_q.filter(Exam.grade.in_(visible))
    return {
        'student_total': total_students,
        'user_total': User.query.count() if user.role == 'admin' else None,
        'exam_total': exam_q.count(),
        'grades': grades[0],
    }


def register(server):
    """把本模块的工具注册到 MCP 服务。"""

    @server.tool(name='list_grades',
                 description='列出年级清单及各年级学生人数（受账号数据范围限制）')
    async def list_grades() -> str:
        return await toolkit.db_call(_list_grades, tool='list_grades',
                                     perm='students.view')

    @server.tool(name='list_classes',
                 description='列出班级清单及各班学生人数，可选按年级筛选；传 grade 如 "2025级"')
    async def list_classes(grade: str = '') -> str:
        return await toolkit.db_call(lambda u: _list_classes(u, grade),
                                     tool='list_classes', params={'grade': grade},
                                     perm='students.view')

    @server.tool(name='search_students',
                 description='按姓名或学号模糊检索学生；可选按年级、班级收窄，' 
                             'limit 控制返回条数（默认 20，最大 100）')
    async def search_students(keyword: str = '', grade: str = '',
                              class_name: str = '', limit: int = 20) -> str:
        limit = max(1, min(int(limit or 20), 100))
        params = {'keyword': keyword, 'grade': grade,
                  'class_name': class_name, 'limit': limit}
        return await toolkit.db_call(
            lambda u: _search_students(u, keyword, grade, class_name, limit),
            tool='search_students', params=params, perm='students.view')

    @server.tool(name='get_student',
                 description='按学号查询单个学生详情（身份证号、电话已脱敏），'
                             '含住宿床位信息')
    async def get_student(student_no: str) -> str:
        return await toolkit.db_call(lambda u: _get_student(u, student_no),
                                     tool='get_student', params={'student_no': student_no},
                                     perm='students.view')

    @server.tool(name='get_system_overview',
                 description='系统总览：学生总数、账号数、考试场次数、各年级人数分布')
    async def get_system_overview() -> str:
        return await toolkit.db_call(_system_overview, tool='get_system_overview',
                                     perm='students.view')