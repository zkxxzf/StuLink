# -*- coding: utf-8 -*-
"""考勤工具：记录查询、统计、学生考勤明细、批量记录考勤（写）。"""
from app.models.academic import ATTENDANCE_STATUS

from ..core import guards, respond, toolkit

_STATUS_CODES = [code for code, _label in ATTENDANCE_STATUS]


def _resolve_grade(user, class_name):
    """班级名反查年级（网页端由下拉带出 grade，AI 常只给班级名）。

    先按账号可见年级收窄候选；仍无法唯一确定时给出明确提示，避免误报"权限不足"。
    """
    from app.extensions import db
    from app.models.student import Student
    rows = (db.session.query(Student.grade)
            .filter(Student.class_name == class_name).distinct().all())
    grades = sorted({g for (g,) in rows if g})
    if not grades:
        raise ValueError(f'班级不存在：{class_name}（可用 list_classes 核对班级名）')
    if len(grades) == 1:
        return grades[0]
    from app.modules.grades.services import scope as scope_service
    visible = set(scope_service.visible_grades(user) or [])
    narrowed = [g for g in grades if g in visible]
    if len(narrowed) == 1:
        return narrowed[0]
    raise ValueError(f'班级 {class_name} 在多个年级存在（{"、".join(grades)}），请同时指定 grade')


def _records(user, class_name, grade, date_from, date_to, page, per_page):
    from app.modules.workbench.services import attendance_service
    if not class_name:
        raise ValueError('必须指定 class_name（可用 list_classes 查询班级名）')
    grade = grade or _resolve_grade(user, class_name)
    guards.class_scope(user, grade, class_name)
    pagination = attendance_service.get_attendance(
        class_name=class_name, grade=grade or None,
        date_from=date_from or None, date_to=date_to or None,
        page=max(1, int(page or 1)), per_page=max(1, min(int(per_page or 30), 200)))
    fields = ('student_no', 'student_name', 'grade', 'class_name',
              'attend_date', 'period', 'status', 'remark')
    return {'total': pagination.total, 'page': pagination.page,
            'pages': pagination.pages,
            'records': [respond.pick(r, fields) for r in pagination.items]}


def _stats(user, class_name, grade, month):
    from app.modules.workbench.services import attendance_service
    if not class_name:
        raise ValueError('必须指定 class_name')
    grade = grade or _resolve_grade(user, class_name)
    guards.class_scope(user, grade, class_name)
    return attendance_service.get_attendance_stats(class_name, grade=grade or None,
                                                   month=month or None)


def _student_records(user, student_no, month):
    from app.modules.workbench.services import attendance_service
    guards.find_student(student_no)
    guards.student_scope(user, student_no)
    rows = attendance_service.get_student_attendance(student_no, month=month or None)
    fields = ('attend_date', 'period', 'status', 'remark')
    return {'student_no': student_no, 'month': month or '全部',
            'count': len(rows), 'records': [respond.pick(r, fields) for r in rows]}


def _record(user, records):
    """批量写入考勤。仅需 student_no + attend_date + status，其余自动补齐/校验。"""
    from app.models.student import Student
    from app.modules.workbench.services import attendance_service
    if not isinstance(records, list) or not records:
        raise ValueError('records 必须是非空数组')
    if len(records) > 500:
        raise ValueError('单次最多写入 500 条，请分批提交')

    # 一次性取回涉及的学生，避免逐条查询（也用于校验学号真实存在）
    numbers = [str(r.get('student_no') or '').strip() for r in records if isinstance(r, dict)]
    students = {s.student_number: s for s in Student.query.filter(
        Student.student_number.in_([n for n in numbers if n])).all()}

    prepared, pairs = [], set()
    for idx, raw in enumerate(records, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f'第 {idx} 条不是对象')
        number = str(raw.get('student_no') or '').strip()
        attend_date = str(raw.get('attend_date') or '').strip()
        status = str(raw.get('status') or 'present').strip()
        if not number or not attend_date:
            raise ValueError(f'第 {idx} 条缺少 student_no 或 attend_date')
        if status not in _STATUS_CODES:
            raise ValueError(f'第 {idx} 条 status="{status}" 非法，可选：{"、".join(_STATUS_CODES)}')
        student = students.get(number)
        if student is None:
            raise ValueError(f'第 {idx} 条学号不存在：{number}')
        pairs.add((student.grade, student.class_name))
        prepared.append({
            'student_no': number,
            'student_name': student.name,
            'grade': student.grade,
            'class_name': student.class_name,
            'attend_date': attend_date,
            'period': raw.get('period'),
            'status': status,
            'remark': raw.get('remark') or '',
        })

    # 逐班校验范围：越界直接拒绝整批（避免"部分写入"造成的脏数据）
    for grade, class_name in sorted(pairs):
        guards.class_scope(user, grade, class_name)

    created, skipped = attendance_service.record_attendance(prepared, user.id)
    return {'created': len(created), 'skipped': skipped,
            'message': f'已记录 {len(created)} 条考勤' + (f'，{skipped} 条被忽略' if skipped else '')}


def register(server):
    """把本模块的工具注册到 MCP 服务。"""

    @server.tool(name='get_attendance_records',
                 description='查询班级考勤记录（分页）；date_from/date_to 格式 YYYY-MM-DD，'
                             'page 默认 1，per_page 默认 30')
    async def get_attendance_records(class_name: str, grade: str = '',
                                     date_from: str = '', date_to: str = '',
                                     page: int = 1, per_page: int = 30) -> str:
        params = {'class_name': class_name, 'grade': grade, 'date_from': date_from,
                  'date_to': date_to, 'page': page, 'per_page': per_page}
        return await toolkit.db_call(
            lambda u: _records(u, class_name, grade, date_from, date_to, page, per_page),
            tool='get_attendance_records', params=params,
            perm='workbench.attendance_view')

    @server.tool(name='get_attendance_stats',
                 description='班级考勤统计：应到人数、出勤/缺勤/迟到/请假天数与出勤率；'
                             'month 格式 YYYY-MM，留空为全部')
    async def get_attendance_stats(class_name: str, grade: str = '',
                                   month: str = '') -> str:
        return await toolkit.db_call(lambda u: _stats(u, class_name, grade, month),
                                     tool='get_attendance_stats',
                                     params={'class_name': class_name, 'grade': grade,
                                             'month': month},
                                     perm='workbench.attendance_view')

    @server.tool(name='get_student_attendance',
                 description='查询单个学生的考勤明细；month 格式 YYYY-MM，留空为全部')
    async def get_student_attendance(student_no: str, month: str = '') -> str:
        return await toolkit.db_call(lambda u: _student_records(u, student_no, month),
                                     tool='get_student_attendance',
                                     params={'student_no': student_no, 'month': month},
                                     perm='workbench.attendance_view')

    @server.tool(name='record_attendance',
                 description='【写操作】批量记录考勤。records 为对象数组，每项至少含 '
                             'student_no、attend_date(YYYY-MM-DD)，可选 status'
                             '（present出勤/absent缺勤/late迟到/leave请假，默认 present）、'
                             'period、remark；学生姓名与班级由学号自动补齐')
    async def record_attendance(records: list) -> str:
        return await toolkit.db_write(lambda u: _record(u, records),
                                      tool='record_attendance',
                                      params={'count': len(records) if isinstance(records, list) else 0},
                                      perm='workbench.attendance')