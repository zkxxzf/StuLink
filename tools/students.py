# -*- coding: utf-8 -*-
"""学生写工具（2026-10-11 第 2 批）：编辑 / 调班。

权限（与网页端 students.py 装饰器一致）：
  · 编辑 → students.edit      （年级/班级**不可改**，改班走 transfer）
  · 调班 → students.transfer
  · 删除 → students.import    —— 刻意未开放（删除会解绑床位与住宿档案，影响面大，
                                且网页端用的是 import 权限点，易误授）

范围校验：网页端写路径是**内联角色判断**（不是 apply_student_scope）：
  · homeroom_teacher → 限本班；grade_leader → 限本年级；其余角色一律拒绝；
  · admin 放行。
MCP 侧 1:1 复刻（见 _student_write_scope），不使用 apply_student_scope——
后者是读侧口径且更严（无范围→空集），照搬会让年级长/班主任以外的合法账号被挡，
但也会与网页端行为不一致；此处以"与网页端一致"为准。

调班的强一致约定（网页端 v1.19.5.0 修复，students.py:1141-1158）：
  **先写变迁日志，再提交业务**；日志失败则业务不提交。
  write_change_log 内部不抛异常（helpers.py:429），故必须 try 并据此中止。
"""
from ..core import respond, toolkit

_STU_FIELDS = ('id', 'name', 'gender', 'student_number', 'grade', 'class_name',
               'ethnicity', 'subject_selection', 'enrollment_status',
               'enrollment_notes', 'graduation_school')


def _student_or_raise(student_id):
    from app.models.student import Student
    student = Student.query.get(int(student_id)) if student_id else None
    if student is None:
        raise ValueError(f'学生不存在：id={student_id}')
    return student


def _student_write_scope(user, student, action='编辑'):
    """复刻网页端 edit/transfer 的内联角色判定（越界抛 PermissionError）。"""
    role = getattr(user, 'role', None)
    if role == 'admin':
        return student
    if role == 'homeroom_teacher':
        if student.grade != getattr(user, 'grade', None) or \
                student.class_name != getattr(user, 'class_name', None):
            raise PermissionError(f'无权{action}该学生（仅限本班）')
        return student
    if role == 'grade_leader':
        if student.grade != getattr(user, 'grade', None):
            raise PermissionError(f'无权{action}该学生（仅限本年级）')
        return student
    raise PermissionError(f'无权{action}学生')


def _update(user, student_id, phone1, phone2, subject_selection,
            enrollment_status, enrollment_notes):
    """编辑学生：只改可编辑字段（年级/班级永不在此处改）。"""
    from app.extensions import db
    from app.utils.helpers import log_operation

    student = _student_or_raise(student_id)
    _student_write_scope(user, student, '编辑')
    changed = []
    if phone1 is not None:
        student.phone1 = phone1.strip() or None
        changed.append('phone1')
    if phone2 is not None:
        student.phone2 = phone2.strip() or None
        changed.append('phone2')
    if subject_selection is not None:
        student.subject_selection = subject_selection.strip() or None
        changed.append('subject_selection')
    if enrollment_status is not None:
        student.enrollment_status = enrollment_status.strip() or None
        changed.append('enrollment_status')
    if enrollment_notes is not None:
        student.enrollment_notes = enrollment_notes.strip() or None
        changed.append('enrollment_notes')
    if not changed:
        raise ValueError('没有要修改的字段（可改：phone1/phone2/'
                         'subject_selection/enrollment_status/enrollment_notes）')
    db.session.commit()
    log_operation(user, '更新', '学生', student.id,
                  f'{student.name} 编辑 {",".join(changed)}（MCP）', module='system')
    return {'updated': True, 'student': respond.pick(student, _STU_FIELDS),
            'changed': changed,
            'message': f'已更新 {student.name}：{",".join(changed)}'}


def _transfer(user, student_id, new_grade, new_class, change_kind):
    """调班：先写变迁日志，再提交业务（日志失败即中止）。"""
    import logging

    from app.extensions import db
    from app.utils.helpers import (log_operation, student_location_desc,
                                   write_change_log)

    logger = logging.getLogger(__name__)
    student = _student_or_raise(student_id)
    _student_write_scope(user, student, '调班')
    new_grade = (new_grade or '').strip()
    new_class = (new_class or '').strip()
    if not new_grade or not new_class:
        raise ValueError('年级和班级不能为空')
    kind = (change_kind or 'transfer').strip()
    if kind not in ('transfer', 'correct'):
        kind = 'transfer'

    old_info = f'{student.grade} {student.class_name}'
    old_loc = student_location_desc(student)
    new_loc_preview = f'{new_grade}{new_class}'
    # 与网页端一致：**先落日志再提交业务**；日志失败则业务不提交
    if old_loc != new_loc_preview:
        try:
            write_change_log(
                kind,
                [{'id': student.id,
                  'student_number': student.student_number or '',
                  'name': student.name}],
                old_value=old_loc, new_value=new_loc_preview,
                detail='修正班级录入' if kind == 'correct' else '个别调班',
                operator_name=user.real_name)
        except Exception as exc:  # noqa: BLE001  日志写失败 → 中止，不做改动
            logger.error('调班记录写入失败，已中止（学生 %s）：%s', student.id, exc)
            raise ValueError('调班失败：变迁记录写入异常，未做任何改动')
    student.grade = new_grade
    student.class_name = new_class
    db.session.commit()
    log_operation(user, '更新', '学生', student.id,
                  f'{student.name} {old_info}→{new_grade}{new_class}（MCP）',
                  module='system')
    return {'transferred': True, 'student': respond.pick(student, _STU_FIELDS),
            'change_kind': kind,
            'message': f'{student.name} 已从 {old_info} 调至 {new_grade}{new_class}'}


def register(server):
    """把本模块的工具注册到 MCP 服务。"""

    @server.tool(name='update_student',
                 description='【写操作】编辑学生基本信息。可改：phone1 / phone2 / '
                             'subject_selection(选科) / enrollment_status(学籍) / '
                             'enrollment_notes。**年级与班级不可在此修改**，'
                             '改班请用 transfer_student')
    async def update_student(student_id: int, phone1: str = None,
                             phone2: str = None, subject_selection: str = None,
                             enrollment_status: str = None,
                             enrollment_notes: str = None) -> str:
        params = {'student_id': student_id}
        return await toolkit.db_write(
            lambda u: _update(u, student_id, phone1, phone2, subject_selection,
                              enrollment_status, enrollment_notes),
            tool='update_student', params=params, perm='students.edit')

    @server.tool(name='transfer_student',
                 description='【写操作】学生调班/调年级。change_kind 取 '
                             'transfer(个别调班，默认) / correct(修正班级录入)；'
                             '会先写变迁日志再改班级，日志失败则不做改动')
    async def transfer_student(student_id: int, new_grade: str, new_class: str,
                               change_kind: str = 'transfer') -> str:
        params = {'student_id': student_id, 'new_grade': new_grade,
                  'new_class': new_class, 'change_kind': change_kind}
        return await toolkit.db_write(
            lambda u: _transfer(u, student_id, new_grade, new_class, change_kind),
            tool='transfer_student', params=params, perm='students.transfer')
