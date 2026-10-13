# StuLink v1.19.47.4 2026-10-10
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""教务工作台首页（2026-09-26）。

教务模块此前没有统一入口：侧栏进去就是一个个子页面，看不到"整体情况"和
"待办事项"。本页把教务关心的 KPI、待办与常用入口汇总到一处：

- KPI（2026-10-12 用户口径改为实时数据）：今日/本月查课 + 今日覆盖率、
  今日/本月迟到、今日/本月缺勤、待审批调课申请、教师业绩待审核、
  材料回收率与待回收份数
- 待办：待审调课、待审业绩、进行中问卷（都带一键跳转）
- 常用入口：课表/任课/备课组长/教师名单/查课/业绩/问卷/调课
- 最近动态：academic 模块的审计日志（谁在什么时候做了什么）

数据全部实时统计（跨 timetable/academic/表单收集/主库几处查询），无新增落库。
"""
import logging
from datetime import date

from flask import render_template
from flask_login import login_required, current_user
from sqlalchemy import func

from app.extensions import db
from app.models import OperationLog, Student, User
from app.models.academic import (FormTemplate, INSPECTION_EXEMPT_KEYS,
                                 InspectionRecord, TeacherAchievement)
from app.models.timetable import ScheduleSwap, ScheduleEntry
from app.modules.academic import bp
from app.modules.academic.services.access_scope import (
    apply_academic_scope,
)
from app.modules.academic.services import schedule_service as sch_svc
from app.modules.academic.services.schedule_common import get_active_schedule
from app.utils.decorators import perm_required

RECENT_LIMIT = 8

logger = logging.getLogger(__name__)


def _first_day_of_month():
    today = date.today()
    return today.replace(day=1)


def _count(query):
    """安全计数：单表查询失败不至于让整个工作台 500"""
    try:
        return query.count()
    except Exception:  # noqa: BLE001
        return 0


def _today_expected_cells(today):
    """今日应查（= 当天有课的课表格子数），用于「今日查课覆盖率」的分母。

    口径与查课页（inspection_stats.coverage）**完全一致**：按当前启用学期的周课表
    估算当天的课次（不区分单双周），且不做年级/班级范围过滤 —— 覆盖率是"全校今天
    被查了多少课"的宏观指标，与查课页显示的数字必须对得上。
    """
    try:
        from app.models.timetable import ScheduleEntry
        from app.modules.academic.services import term_service
        sched, _wk = term_service.resolve_schedule_by_date(today)
        if not sched:
            return 0
        return db.session.query(func.count(ScheduleEntry.id)).filter(
            ScheduleEntry.term_schedule_id == sched.id,
            ScheduleEntry.is_deleted.is_(False),
            ScheduleEntry.weekday == today.isoweekday()).scalar() or 0
    except Exception as _e:  # noqa: BLE001 统计失败不影响工作台渲染
        logger.warning('academic_home 今日应查统计失败（已降级为 0）: %s', _e)
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
        entry_q = ScheduleEntry.query.filter_by(
            term_schedule_id=ts.id, is_deleted=False)
        entry_q = apply_academic_scope(entry_q, current_user, ScheduleEntry)
        class_q = db.session.query(ScheduleEntry.grade, ScheduleEntry.class_name).filter(
            ScheduleEntry.term_schedule_id == ts.id,
            ScheduleEntry.is_deleted.is_(False))
        class_q = apply_academic_scope(class_q, current_user,
                                       ScheduleEntry).distinct()
        class_rows = class_q.all()
        timetable['entries'] = _count(entry_q)
        timetable['classes'] = len([r for r in class_rows if r[1]])
        # v1.19.47.4 审核（🟡-1）：原实现用 _count(聚合查询) 会对“已聚合的 1 行”再 .count()，
        # 结果恒为 1；改为 .scalar() 取 distinct teacher_uid 真实去重数。
        try:
            teacher_q = db.session.query(
                func.count(func.distinct(ScheduleEntry.teacher_uid))).filter(
                    ScheduleEntry.term_schedule_id == ts.id,
                    ScheduleEntry.is_deleted.is_(False))
            teacher_q = apply_academic_scope(teacher_q, current_user,
                                             ScheduleEntry)
            timetable['teachers'] = teacher_q.scalar() or 0
        except Exception:  # noqa: BLE001
            timetable['teachers'] = 0

    insp_q = apply_academic_scope(
        InspectionRecord.query.filter(InspectionRecord.inspect_date >= month_start),
        current_user, InspectionRecord)
    insp_today_q = apply_academic_scope(
        InspectionRecord.query.filter(InspectionRecord.inspect_date == today),
        current_user, InspectionRecord)
    students_q = apply_academic_scope(Student.query, current_user, Student)
    class_rows_q = db.session.query(Student.grade, Student.class_name)
    class_rows_q = apply_academic_scope(class_rows_q, current_user, Student)
    class_rows = class_rows_q.distinct().all()
    insp_month = _count(insp_q)
    # 「考试」不算异常（与查课统计页同口径，见 models/academic.py::INSPECTION_EXEMPT_KEYS）
    insp_abnormal = _count(insp_q.filter(
        InspectionRecord.result.notin_(tuple(INSPECTION_EXEMPT_KEYS))))

    # ── 查课（2026-10-12 用户口径：今日为主、本月为辅）──────────────
    # 记录本身按用户数据范围过滤（与查课页一致）；覆盖率分母（应查）为全校当天课次。
    insp_base = apply_academic_scope(InspectionRecord.query, current_user,
                                     InspectionRecord)
    insp_today_q = insp_base.filter(InspectionRecord.inspect_date == today)
    insp_month_q = insp_base.filter(InspectionRecord.inspect_date >= month_start,
                                    InspectionRecord.inspect_date <= today)
    insp_today = _count(insp_today_q)
    insp_expected = _today_expected_cells(today)
    kpi = {
        'insp_today': insp_today,
        'insp_month': _count(insp_month_q),
        # 覆盖率 = 今日已查 / 今日应查；当天无课（分母 0）时不给百分比（模板显示 —）
        'insp_today_expected': insp_expected,
        'insp_today_rate': (insp_today * 100 // insp_expected) if insp_expected else None,
        'late_today': _count(insp_today_q.filter(InspectionRecord.result == 'late')),
        'late_month': _count(insp_month_q.filter(InspectionRecord.result == 'late')),
        'absent_today': _count(insp_today_q.filter(InspectionRecord.result == 'absent')),
        'absent_month': _count(insp_month_q.filter(InspectionRecord.result == 'absent')),
    }

    # ── 材料回收（进行中的收集：回收率 / 待回收份数）──────────────
    # 口径 = 表单汇总看板 get_submission_stats：应交/已交按「当前轮」算，
    # 驳回（需重交）不算已交；对所有「进行中(open)」的收集汇总。
    # 与「问卷收集」管理页一致要求 academic.edit —— 无权限者看不到数字（模板显示 —）。
    recycle = {'rate': None, 'pending': 0, 'forms': 0}
    if current_user.has_perm('academic.edit'):
        try:
            from app.modules.academic.services import form_summary_service as fss
            tpls = FormTemplate.query.filter_by(status='open').all()
            exp_n = sub_n = 0
            for t in tpls:
                st = fss.get_submission_stats(t.id)
                exp_n += st['expected']
                sub_n += st['submitted']
            recycle = {
                'rate': (round(sub_n * 100.0 / exp_n, 1) if exp_n else None),
                'pending': max(exp_n - sub_n, 0),
                'forms': len(tpls),
            }
        except Exception as _e:  # noqa: BLE001 统计失败不影响工作台渲染
            logger.warning('academic_home 材料回收统计失败（已降级）: %s', _e)
    kpi['recycle'] = recycle
    is_reviewer = (current_user.role == 'admin' or
                   current_user.has_perm('academic.timetable'))
    # 2026-10-10：待审数按批次折叠（与 /academic/swap 列表、待审徽章同一口径）——
    # 一次统一调课 260 门课在待办里也只能算 1 件，不能把审批人吓死
    from app.modules.academic.services.swap_service import batch_rep_filter
    swap_q = ScheduleSwap.query.filter_by(status='pending').filter(batch_rep_filter())
    if not is_reviewer:
        if current_user.has_perm('academic.swap'):
            from app.modules.academic.services.teacher_service import teacher_of_user
            teacher = teacher_of_user(current_user)
            applicant_uid = (teacher.teacher_uid if teacher else
                             (current_user.username or str(current_user.id))[:16])
            swap_q = swap_q.filter_by(applicant_uid=applicant_uid)
        else:
            swap_q = swap_q.filter(False)
    todo = {
        'swaps': _count(swap_q),
        'achievements': (_count(TeacherAchievement.query.filter_by(status='pending'))
                         if current_user.has_perm('academic.edit') else 0),
        'forms': (_count(FormTemplate.query.filter_by(status='open'))
                  if current_user.has_perm('academic.edit') else 0),
    }

    # 最近动态（审计日志，取 academic 模块最近若干条）
    recent = []
    try:
        logs_q = OperationLog.query.filter_by(module='academic')
        if not (current_user.role == 'admin' or
                current_user.has_perm('academic.timetable')):
            logs_q = logs_q.filter_by(user_id=current_user.id)
        logs = logs_q.order_by(OperationLog.id.desc()).limit(RECENT_LIMIT).all()
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
                           kpi=kpi, todo=todo,
                           recent=recent, ts=ts, today=today,
                           schedules=sch_svc.list_schedules(),
                           can_edit=current_user.has_perm('academic.edit'))
