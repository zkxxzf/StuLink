# StuLink v1.18.9.2 2026-10-10
# 教务 · 学期课表（timetable.db）：管理 / 视图 / 条目CRUD / 导入导出 / 查课联动 / JSON API
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""新课表系统路由。

- 管理端（/academic/schedule/...）：需 academic.timetable 权限
- 查看端（today / inspection 联动 / api）：需 academic.view 权限
- 教师个人课表：2026-10-10 起归教师工作台（workbench.my_schedule，?sid= 回看历史学期），
  本模块不再提供，旧地址 /academic/my-schedule 已下线
- 所有 JSON 接口统一返回 {success, message, data}
"""
from datetime import date, datetime

import io
import threading   # L-4：智能导入草稿的多线程保护

from flask import (render_template, request, redirect, url_for, flash,
                   send_file, abort, jsonify, session)
from flask_login import login_required, current_user
from sqlalchemy import func

from app.extensions import db
from app.models.timetable import (TermSchedule, ScheduleEntry, WEEKDAY_NAMES,
                                  PERIOD_NUMBER_CEILING, PERIOD_TYPES)
from app.modules.academic import bp
from app.modules.academic.services import schedule_service as svc
from app.modules.academic.services import term_service as tsvc
from app.modules.academic.services import teaching_scope_service
from app.modules.academic.services.access_scope import (
    academic_class_authorizer, academic_class_is_visible,
    academic_has_class_restrictions, apply_academic_scope,
    visible_academic_class_scope, visible_academic_grades,
)
from app.utils.decorators import perm_required
from app.utils.export_helpers import xl_row, xl_safe
from app.utils.helpers import log_operation

_XLSX_MIME = ('application/vnd.openxmlformats-officedocument'
              '.spreadsheetml.sheet')


# ─── 内部工具 ──────────────────────────────────────────────────────────────

def _json_ok(data=None, message='ok'):
    return jsonify({'success': True, 'message': message, 'data': data})


def _json_err(message, code=400):
    return jsonify({'success': False, 'message': message, 'data': None}), code


def _payload():
    """兼容 JSON 与表单两种提交方式"""
    if request.is_json:
        return request.get_json(silent=True) or {}
    return request.form.to_dict()


def _resolve_teacher(teacher_uid, teacher_name):
    """按教师编号补全姓名（跨库查 academic.db Teacher，失败静默）"""
    if teacher_uid and not teacher_name:
        try:
            from app.models.academic import Teacher
            t = Teacher.query.filter_by(teacher_uid=teacher_uid).first()
            if t:
                teacher_name = t.name
        except Exception:
            pass
    return teacher_uid or None, teacher_name or None


def _get_schedule_or_404(sid):
    ts = db.session.get(TermSchedule, sid)
    if not ts:
        abort(404)
    return ts


def _can_edit():
    return current_user.has_perm('academic.timetable')


def _week_param(ts=None):
    """解析 ?week=N 周次参数。

    2026-10-10：周次选择器已去掉「全部周」——用户未指定周次时，**默认取当前教学周**
    （与页面顶部「第 N 周」一致），不再默认铺开整学期。
    ts 不传（如导出接口）时保持旧行为：返回 None = 不过滤，导出的仍是整学期。
    """
    w = request.args.get('week', type=int)
    if w and w > 0:
        return w
    if ts is not None:
        try:
            return ts.get_current_week()   # 无法判断当前周（未配日期/不在学期内）时返回 None
        except Exception:  # noqa: BLE001  取值失败不影响页面，退回"不过滤"
            return None
    return None


def _editable(ts):
    """页面编辑态：有课表管理权限且学期未归档（archived 只读回看）"""
    return _can_edit() and ts.status != 'archived'


def _view_scope():
    """查看类页面的数据范围：统一放开为「全部年级 / 全部班级」（2026-10-10）。

    背景：用户要求「移除按年级筛选的限制，确保所有年级的课表均可显示」。
    写操作（录课 / 导入 / 调课 / 审批）的范围校验不受影响，仍在各自入口把关。
    返回 (allowed_grades, allowed_classes)，None = 不过滤。
    """
    return None, None


def _readonly_guard(ts, back_endpoint, **back_kwargs):
    """写操作拦截：归档学期只读，返回重定向响应或 None"""
    if ts.status == 'archived':
        flash('历史学期课表为只读，不能修改', 'warning')
        return redirect(url_for(back_endpoint, **back_kwargs))
    return None


# ═══════════════════════════════════════════════════════════════════════════
# 管理端页面
# ═══════════════════════════════════════════════════════════════════════════

@bp.route('/schedule')
@login_required
@perm_required('academic.timetable')
def schedule_manage():
    """学期课表管理首页：只列「在用 / 待启用」的学期 + 创建/激活/归档/删除入口。

    2026-10-10（用户报障）：原实现把**全部学期**都列出来，于是 4 个已归档学期占了
    4 张卡，这一页看起来就是个历史列表 —— 而页面右上角本来就有「历史课表」入口。
    现在归档的学期不再在这里出现，统一到「历史课表」页（回看 / 快照 / 重新启用 /
    作为底版），本页只提示归档数量 + 入口，职责分干净。
    """
    all_terms = svc.list_schedules()
    schedules = [ts for ts in all_terms if ts.status != 'archived']
    archived_count = len(all_terms) - len(schedules)
    ids = [ts.id for ts in schedules]
    # 条目数一次聚合（原为逐学期 count，N 次查询）
    counts = {}
    if ids:
        counts = dict(
            db.session.query(ScheduleEntry.term_schedule_id,
                             func.count(ScheduleEntry.id))
            .filter(ScheduleEntry.is_deleted.is_(False),
                    ScheduleEntry.term_schedule_id.in_(ids))
            .group_by(ScheduleEntry.term_schedule_id).all())
    # 各学期的年级/班级（用于「年级课表」入口，不再硬编码高一）
    grade_map = svc.grade_class_map(ids)
    return render_template('academic/schedule_manage.html',
                           schedules=schedules, counts=counts,
                           grade_map=grade_map)


@bp.route('/schedule/create', methods=['POST'])
@login_required
@perm_required('academic.timetable')
def schedule_create():
    """创建学期（自动带 13 节默认节次）"""
    name = (request.form.get('name') or '').strip()
    school_year = (request.form.get('school_year') or '').strip()
    term = (request.form.get('term') or '').strip()
    description = (request.form.get('description') or '').strip()
    if not name or not school_year or not term:
        flash('名称、学年、学期为必填项', 'danger')
        return redirect(url_for('academic.schedule_manage'))
    try:
        ts = svc.create_schedule(name, school_year, term,
                                 description=description or None,
                                 created_by=current_user)
    except Exception:
        db.session.rollback()
        flash('创建失败，请重试', 'danger')
        return redirect(url_for('academic.schedule_manage'))
    log_operation(current_user, '新增', '学期课表', ts.id, ts.name, module='academic')
    flash(f'学期「{ts.name}」已创建（未启用状态，含 13 节默认作息）', 'success')
    return redirect(url_for('academic.schedule_manage'))


@bp.route('/schedule/<int:sid>/edit', methods=['POST'])
@login_required
@perm_required('academic.timetable')
def schedule_edit(sid):
    """编辑学期基本信息（名称 / 学年 / 学期 / 说明）。

    2026-10-10 新增：学期卡片此前只能建、激活、归档、删，**名称写错了没法改**
    （用户报障「没办法编辑」）。这里补上基本信息编辑；归档学期仍只读。
    """
    ts = _get_schedule_or_404(sid)
    guard = _readonly_guard(ts, 'academic.schedule_manage')
    if guard:
        return guard
    name = (request.form.get('name') or '').strip()
    school_year = (request.form.get('school_year') or '').strip()
    term = (request.form.get('term') or '').strip()
    description = (request.form.get('description') or '').strip()
    if not name or not school_year or not term:
        flash('名称、学年、学期为必填项', 'danger')
        return redirect(url_for('academic.schedule_manage'))
    # 同名学期会让顶部切换下拉分不清，这里挡掉（改回自己原名不算重复）
    dup = TermSchedule.query.filter(TermSchedule.id != sid,
                                    TermSchedule.name == name).first()
    if dup:
        flash(f'已存在同名学期「{name}」，请换一个名称', 'danger')
        return redirect(url_for('academic.schedule_manage'))
    old_name = ts.name
    try:
        svc.update_schedule(sid, name=name, school_year=school_year,
                            term=term, description=description or None)
    except Exception:
        db.session.rollback()
        flash('保存失败，请重试', 'danger')
        return redirect(url_for('academic.schedule_manage'))
    log_operation(current_user, '更新', '学期课表', sid,
                  f'{old_name} → {name}', module='academic')
    flash(f'学期信息已更新：{old_name} → {name}', 'success')
    return redirect(url_for('academic.schedule_manage'))


@bp.route('/schedule/<int:sid>/activate', methods=['POST'])
@login_required
@perm_required('academic.timetable')
def schedule_activate(sid):
    """激活学期（其他 active 自动归档）"""
    ts = _get_schedule_or_404(sid)
    try:
        svc.activate_schedule(sid)
    except ValueError as e:
        flash(str(e), 'danger')
        return redirect(url_for('academic.schedule_manage'))
    log_operation(current_user, '激活', '学期课表', sid, ts.name, module='academic')
    flash(f'学期「{ts.name}」已激活；原启用学期的课表已归档进「历史课表」', 'success')
    return redirect(url_for('academic.schedule_manage'))


@bp.route('/schedule/<int:sid>/delete', methods=['POST'])
@login_required
@perm_required('academic.timetable')
def schedule_delete(sid):
    """删除学期（仅 draft）"""
    ts = _get_schedule_or_404(sid)
    name = ts.name
    try:
        svc.delete_schedule(sid)
    except ValueError as e:
        flash(str(e), 'danger')
        return redirect(url_for('academic.schedule_manage'))
    log_operation(current_user, '删除', '学期课表', sid, name, module='academic')
    flash(f'学期「{name}」已删除', 'success')
    return redirect(url_for('academic.schedule_manage'))


# 2026-10-10：原「大课表」页（GET /schedule/<sid>/master）已整体下线。
# 它做的事 = 年级标签 + 班级 pills + AJAX 换班加载"行=节次、列=星期"的班级网格，
# 与「全校总课表」（schedule_overview：行=节次、列=班级，一屏看全校）重叠；
# 编辑能力（条目弹窗、拖拽换格）在「年级课表 / 班级课表」里同样具备，删除无功能损失。
# 合并 origin/master 时保留本分支的删除（其模板 schedule_master.html 已一并删除）。


@bp.route('/schedule/<int:sid>/grade/<grade>')
@login_required
@perm_required('academic.timetable')
def schedule_grade(sid, grade):
    """年级课表：年级内班级标签切换（?class= 指定班级）"""
    ts = _get_schedule_or_404(sid)
    week = _week_param(ts)
    data = svc.get_grade_view(sid, grade, week=week)
    classes = data['classes']
    cur_class = (request.args.get('class') or '').strip()
    if cur_class not in classes:
        cur_class = classes[0] if classes else ''
    view = svc.get_class_view(sid, grade, cur_class, week=week) if cur_class else \
        {'grid': {}, 'periods': data['periods'], 'stats': {}, 'total': 0}
    # 2026-10-10：页内年级切换（所有年级均可显示，不再只有入口那一个年级）
    all_grades = sorted(svc.get_grade_class_list(sid))
    if grade and grade not in all_grades:
        all_grades = sorted(all_grades + [grade])
    return render_template('academic/schedule_grade.html',
                           ts=ts, grade=grade, classes=classes,
                           all_grades=all_grades,
                           cur_class=cur_class, view=view, week=week,
                           schedules=svc.list_schedules(),
                           can_edit=_editable(ts))


@bp.route('/schedule/<int:sid>/class/<grade>/<class_name>')
@login_required
@perm_required('academic.timetable')
def schedule_class(sid, grade, class_name):
    """班级课表：13x7 网格 + 学科课时统计"""
    ts = _get_schedule_or_404(sid)
    week = _week_param(ts)
    view = svc.get_class_view(sid, grade, class_name, week=week)
    # 高中：班型 + 选科方向 + 选科组合（新高考 3+1+2），班级档案里有就显示
    from app.modules.academic.services.grade_utils import (class_profile_map,
                                                           grade_labels)
    meta = class_profile_map().get((grade, class_name), {})
    label = grade_labels([grade]).get(grade, grade)
    return render_template('academic/schedule_class.html',
                           ts=ts, grade=grade, class_name=class_name,
                           grade_label=label, class_meta=meta,
                           view=view, week=week,
                           schedules=svc.list_schedules(),
                           can_edit=_editable(ts))


@bp.route('/schedule/<int:sid>/teacher/<uid>')
@login_required
@perm_required('academic.timetable')
def schedule_teacher(sid, uid):
    """教师个人课表：搜索选择 + 网格 + 课时统计"""
    ts = _get_schedule_or_404(sid)
    week = _week_param(ts)
    view = svc.get_teacher_view(sid, uid, week=week)
    teacher = None
    try:
        from app.models.academic import Teacher
        teacher = Teacher.query.filter_by(teacher_uid=uid).first()
    except Exception:
        pass
    teachers = svc.get_all_teachers()
    return render_template('academic/schedule_teacher.html',
                           ts=ts, uid=uid, teacher=teacher, view=view,
                           teachers=teachers, week=week,
                           schedules=svc.list_schedules(),
                           can_edit=_editable(ts), my_mode=False)


_SHARE_SALT = 'stulink-schedule-share'
_SHARE_MAX_AGE = 86400 * 7   # 7 天


def _share_serializer():
    from itsdangerous import URLSafeTimedSerializer
    from flask import current_app
    return URLSafeTimedSerializer(current_app.secret_key, salt=_SHARE_SALT)


@bp.route('/schedule/<int:sid>/share', methods=['POST'])
@login_required
@perm_required('academic.view')
def schedule_share(sid):
    """签发课表分享短链（批次 D）。

    安全取舍：链接只承载"视图参数 + 7 天有效期"，**打开时依然要求登录且有
    academic.view 权限**，不会绕过鉴权把课表公开到校外 —— 它解决的是"登录后
    还要点四五下才能到某个班课表"的问题，而不是匿名访问。
    """
    _get_schedule_or_404(sid)
    data = _payload()
    # 2026-10-10：'master' 随大课表页下线，默认视图改 'overview'（全校总课表）
    view_type = (data.get('view') or 'overview').strip()
    if view_type not in ('overview', 'grade', 'class', 'teacher', 'room'):
        return _json_err('不支持的视图类型')
    payload = {
        'sid': sid,
        'view': view_type,
        'week': data.get('week') or None,
        'grade': (data.get('grade') or '').strip() or None,
        'class_name': (data.get('class_name') or '').strip() or None,
        'room': (data.get('room') or '').strip() or None,
        'uid': (data.get('uid') or '').strip() or None,
    }
    token = _share_serializer().dumps(payload)
    url = url_for('academic.schedule_shared', token=token, _external=True)
    log_operation(current_user, '分享', '课表链接', sid,
                  f'{view_type} {payload.get("grade") or ""}{payload.get("class_name") or ""}',
                  module='academic')
    return _json_ok({'url': url, 'expires_days': _SHARE_MAX_AGE // 86400})


@bp.route('/schedule/shared/<token>')
@login_required
@perm_required('academic.view')
def schedule_shared(token):
    """打开分享短链：校验签名与有效期后跳到对应的课表页面（仍受权限约束）"""
    try:
        payload = _share_serializer().loads(token, max_age=_SHARE_MAX_AGE)
    except Exception:
        flash('分享链接无效或已过期（有效期 7 天）', 'warning')
        return redirect(url_for('academic.schedule_manage'))
    sid = payload.get('sid')
    ts = db.session.get(TermSchedule, sid) if sid else None
    if not ts:
        flash('分享链接对应的学期已不存在', 'warning')
        return redirect(url_for('academic.schedule_manage'))
    week = payload.get('week')
    try:
        week = int(week) if week else None
    except (TypeError, ValueError):
        week = None
    view_type = payload.get('view')
    if view_type == 'class' and payload.get('grade') and payload.get('class_name'):
        return redirect(url_for('academic.schedule_class', sid=sid,
                                grade=payload['grade'], class_name=payload['class_name'],
                                week=week))
    if view_type == 'grade' and payload.get('grade'):
        return redirect(url_for('academic.schedule_grade', sid=sid,
                                grade=payload['grade'], week=week))
    if view_type == 'teacher' and payload.get('uid'):
        return redirect(url_for('academic.schedule_teacher', sid=sid,
                                uid=payload['uid'], week=week))
    if view_type == 'overview':
        return redirect(url_for('academic.schedule_overview', sid=sid, week=week))
    # 未知视图 / 无 grade 上下文的旧 'master' 短链 → 兜底到全校总课表
    return redirect(url_for('academic.schedule_overview', sid=sid, week=week))


@bp.route('/schedule/overview')
@login_required
@perm_required('academic.view')
def schedule_overview_index():
    """全校总课表入口（侧栏导航用）：解析当前学期后跳转。"""
    from app.modules.academic.services.schedule_common import get_active_schedule
    ts = get_active_schedule()
    if not ts:
        flash('尚未建立学期课表，请先创建学期', 'warning')
        return redirect(url_for('academic.schedule_manage'))
    return redirect(url_for('academic.schedule_overview', sid=ts.id))


@bp.route('/schedule/<int:sid>/overview')
@login_required
@perm_required('academic.view')
def schedule_overview(sid):
    """全校总课表（2026-09-25 新增；2026-10-10 三改为**竖版**）：三个年级并进一张表。

    版式：**行＝节次**（左列作息分组 + 节次名/时间）、**列＝班级**（两级表头：
    年级跨列 → 班级）；星期用顶部标签切换，每次只渲染一天（默认今天）。
    顶部筛选只保留「星期 / 年级 / 只看正课」——「选科方向 / 班型」筛选与
    「显示教室」开关已按用户要求去掉（使用率低、占空间）。

    query 参数：
    - week=N       只看第 N 周（单双周课表不会串）
    - grades=a,b   只看指定年级（默认全部）
    - day=N        看星期几（1-7，缺省=今天，周末退回周一）
    - main=1       隐藏「课间/午休」节次（打印常用）
    """
    ts = _get_schedule_or_404(sid)
    week = _week_param(ts)   # 2026-10-10：缺省 = 当前教学周（已去掉「全部周」）
    grades = [g.strip() for g in (request.args.get('grades') or '').split(',') if g.strip()]
    # 2026-10-10：查看类页面不再按年级限制可见范围（用户要求「所有年级的课表均可显示」）。
    # grades= 仍作为「自己选看哪个年级」的筛选参数；写操作/导入的范围校验保持不变。
    allowed_grades = None
    allowed_classes = None
    include_break = request.args.get('main') != '1'
    # 高中场景：按选科方向（物理/历史）与班型（强基班等）看总课表
    direction = (request.args.get('direction') or '').strip() or None
    class_type = (request.args.get('class_type') or '').strip() or None
    # day=1..7 看星期几：缺省=今天；今天不在课表范围（如周六日的 5 天课表）则退回周一
    today_wd = date.today().isoweekday()
    day = request.args.get('day', type=int)
    day_explicit = day is not None and 1 <= day <= 7
    if not day_explicit:
        day = today_wd if 1 <= today_wd <= 7 else 1

    # 2026-10-10：把"当前所选这一天"传进服务层，只构建该天网格——模板一次只渲染
    # 一天，其余 6 天的 to_dict 白干。全周能力保留：不传 weekday 的老调用（如导出
    # _export_overview_sheet）行为与优化前完全一致。
    # 2026-10-10 二次优化：缺省入口不再"先按今天算一遍 → 超范围再算一遍"（周末每天命中，
    # SQL 8→11、+7ms）；改由服务层在一次取数后用 prefer_weekday 决定实际渲染天。
    view = svc.get_overview_view(sid, week=week, grades=grades or None,
                                 include_break=include_break,
                                 direction=direction, class_type=class_type,
                                 allowed_grades=allowed_grades,
                                 allowed_classes=allowed_classes,
                                 weekday=day if day_explicit else None,
                                 prefer_weekday=None if day_explicit else day)
    if not day_explicit:
        day = view.get('day') or 1
    return render_template('academic/schedule_overview.html',
                           ts=ts, view=view, week=week, day=day, today_wd=today_wd,
                           grades=grades, include_break=include_break,
                           direction=direction, class_type=class_type,
                           schedules=svc.list_schedules(),
                           can_edit=_editable(ts))



@bp.route('/schedule/timetable')
@login_required
@perm_required('academic.view')
def schedule_timetable_index():
    """（2026-10-10）作息时间表已并入「节次配置」：旧入口解析学期后重定向。

    侧栏导航不再提供独立入口，保留本端点只为兼容旧书签/旧链接。
    """
    from app.modules.academic.services.schedule_common import get_active_schedule
    ts = get_active_schedule()
    if not ts:
        flash('尚未建立学期课表，请先创建学期', 'warning')
        return redirect(url_for('academic.schedule_manage'))
    return redirect(url_for('academic.schedule_periods', sid=ts.id))


def _minutes(hhmm):
    try:
        h, m = (hhmm or '').split(':')
        return int(h) * 60 + int(m)
    except Exception:  # noqa: BLE001
        return None


def _timetable_rows(sid):
    """作息表视图数据（2026-10-10 并入节次配置页）：几点上什么、多久。

    返回 (rows, stats)：rows 元素 {p, kind, dur}，kind ∈ reading/class/evening/break。
    高中教务的刚需是"贴墙的一张作息表"——早读几点、正课几节、午休多长、
    晚自习到几点；这里把 period_defs 按高中语义归类统计。
    """
    periods = [p.to_dict() for p in svc.get_periods(sid)]

    def _cls(p):
        name = p.get('period_name') or ''
        if p.get('period_type') == 'break':
            return 'break'
        if p.get('period_type') == 'evening':
            return 'evening'
        if '早读' in name or '晨读' in name:
            return 'reading'
        return 'class'

    rows = []
    stats = {'class': 0, 'reading': 0, 'evening': 0, 'break': 0,
             'class_min': 0, 'evening_min': 0}
    for p in periods:
        a, b = _minutes(p.get('start_time')), _minutes(p.get('end_time'))
        dur = (b - a) if (a is not None and b is not None and b > a) else None
        kind = _cls(p)
        rows.append({'p': p, 'kind': kind, 'dur': dur})
        stats[kind] = stats.get(kind, 0) + 1
        if kind == 'class' and dur:
            stats['class_min'] += dur
        if kind == 'evening' and dur:
            stats['evening_min'] += dur
    stats['class_text'] = {'class': '正课', 'reading': '早读', 'evening': '晚自习',
                           'break': '课间/午休'}
    return rows, stats


@bp.route('/schedule/<int:sid>/timetable')
@login_required
@perm_required('academic.view')
def schedule_timetable(sid):
    """（2026-10-10）作息时间表已并入「节次配置」页 → 重定向（旧链接保持可用）"""
    ts = _get_schedule_or_404(sid)
    return redirect(url_for('academic.schedule_periods', sid=ts.id))


@bp.route('/schedule/<int:sid>/periods', methods=['GET', 'POST'])
@login_required
@perm_required('academic.timetable')
def schedule_periods(sid):
    """节次时间配置（GET 表单 / POST 保存）"""
    ts = _get_schedule_or_404(sid)
    if request.method == 'POST':
        guard = _readonly_guard(ts, 'academic.schedule_periods', sid=sid)
        if guard:
            return guard
        nums = request.form.getlist('period_number')
        names = request.form.getlist('period_name')
        starts = request.form.getlist('start_time')
        ends = request.form.getlist('end_time')
        types = request.form.getlist('period_type')
        periods_data = []
        dropped = []      # 2026-10-10：编号非法的行不再静默丢弃，保存后给出提示
        for i, n in enumerate(nums):
            try:
                pn = int(n)
            except (ValueError, TypeError):
                dropped.append(str(n))
                continue
            # 2026-10-10：原来是 1..13 的业务上限（"一天最多 13 节"），现只挡非法编号 ——
            # 一天几节由学校自己定，系统不设上限
            if not (1 <= pn <= PERIOD_NUMBER_CEILING):
                dropped.append(str(pn))
                continue
            periods_data.append({
                'period_number': pn,
                'period_name': (names[i] if i < len(names) else '').strip() or f'第{pn}节',
                'start_time': (starts[i] if i < len(starts) else '').strip() or None,
                'end_time': (ends[i] if i < len(ends) else '').strip() or None,
                'period_type': (types[i] if i < len(types) else '').strip() or 'morning',
                'sort_order': i + 1,
            })
        if dropped:
            flash(f'有 {len(dropped)} 行的节次编号不合法（{"、".join(dropped[:5])}），已忽略；'
                  f'编号需为 1~{PERIOD_NUMBER_CEILING} 的整数', 'warning')
        try:
            result = svc.save_periods(sid, periods_data, remove_missing=True)
        except Exception:
            db.session.rollback()
            flash('保存失败，请检查时间格式', 'danger')
            return redirect(url_for('academic.schedule_periods', sid=sid))
        log_operation(current_user, '更新', '节次配置', sid,
                      f'{ts.name}：{len(periods_data)} 节'
                      f"{'，移除 ' + '、'.join(str(n) for n in result['removed']) if result['removed'] else ''}"
                      f"{'，保留在用 ' + '、'.join(str(n) for n in result['kept_in_use']) if result['kept_in_use'] else ''}",
                      module='academic')
        msg = '节次配置已保存'
        if result['removed']:
            msg += f"；已移除第 {'、'.join(str(n) for n in result['removed'])} 节"
        flash(msg, 'success')
        if result['kept_in_use']:
            nums = '、'.join(str(n) for n in result['kept_in_use'])
            flash(f'第 {nums} 节仍有课程安排，未移除（请先调整这些课，再删除节次）', 'warning')
        return redirect(url_for('academic.schedule_periods', sid=sid))

    periods = svc.get_periods(sid)
    has_entries = ScheduleEntry.query.filter_by(term_schedule_id=sid).first() is not None
    # 2026-10-10：作息表视图随本页一起展示（原「作息时间表」独立页面已合并进来）
    tt_rows, tt_stats = _timetable_rows(sid)
    return render_template('academic/schedule_periods.html',
                           ts=ts, periods=periods, period_types=PERIOD_TYPES,
                           tt_rows=tt_rows, tt_stats=tt_stats,
                           schedules=svc.list_schedules(),
                           can_reset_defaults=not has_entries,
                           global_info=svc.get_global_periods_info(),
                           can_edit=_editable(ts))


@bp.route('/schedule/<int:sid>/periods/reset', methods=['POST'])
@login_required
@perm_required('academic.timetable')
def schedule_periods_reset(sid):
    """重置为内置默认作息（早读 + 上午5 + 下午4 + 晚自习3）"""
    ts = _get_schedule_or_404(sid)
    guard = _readonly_guard(ts, 'academic.schedule_periods', sid=sid)
    if guard:
        return guard
    try:
        svc.reset_default_periods(sid)
    except ValueError as e:
        flash(str(e), 'warning')
        return redirect(url_for('academic.schedule_periods', sid=sid))
    log_operation(current_user, '重置', '节次配置', sid, ts.name, module='academic')
    flash('已重置为默认作息：早读、上午5节、下午4节、晚自习3节', 'success')
    return redirect(url_for('academic.schedule_periods', sid=sid))


@bp.route('/schedule/<int:sid>/periods/global-save', methods=['POST'])
@login_required
@perm_required('academic.timetable')
def schedule_periods_global_save(sid):
    """把本学期的作息存为「全局作息模板」（2026-10-10 新增）。

    学校作息一般全校固定，存一次之后：新建学期默认套用，任意学期可一键套用。
    """
    ts = _get_schedule_or_404(sid)
    guard = _readonly_guard(ts, 'academic.schedule_periods', sid=sid)
    if guard:
        return guard
    ok, msg = svc.save_global_periods(sid, operator=current_user)
    flash(msg, 'success' if ok else 'warning')
    if ok:
        log_operation(current_user, '更新', '全局作息模板', sid, ts.name,
                      module='academic')
    return redirect(url_for('academic.schedule_periods', sid=sid))


@bp.route('/schedule/<int:sid>/periods/global-apply', methods=['POST'])
@login_required
@perm_required('academic.timetable')
def schedule_periods_global_apply(sid):
    """把「全局作息模板」套用到本学期（2026-10-10 新增）。"""
    ts = _get_schedule_or_404(sid)
    guard = _readonly_guard(ts, 'academic.schedule_periods', sid=sid)
    if guard:
        return guard
    ok, msg, result = svc.apply_global_periods(sid)
    flash(msg, 'success' if ok else 'warning')
    if ok:
        log_operation(current_user, '更新', '节次配置', sid,
                      f'{ts.name}：套用全局作息', module='academic')
        if result and result['kept_in_use']:
            nums = '、'.join(str(n) for n in result['kept_in_use'])
            flash(f'第 {nums} 节仍有课程安排，未移除（请先调整这些课，再删除节次）',
                  'warning')
    return redirect(url_for('academic.schedule_periods', sid=sid))


@bp.route('/schedule/<int:sid>/versions')
@login_required
@perm_required('academic.timetable')
def schedule_versions(sid):
    """课表变更历史（分页）"""
    ts = _get_schedule_or_404(sid)
    page = request.args.get('page', 1, type=int)
    pagination = svc.get_schedule_versions(sid, page=page, per_page=50)
    return render_template('academic/schedule_versions.html',
                           ts=ts, pagination=pagination,
                           versions=pagination.items)


# ═══════════════════════════════════════════════════════════════════════════════
# 学校原样课表导入（矩阵式，2026-09-26）
# 老师手上的课表是"行=节次、列=星期、格内学科+教师"的矩阵，不再要求转成长表。
# 解析只出计划（内存草稿），确认后才调 batch_add_entries 写入。
# ═══════════════════════════════════════════════════════════════════════════════
_SMART_DRAFT = {}
_SMART_TTL = 3600
# L-4：waitress 多线程下并发读写 dict 会互相覆盖/丢失，这里加锁（同教师导入的做法）
_SMART_DRAFT_LOCK = threading.RLock()
# L-4：草稿数量上限，避免被反复上传刷爆内存
_SMART_DRAFT_MAX = 50


def _smart_purge():
    """按 TTL 清理过期草稿；超过上限时淘汰最旧的（加锁，多线程安全）。"""
    import time as _time
    now = _time.time()
    with _SMART_DRAFT_LOCK:
        for k in [k for k, v in list(_SMART_DRAFT.items())
                  if now - v.get('_ts', 0) > _SMART_TTL]:
            _SMART_DRAFT.pop(k, None)
        if len(_SMART_DRAFT) > _SMART_DRAFT_MAX:
            for k in sorted(_SMART_DRAFT,
                            key=lambda k: _SMART_DRAFT[k].get('_ts', 0))[
                                :len(_SMART_DRAFT) - _SMART_DRAFT_MAX]:
                _SMART_DRAFT.pop(k, None)


def _smart_draft_set(token, payload):
    """写入导入草稿（先清理过期，再加锁写入）"""
    _smart_purge()
    with _SMART_DRAFT_LOCK:
        _SMART_DRAFT[token] = payload


def _smart_draft_get(token):
    """读取导入草稿（加锁）"""
    with _SMART_DRAFT_LOCK:
        return _SMART_DRAFT.get(token)


def _smart_draft_pop(token):
    """取出并移除导入草稿（加锁，确认导入时用）"""
    with _SMART_DRAFT_LOCK:
        return _SMART_DRAFT.pop(token, None)


def _smart_context(ts, user):
    """解析所需的上下文：学期节次 + 班级档案里的年级/班级 + 在职教师"""
    from app.models import ClassProfile
    from app.models.academic import Teacher
    visible_grades = visible_academic_grades(user)
    class_visible = academic_class_authorizer(user)
    rows = db.session.query(ClassProfile.grade, ClassProfile.class_name).distinct().all()
    grade_classes = {}
    for g, c in rows:
        if (g and (visible_grades is None or g in visible_grades) and
                class_visible(g, c)):
            grade_classes.setdefault(g, []).append(c)
    grades = sorted(grade_classes)
    teachers = Teacher.query.filter_by(status='active').all()
    if academic_has_class_restrictions(user):
        teacher_rows = apply_academic_scope(
            ScheduleEntry.query.filter_by(term_schedule_id=ts.id,
                                           is_deleted=False),
            user, ScheduleEntry).with_entities(ScheduleEntry.teacher_uid).distinct().all()
        teacher_uids = {row[0] for row in teacher_rows}
        teachers = [teacher for teacher in teachers
                    if teacher.teacher_uid in teacher_uids]
    return grades, grade_classes, teachers


def _smart_matrix(ts, data, pday=None):
    """导入预览的矩阵上下文：行＝班级、列＝节次（与全校总课表 / 查课核对同版式）。

    返回 (matrix, 当前星期几, 条目总数)。单双周标注转成 week_badge，预览里也能看到
    「单/双」小徽标，和导入后的课表页一致。
    """
    flat = []
    for sh in (data.get('sheets') or []):
        for e in (sh.get('entries') or []):
            d = dict(e)
            wr = (d.get('week_range') or '').strip()
            if wr in ('单周', '双周'):
                d['week_badge'] = wr
            flat.append(d)
    mx = svc.build_matrix_from_entries(flat, svc.get_periods(ts.id))
    days = mx['days'] or [1]
    return mx, (pday if pday in days else days[0]), len(flat)


@bp.route('/schedule/<int:sid>/smart-import', methods=['GET', 'POST'])
@login_required
@perm_required('academic.timetable')
def schedule_smart_import(sid):
    """学校原样课表导入：上传 → 解析预览（不写库）。

    预览含两块：① 每个 sheet 的逐条明细（可改年级/班级）② **矩阵预览** ——
    行＝班级、列＝节次，与全校总课表/查课核对页完全同版式，导入前先看"长什么样"。
    预览页可通过 ?draft=token&pday=N 回看（切换星期几），有效期 1 小时。
    """
    import io as _io
    import time as _time
    import uuid as _uuid

    from app.modules.academic.services import schedule_matrix_import as sheet_parser

    ts = _get_schedule_or_404(sid)
    grades, grade_classes, teachers = _smart_context(ts, current_user)
    result = session.pop('schedule_smart_result', None)

    # 回看草稿（切换星期几时不用重新上传）
    draft_token = (request.args.get('draft') or '').strip()
    if draft_token and request.method == 'GET':
        draft = _smart_draft_get(draft_token)
        if not draft or draft.get('owner_id') != current_user.id:
            flash('预览草稿已过期，请重新上传', 'warning')
            return redirect(url_for('academic.schedule_smart_import', sid=sid))
        data = draft['data']
        mx, day, flat_n = _smart_matrix(ts, data, request.args.get('pday', type=int))
        return render_template('academic/schedule_smart_import.html',
                               ts=ts, token=draft_token, data=data,
                               fname=draft.get('fname'), grades=grades,
                               grade_classes=grade_classes, preview=True, result=None,
                               matrix=mx, pday=day, flat_count=flat_n,
                               week_range=draft.get('week_range') or '',
                               weekday_names=WEEKDAY_NAMES)

    if request.method == 'POST':
        guard = _readonly_guard(ts, 'academic.schedule_smart_import', sid=sid)
        if guard:
            return guard
        file = request.files.get('file')
        if not file or not file.filename:
            flash('请选择课表 Excel 文件', 'danger')
            return redirect(url_for('academic.schedule_smart_import', sid=sid))
        # 2026-10-10：整批周次（按周次为单位批量导入）——如 5 / 1-9 / 单周；
        # 留空 = 按格内标注（缺省 1-18）
        week_range = (request.form.get('week_range') or '').strip()
        try:
            data = sheet_parser.parse_school_workbook(
                _io.BytesIO(file.read()), svc.get_periods(sid), grades, teachers,
                default_week_range=week_range or None)
        except Exception as exc:  # noqa: BLE001
            flash(f'文件解析失败：{exc}', 'danger')
            return redirect(url_for('academic.schedule_smart_import', sid=sid))

        token = _uuid.uuid4().hex
        _smart_draft_set(token, {'data': data, 'fname': file.filename,
                                 'owner_id': current_user.id,
                                 'week_range': week_range,
                                 '_ts': _time.time()})
        mx, day, flat_n = _smart_matrix(ts, data, request.args.get('pday', type=int))
        return render_template('academic/schedule_smart_import.html',
                               ts=ts, token=token, data=data, fname=file.filename,
                               grades=grades, grade_classes=grade_classes,
                               preview=True, result=None,
                               matrix=mx, pday=day, flat_count=flat_n,
                               week_range=week_range,
                               weekday_names=WEEKDAY_NAMES)

    return render_template('academic/schedule_smart_import.html',
                           ts=ts, preview=False, result=result,
                           grades=grades, grade_classes=grade_classes)


@bp.route('/schedule/<int:sid>/smart-import/confirm', methods=['POST'])
@login_required
@perm_required('academic.timetable')
def schedule_smart_import_confirm(sid):
    """确认导入：把预览里的课程写入课表（冲突自动跳过并报告）"""
    ts = _get_schedule_or_404(sid)
    guard = _readonly_guard(ts, 'academic.schedule_smart_import', sid=sid)
    if guard:
        return guard
    token = (request.form.get('token') or '').strip()
    draft = _smart_draft_get(token)
    if not draft:
        flash('导入批次已失效，请重新上传文件', 'danger')
        return redirect(url_for('academic.schedule_smart_import', sid=sid))
    if draft.get('owner_id') != current_user.id:
        abort(403)
    _smart_draft_pop(token)

    data = draft['data']
    entries = []
    for i, sh in enumerate(data['sheets']):
        grade = (request.form.get(f'grade_{i}') or sh.get('grade') or '').strip()
        cls = (request.form.get(f'class_{i}') or sh.get('class_name') or '').strip()
        for e in sh['entries']:
            item = dict(e)
            item['grade'] = grade
            item['class_name'] = cls
            entries.append(item)
    class_visible = academic_class_authorizer(current_user)
    if any(not class_visible(item.get('grade'), item.get('class_name'))
           for item in entries):
        flash('导入内容包含你无权管理的班级，没有写入任何课程', 'danger')
        return redirect(url_for('academic.schedule_smart_import', sid=sid))
    if not entries:
        flash('没有可导入的课程', 'warning')
        return redirect(url_for('academic.schedule_smart_import', sid=sid))

    result = svc.batch_add_entries(sid, entries, operator=current_user)
    session['schedule_smart_result'] = {
        'success': result['success'], 'failed': result['failed'],
        'errors': result['errors'][:50],
        'sheets': [{'sheet': s['sheet'], 'grade': s['grade'],
                    'class_name': s['class_name'], 'count': len(s['entries'])}
                   for s in data['sheets']],
    }
    try:
        log_operation(current_user, '导入', '课表', sid,
                      f'学校原样课表导入：成功 {result["success"]} 条，'
                      f'跳过 {result["failed"]} 条', module='academic')
    except Exception:  # noqa: BLE001
        pass
    flash(f'导入完成：成功 {result["success"]} 条，跳过 {result["failed"]} 条',
          'success' if result['success'] else 'warning')
    return redirect(url_for('academic.schedule_smart_import', sid=sid))


@bp.route('/schedule/<int:sid>/smart-import/template.xlsx')
@login_required
@perm_required('academic.view')
def schedule_smart_template(sid):
    """下载课表导入模板（两种版式，导入时自动识别）。

    query：
      layout=class（默认）「整班矩阵」：一个 sheet 一个班，行=节次、列=星期；
      layout=full           「全校总课表」：一张表放全校，行=星期×节次、列=班级；
      grade / class_name    版式 A 的 sheet 名与标题；
      blank=1               不带示例数据（空白模板）；
      week                  在标题里标注周次（版式 A）。
    """
    from app.modules.academic.services import schedule_matrix_import as sheet_parser
    ts = _get_schedule_or_404(sid)
    blank = request.args.get('blank') in ('1', 'true', 'yes')
    suffix = '空白' if blank else '示例'
    periods = svc.get_periods(sid)
    mime = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

    if (request.args.get('layout') or 'class').strip() == 'full':
        # 列 =(该学期已有的年级/班级)；新学期限 30 列以内，没有就退回示例 3 个班
        gc = svc.get_grade_class_list(sid) or {}
        classes = [(g, c) for g in sorted(gc) for c in sorted(gc[g] or [])][:30]
        buf = sheet_parser.build_full_school_template(
            periods, classes=classes or None, sample=not blank)
        return send_file(buf, as_attachment=True,
                         download_name=f'全校总课表模板_{ts.name}_{suffix}.xlsx',
                         mimetype=mime)

    grade = (request.args.get('grade') or '高三').strip()
    class_name = (request.args.get('class_name') or '01班').strip()
    buf = sheet_parser.build_school_template(
        periods, grade=grade, class_name=class_name,
        sample=not blank,
        week_range=(request.args.get('week') or '').strip() or None)
    return send_file(buf, as_attachment=True,
                     download_name=(f'整班课表模板_{ts.name}_{grade}{class_name}'
                                    f'_{suffix}.xlsx'),
                     mimetype=mime)


@bp.route('/schedule/<int:sid>/import', methods=['GET', 'POST'])
@login_required
@perm_required('academic.timetable')
def schedule_import(sid):
    """（2026-10-10 起）逐行记录的长表导入已下线，统一走「整班 · 原样课表导入」。

    保留端点只为兼容旧书签/旧链接：直接 302 到原样导入页。
    """
    ts = _get_schedule_or_404(sid)
    flash('课表导入已统一为「原样课表导入」（一个 sheet 一个班，可按班级/周次批量导入）', 'info')
    return redirect(url_for('academic.schedule_smart_import', sid=ts.id))


@bp.route('/schedule/<int:sid>/export')
@login_required
@perm_required('academic.timetable')
def schedule_export(sid):
    """导出 Excel（query: view_type/grade/class/teacher）"""
    ts = _get_schedule_or_404(sid)
    # 默认导「全校总课表」；整校分班（一个班一张 sheet）用 view_type=all
    view_type = (request.args.get('view_type') or 'overview').strip()
    grade = (request.args.get('grade') or '').strip() or None
    class_name = (request.args.get('class') or '').strip() or None
    teacher_uid = (request.args.get('teacher') or '').strip() or None
    allowed_grades = visible_academic_grades(current_user)
    allowed_classes = visible_academic_class_scope(current_user)
    if grade and allowed_grades is not None and grade not in allowed_grades:
        abort(403)
    if (view_type == 'class' and grade and class_name and
            not academic_class_is_visible(current_user, grade, class_name)):
        abort(403)
    try:
        buf = svc.export_schedule(sid, view_type=view_type, grade=grade,
                                  class_name=class_name, teacher_uid=teacher_uid,
                                  week=_week_param(),
                                  allowed_grades=allowed_grades,
                                  allowed_classes=allowed_classes)
    except Exception:
        db.session.rollback()
        flash('导出失败，请重试', 'danger')
        return redirect(url_for('academic.schedule_manage'))
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    suffix = {'class': f'_{grade or ""}{class_name or ""}',
              'grade': f'_{grade or ""}',
              'teacher': f'_{teacher_uid or ""}'}.get(view_type, '')
    return send_file(buf, as_attachment=True,
                     download_name=f'{ts.name}{suffix}_{stamp}.xlsx',
                     mimetype=_XLSX_MIME)


@bp.route('/schedule/<int:sid>/template')
@login_required
@perm_required('academic.timetable')
def schedule_template(sid):
    """（2026-10-10 起）逐行记录的长表模板已下线 → 统一下载「整班 · 原样」模板"""
    ts = _get_schedule_or_404(sid)
    return redirect(url_for('academic.schedule_smart_template', sid=ts.id))


# ═══════════════════════════════════════════════════════════════════════════
# 条目 CRUD（JSON）
# ═══════════════════════════════════════════════════════════════════════════

@bp.route('/schedule/<int:sid>/entry/add', methods=['POST'])
@login_required
@perm_required('academic.timetable')
def schedule_entry_add(sid):
    """添加课条目"""
    ts = _get_schedule_or_404(sid)
    if ts.status == 'archived':
        return _json_err('历史学期课表为只读，不能修改', 403)
    d = _payload()
    grade = (d.get('grade') or '').strip()
    class_name = (d.get('class_name') or '').strip()
    subject = (d.get('subject') or '').strip()
    try:
        weekday = int(d.get('weekday') or 0)
        period_number = int(d.get('period_number') or 0)
    except (ValueError, TypeError):
        return _json_err('星期/节次格式无效')
    if not grade or not class_name or not subject:
        return _json_err('年级、班级、学科为必填项')
    if not academic_class_is_visible(current_user, grade, class_name):
        return _json_err('该班级不在你的管理范围内', 403)
    if not (1 <= weekday <= 7):
        return _json_err('星期超出范围（1-7）')

    teacher_uid, teacher_name = _resolve_teacher(
        (d.get('teacher_uid') or '').strip(), (d.get('teacher_name') or '').strip())
    try:
        ok, result = svc.add_entry(
            sid, grade, class_name, weekday, period_number, subject,
            teacher_uid=teacher_uid, teacher_name=teacher_name,
            room=(d.get('room') or '').strip() or None,
            week_range=(d.get('week_range') or '').strip() or '1-18',
            note=(d.get('note') or '').strip() or None,
            teaching_class=(d.get('teaching_class') or '').strip() or None,
            operator=current_user)
    except Exception:
        db.session.rollback()
        return _json_err('添加失败，请重试', 500)
    if not ok:
        return _json_err(result, 409)
    log_operation(current_user, '新增', '课表条目', result.id,
                  f'{ts.name}：{grade}{class_name} {WEEKDAY_NAMES.get(weekday,"")}第{period_number}节 {subject}',
                  module='academic')
    return _json_ok(result.to_dict(), '课表条目已添加')


@bp.route('/schedule/<int:sid>/entry/<int:eid>/edit', methods=['POST'])
@login_required
@perm_required('academic.timetable')
def schedule_entry_edit(sid, eid):
    """编辑课条目"""
    ts = _get_schedule_or_404(sid)
    if ts.status == 'archived':
        return _json_err('历史学期课表为只读，不能修改', 403)
    entry = db.session.get(ScheduleEntry, eid)
    if not entry or entry.term_schedule_id != sid or entry.is_deleted:
        return _json_err('条目不存在', 404)
    if not academic_class_is_visible(current_user, entry.grade, entry.class_name):
        return _json_err('该课程不在你的管理范围内', 403)
    d = _payload()
    fields = {}
    for key in ('grade', 'class_name', 'subject', 'room', 'week_range', 'note',
                'teaching_class'):
        if key in d:
            fields[key] = (d.get(key) or '').strip() or None
    for key in ('weekday', 'period_number'):
        if d.get(key):
            try:
                fields[key] = int(d[key])
            except (ValueError, TypeError):
                return _json_err(f'{key} 格式无效')
    if not academic_class_is_visible(
            current_user, fields.get('grade', entry.grade),
            fields.get('class_name', entry.class_name)):
        return _json_err('目标班级不在你的管理范围内', 403)
    if 'weekday' in fields and not (1 <= fields['weekday'] <= 7):
        return _json_err('星期超出范围（1-7）')
    if 'teacher_uid' in d:
        fields['teacher_uid'], fields['teacher_name'] = _resolve_teacher(
            (d.get('teacher_uid') or '').strip(), (d.get('teacher_name') or '').strip())
    try:
        ok, result = svc.edit_entry(eid, operator=current_user, **fields)
    except Exception:
        db.session.rollback()
        return _json_err('保存失败，请重试', 500)
    if not ok:
        return _json_err(result, 409)
    log_operation(current_user, '更新', '课表条目', eid, ts.name, module='academic')
    return _json_ok(result.to_dict(), '课表条目已更新')


@bp.route('/schedule/<int:sid>/entry/<int:eid>/delete', methods=['POST'])
@login_required
@perm_required('academic.timetable')
def schedule_entry_delete(sid, eid):
    """删除课条目（软删除）"""
    ts = _get_schedule_or_404(sid)
    if ts.status == 'archived':
        return _json_err('历史学期课表为只读，不能修改', 403)
    entry = db.session.get(ScheduleEntry, eid)
    if not entry or entry.term_schedule_id != sid or entry.is_deleted:
        return _json_err('条目不存在', 404)
    if not academic_class_is_visible(current_user, entry.grade, entry.class_name):
        return _json_err('该课程不在你的管理范围内', 403)
    detail = f'{entry.grade}{entry.class_name} {entry.subject}'
    try:
        ok, msg = svc.delete_entry(eid, operator=current_user)
    except Exception:
        db.session.rollback()
        return _json_err('删除失败，请重试', 500)
    if not ok:
        return _json_err(msg, 409)
    log_operation(current_user, '删除', '课表条目', eid, f'{ts.name}：{detail}',
                  module='academic')
    return _json_ok(None, '课表条目已删除')


# 2026-10-10：「我的课表」整页搬到教师工作台（workbench.my_schedule），此处不再提供，
# 旧地址 /academic/my-schedule 已下线（详情见模块 docstring）。


# ═══════════════════════════════════════════════════════════════════════════
# 查课联动
# ═══════════════════════════════════════════════════════════════════════════

@bp.route('/inspection/schedule')
@login_required
@perm_required('academic.view')
def inspection_schedule():
    """查课联动：已统一到 inspection_live，本路由保留并重定向。"""
    return redirect(url_for('academic.inspection_live_schedule',
                            date=request.args.get('date', ''),
                            grade=request.args.get('grade', '')))


@bp.route('/inspection/schedule/period/<int:n>')
@login_required
@perm_required('academic.view')
def inspection_schedule_period(n):
    """查课联动：指定节次 → 重定向到实时课表（带 period 参数）。"""
    # 2026-10-10：原为 1..13 上限，导致第 14 节起的查课链接 404；现在只挡非法编号
    if not (1 <= n <= PERIOD_NUMBER_CEILING):
        abort(404)
    d = (request.args.get('date') or '').strip()
    grade = (request.args.get('grade') or '').strip()
    return redirect(url_for('academic.inspection_live_schedule',
                            date=d, period=n, grade=grade))


# ═══════════════════════════════════════════════════════════════════════════
# AJAX 数据接口（JSON）
# ═══════════════════════════════════════════════════════════════════════════

@bp.route('/api/schedule/<int:sid>/class-data')
@login_required
@perm_required('academic.view')
def api_schedule_class_data(sid):
    """班级网格 JSON（query: grade/class_name/weekday）"""
    _get_schedule_or_404(sid)
    grade = (request.args.get('grade') or '').strip()
    class_name = (request.args.get('class_name') or '').strip()
    weekday = request.args.get('weekday', type=int)
    if not grade or not class_name:
        return _json_err('缺少 grade / class_name 参数')
    # 2026-10-10：查看类数据不再按年级限制（「所有年级的课表均可显示」）
    allowed_grades, allowed_classes = _view_scope()
    view = svc.get_class_view(sid, grade, class_name, weekday=weekday,
                              week=_week_param(), allowed_grades=allowed_grades,
                              allowed_classes=allowed_classes)
    return _json_ok(view)


# 2026-10-10：/api/schedule/<sid>/grade-data（原给大课表切换年级用）已删。
# 它在大课表页里只是配置块中的一个 URL 键，前端 JS 从未请求过——大课表一下线就是纯孤儿接口。


@bp.route('/api/schedule/entry/<int:eid>')
@login_required
@perm_required('academic.view')
def api_schedule_entry_detail(eid):
    """条目详情 JSON（含变更历史）"""
    detail = svc.get_entry_detail(eid)
    if not detail:
        return _json_err('条目不存在', 404)
    versions = [v.to_dict() for v in svc.get_entry_versions(eid, limit=20)]
    return _json_ok({'entry': detail, 'versions': versions})


@bp.route('/api/schedule/teachers')
@login_required
@perm_required('academic.view')
def api_schedule_teachers():
    """教师列表 JSON（供下拉选择，academic.db Teacher）。

    2026-10-09：支持 ?grade=&class_name= —— 录某个班的课时，优先返回**该班任课教师**
    （成绩管理任课映射 + 课表），不再让老师在几十上百人里翻；不传参数时行为不变。
    """
    try:
        teachers = svc.get_all_teachers()
    except Exception:
        teachers = []
    grade = (request.args.get('grade') or '').strip()
    class_name = (request.args.get('class_name') or '').strip()
    if grade and class_name:
        sid = request.args.get('sid', type=int)
        if not sid:
            active = svc.get_active_schedule()
            sid = active.id if active else None
        rows = teaching_scope_service.teachers_of_class(grade, class_name, sid)
        uids = {r[0] for r in rows if r[0]}
        ordered = [t for t in teachers if t.get('uid') in uids]
        for uid, name, subj in rows:      # 任课映射里有、教师档案没有的也补上
            if uid and not any(t.get('uid') == uid for t in ordered):
                ordered.append({'uid': uid, 'name': name or uid,
                                'subject': subj or ''})
        if ordered:
            teachers = ordered
    kw = (request.args.get('q') or '').strip()
    if kw:
        teachers = [t for t in teachers
                    if kw in t['name'] or kw in t['uid'] or kw in t['subject']]
    return _json_ok(teachers)


@bp.route('/api/schedule/classes')
@login_required
@perm_required('academic.view')
def api_schedule_classes():
    """年级+班级列表 JSON（供级联下拉）。

    2026-10-09：原先直接取学籍库 distinct —— 学籍库里混着历史 / 批量导入的班级，
    下拉会被撑到几百个班（与"教务 / 学生 / 成绩是同一套人马"但口径不一致）。
    现在统一走 teaching_scope_service：以班级档案的**在用班级**为准、课表里出现过
    的班兜底；学籍库只在上述结果为空（新系统还没排课）时兜底，保证不丢班。
    """
    merged = {}
    # 2026-10-10：下拉候选不再按年级/班级限制（与「所有年级的课表均可显示」一致）
    allowed_grades, allowed_classes = _view_scope()
    sid = request.args.get('sid', type=int)

    for g, cs in svc.get_grade_class_list(
            sid, allowed_grades=allowed_grades,
            allowed_classes=allowed_classes).items():
        merged.setdefault(g, set()).update(cs)
    for g, cs in teaching_scope_service.class_candidates(sid)['grade_classes'].items():
        merged.setdefault(g, set()).update(cs)

    filtered = {}
    for g, cs in merged.items():
        if cs:
            filtered.setdefault(g, set()).update(cs)

    if not filtered:        # 兜底：还没排课的新系统，退回学籍库
        try:
            from app.models.student import Student
            student_q = db.session.query(Student.grade, Student.class_name)
            skip = {'已转出', '离校', '不分班', '已毕业', ''}
            for g, cn in student_q.distinct().all():
                if g and cn and cn not in skip:
                    filtered.setdefault(g, set()).add(cn)
        except Exception:  # noqa: BLE001
            pass
    data = {g: sorted(cs) for g, cs in sorted(filtered.items())}
    return _json_ok(data)


@bp.route('/api/schedule/check-conflict')
@login_required
@perm_required('academic.timetable')
def api_schedule_check_conflict():
    """冲突检测 JSON（query: sid/teacher_uid/grade/class_name/weekday/period_number/week_range）

    week_range 参与判定：周次无交集的条目不算冲突（单周课与双周课可同格）。
    """
    sid = request.args.get('sid', type=int)
    if not sid:
        return _json_err('缺少 sid 参数')
    teacher_uid = (request.args.get('teacher_uid') or '').strip()
    grade = (request.args.get('grade') or '').strip()
    class_name = (request.args.get('class_name') or '').strip()
    weekday = request.args.get('weekday', type=int)
    period_number = request.args.get('period_number', type=int)
    exclude = request.args.get('exclude_entry_id', type=int)
    week_range = (request.args.get('week_range') or '').strip() or None
    if not weekday or not period_number:
        return _json_err('缺少 weekday / period_number 参数')

    data = {'class_conflict': None, 'teacher_conflict': None}
    if grade and class_name:
        c = svc.check_class_conflict(sid, grade, class_name, weekday,
                                     period_number, exclude_entry_id=exclude,
                                     week_range=week_range)
        if c:
            data['class_conflict'] = c.to_dict()
    if teacher_uid:
        t = svc.check_teacher_conflict(sid, teacher_uid, weekday, period_number,
                                       exclude_entry_id=exclude, week_range=week_range)
        if t:
            data['teacher_conflict'] = t.to_dict()
    data['has_conflict'] = bool(data['class_conflict'] or data['teacher_conflict'])
    return _json_ok(data)


# ═══════════════════════════════════════════════════════════════════════════
# 学期周期维度 / 历史课表追溯（Task#27 增量追加，不改动以上已有路由）
# ═══════════════════════════════════════════════════════════════════════════

def _parse_date(raw):
    """解析 YYYY-MM-DD 字符串为 date，空/非法返回 None"""
    raw = (raw or '').strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw[:10], '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None


@bp.route('/schedule/<int:sid>/dates', methods=['GET', 'POST'])
@login_required
@perm_required('academic.timetable')
def schedule_dates(sid):
    """学期起止日期与教学周配置（GET 表单 / POST 保存 + 校验警告展示）"""
    ts = _get_schedule_or_404(sid)
    warnings = []
    if request.method == 'POST':
        guard = _readonly_guard(ts, 'academic.schedule_dates', sid=sid)
        if guard:
            return guard
        start_date = _parse_date(request.form.get('start_date'))
        end_date = _parse_date(request.form.get('end_date'))
        try:
            total_weeks = int(request.form.get('total_weeks') or 0)
        except (ValueError, TypeError):
            total_weeks = 0
        try:
            week_start_offset = int(request.form.get('week_start_offset') or 0)
        except (ValueError, TypeError):
            week_start_offset = 0
        is_current = request.form.get('is_current') in ('1', 'on', 'true', 'True')
        warnings = tsvc.validate_term_dates(start_date, end_date, total_weeks)
        # 硬错误拦截：结束<=开始 或 周数<=0 时不保存
        if (start_date and end_date and end_date <= start_date) or total_weeks <= 0:
            flash('保存失败：' + '；'.join(warnings), 'danger')
            return redirect(url_for('academic.schedule_dates', sid=sid))
        try:
            if is_current:
                TermSchedule.query.filter(TermSchedule.id != sid)\
                    .update({'is_current': False}, synchronize_session=False)
            svc.update_schedule(sid, start_date=start_date, end_date=end_date,
                                total_weeks=total_weeks,
                                week_start_offset=week_start_offset,
                                is_current=is_current)
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash('保存失败，请重试', 'danger')
            return redirect(url_for('academic.schedule_dates', sid=sid))
        log_operation(current_user, '更新', '学期日期配置', sid, ts.name, module='academic')
        if warnings:
            flash('已保存，但有提醒：' + '；'.join(warnings), 'warning')
        else:
            flash('学期日期与周次配置已保存', 'success')
        return redirect(url_for('academic.schedule_dates', sid=sid))
    return render_template('academic/schedule_dates.html',
                           ts=ts, warnings=warnings,
                           schedules=svc.list_schedules(),
                           can_edit=_editable(ts))


@bp.route('/schedule/<int:sid>/copy', methods=['GET', 'POST'])
@login_required
@perm_required('academic.timetable')
def schedule_copy(sid):
    """学期交接：以该学期为底版创建新学期草稿（GET 表单预填 / POST 执行）"""
    ts = _get_schedule_or_404(sid)
    if request.method == 'POST':
        new_name = (request.form.get('new_name') or '').strip()
        new_year = (request.form.get('new_school_year') or '').strip()
        new_term = (request.form.get('new_term') or '').strip()
        start_date = _parse_date(request.form.get('start_date'))
        end_date = _parse_date(request.form.get('end_date'))
        try:
            total_weeks = int(request.form.get('total_weeks') or 0) or None
        except (ValueError, TypeError):
            total_weeks = None
        copy_entries = request.form.get('copy_entries') in ('1', 'on', 'true', 'True')
        copy_periods = request.form.get('copy_periods') in ('1', 'on', 'true', 'True')
        if not new_name or not new_year or not new_term:
            flash('新学期名称、学年、学期为必填项', 'danger')
            return redirect(url_for('academic.schedule_copy', sid=sid))
        warnings = tsvc.validate_term_dates(start_date, end_date, total_weeks or 20)
        try:
            ok, msg, new, stats = tsvc.copy_term_as_new_draft(
                sid, new_name, new_year, new_term,
                start_date=start_date, end_date=end_date, total_weeks=total_weeks,
                copy_entries=copy_entries, copy_periods=copy_periods,
                created_by=current_user)
        except Exception:
            db.session.rollback()
            flash('复制失败，请重试', 'danger')
            return redirect(url_for('academic.schedule_copy', sid=sid))
        if not ok:
            flash(msg, 'danger')
            return redirect(url_for('academic.schedule_copy', sid=sid))
        log_operation(current_user, '新增', '学期交接', new.id,
                      f'{ts.name} → {new_name}（节次 {stats["periods"]}，条目 {stats["entries"]}）',
                      module='academic')
        flash(f'{msg}：复制节次 {stats["periods"]} 条、课表条目 {stats["entries"]} 条'
              + ('（提醒：' + '；'.join(warnings) + '）' if warnings else ''),
              'warning' if warnings else 'success')
        return redirect(url_for('academic.schedule_manage'))
    # GET：预填建议
    suggest_year = ts.school_year
    suggest_term = '第二学期' if '第一' in (ts.term or '') else '第一学期'
    return render_template('academic/schedule_copy.html',
                           ts=ts, suggest_year=suggest_year, suggest_term=suggest_term)


@bp.route('/schedule/<int:sid>/set-current', methods=['POST'])
@login_required
@perm_required('academic.timetable')
def schedule_set_current(sid):
    """设为当前学期（仅切「当前」标记；激活 / 归档是另外两个独立操作）"""
    ts = _get_schedule_or_404(sid)
    try:
        svc.set_current_term(sid)
    except ValueError as e:
        flash(str(e), 'danger')
        return redirect(request.referrer or url_for('academic.schedule_manage'))
    log_operation(current_user, '更新', '当前学期', sid, ts.name, module='academic')
    flash(f'已将「{ts.name}」设为当前学期', 'success')
    return redirect(request.referrer or url_for('academic.schedule_manage'))


@bp.route('/schedule/<int:sid>/archive-term', methods=['POST'])
@login_required
@perm_required('academic.timetable')
def schedule_archive_term(sid):
    """归档学期：写归档快照 → 移入「历史课表」（只读回看）

    2026-10-10：归档后直接落到「历史课表」页，与「设为当前」彻底分开。
    """
    ts = _get_schedule_or_404(sid)
    try:
        ok, msg = tsvc.archive_term(sid, operator=current_user)
    except Exception:
        db.session.rollback()
        flash('归档失败，请重试', 'danger')
        return redirect(url_for('academic.schedule_manage'))
    if not ok:
        flash(msg, 'danger')
        return redirect(url_for('academic.schedule_manage'))
    log_operation(current_user, '归档', '学期课表', sid, ts.name, module='academic')
    flash(msg + '：已生成归档快照并移入「历史课表」', 'success')
    return redirect(url_for('academic.schedule_history'))


@bp.route('/schedule/history')
@login_required
@perm_required('academic.timetable')
def schedule_history():
    """历史课表总览：按学年分组列出**已归档**的学期（2026-10-10 与「学期管理」分工）。

    - 学期管理：在用 / 待启用（draft + active），创建、导入、激活、归档、删除；
    - 历史课表（本页）：已归档，只读回看 + 归档快照 + 重新启用 + 作为底版复制。
    原实现列「全部学期」，归档的在两个页面重复出现（用户报障：别老在学期管理里
    显示，直接放历史课表）。
    """
    all_terms = tsvc.list_terms_with_stats()
    terms = [t for t in all_terms if t['status'] == 'archived']
    grouped = {}
    for t in terms:
        grouped.setdefault(t['school_year'] or '未分学年', []).append(t)
    # 学年倒序
    years = sorted(grouped.keys(), reverse=True)
    # 当前启用中的学期不在本页列表里，「学期交接」入口单独取一次
    return render_template('academic/schedule_history.html',
                           grouped=grouped, years=years, total=len(terms),
                           current_term=svc.get_active_schedule())


@bp.route('/schedule/history/<int:sid>')
@login_required
@perm_required('academic.timetable')
def schedule_history_detail(sid):
    """历史学期课表入口页（全校总课表/年级/班级/教师 视图导航，只读）"""
    ts = _get_schedule_or_404(sid)
    periods = svc.get_periods(sid)
    grade_classes = svc.get_grade_class_list(sid)
    versions = tsvc.compare_term_versions(sid, limit=20)
    return render_template('academic/schedule_history_detail.html',
                           ts=ts, periods=periods, grade_classes=grade_classes,
                           versions=versions, schedules=svc.list_schedules())


@bp.route('/schedule/compare')
@login_required
@perm_required('academic.timetable')
def schedule_compare():
    """跨学期对比页（选择学期 A/B + 可选年级班级）"""
    schedules = svc.list_schedules()
    sid_a = request.args.get('sid_a', type=int)
    sid_b = request.args.get('sid_b', type=int)
    grade = (request.args.get('grade') or '').strip() or None
    class_name = (request.args.get('class') or '').strip() or None
    result = None
    if sid_a and sid_b:
        result = tsvc.compare_terms(sid_a, sid_b, grade=grade, class_name=class_name)
    return render_template('academic/schedule_compare.html',
                           schedules=schedules, sid_a=sid_a, sid_b=sid_b,
                           f_grade=grade or '', f_class=class_name or '',
                           result=result)


@bp.route('/schedule/compare/export')
@login_required
@perm_required('academic.timetable')
def schedule_compare_export():
    """导出跨学期对比结果 Excel"""
    sid_a = request.args.get('sid_a', type=int)
    sid_b = request.args.get('sid_b', type=int)
    if not sid_a or not sid_b:
        abort(400)
    grade = (request.args.get('grade') or '').strip() or None
    class_name = (request.args.get('class') or '').strip() or None
    result = tsvc.compare_terms(sid_a, sid_b, grade=grade, class_name=class_name)
    try:
        buf = _build_compare_workbook(result)
    except Exception:
        flash('导出失败，请重试', 'danger')
        return redirect(url_for('academic.schedule_compare',
                                sid_a=sid_a, sid_b=sid_b))
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    return send_file(buf, as_attachment=True,
                     download_name=f'课表对比_{stamp}.xlsx', mimetype=_XLSX_MIME)


# ── 学期周期 JSON API ──────────────────────────────────────────────────────

@bp.route('/api/schedule/compare-data')
@login_required
@perm_required('academic.timetable')
def api_schedule_compare_data():
    """跨学期对比结果 JSON（query: sid_a/sid_b/grade/class_name）"""
    sid_a = request.args.get('sid_a', type=int)
    sid_b = request.args.get('sid_b', type=int)
    if not sid_a or not sid_b:
        return _json_err('缺少 sid_a / sid_b 参数')
    grade = (request.args.get('grade') or '').strip() or None
    class_name = (request.args.get('class_name') or '').strip() or None
    data = tsvc.compare_terms(sid_a, sid_b, grade=grade, class_name=class_name)
    return _json_ok(data)


@bp.route('/api/schedule/<int:sid>/week-calendar')
@login_required
@perm_required('academic.view')
def api_schedule_week_calendar(sid):
    """周次日历 JSON（供前端周次选择器）"""
    _get_schedule_or_404(sid)
    return _json_ok(tsvc.get_week_calendar(sid))


@bp.route('/api/schedule/current-context')
@login_required
@perm_required('academic.view')
def api_schedule_current_context():
    """当前学期上下文 JSON（schedule_id/week/weekday/date/period_text）"""
    ctx = tsvc.get_current_term_context()
    sched = ctx.get('schedule')
    d = ctx.get('date')
    return _json_ok({
        'schedule_id': ctx.get('schedule_id'),
        'schedule_name': sched.name if sched else None,
        'week': ctx.get('week'),
        'weekday': ctx.get('weekday'),
        'weekday_text': WEEKDAY_NAMES.get(ctx.get('weekday'), ''),
        'date': d.strftime('%Y-%m-%d') if d else None,
        'period_text': ctx.get('period_text'),
        'term': sched.to_dict() if sched else None,
    })


@bp.route('/api/schedule/terms-stats')
@login_required
@perm_required('academic.timetable')
def api_schedule_terms_stats():
    """学期列表 + 统计 JSON"""
    return _json_ok(tsvc.list_terms_with_stats())


# ── Excel 导出辅助（对比 / 使用报告） ────────────────────────────────────

def _build_compare_workbook(result):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    wb = Workbook()
    wb.remove(wb.active)
    hf = Font(bold=True, color='FFFFFF')
    center = Alignment(horizontal='center', vertical='center')

    def _sheet(name, rows, headers, color):
        ws = wb.create_sheet(name[:31])
        fill = PatternFill(start_color=color, end_color=color, fill_type='solid')
        for ci, h in enumerate(headers, 1):
            c = ws.cell(row=1, column=ci, value=h)
            c.font = hf; c.fill = fill; c.alignment = center
        for ri, row in enumerate(rows, 2):
            for ci, v in enumerate(row, 1):
                ws.cell(row=ri, column=ci, value=xl_safe(v))
        return ws

    ta = (result.get('term_a') or {}).get('name', 'A')
    tb = (result.get('term_b') or {}).get('name', 'B')
    base_h = ['年级', '班级', '星期', '节次', '学科', '教师', '教室', '周次']
    _sheet(f'新增({tb}有{ta}无)',
           [[r['grade'], r['class_name'], r['weekday_text'], r['period_number'],
             r['subject'], r['teacher_name'] or '', r['room'] or '', r['week_range'] or '']
            for r in result['added']], base_h, '2E7D32')
    _sheet(f'减少({ta}有{tb}无)',
           [[r['grade'], r['class_name'], r['weekday_text'], r['period_number'],
             r['subject'], r['teacher_name'] or '', r['room'] or '', r['week_range'] or '']
            for r in result['removed']], base_h, 'C62828')
    _sheet('变更',
           [[r['grade'], r['class_name'], r['weekday_text'], r['period_number'],
             r['field'], r['old'] or '', r['new'] or '']
            for r in result['changed']],
           ['年级', '班级', '星期', '节次', '字段', '原值', '新值'], 'F9A825')
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
