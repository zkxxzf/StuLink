# StuLink v1.19.49.45 2026-10-10
# 教务 · 查课：统计页（列表筛选 / 月度统计 / 导出 / 图表API）+ 独立录入页（单条/批量）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import io
import json
from datetime import date, datetime, timedelta

from flask import render_template, request, redirect, url_for, flash, abort, jsonify, send_file
from flask_login import login_required, current_user
from sqlalchemy import func

from app.extensions import db
from app.models.academic import (InspectionRecord, Teacher,
                                 INSPECTION_EXEMPT_KEYS, INSPECTION_RESULTS)
from app.models.timetable import WEEKDAY_NAMES
from app.modules.academic import bp
from app.modules.academic.services.access_scope import (
    academic_class_authorizer, academic_class_is_visible,
    academic_has_class_restrictions, apply_academic_scope,
    visible_academic_class_scope, visible_academic_grades,
)
from app.modules.academic.services import swap_service
from app.modules.academic.services import inspection_service  # 2026-10-11 抽出
from app.modules.academic.services import inspection_lookup  # 2026-10-14 录入自动带出
from app.modules.notifications.services import notification_service
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation

# 2026-10-10 架构审计修复：本文件原有静默 `except Exception: pass`，失败不可观测。
# 统一加 logger 记录 warning（仅留痕，不改变任何降级行为与返回值）。
import logging

logger = logging.getLogger(__name__)

_RESULT_KEYS = {k for k, _ in INSPECTION_RESULTS}


def _active_teachers():
    return Teacher.query.filter_by(status='active').order_by(Teacher.teacher_uid).all()


def _visible_active_teachers(user):
    """Keep the teacher picker useful while respecting class-scoped accounts."""
    if not academic_has_class_restrictions(user):
        return _active_teachers()
    from app.models.timetable import ScheduleEntry
    entry_rows = apply_academic_scope(
        ScheduleEntry.query.filter_by(is_deleted=False), user, ScheduleEntry).with_entities(
        ScheduleEntry.teacher_uid).distinct().all()
    record_rows = apply_academic_scope(
        InspectionRecord.query, user, InspectionRecord).with_entities(
            InspectionRecord.teacher_uid).distinct().all()
    teacher_uids = {row[0] for row in entry_rows + record_rows if row[0]}
    if not teacher_uids:
        return []
    return (Teacher.query.filter(Teacher.status == 'active',
                                 Teacher.teacher_uid.in_(teacher_uids))
            .order_by(Teacher.teacher_uid).all())


def _period_options():
    """节次下拉选项（2026-10-14）：本学期作息里的节次定义 —— [{number, name}]。

    节次名口径与 `app/utils/period_label.py` 一致（period_number 只是行号：
    1=早读、2=第1节），录入 / 编辑一律用下拉，避免手填数字错位；
    无学期课表或未配节次时返回 []，模板回落为数字输入。
    """
    sched = swap_service.get_active_schedule()
    if not sched:
        return []
    return [{'number': p.period_number,
             'name': (p.period_name or '').strip() or f'第{p.period_number}节'}
            for p in swap_service.get_periods(sched.id)]


def _grade_options():
    """年级字典选项（活跃年级）"""
    from app.models import DictCategory
    cat = DictCategory.query.filter_by(code='grade').first()
    if not cat:
        return []
    return [i.value for i in cat.items.filter_by(is_active=True)
            .order_by('sort_order').all()]


def _inspector_names(records):
    """{user_id: 姓名}：列表与导出统一把"检查人"从数字 id 显示成姓名"""
    ids = {r.inspector_id for r in records if r.inspector_id}
    if not ids:
        return {}
    from app.models import User
    return {u.id: u.real_name for u in User.query.filter(User.id.in_(ids)).all()}


@bp.route('/inspection')
@login_required
@perm_required('academic.view')
def inspection_page():
    """查课记录与统计（按用户数据范围过滤年级）"""
    ug = visible_academic_grades(current_user)
    teacher_uid = (request.args.get('teacher_uid') or '').strip()
    result_f = (request.args.get('result') or '').strip()
    grade_f = (request.args.get('grade') or '').strip()
    d_from = (request.args.get('date_from') or '').strip()
    d_to = (request.args.get('date_to') or '').strip()

    q = apply_academic_scope(InspectionRecord.query, current_user,
                             InspectionRecord)
    if teacher_uid:
        q = q.filter_by(teacher_uid=teacher_uid)
    if result_f in _RESULT_KEYS:
        q = q.filter_by(result=result_f)
    if grade_f:
        q = q.filter_by(grade=grade_f)
    if d_from:
        try:
            q = q.filter(InspectionRecord.inspect_date >= date.fromisoformat(d_from))
        except ValueError as _e:
            # 用户填了非法日期（如 2026-13-45）→ 忽略该筛选条件（行为不变）
            logger.debug('查课日期下界非法(%r)，已忽略该条件: %s', d_from, _e)
    if d_to:
        try:
            q = q.filter(InspectionRecord.inspect_date <= date.fromisoformat(d_to))
        except ValueError as _e:
            logger.debug('查课日期上界非法(%r)，已忽略该条件: %s', d_to, _e)
    # 分页（此前硬上限 300 条且无法翻页，历史记录一多就"看不全也翻不到"）
    page = request.args.get('page', 1, type=int)
    pagination = (q.order_by(InspectionRecord.inspect_date.desc(),
                             InspectionRecord.id.desc())
                  .paginate(page=page, per_page=30, error_out=False))
    records = pagination.items
    inspectors = _inspector_names(records)

    # 本月统计（同样按数据范围）
    today = date.today()
    month_start = today.replace(day=1)
    month_q = apply_academic_scope(
        InspectionRecord.query.filter(InspectionRecord.inspect_date >= month_start),
        current_user, InspectionRecord)
    month_total = month_q.count()
    # 「考试」不算异常（考试期间有正常考务工作量，不能计为违纪/迟到）
    month_abnormal = month_q.filter(
        InspectionRecord.result.notin_(tuple(INSPECTION_EXEMPT_KEYS))).count()
    teacher_month_q = apply_academic_scope(
        InspectionRecord.query.filter(InspectionRecord.inspect_date >= month_start),
        current_user, InspectionRecord)
    by_teacher = (teacher_month_q.with_entities(
        InspectionRecord.teacher_uid, InspectionRecord.teacher_name,
        func.count(InspectionRecord.id))
        .group_by(InspectionRecord.teacher_uid, InspectionRecord.teacher_name)
        .order_by(func.count(InspectionRecord.id).desc()).limit(10).all())

    grade_opts = _grade_options()
    if ug is not None:
        grade_opts = [g for g in grade_opts if g in ug]
    return render_template('academic/inspection.html',
                           records=records, pagination=pagination, inspectors=inspectors,
                           teachers=_visible_active_teachers(current_user),
                           results=INSPECTION_RESULTS, grade_opts=grade_opts,
                           period_opts=_period_options(),
                           f_teacher=teacher_uid, f_result=result_f, f_grade=grade_f,
                           f_from=d_from, f_to=d_to,
                           month_total=month_total, month_abnormal=month_abnormal,
                           by_teacher=by_teacher,
                           can_edit=current_user.has_perm('academic.edit'))


@bp.route('/inspection/entry')
@login_required
@perm_required('academic.edit')
def inspection_entry_page():
    """查课录入（2026-10-13 从查课统计页拆出为**独立页**）。

    页内二选一：单条录入（日常补录）/ 批量录入（巡课回来一屏几十条）。
    统计页只负责统计与导出，不再承载任何录入入口；本页提交后仍回到本页，
    便于连续录入。默认进入单条方式，?mode=batch 直达批量。
    """
    mode = (request.args.get('mode') or 'single').strip()
    if mode not in ('single', 'batch'):
        mode = 'single'
    grade_opts = _grade_options()
    ug = visible_academic_grades(current_user)
    if ug is not None:
        grade_opts = [g for g in grade_opts if g in ug]
    return render_template('academic/inspection_entry.html',
                           teachers=_visible_active_teachers(current_user),
                           results=INSPECTION_RESULTS, grade_opts=grade_opts,
                           period_opts=_period_options(),
                           today=date.today().isoformat(), mode=mode)


@bp.route('/inspection/<int:rid>/edit', methods=['POST'])
@login_required
@perm_required('academic.edit')
def inspection_edit(rid):
    """修改查课记录（结果 / 班级 / 科目 / 节次 / 备注）。

    此前查课记录只能"删了重录"，改一个备注都得丢掉原始记录（含录入人与时间），
    这里补上就地编辑；仍受用户数据范围约束。
    """
    rec = db.session.get(InspectionRecord, rid)
    if not rec:
        abort(404)
    try:
        # 2026-10-11：校验与落库下沉到 inspection_service（MCP 与网页端共用）
        rec = inspection_service.edit_record(
            current_user, rid,
            result=(request.form.get('result') or '').strip(),
            class_name=request.form.get('class_name'),
            subject=(request.form.get('subject') or '').strip(),
            period=request.form.get('period', type=int),
            note=request.form.get('note'))
    except PermissionError as exc:
        flash(str(exc), 'danger')
        return redirect(url_for('academic.inspection_page'))
    db.session.commit()
    inspection_service.log_inspection(
        current_user, '更新', rid, f'{rec.teacher_name} 结果→{rec.result}')
    flash('查课记录已更新', 'success')
    return redirect(url_for('academic.inspection_page',
                            grade=(request.form.get('back_grade') or None),
                            teacher_uid=(request.form.get('back_teacher') or None)))


@bp.route('/inspection/add', methods=['POST'])
@login_required
@perm_required('academic.edit')
def inspection_add():
    """录入一条查课记录（年级必选；用户级数据范围时仅限授权年级）

    2026-10-13：入口移到独立录入页，提交后回录入页（连续录入不跳走）。
    """
    back = redirect(url_for('academic.inspection_entry_page'))
    date_str = (request.form.get('inspect_date') or '').strip()
    grade = (request.form.get('grade') or '').strip()
    teacher_uid = (request.form.get('teacher_uid') or '').strip()
    result = (request.form.get('result') or 'normal').strip()
    try:
        inspect_date = date.fromisoformat(date_str)
    except ValueError:
        flash('请选择正确的查课日期', 'danger')
        return back
    if not grade:
        flash('请选择年级', 'danger')
        return back
    try:
        # 2026-10-11：校验与落库下沉到 inspection_service（MCP 与网页端共用）
        rec = inspection_service.add_record(
            current_user, inspect_date=date_str, grade=grade,
            teacher_uid=teacher_uid,
            class_name=(request.form.get('class_name') or '').strip(),
            period=request.form.get('period', type=int),
            subject=(request.form.get('subject') or '').strip(),
            result=result, note=(request.form.get('note') or '').strip())
    except (ValueError, PermissionError) as exc:
        flash(str(exc), 'danger')
        return back
    db.session.commit()
    inspection_service.log_inspection(
        current_user, '新增', rec.id,
        f'{rec.inspect_date} 第{rec.period or "?"}节 {rec.teacher_name} '
        f'{rec.class_name or ""}')
    flash(f'已记录 {rec.teacher_name} 的查课情况', 'success')
    return back


@bp.route('/inspection/batch', methods=['POST'])
@login_required
@perm_required('academic.edit')
def inspection_batch():
    """批量录入查课记录（提交后回独立录入页的批量方式）"""
    back = redirect(url_for('academic.inspection_entry_page', mode='batch'))
    date_str = (request.form.get('inspect_date') or '').strip()
    try:
        inspect_date = date.fromisoformat(date_str)
    except ValueError:
        flash('请选择正确的查课日期', 'danger')
        return back
    # 2026-10-11：校验与落库下沉到 inspection_service（MCP 与网页端共用）
    cols = {k: request.form.getlist('row_' + k) for k in
            ('teacher_uid', 'grade', 'period', 'class_name', 'subject',
             'result', 'note')}
    total = len(cols['teacher_uid'])
    rows = [{k: (cols[k][i] if i < len(cols[k]) else '') for k in cols}
            for i in range(total)]
    try:
        added = inspection_service.add_records_batch(current_user, inspect_date, rows)
    except (ValueError, PermissionError) as exc:
        flash(str(exc), 'warning' if isinstance(exc, ValueError) else 'danger')
        return back
    db.session.commit()
    inspection_service.log_inspection(
        current_user, '批量新增', 0, f'{inspect_date} 共{added}条')
    flash(f'已批量录入 {added} 条查课记录', 'success')
    return back


@bp.route('/api/inspection/teacher-courses')
@login_required
@perm_required('academic.edit')
def api_inspection_teacher_courses():
    """查课录入「自动带出」：按日期 + 教师反查当天生效课表的班级 / 科目 / 节次。

    2026-10-14 用户口径：录入查课时选中教师后自动追踪班级与科目（仍可手动修改）。
    数据口径与查课核对页同源（常规周课表 + 当天临时调课 / 代课叠加），
    见 services/inspection_lookup.py；只返回当前账号可见范围内的课。
    返回 {success, date, teacher_uid, courses:[{period, grade, class_name, subject,
    room, teacher_uid, teacher_name, entry_id, is_temp_swap}], message?}
    """
    uid = (request.args.get('teacher_uid') or '').strip()
    target_date = _parse_iso_date(request.args.get('date')) or date.today()
    if not uid:
        return jsonify({'success': False, 'message': '缺少教师编号'}), 400
    sched = swap_service.get_active_schedule()
    if not sched:
        return jsonify({'success': True, 'date': target_date.isoformat(),
                        'teacher_uid': uid, 'courses': [],
                        'message': '尚未建立学期课表'})
    courses = inspection_lookup.teacher_courses_on(sched.id, target_date, uid)
    # 与录入页下拉同口径：受限账号只看得到自己范围内的年级 / 班级
    ug = visible_academic_grades(current_user)
    if ug is not None:
        courses = [c for c in courses if c['grade'] in ug]
    courses = [c for c in courses
               if academic_class_is_visible(current_user, c['grade'], c['class_name'])]
    return jsonify({'success': True, 'date': target_date.isoformat(),
                    'teacher_uid': uid, 'courses': courses})


@bp.route('/inspection/export')
@login_required
@perm_required('academic.edit')
def inspection_export():
    """导出查课记录 Excel"""
    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from app.utils.export_helpers import xl_safe
    from app.utils.period_label import period_label      # 节次名口径（1=早读→第1节=2）

    ug = visible_academic_grades(current_user)
    teacher_uid = (request.args.get('teacher_uid') or '').strip()
    result_f = (request.args.get('result') or '').strip()
    d_from = (request.args.get('date_from') or '').strip()
    d_to = (request.args.get('date_to') or '').strip()
    grade_f = (request.args.get('grade') or '').strip()

    q = apply_academic_scope(InspectionRecord.query, current_user,
                             InspectionRecord)
    if teacher_uid:
        q = q.filter_by(teacher_uid=teacher_uid)
    if result_f in _RESULT_KEYS:
        q = q.filter_by(result=result_f)
    if d_from:
        try:
            q = q.filter(InspectionRecord.inspect_date >= date.fromisoformat(d_from))
        except ValueError as _e:
            logger.debug('查课统计日期下界非法(%r)，已忽略该条件: %s', d_from, _e)
    if d_to:
        try:
            q = q.filter(InspectionRecord.inspect_date <= date.fromisoformat(d_to))
        except ValueError as _e:
            logger.debug('查课统计日期上界非法(%r)，已忽略该条件: %s', d_to, _e)
    if grade_f:
        q = q.filter(InspectionRecord.grade == grade_f)

    records = q.order_by(InspectionRecord.inspect_date.desc(),
                         InspectionRecord.id.desc()).all()
    if not records:
        flash('无数据可导出', 'warning')
        return redirect(url_for('academic.inspection_page',
                                teacher_uid=teacher_uid, result=result_f,
                                date_from=d_from, date_to=d_to, grade=grade_f))

    inspectors = _inspector_names(records)
    result_map = dict(INSPECTION_RESULTS)
    columns = ['日期', '年级', '节次', '教师编号', '教师姓名',
               '班级', '科目', '结果', '检查人', '备注']
    widths = [12, 8, 9, 14, 12, 10, 12, 8, 8, 20]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '查课记录'
    hf = Font(bold=True, color='FFFFFF')
    hfl = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
    tb = Border(left=Side(style='thin'), right=Side(style='thin'),
                top=Side(style='thin'), bottom=Side(style='thin'))

    for ci, label in enumerate(columns, 1):
        c = ws.cell(row=1, column=ci, value=label)
        c.font = hf
        c.fill = hfl
        c.alignment = Alignment(horizontal='center')
        c.border = tb

    for ri, r in enumerate(records, 2):
        row_data = [
            r.inspect_date.strftime('%Y-%m-%d'),
            r.grade or '',
            period_label(r.period) if r.period else '',
            r.teacher_uid or '',
            r.teacher_name or '',
            r.class_name or '',
            r.subject or '',
            result_map.get(r.result, r.result),
            inspectors.get(r.inspector_id) or (r.inspector_id or ''),
            r.note or '',
        ]
        for ci, v in enumerate(row_data, 1):
            c = ws.cell(row=ri, column=ci, value=xl_safe(v))
            c.border = tb

    for i, w in enumerate(widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    download_name = f'查课记录_{timestamp}.xlsx'
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)

    return send_file(out, as_attachment=True, download_name=download_name,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@bp.route('/inspection/api/stats')
@login_required
@perm_required('academic.view')
def inspection_stats():
    """返回查课统计数据 JSON（供 ECharts 图表使用）。

    2026-10-10 优化：原实现按各维度分别发 15 条聚合查询（同一张 inspection_records
    被反复全表扫描），改为「一次取数 + 内存聚合」：输出保持一致，且不随数据量放大。
    """
    year = request.args.get('year', type=int)

    base_q = apply_academic_scope(InspectionRecord.query, current_user,
                                  InspectionRecord)
    if year:
        base_q = base_q.filter(
            func.strftime('%Y', InspectionRecord.inspect_date) == str(year))
    rows = base_q.with_entities(
        InspectionRecord.result, InspectionRecord.inspect_date,
        InspectionRecord.grade, InspectionRecord.class_name,
        InspectionRecord.inspector_id, InspectionRecord.period).all()

    result_map = dict(INSPECTION_RESULTS)
    today = date.today()
    this_month_start = today.replace(day=1)
    total = len(rows)
    normal_cnt = this_month = today_checked = month_checked = 0
    by_result = {}
    monthly_map = {}
    class_cnt = {}
    insp_total, insp_bad = {}, {}
    per_total, per_bad = {}, {}
    for res, d, g, cn, iid, pn in rows:
        by_result[res] = by_result.get(res, 0) + 1
        if res == 'normal':
            normal_cnt += 1
        # 与 SQL 同口径：NULL 不算异常；「考试」也不算异常（考试期间有正常考务工作量）
        abnormal = res is not None and res not in INSPECTION_EXEMPT_KEYS
        m = d.strftime('%Y-%m') if d else None
        mm = monthly_map.setdefault(m, {'month': m, 'normal': 0, 'late': 0,
                                        'absent': 0, 'swap': 0, 'exam': 0, 'other': 0})
        if res in mm:
            mm[res] = mm[res] + 1
        if abnormal:
            class_cnt[(g, cn)] = class_cnt.get((g, cn), 0) + 1
            insp_bad[iid] = insp_bad.get(iid, 0) + 1
            per_bad[pn] = per_bad.get(pn, 0) + 1
        insp_total[iid] = insp_total.get(iid, 0) + 1
        per_total[pn] = per_total.get(pn, 0) + 1
        if d:
            if d == today:
                today_checked += 1
            if this_month_start <= d <= today:
                month_checked += 1
            if d >= this_month_start:
                this_month += 1

    # 2026-10-13「考试」口径：考试格既不算正常、也不算异常 ——
    # 正常率的分母排除考试格；异常只数 INSPECTION_EXEMPT_KEYS 之外的结果
    # （与上面 SQL notin_ 口径、以及 NULL 不算异常的行为保持一致）。
    exam_cnt = by_result.get('exam', 0)
    rated_total = total - exam_cnt
    normal_rate = round(normal_cnt * 100.0 / rated_total, 1) if rated_total else 0
    abnormal_cnt = sum(cnt for k, cnt in by_result.items()
                       if k is not None and k not in INSPECTION_EXEMPT_KEYS)

    # 结果分布
    result_distribution = [{'name': result_map.get(k, k), 'value': cnt}
                           for k, cnt in by_result.items()]

    # 月度趋势
    monthly_trend = sorted(monthly_map.values(), key=lambda x: x['month'] or '')

    # ── 2026-10-09 扩充：班级 / 检查人 / 节次 / 覆盖率（原来只有结果分布 + 月度趋势）──
    # ① 班级维度：异常最多的班级（巡课重点班）
    by_class = [
        {'grade': g or '', 'class_name': cn or '未填班级',
         'label': f'{g or ""}{cn or "未填班级"}', 'count': cnt}
        for (g, cn), cnt in sorted(
            class_cnt.items(),
            key=lambda kv: (-kv[1], kv[0][0] or '', kv[0][1] or ''))[:10]
    ]

    # ② 检查人维度：谁查得多、查出多少异常（巡课工作量）
    insp_ids = [i for i in insp_total if i]
    insp_names = {}
    if insp_ids:
        from app.models import User
        insp_names = {u.id: (u.real_name or u.username)
                      for u in User.query.filter(User.id.in_(insp_ids)).all()}
    by_inspector = [
        {'inspector_id': i, 'name': insp_names.get(i, f'#{i}' if i else '未知'),
         'count': c, 'abnormal': insp_bad.get(i, 0)}
        for i, c in sorted(insp_total.items(),
                           key=lambda kv: (-kv[1], kv[0] or 0))[:10]
    ]

    # ③ 节次维度：每节次已查 / 异常（看哪个时段最容易出问题）
    by_period = [{'period': p, 'count': per_total.get(p, 0),
                  'abnormal': per_bad.get(p, 0)}
                 for p in sorted(k for k in per_total if k)]

    # ④ 覆盖率：已查 / 应查（应查 = 当天课表里的"有课格子数"，按周课表估算，不分单双周）
    coverage = {'today_checked': 0, 'today_expected': 0,
                'month_checked': 0, 'month_expected': 0}
    try:
        from app.models.timetable import ScheduleEntry
        from app.modules.academic.services import term_service
        sched, _wk = term_service.resolve_schedule_by_date(today)
        if sched:
            if year:
                # 指定年份时覆盖率口径不受 year 过滤影响（与旧实现一致），单独查询
                checked_q = apply_academic_scope(InspectionRecord.query,
                                                 current_user, InspectionRecord)
                coverage['today_checked'] = checked_q.filter(
                    InspectionRecord.inspect_date == today).count()
                coverage['month_checked'] = checked_q.filter(
                    InspectionRecord.inspect_date >= this_month_start,
                    InspectionRecord.inspect_date <= today).count()
            else:
                coverage['today_checked'] = today_checked
                coverage['month_checked'] = month_checked
            per_wd = dict(db.session.query(ScheduleEntry.weekday,
                                           func.count(ScheduleEntry.id))
                          .filter(ScheduleEntry.term_schedule_id == sched.id,
                                  ScheduleEntry.is_deleted.is_(False))
                          .group_by(ScheduleEntry.weekday).all())
            coverage['today_expected'] = per_wd.get(today.isoweekday(), 0)
            want, d = 0, this_month_start
            while d <= today:
                want += per_wd.get(d.isoweekday(), 0)
                d = d + timedelta(days=1)
            coverage['month_expected'] = want
    except Exception as _e:  # noqa: BLE001  统计接口不因课表缺失而挂掉
        logger.warning('inspection.py::inspection_stats 异常（已降级处理）: %s', _e)

    return jsonify({
        'result_distribution': result_distribution,
        'monthly_trend': monthly_trend,
        'by_class': by_class,
        'by_inspector': by_inspector,
        'by_period': by_period,
        'coverage': coverage,
        'summary': {
            'total': total,
            'normal_rate': normal_rate,
            'this_month': this_month,
            'abnormal': abnormal_cnt,
        }
    })


@bp.route('/inspection/<int:rid>/delete', methods=['POST'])
@login_required
@perm_required('academic.edit')
def inspection_delete(rid):
    rec = db.session.get(InspectionRecord, rid)
    if not rec:
        abort(404)
    # 与 inspection_edit 一致：受年级与班级范围约束，避免删除范围外记录
    try:
        # 2026-10-11：校验下沉到 inspection_service（MCP 与网页端共用）
        rec = inspection_service.delete_record(current_user, rid)
    except PermissionError as exc:
        flash(str(exc), 'danger')
        return redirect(url_for('academic.inspection_page'))
    db.session.commit()
    inspection_service.log_inspection(
        current_user, '删除', rid, f'{rec.inspect_date} {rec.teacher_name}')
    flash('查课记录已删除', 'success')
    return redirect(url_for('academic.inspection_page'))


# ══════════════════════════════════════════════════════════════════════
# 查课联动 · 实时课表（v1.16.0，基于 timetable.db；仅增量添加，不改动上方逻辑）
# ══════════════════════════════════════════════════════════════════════

def _resolve_live_params():
    """解析实时课表公共参数。

    返回 (schedule, periods, target_date, period, grade, current_period, week)。
    """
    sched = swap_service.get_active_schedule()
    periods = swap_service.get_periods(sched.id) if sched else []
    date_str = (request.args.get('date') or '').strip()
    target_date = _parse_iso_date(date_str) or date.today()
    current_period = swap_service.current_period_number(periods)
    period = request.args.get('period', type=int)
    if not period:
        period = current_period or (periods[0].period_number if periods else 1)
    grade = (request.args.get('grade') or '').strip()
    # 2026-10-16：单节 JSON 也带周次，供 build_live_schedule 与全天矩阵同口径过滤
    from app.modules.academic.services import term_service
    _week = term_service.resolve_schedule_by_date(target_date)[1]
    return sched, periods, target_date, period, grade, current_period, _week


def _parse_iso_date(value):
    try:
        return date.fromisoformat((value or '').strip())
    except (ValueError, AttributeError):
        return None


def _attach_checks(data, target_date, user):
    """把当天已有的查课标记写进矩阵格子（entry dict 增加 'check'），并统计已查/应查。

    查课页的"标记"落在 inspection_records（同一格 = 同一天 + 同班 + 同节次）。
    """
    labels = dict(INSPECTION_RESULTS)
    rows = apply_academic_scope(
        InspectionRecord.query.filter(InspectionRecord.inspect_date == target_date),
        user, InspectionRecord).all()
    marks = {(r.grade, r.class_name, r.period): r for r in rows}
    checked, expected = 0, 0
    wd = data.get('weekday')
    for b in (data.get('blocks') or []):
        for cn, grid in (b.get('grids') or {}).items():
            for pn, by_day in grid.items():
                for e in (by_day.get(wd) or []):
                    expected += 1
                    rec = marks.get((b['grade'], cn, pn))
                    if rec:
                        checked += 1
                        e['check'] = {
                            'id': rec.id, 'result': rec.result,
                            'label': labels.get(rec.result, rec.result),
                            'note': rec.note or '',
                            'inspector_id': rec.inspector_id,
                        }
                    else:
                        e['check'] = None
    data['checked_total'] = checked
    data['expected_total'] = expected
    return data


@bp.route('/inspection/live-schedule')
@login_required
@perm_required('academic.view')
def inspection_live_schedule():
    """查课核对页：某天全校「行＝班级、列＝节次」矩阵，格内可直接标记查课结果。

    2026-10-09 改版：原来是"先选节次、再看一张单节次的班级列表"，巡课要反复切节次，
    看完还得回记录页手工补录（没法标记 正常/迟到/缺勤/调课）。现在与全校总课表
    同款版式 —— 一天所有节次一次铺开、班级占第一列、格内上学科下教师，单击=正常、
    双击=迟到、右键=缺勤/调课/考试等（状态写回 inspection_records），
    顶部实时显示"已查/应查"。
    """
    from app.modules.academic.services import schedule_service, term_service

    view = (request.args.get('view') or 'matrix').strip()
    target_date = _parse_iso_date(request.args.get('date')) or date.today()
    grade = (request.args.get('grade') or '').strip()
    sched, week = term_service.resolve_schedule_by_date(target_date)
    if not sched:
        flash('尚未建立学期课表，无法查课', 'warning')
        return redirect(url_for('academic.inspection_page'))
    visible = visible_academic_grades(current_user)
    class_scope = visible_academic_class_scope(current_user)
    if visible is not None and grade and grade not in visible:
        abort(403)
    grade_filter = grade or (sorted(visible) if visible is not None else None)
    # 2026-10-16：单节视图**不渲染**全天矩阵 → 不再装配它（原实现无条件跑一遍
    # get_day_matrix + _attach_checks，等于多一次全天课表查询 + 一次全天查课记录查询，
    # 结果整份 data 一个格子都没用上）。单节视图只需要节次表与当前节次。
    if view == 'period':
        _pds = swap_service.get_periods(sched.id)
        data = {'periods': [p.to_dict() for p in _pds],
                'current_period_number': swap_service.current_period_number(_pds),
                'checked_total': 0, 'expected_total': 0}
    else:
        data = schedule_service.get_day_matrix(
            sched.id, target_date, week=week, grade=grade_filter,
            allowed_classes=class_scope)
        _attach_checks(data, target_date, current_user)

    # 2026-10-13：单节课表视图 —— 只看一节，按年级分块列出各班任课教师（可就地发起代课/调课）
    pov_period = None
    pov_blocks, pov_stats = None, None
    if view == 'period':
        period_defs = swap_service.get_periods(sched.id)
        current_period = swap_service.current_period_number(period_defs)
        # 2026-10-16：各节次「有课数」——下拉里标出来（第2节 · 30课 / 空档标「空」），
        # 缺省节次也不再落到无课的节次（夜里打开时 current_period 为 None，
        # 原先退到第一个节次 = 早读，整页空档，看起来像坏了）
        counts = swap_service.count_period_classes(
            sched.id, target_date, week=week, grade_filter=grade_filter,
            allowed_classes=class_scope)
        pov_period = (request.args.get('period', type=int)
                      or _pick_period(period_defs, counts, current_period))
        for _p in (data.get('periods') or []):
            _p['has_class'] = counts.get(_p.get('period_number'), 0)
        pov_blocks, pov_stats = _period_overview_data(sched, target_date, pov_period,
                                                      grade, week=week)
        # 2026-10-14：单节视图同样可标记查课 —— 附上该节已有标记与覆盖率口径
        pov_stats.update(_attach_period_checks(pov_blocks, target_date, pov_period,
                                               current_user))

    grade_opts = swap_service.get_class_options(sched.id)['grades']
    if visible is not None:
        grade_opts = [g for g in grade_opts if g in visible]
    return render_template('academic/inspection_live.html',
                           schedule=sched, data=data, target_date=target_date,
                           week=week, grade=grade, grade_opts=grade_opts,
                           weekday_names=WEEKDAY_NAMES,
                           weekday=target_date.isoweekday(),
                           is_today=(target_date == date.today()),
                           today_iso=date.today().isoformat(),
                           results=INSPECTION_RESULTS,
                           view=view, pov_period=pov_period,
                           pov_blocks=pov_blocks, pov_stats=pov_stats,
                           can_schedule=_can_schedule(current_user),
                           can_mark=current_user.has_perm('academic.edit'),
                           now_str=datetime.now().strftime('%H:%M'))


@bp.route('/api/inspection/live-schedule')
@login_required
@perm_required('academic.view')
def api_inspection_live_schedule():
    """实时课表 JSON：供前端切换节次/年级时无刷新局部加载。"""
    sched, periods, target_date, period, grade, current_period, week = _resolve_live_params()
    if not sched:
        return jsonify({'success': False, 'message': '尚未建立学期课表'}), 400
    visible = visible_academic_grades(current_user)
    class_scope = visible_academic_class_scope(current_user)
    if visible is not None and grade and grade not in visible:
        abort(403)
    grade_filter = grade or (sorted(visible) if visible is not None else None)
    data = swap_service.build_live_schedule(
        sched.id, target_date, period, grade_filter,
        allowed_classes=class_scope, week=week)
    return jsonify({'success': True, 'message': 'ok', 'data': {
        'date': target_date.isoformat(),
        'period': period,
        'current_period': current_period,
        'weekday': target_date.isoweekday(),
        'weekday_text': WEEKDAY_NAMES.get(target_date.isoweekday(), ''),
        'grades': data,
    }})


@bp.route('/inspection/mark', methods=['POST'])
@login_required
@perm_required('academic.edit')
def inspection_mark():
    """查课标记（v1.18.8.0 新增）：给「某天 + 某班 + 某节次」写查课结果，同格覆盖更新。

    请求体（JSON，兼容表单）：
      inspect_date: '2026-10-09'
      cells: [{grade, class_name, period_number, subject, teacher_uid, teacher_name,
               entry_id, result, note}]
      - result ∈ normal/late/absent/swap/other；传空串 = 撤销该格标记
    一次可传多格（巡课"一键全部正常"就是一次几十格），返回更新/撤销/跳过条数。
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        payload = request.form.to_dict(flat=True)
        cells = request.form.get('cells')
        if cells:
            try:
                payload['cells'] = json.loads(cells)
            except ValueError:
                payload['cells'] = None
    cells = payload.get('cells')
    if not cells and payload.get('class_name'):
        cells = [dict(payload)]          # 兼容"单格 + 平铺字段"的老式提交
    if not isinstance(cells, list) or not cells:
        return jsonify({'success': False, 'message': '没有要标记的格子'}), 400
    target_date = _parse_iso_date(payload.get('inspect_date'))
    if not target_date:
        return jsonify({'success': False, 'message': '日期不正确'}), 400

    # 2026-10-11：校验与落库下沉到 inspection_service（MCP 与网页端共用）
    try:
        updated, cleared, skipped, touched = inspection_service.mark_cells(
            current_user, target_date, cells)
    except (ValueError, PermissionError) as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400
    db.session.commit()
    if updated or cleared:
        inspection_service.log_inspection(
            current_user, '查课标记', 0,
            f'{target_date} 标记 {updated} 格 / 撤销 {cleared} 格')
    return jsonify({'success': True, 'updated': updated, 'cleared': cleared,
                    'skipped': skipped, 'cells': touched,
                    'message': f'已标记 {updated} 格' + (f'，撤销 {cleared} 格' if cleared else '')
                               + (f'，忽略 {skipped} 格' if skipped else '')})


# ══════════════════════════════════════════════════════════════════════
# 查课「本节保存」+ 通知领导（2026-10-10 新增）
# ══════════════════════════════════════════════════════════════════════

def _timetable_leaders():
    """拥有「课表管理(academic.timetable)」权限的账号（含管理员），返回 uid（登录名）列表。

    站内通知按 uid 精确推送（notify_users → target_type='users'），所以这里返回
    账号的 username（= uid），而不是 users.id。
    """
    from app.models import User, UserPermissionGroup
    from app.models.permission_group import PermissionGroup
    groups = PermissionGroup.query.filter(
        PermissionGroup.menu_keys.like('%"academic.timetable"%')).all()
    gids = [g.id for g in groups if g.id]
    conds = [User.role == 'admin']
    if gids:
        # P1：改走多身份关联表（等价于原 permission_group_id.in_(gids)）
        _uids = db.session.query(UserPermissionGroup.user_id).filter(
            UserPermissionGroup.group_id.in_(gids)).subquery()
        conds.append(User.id.in_(_uids))
    users = User.query.filter(User.is_active.is_(True), db.or_(*conds)).all()
    return [u.username for u in users if getattr(u, 'username', None)]


@bp.route('/inspection/notify', methods=['POST'])
@login_required
@perm_required('academic.edit')
def inspection_notify():
    """把「本节次查课情况」以站内通知推送给课表管理员（领导）。

    请求体（JSON）：
      inspect_date: 'YYYY-MM-DD'
      period:       节次编号
      grade:        年级（可空，空=全部年级）
      expected:     应有课（应查）格数
      checked:      已查格数
      unchecked:    [{grade, class_name}] 未查班级清单（可空）
    返回 {success, message, data:{notified,...}}。
    """
    payload = request.get_json(silent=True) or request.form.to_dict()
    target_date = _parse_iso_date(payload.get('inspect_date')) or date.today()
    try:
        period = int(payload.get('period'))
    except (TypeError, ValueError):
        period = None
    if not period:
        return jsonify({'success': False, 'message': '缺少节次'}), 400
    grade = (payload.get('grade') or '').strip()
    try:
        expected = int(payload.get('expected') or 0)
    except (TypeError, ValueError):
        expected = 0
    try:
        checked = int(payload.get('checked') or 0)
    except (TypeError, ValueError):
        checked = 0
    unchecked = payload.get('unchecked') or []
    if not isinstance(unchecked, list):
        unchecked = []
    names = [f"{c.get('grade') or ''}{c.get('class_name') or ''}".strip()
             for c in unchecked if isinstance(c, dict)]
    names = [n for n in names if n]

    # 2026-10-11：推送逻辑下沉到 inspection_service（MCP 与网页端共用）
    ok, msg, data = inspection_service.notify_leaders(
        current_user, target_date, period, grade=grade, expected=expected,
        checked=checked, unchecked=unchecked)
    if not ok:
        return jsonify({'success': False, 'message': msg}), 400
    inspection_service.log_inspection(
        current_user, '通知', 0,
        f'{target_date} 第{period}节：通知 {data.get("notified", 0)} 人')
    return jsonify({'success': True, 'message': msg, 'data': data})


# ══════════════════════════════════════════════════════════════════════
# 单节总览（2026-10-13 新增）：某天某一节课 × 多年级 × 各班任课教师
# ══════════════════════════════════════════════════════════════════════
#
# 与「实时查课」的分工：
#   - 实时查课 = 全天所有节次的矩阵（行=班级、列=节次），用于**标记**查课结果；
#   - 本页     = **单节课**铺开成"班级卡片墙"，用于一眼看清这一节全校每个班谁在上课，
#                并可就地发起**代课 / 调课**（教务调度视角）。
#
# 班级数量完全由课表数据派生（build_live_schedule 按 distinct 班级取），
# 不假设"每年级 10 个班"—— 12 班 / 20 班等规模自动适应。
#
# 代课 / 调课的落库口径见 inspection_quick_swap 的 docstring。

def _truthy(value, default=False):
    """表单 / JSON 布尔解析：'1'、'true'、'on'、'yes' → True；None → default。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ('1', 'true', 'on', 'yes', 'y')


def _can_schedule(user):
    """单节总览能否就地发起代课/调课：课表管理 或 调课审批 权限（教务 / 领导）。"""
    return bool(user.has_perm('academic.timetable')
                or user.has_perm('academic.swap_approve'))


def _period_blocks(data):
    """把 build_live_schedule 的 {grade: [rows]} 组装成按年级分块的 blocks（含统计）。

    年级顺序沿用系统既有口径：毕业年级在前（高三 → 高二 → 高一）。
    """
    from app.modules.academic.services.grade_utils import grade_labels, grade_sort_key
    grades = [g for g in (data or {}).keys() if g]
    labels = grade_labels(grades)
    blocks = []
    for g in sorted(grades, key=lambda x: grade_sort_key(x, labels), reverse=True):
        rows = data.get(g) or []
        blocks.append({
            'grade': g,
            'grade_label': labels.get(g, g),
            'total': len(rows),
            'scheduled': sum(1 for r in rows if r.get('subject')),
            'rows': rows,
        })
    return blocks


def _pick_period(period_defs, counts, current_period):
    """单节视图的缺省节次：当前节次有课就用它；否则就近找有课的节次。

    2026-10-16：夜里（非当堂）打开页面时 current_period 为 None，原实现退到第一个
    节次（早读 / 课间，通常无课）→ 整页空档。现在改成「从当前节次起就近找一个有课的」，
    再退到当前节次 / 第一节。
    """
    nums = [p.period_number for p in (period_defs or [])]
    if not nums:
        return current_period or 1
    if current_period and counts.get(current_period):
        return current_period
    start = nums.index(current_period) if current_period in nums else 0
    for n in nums[start:] + list(reversed(nums[:start])):
        if counts.get(n):
            return n
    return current_period or nums[0]


def _period_overview_data(sched, target_date, period, grade, week=None):
    """单节总览数据（页面首屏与 JSON 接口共用）：返回 (blocks, stats)。

    - 受限账号（年级 / 班级数据范围）只下发范围内的班级，越界年级直接 403；
    - 班级数量按课表数据动态派生，不假设每年级固定班数；
    - week 与全天矩阵同口径（单双周 / 限定周次的课不在本周就显示为空档）。
    """
    visible = visible_academic_grades(current_user)
    class_scope = visible_academic_class_scope(current_user)
    if visible is not None and grade and grade not in visible:
        abort(403)
    grade_filter = grade or (sorted(visible) if visible is not None else None)
    data = swap_service.build_live_schedule(sched.id, target_date, period, grade_filter,
                                            allowed_classes=class_scope, week=week)
    blocks = _period_blocks(data)
    total_classes = sum(b['total'] for b in blocks)
    total_scheduled = sum(b['scheduled'] for b in blocks)
    stats = {'grades': len(blocks), 'classes': total_classes,
             'scheduled': total_scheduled, 'empty': total_classes - total_scheduled}
    return blocks, stats


def _attach_period_checks(blocks, target_date, period, user):
    """把「某天 + 某节」已有的查课标记附到单节总览卡片上，并返回该节统计。

    2026-10-14：单节视图也支持标记查课（与全天矩阵共用 inspection_records）：
    卡片右下角显示"未查 / 正常 / 迟到 / …"徽标，顶部覆盖率改成**这一节**的口径。
    空档（这节课没排课）不参与统计，也不可标记 —— 与矩阵里没有格子一致。
    """
    labels = dict(INSPECTION_RESULTS)
    rows = apply_academic_scope(
        InspectionRecord.query.filter(InspectionRecord.inspect_date == target_date,
                                      InspectionRecord.period == period),
        user, InspectionRecord).all()
    marks = {(r.grade, r.class_name): r for r in rows}
    checked = expected = 0
    for b in (blocks or []):
        for r in (b.get('rows') or []):
            if not r.get('subject'):
                r['check'] = None
                continue
            expected += 1
            rec = marks.get((b.get('grade'), r.get('class_name')))
            if rec:
                checked += 1
                r['check'] = {'id': rec.id, 'result': rec.result,
                              'label': labels.get(rec.result, rec.result),
                              'note': rec.note or ''}
            else:
                r['check'] = None
    return {'expected': expected, 'checked': checked,
            'rate': (checked * 100 // expected) if expected else 0}


@bp.route('/inspection/quick-swap', methods=['POST'])
@login_required
@perm_required('academic.swap')   # 2026-10-16 第八轮审计：发起调课/代课属写操作，
#                                  此前本文件唯一只挂 login 的写端点（其余 19 个均有权限点），
#                                  任何登录账号都能发起调课链路；权限点与 swap_apply/swap_cover 一致。
def inspection_quick_swap():
    """单节总览 → 就地发起「代课 / 调课」（2026-10-13 新增）。

    请求体（JSON，兼容表单）：
      entry_id:         本班这节课的条目 id（从卡片带过来）
      kind:             'cover'（代课：只换授课人）/ 'exchange'（调课：两节课换上课时间）
      partner_uid:      代课教师 / 对调教师 uid
      partner_entry_id: 对调教师的某一节课（kind=exchange 必填）
      swap_date:        临时调课日期（默认今天；is_permanent=true 时忽略）
      is_permanent:     是否长期（默认 false）
      reason:           事由（必填）

    2026-10-14（用户口径）：对调**只换上课时间**（班级 / 教室 / 学生不动），
    旧参数 swap_class 一律按 False 处理（"换班上课"已删除）。

    落库口径：以**原任课教师**为申请人（教务 / 领导代办），复用
    swap_service.apply_exchange_batch —— 与「请人代课」页完全同一条链路
    （对方确认 → 教务审批 → 执行生效），不绕过任何环节。

    权限：课表管理(academic.timetable) 或 调课审批(academic.swap_approve)。
    普通教师请走 /swap/cover、/swap/apply（申请人＝本人的课）。
    """
    from app.models.timetable import ScheduleEntry  # 局部导入（与 _visible_active_teachers 同风格）

    if not _can_schedule(current_user):
        return jsonify({'success': False, 'message': '需要课表管理或调课审批权限'}), 403

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        payload = request.form.to_dict()

    try:
        entry = db.session.get(ScheduleEntry, int(payload.get('entry_id') or 0))
    except (TypeError, ValueError):
        entry = None
    if not entry or entry.is_deleted:
        return jsonify({'success': False, 'message': '这节课不存在或已调整，请刷新页面'}), 400
    if not academic_class_is_visible(current_user, entry.grade, entry.class_name):
        return jsonify({'success': False, 'message': '没有该班级的操作权限'}), 403
    if not entry.teacher_uid:
        return jsonify({'success': False, 'message': '这节课还没排任课教师，无法代课'}), 400

    kind = (payload.get('kind') or 'cover').strip()
    if kind not in ('cover', 'exchange'):
        return jsonify({'success': False, 'message': '类型只能是代课或调课'}), 400
    partner_uid = (payload.get('partner_uid') or '').strip()
    if not partner_uid:
        return jsonify({'success': False,
                        'message': '请选择' + ('代课教师' if kind == 'cover' else '对调教师')}), 400
    reason = (payload.get('reason') or '').strip()
    if not reason:
        return jsonify({'success': False, 'message': '请填写事由'}), 400

    is_permanent = _truthy(payload.get('is_permanent'))
    item = {'entry_id': entry.id, 'partner_uid': partner_uid,
            'swap_time': _truthy(payload.get('swap_time'), True),
            'swap_class': _truthy(payload.get('swap_class'), True)}
    if kind == 'exchange':
        try:
            item['partner_entry_id'] = int(payload.get('partner_entry_id') or 0)
        except (TypeError, ValueError):
            item['partner_entry_id'] = 0
        if not item['partner_entry_id']:
            return jsonify({'success': False, 'message': '调课请再选中对方的具体一节课'}), 400
        # 2026-10-14（用户口径）：对调**只换上课时间** —— 不再换班级 / 教室 / 学生
        item['swap_time'] = True
        item['swap_class'] = False
    else:
        # 代课：时间、班级都不动，只换授课人
        item['swap_time'] = item['swap_class'] = True
    if not is_permanent:
        item['swap_date'] = _parse_iso_date(payload.get('swap_date')) or date.today()

    # 2026-10-14 第四轮审计修复（SEC-03）：本入口申请人记原任课教师（教务/领导
    # 代办），但单据原先**看不出是谁操作的**（对方收到的通知也像是教师本人发的）。
    # 现在把实际操作人写入 operator_uid / operator_name，通知文案与详情页都会标注。
    from app.modules.academic.services import teacher_service
    _op_teacher = teacher_service.teacher_of_user(current_user)
    if _op_teacher:
        op_uid, op_name = _op_teacher.teacher_uid, _op_teacher.name
    else:
        op_uid = (getattr(current_user, 'username', None) or str(current_user.id))[:16]
        op_name = current_user.real_name or current_user.username or ''

    # 2026-10-14 第三轮审计修复（SEC-02）：kind 原写死 'cover' —— 用户在上面选了
    # 「调课(exchange)」时，单据仍按「代课」落库/通知（对方课条目不校验、exchange_kind
    # 记错、通知文案变成"请你代课"）。改为透传上面已校验过的 kind。
    ok, msg, result = swap_service.apply_exchange_batch(
        applicant_uid=entry.teacher_uid,
        applicant_name=entry.teacher_name or '',
        items=[item], kind=kind, is_permanent=is_permanent, reason=reason,
        operator_uid=op_uid, operator_name=op_name)
    if not ok:
        db.session.rollback()
        return jsonify({'success': False, 'message': msg}), 400

    log_operation(current_user, '新增', '代课申请', result.get('rep_id') or 0,
                  f'单节总览：{entry.grade}{entry.class_name} {entry.subject}'
                  f'（周{entry.weekday}第{entry.period_number}节，{entry.teacher_name}）'
                  f'→ {partner_uid}',
                  module='academic')
    rep_id = result.get('rep_id')
    return jsonify({'success': True, 'message': msg, 'data': {
        'swap_id': rep_id,
        'detail_url': (url_for('academic.swap_detail', swap_id=rep_id)
                       if rep_id else None),
    }})


# ── 课时量统计（2026-10-13 用户口径）──────────────────────────────
# 查课统计页负责「统计与导出课时量」：课型折算规则与「考试期间」标记都在统计页的
# 弹窗里维护，这里只提供**读写配置 + 导出**两个能力；统计口径全部在
# services/workload_service.py（考试期间同时影响课时量与查课异常，口径只有一处）。

def _iso_d(value):
    """'YYYY-MM-DD' → date；非法/空返回 None"""
    try:
        return date.fromisoformat((value or '').strip())
    except (ValueError, AttributeError):
        return None


@bp.route('/inspection/workload/config')
@login_required
@perm_required('academic.edit')
def inspection_workload_config():
    """课时量配置：每个节次（课型）的折算系数 + 监考折算 + 考试期间清单。"""
    from app.modules.academic.services import workload_service as wl

    term = wl.resolve_term(date.today())
    term_id = term.id if term else 0
    rules = wl.rules_of(term_id)
    periods = wl.period_options(term)
    for p in periods:
        p['factor'] = wl.factor_of(rules, 'period', p['number'])
    rows = wl.exam_rows()
    grades = _grade_options()
    ug = visible_academic_grades(current_user)
    if ug is not None:
        grades = [g for g in grades if g in ug]
    return jsonify({'success': True, 'data': {
        'term_name': term.name if term else '',
        'periods': periods,
        'invigilation_factor': wl.factor_of(rules, 'invigilation', 0),
        'exam_periods': [{'id': r.id, 'grade': r.grade,
                          'class_name': r.class_name or '',
                          'start_date': r.start_date.isoformat(),
                          'end_date': r.end_date.isoformat(),
                          'note': r.note or ''} for r in rows],
        'grades': grades,
    }})


@bp.route('/inspection/workload/rules', methods=['POST'])
@login_required
@perm_required('academic.edit')
def inspection_workload_rules_save():
    """保存课型折算规则：每一项（节次 / 监考）单独设置一个折算值。

    折算值含义：**一节课算几课时**；0 = 不计入；留空 = 恢复默认 1。
    """
    from app.modules.academic.services import workload_service as wl
    from app.modules.academic.services.inspection_service import log_inspection

    payload = request.get_json(silent=True) or request.form
    items = payload.get('items') or []
    if not isinstance(items, list):
        return jsonify({'success': False, 'message': '参数错误：items 应为数组'}), 400
    term = wl.resolve_term(date.today())
    try:
        changed = wl.save_rules(term.id if term else 0, items)
        db.session.commit()
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        # 2026-10-18：异常原文不外露（含 SQL / 路径），详情进日志
        from flask import current_app
        current_app.logger.error('保存失败: %s', e, exc_info=True)
        return jsonify({'success': False, 'message': '保存失败，请稍后重试'}), 400
    log_inspection(current_user, '保存课时量折算规则', 0, f'{changed} 项变更')
    return jsonify({'success': True, 'message': f'已保存 {changed} 项折算规则'})


@bp.route('/inspection/exam-periods', methods=['POST'])
@login_required
@perm_required('academic.edit')
def inspection_exam_period_add():
    """标记一段「考试期间」（年级+班级+起止日期）。

    期间该班的课**不计课时工作量**，查课记录也**不计违纪/迟到** ——
    考试期间教师有正常考务工作量（监考/考务），不能算缺勤。
    """
    from app.modules.academic.services import workload_service as wl
    from app.modules.academic.services.inspection_service import log_inspection

    src = request.get_json(silent=True) or request.form
    grade = (src.get('grade') or '').strip()
    class_name = (src.get('class_name') or '').strip()
    start_d, end_d = _iso_d(src.get('start_date')), _iso_d(src.get('end_date'))
    if not academic_class_is_visible(current_user, grade, class_name or None):
        return jsonify({'success': False, 'message': '该班级不在你的可见范围内'}), 403
    ok, msg, row = wl.add_exam_period(grade, class_name, start_d, end_d,
                                      (src.get('note') or '').strip(),
                                      current_user.id)
    if not ok:
        return jsonify({'success': False, 'message': msg}), 400
    try:
        db.session.commit()
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        # 2026-10-18：异常原文不外露（含 SQL / 路径），详情进日志
        from flask import current_app
        current_app.logger.error('保存失败: %s', e, exc_info=True)
        return jsonify({'success': False, 'message': '保存失败，请稍后重试'}), 400
    log_inspection(current_user, '标记考试期间', row.id,
                   f'{grade}{class_name} {start_d}~{end_d}')
    return jsonify({'success': True, 'message': msg, 'data': {'id': row.id}})


@bp.route('/inspection/exam-periods/<int:pid>/delete', methods=['POST'])
@login_required
@perm_required('academic.edit')
def inspection_exam_period_delete(pid):
    from app.modules.academic.services import workload_service as wl
    from app.modules.academic.services.inspection_service import log_inspection

    ok, msg = wl.delete_exam_period(pid)
    if not ok:
        return jsonify({'success': False, 'message': msg}), 404
    try:
        db.session.commit()
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        return jsonify({'success': False, 'message': f'删除失败：{e}'}), 400
    log_inspection(current_user, '删除考试期间', pid, '')
    return jsonify({'success': True, 'message': msg})


@bp.route('/inspection/workload/export')
@login_required
@perm_required('academic.edit')
def inspection_workload_export():
    """导出课时量统计 Excel（按当前折算规则 + 考试期间口径）。"""
    from app.modules.academic.services import workload_service as wl
    from app.modules.academic.services.inspection_service import log_inspection

    from_d = _iso_d(request.args.get('date_from'))
    to_d = _iso_d(request.args.get('date_to'))
    if not from_d:
        from_d = date.today().replace(day=1)
    if not to_d:
        to_d = date.today()
    grade = (request.args.get('grade') or '').strip()
    class_name = (request.args.get('class_name') or '').strip()
    ug = visible_academic_grades(current_user)
    # 2026-10-16 审计修复：原判据 `if ug is not None and grade and grade not in ug`
    # 在「不带 grade」时整体短路放行，受限账号因此可导出**全校**课时量；class_name
    # 也从未校验。改为：受限账号必须指定可见范围内的年级，且逐班校验（与查课/课表
    # 同一口径 academic_class_authorizer）。
    if ug is not None:
        if not grade or grade not in ug:
            flash('请选择你可见范围内的年级后再导出' if not grade
                  else '该年级不在你的可见范围内', 'warning')
            return redirect(url_for('academic.inspection_page'))
        if class_name and not academic_class_authorizer(current_user)(grade, class_name):
            flash('该班级不在你的可见范围内', 'warning')
            return redirect(url_for('academic.inspection_page'))
    try:
        out, data = wl.export_workload(from_d, to_d, grade=grade,
                                       class_name=class_name)
    except ValueError as e:
        flash(str(e), 'warning')
        return redirect(url_for('academic.inspection_page'))
    log_inspection(current_user, '导出课时量', 0,
                   f'{from_d}~{to_d} {grade}{class_name}'
                   f' 共 {data["summary"]["teachers"]} 位教师')
    return send_file(out, as_attachment=True,
                     download_name=f'课时量统计_{from_d}_{to_d}.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
