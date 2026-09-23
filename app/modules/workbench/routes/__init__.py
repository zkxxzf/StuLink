# StuLink v1.18.0 2026-09-23
# 教师工作台：我的信息 / 今日课程 / 我的课表 / 我的业绩 / 手机号修改
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import re
from datetime import date

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user

from app.extensions import db
from app.models import User
from app.models.academic import (TimetableEntry, TeacherAchievement, Teacher,
                                 ACHIEVEMENT_CATEGORIES, ACHIEVEMENT_LEVELS,
                                 ACHIEVEMENT_STATUS, CourseSwap)
from app.modules.academic.services import teacher_service
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
    if teacher:
        entries = (TimetableEntry.query.filter_by(teacher_uid=teacher.teacher_uid)
                   .order_by(TimetableEntry.weekday, TimetableEntry.period).all())
        weekday_today = date.today().isoweekday()
        today_entries = [e for e in entries if e.weekday == weekday_today]
        achievements = (TeacherAchievement.query
                        .filter_by(teacher_uid=teacher.teacher_uid)
                        .order_by(TeacherAchievement.created_at.desc()).all())
        # 调课：当前教师 pending 状态的记录
        pending_swaps = (CourseSwap.query
                         .filter_by(applicant_uid=teacher.teacher_uid, status='pending')
                         .order_by(CourseSwap.created_at.desc())
                         .limit(10).all())

    return render_template('workbench/index.html', teacher=teacher,
                           entries=entries, today_entries=today_entries,
                           achievements=achievements,
                           categories=ACHIEVEMENT_CATEGORIES,
                           levels=ACHIEVEMENT_LEVELS,
                           status_map=ACHIEVEMENT_STATUS,
                           weekday_names=_WEEKDAYS,
                           is_head=(current_user.role == 'homeroom_teacher'),
                           today=date.today(),
                           pending_swaps=pending_swaps)


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
