# StuLink v1.18.8.0 2026-10-09
# 教师工作台：我的信息 / 今日课程 / 我的课表 / 我的业绩 / 手机号修改
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import re
from datetime import date

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, current_app)
from flask_login import login_required, current_user

from app.extensions import db
from app.models import User
from app.models.academic import (TeacherAchievement, Teacher,
                                 ACHIEVEMENT_CATEGORIES, ACHIEVEMENT_LEVELS,
                                 ACHIEVEMENT_STATUS)
from app.models.timetable import (ScheduleEntry,  # 调课（2026-10-09：改读 timetable.db 的在用的表）
                                  ScheduleSwap, TermSchedule)
from app.modules.academic.services import schedule_service, teacher_service
from app.modules.academic.services.access_scope import (
    visible_academic_class_scope, visible_academic_grades)
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation

bp = Blueprint('workbench', __name__, url_prefix='/workbench')

_PHONE_RE = re.compile(r'^1\d{10}$')
_WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
_CATEGORY_KEYS = {k for k, _ in ACHIEVEMENT_CATEGORIES}


def _parse_date(value):
    try:
        return date.fromisoformat((value or '').strip())
    except ValueError:
        return None


@bp.route('/')
@login_required
def index():
    """教师工作台首页：只展示当前登录教师本人的数据"""
    teacher = teacher_service.teacher_of_user(current_user)

    entries, today_entries, achievements = [], [], []
    pending_swaps = []
    active_term = None
    if teacher:
        # 2026-10-10：课表改读 timetable.db 的 schedule_entries（与教务端「我的课表」同源）。
        # 旧表 academic.timetable_entries 已停用，此前读旧表导致本卡片恒为空。
        try:
            active_term = schedule_service.get_active_schedule()
            if active_term:
                view = schedule_service.get_teacher_view(active_term.id,
                                                         teacher.teacher_uid)
                entries = sorted(view.get('entries') or [],
                                 key=lambda e: (e.get('weekday') or 0,
                                                e.get('period_number') or 0))
        except Exception:   # 课表库异常（bind 未配置 / 表缺失）只降级为空态，不拖垮首页
            db.session.rollback()
            current_app.logger.exception('工作台课表加载失败 teacher_uid=%s',
                                         teacher.teacher_uid)
        weekday_today = date.today().isoweekday()
        today_entries = [e for e in entries if e['weekday'] == weekday_today]
        achievements = (TeacherAchievement.query
                        .filter_by(teacher_uid=teacher.teacher_uid)
                        .order_by(TeacherAchievement.created_at.desc()).all())
        # 调课：当前教师 pending 状态的记录
        # 2026-10-09：改读 timetable.db 的 ScheduleSwap —— 调课模块 2026-09 起已整表迁到
        # timetable.db；旧的 CourseSwap 表（读它会一直显示"没有待审调课"）已废弃，
        # 并已于 2026-10-10 随教务分库删除。
        rows = (ScheduleSwap.query
                .filter_by(applicant_uid=teacher.teacher_uid, status='pending')
                .order_by(ScheduleSwap.created_at.desc())
                .limit(10).all())
        pending_swaps = []
        for sw in rows:
            orig = (db.session.get(ScheduleEntry, sw.original_entry_id)
                    if sw.original_entry_id else None)
            pending_swaps.append({
                'id': sw.id,
                'swap_type': sw.swap_type,
                'original_subject': orig.subject if orig else '（原条目已删）',
                'original_class': f'{orig.grade}{orig.class_name}' if orig else '',
                'original_date': sw.source_date or sw.swap_date,
                'original_period': orig.period_number if orig else sw.new_period,
                'reason': sw.reason,
                'created_at': sw.created_at,
            })

    return render_template('workbench/index.html', teacher=teacher,
                           entries=entries, today_entries=today_entries,
                           active_term=active_term,
                           achievements=achievements,
                           categories=ACHIEVEMENT_CATEGORIES,
                           levels=ACHIEVEMENT_LEVELS,
                           status_map=ACHIEVEMENT_STATUS,
                           weekday_names=_WEEKDAYS,
                           is_head=(current_user.role == 'homeroom_teacher'),
                           today=date.today(),
                           pending_swaps=pending_swaps)


@bp.route('/my-schedule')
@login_required
@perm_required('workbench.class_view')
def my_schedule():
    """我的课表：教师本人视角的完整周课表（网格 + 课时统计 + 学科占比 + 任教班级）。

    2026-10-10：把教务端 /academic/my-schedule 的版式整体搬进工作台，教师不必再跳到
    教务模块。复用同一 service（schedule_service.get_teacher_view，数据源 timetable.db
    的 schedule_entries）与同一模板（my_mode=True 时教务专属的导出/学期管理按钮不渲染）；
    权限用 workbench.class_view —— 教务侧依赖的 academic.view 在两个权限种子里对教师组
    不一致，而 class_view 在两处都有。?sid= 回看历史学期，?week=N 切换周次。
    """
    sid = request.args.get('sid', type=int)
    ts = (db.session.get(TermSchedule, sid) if sid
          else schedule_service.get_active_schedule())
    teacher = teacher_service.teacher_of_user(current_user) if ts else None
    week = request.args.get('week', type=int) or None

    view = None
    if ts and teacher:
        view = schedule_service.get_teacher_view(
            ts.id, teacher.teacher_uid, week=week,
            allowed_grades=visible_academic_grades(current_user),
            allowed_classes=visible_academic_class_scope(current_user))

    return render_template('academic/schedule_teacher.html',
                           ts=ts, uid=teacher.teacher_uid if teacher else '',
                           teacher=teacher, view=view, teachers=[],
                           week=week, schedules=schedule_service.list_schedules(),
                           can_edit=False, my_mode=True)


@bp.route('/phone', methods=['POST'])
@login_required
def change_phone():
    """教师修改自己的手机号：校验密码与全局唯一后，同步登录名与教师名单"""
    teacher = teacher_service.teacher_of_user(current_user)
    new_phone = (request.form.get('new_phone') or '').strip()
    pwd = request.form.get('current_password') or ''

    if not current_user.check_password(pwd):
        flash('当前密码不正确', 'danger')
        return redirect(url_for('workbench.index'))
    if not _PHONE_RE.match(new_phone):
        flash('新手机号格式不正确（11 位，1 开头）', 'danger')
        return redirect(url_for('workbench.index'))

    exist = User.query.filter_by(username=new_phone).first()
    if exist and exist.id != current_user.id:
        flash('该手机号已被其他账号使用', 'danger')
        return redirect(url_for('workbench.index'))
    if teacher:
        dup = Teacher.query.filter_by(phone=new_phone).first()
        if dup and dup.id != teacher.id:
            flash('该手机号已被其他教师使用', 'danger')
            return redirect(url_for('workbench.index'))

    old_username = current_user.username
    current_user.username = new_phone
    if teacher:
        teacher.phone = new_phone
    db.session.commit()
    log_operation(current_user, '修改', '手机号', current_user.id,
                  f'{old_username} → {new_phone}', module='workbench')
    flash('手机号已更新，下次登录请使用新手机号', 'success')
    return redirect(url_for('workbench.index'))


@bp.route('/achievement/add', methods=['POST'])
@login_required
def achievement_add():
    """教师提交业绩（进入待审核，由教务审核）"""
    teacher = teacher_service.teacher_of_user(current_user)
    if not teacher:
        flash('当前账号尚未关联教务教师名单，请联系管理员在教务模块中关联', 'warning')
        return redirect(url_for('workbench.index'))

    category = (request.form.get('category') or '').strip()
    title = (request.form.get('title') or '').strip()
    if category not in _CATEGORY_KEYS or not title:
        flash('请填写完整的业绩信息（类别与名称必填）', 'danger')
        return redirect(url_for('workbench.index'))

    rec = TeacherAchievement(
        teacher_uid=teacher.teacher_uid, teacher_name=teacher.name,
        category=category, title=title,
        level=(request.form.get('level') or '').strip() or None,
        obtain_date=_parse_date(request.form.get('obtain_date')),
        issuer=(request.form.get('issuer') or '').strip() or None,
        note=(request.form.get('note') or '').strip() or None,
        status='pending', submitted_by=current_user.id,
    )
    db.session.add(rec)
    db.session.commit()
    log_operation(current_user, '提交', '教师业绩', rec.id, title,
                  module='workbench')
    flash('业绩已提交，等待教务审核', 'success')
    return redirect(url_for('workbench.index'))
