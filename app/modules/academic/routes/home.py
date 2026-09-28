# StuLink v1.18.2.2 2026-09-28
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""教务工作台首页（2026-09-26）。

教务模块此前没有统一入口：侧栏进去就是一个个子页面，看不到"整体情况"和
"待办事项"。本页把教务关心的 KPI、待办与常用入口汇总到一处：

- KPI：在校班级/学生、在职教师、本学期课表条目、本月查课与异常
- 待办：待审调课、待审业绩、进行中问卷（都带一键跳转）
- 常用入口：课表/任课/备课组长/教师名单/查课/业绩/问卷/调课
- 最近动态：academic 模块的审计日志（谁在什么时候做了什么）

数据全部实时统计（跨 timetable/academic/主库三处查询），无新增落库。
"""
from datetime import date

from flask import render_template
from flask_login import login_required, current_user
from sqlalchemy import func

from app.extensions import db
from app.models import OperationLog, Student, User
from app.models.academic import (FormTemplate, InspectionRecord, SubjectLeader,
                                 Teacher, TeacherAchievement)
from app.models.timetable import ScheduleEntry, ScheduleSwap, TermSchedule
from app.modules.academic import bp
from app.modules.academic.services import schedule_service as sch_svc
from app.modules.academic.services.schedule_common import get_active_schedule
from app.utils.decorators import perm_required

RECENT_LIMIT = 8


def _first_day_of_month():
    today = date.today()
    return today.replace(day=1)


def _count(query):
    """安全计数：单表查询失败不至于让整个工作台 500"""
    try:
        return query.count()
    except Exception:  # noqa: BLE001
        return 0


@bp.route('/')
@login_required
@perm_required('academic.view')
def academic_home():
    """教务工作台：KPI + 待办 + 快捷入口 + 最近动态。"""
    today = date.today()
    month_start = _first_day_of_month()

    ts = get_active_schedule()
    timetable = {
        'ts': ts,
        'entries': 0,
        'classes': 0,
        'teachers': 0,
    }
    if ts:
        timetable['entries'] = _count(ScheduleEntry.query.filter_by(
            term_schedule_id=ts.id, is_deleted=False))
        rows = db.session.query(ScheduleEntry.grade, ScheduleEntry.class_name).filter(
            ScheduleEntry.term_schedule_id == ts.id,
            ScheduleEntry.is_deleted.is_(False)).distinct().all()
        timetable['classes'] = len([r for r in rows if r[1]])
        # v1.18.2.2 审核（🟡-1）：原实现用 _count(聚合查询) 会对“已聚合的 1 行”再 .count()，
        # 结果恒为 1；改为 .scalar() 取 distinct teacher_uid 真实去重数。
        try:
            timetable['teachers'] = db.session.query(
                func.count(func.distinct(ScheduleEntry.teacher_uid))).filter(
                    ScheduleEntry.term_schedule_id == ts.id,
                    ScheduleEntry.is_deleted.is_(False)).scalar() or 0
        except Exception:  # noqa: BLE001
            timetable['teachers'] = 0

    insp_month = _count(InspectionRecord.query.filter(
        InspectionRecord.inspect_date >= month_start))
    insp_abnormal = _count(InspectionRecord.query.filter(
        InspectionRecord.inspect_date >= month_start,
        InspectionRecord.result != 'normal'))

    class_rows = db.session.query(Student.grade, Student.class_name).distinct().all()

    kpi = {
        'students': _count(Student.query),
        'classes': len([r for r in class_rows if r[1]]),
        'teachers_active': _count(Teacher.query.filter_by(status='active')),
        'teachers_total': _count(Teacher.query),
        'insp_month': insp_month,
        'insp_abnormal': insp_abnormal,
        'insp_today': _count(InspectionRecord.query.filter(
            InspectionRecord.inspect_date == today)),
        'leaders': _count(SubjectLeader.query),
    }

    todo = {
        'swaps': _count(ScheduleSwap.query.filter_by(status='pending')),
        'achievements': _count(TeacherAchievement.query.filter_by(status='pending')),
        'forms': _count(FormTemplate.query.filter_by(status='open')),
    }

    # 最近动态（审计日志，取 academic 模块最近若干条）
    recent = []
    try:
        logs = (OperationLog.query.filter_by(module='academic')
                .order_by(OperationLog.id.desc()).limit(RECENT_LIMIT).all())
        user_ids = {l.user_id for l in logs if l.user_id}
        names = {}
        if user_ids:
            for uid, real_name in db.session.query(User.id, User.real_name).filter(
                    User.id.in_(user_ids)).all():
                names[uid] = real_name
        for l in logs:
            recent.append({
                'time': l.created_at.strftime('%m-%d %H:%M') if l.created_at else '',
                'user': names.get(l.user_id, '系统'),
                'action': l.action or '',
                'target': l.target_type or '',
                'detail': (l.detail or '')[:80],
                'severity': l.severity or 'INFO',
            })
    except Exception:  # noqa: BLE001
        recent = []

    return render_template('academic/index.html',
                           kpi=kpi, todo=todo, timetable=timetable,
                           recent=recent, ts=ts, today=today,
                           schedules=sch_svc.list_schedules(),
                           can_edit=current_user.has_perm('academic.edit'))
