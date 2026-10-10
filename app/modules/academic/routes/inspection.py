# StuLink v1.18.9.1 2026-10-10
# 教务 · 查课统计：记录录入 / 列表筛选 / 月度统计 / 批量录入 / 导出 / 图表API
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import io
import json
from datetime import date, datetime, timedelta

from flask import render_template, request, redirect, url_for, flash, abort, jsonify, send_file
from flask_login import login_required, current_user
from sqlalchemy import func

from app.extensions import db
from app.models.academic import (InspectionRecord, Teacher, INSPECTION_RESULTS)
from app.models.timetable import WEEKDAY_NAMES
from app.modules.academic import bp
from app.modules.academic.services.access_scope import (
    academic_class_authorizer, academic_class_is_visible,
    academic_has_class_restrictions, apply_academic_scope,
    visible_academic_class_scope, visible_academic_grades,
)
from app.modules.academic.services import swap_service
from app.modules.notifications.services import notification_service
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation

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
        except ValueError:
            pass
    if d_to:
        try:
            q = q.filter(InspectionRecord.inspect_date <= date.fromisoformat(d_to))
        except ValueError:
            pass
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
    month_abnormal = month_q.filter(
        InspectionRecord.result != 'normal').count()
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
                           f_teacher=teacher_uid, f_result=result_f, f_grade=grade_f,
                           f_from=d_from, f_to=d_to,
                           month_total=month_total, month_abnormal=month_abnormal,
                           by_teacher=by_teacher, today=today.isoformat(),
                           can_edit=current_user.has_perm('academic.edit'))


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
    if not academic_class_is_visible(current_user, rec.grade, rec.class_name):
        flash('该记录不在你的可见范围内', 'danger')
        return redirect(url_for('academic.inspection_page'))

    before = rec.result
    result = (request.form.get('result') or '').strip()
    if result in _RESULT_KEYS:
        rec.result = result
    class_name = (request.form.get('class_name') or '').strip() or None
    if not academic_class_is_visible(current_user, rec.grade, class_name):
        flash('所选班级不在你的可见范围内', 'danger')
        return redirect(url_for('academic.inspection_page'))
    rec.class_name = class_name
    subject = (request.form.get('subject') or '').strip()
    if subject:
        rec.subject = subject
    rec.period = request.form.get('period', type=int)
    rec.note = (request.form.get('note') or '').strip() or None
    db.session.commit()
    log_operation(current_user, '更新', '查课记录', rid,
                  f'{rec.teacher_name} 结果 {before}→{rec.result}', module='academic')
    flash('查课记录已更新', 'success')
    return redirect(url_for('academic.inspection_page',
                            grade=(request.form.get('back_grade') or None),
                            teacher_uid=(request.form.get('back_teacher') or None)))


@bp.route('/inspection/add', methods=['POST'])
@login_required
@perm_required('academic.edit')
def inspection_add():
    """录入一条查课记录（年级必选；用户级数据范围时仅限授权年级）"""
    back = redirect(url_for('academic.inspection_page'))
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
    ug = visible_academic_grades(current_user)
    if ug is not None and grade not in ug:
        flash('该年级不在你的可见范围内', 'danger')
        return back
    class_name = (request.form.get('class_name') or '').strip() or None
    if not academic_class_is_visible(current_user, grade, class_name):
        flash('请选择你负责范围内的班级', 'danger')
        return back
    if academic_has_class_restrictions(current_user):
        allowed_teacher_uids = {t.teacher_uid for t in _visible_active_teachers(current_user)}
        if teacher_uid not in allowed_teacher_uids:
            flash('请选择你负责范围内的任课教师', 'danger')
            return back
    t = Teacher.query.filter_by(teacher_uid=teacher_uid).first()
    if not t:
        flash('请选择被查课的教师', 'danger')
        return back
    if result not in _RESULT_KEYS:
        result = 'normal'
    period = request.form.get('period', type=int)

    rec = InspectionRecord(
        inspect_date=inspect_date,
        grade=grade,
        period=period,
        teacher_uid=t.teacher_uid,
        teacher_name=t.name,
        class_name=class_name,
        subject=(request.form.get('subject') or '').strip() or t.subject,
        result=result,
        inspector_id=current_user.id,
        note=(request.form.get('note') or '').strip() or None,
    )
    db.session.add(rec)
    db.session.commit()
    log_operation(current_user, '新增', '查课记录', rec.id,
                  f'{inspect_date} 第{period or "?"}节 {t.name} {rec.class_name or ""}',
                  module='academic')
    flash(f'已记录 {t.name} 的查课情况', 'success')
    return back


@bp.route('/inspection/batch', methods=['POST'])
@login_required
@perm_required('academic.edit')
def inspection_batch():
    """批量录入查课记录"""
    back = redirect(url_for('academic.inspection_page'))
    date_str = (request.form.get('inspect_date') or '').strip()
    try:
        inspect_date = date.fromisoformat(date_str)
    except ValueError:
        flash('请选择正确的查课日期', 'danger')
        return back
    ug = visible_academic_grades(current_user)

    rows = request.form.getlist('row_teacher_uid')
    grades = request.form.getlist('row_grade')
    periods = request.form.getlist('row_period')
    class_names = request.form.getlist('row_class_name')
    subjects = request.form.getlist('row_subject')
    results_list = request.form.getlist('row_result')
    notes = request.form.getlist('row_note')
    allowed_teacher_uids = None
    class_visible = academic_class_authorizer(current_user)
    if academic_has_class_restrictions(current_user):
        allowed_teacher_uids = {t.teacher_uid for t in _visible_active_teachers(current_user)}

    added = 0
    for i, t_uid in enumerate(rows):
        t_uid = (t_uid or '').strip()
        if not t_uid:
            continue
        grade = (grades[i] if i < len(grades) else '').strip()
        if not grade:
            continue
        if ug is not None and grade not in ug:
            continue
        class_name = (class_names[i] if i < len(class_names) else '').strip() or None
        if not class_visible(grade, class_name):
            continue
        if allowed_teacher_uids is not None and t_uid not in allowed_teacher_uids:
            continue
        t = Teacher.query.filter_by(teacher_uid=t_uid).first()
        if not t:
            continue
        result = (results_list[i] if i < len(results_list) else 'normal').strip()
        if result not in _RESULT_KEYS:
            result = 'normal'
        period = None
        if i < len(periods) and periods[i]:
            try:
                period = int(periods[i])
            except (ValueError, TypeError):
                pass
        rec = InspectionRecord(
            inspect_date=inspect_date,
            grade=grade,
            period=period,
            teacher_uid=t.teacher_uid,
            teacher_name=t.name,
            class_name=class_name,
            subject=(subjects[i] if i < len(subjects) else '').strip() or t.subject,
            result=result,
            inspector_id=current_user.id,
            note=(notes[i] if i < len(notes) else '').strip() or None,
        )
        db.session.add(rec)
        added += 1

    if added == 0:
        flash('没有有效的记录被添加', 'warning')
        return back
    db.session.commit()
    log_operation(current_user, '批量新增', '查课记录', 0,
                  f'{inspect_date} 共{added}条', module='academic')
    flash(f'已批量录入 {added} 条查课记录', 'success')
    return back


@bp.route('/inspection/export')
@login_required
@perm_required('academic.edit')
def inspection_export():
    """导出查课记录 Excel"""
    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from app.utils.export_helpers import xl_safe

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
        except ValueError:
            pass
    if d_to:
        try:
            q = q.filter(InspectionRecord.inspect_date <= date.fromisoformat(d_to))
        except ValueError:
            pass
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
    widths = [12, 8, 6, 14, 12, 10, 12, 8, 8, 20]

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
            r.period or '',
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
        # 与 SQL 的 result != 'normal' 语义一致：NULL 不算异常
        abnormal = res is not None and res != 'normal'
        m = d.strftime('%Y-%m') if d else None
        mm = monthly_map.setdefault(m, {'month': m, 'normal': 0, 'late': 0,
                                        'absent': 0, 'swap': 0, 'other': 0})
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

    normal_rate = round(normal_cnt * 100.0 / total, 1) if total else 0
    abnormal_cnt = total - normal_cnt

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
    except Exception:  # noqa: BLE001  统计接口不因课表缺失而挂掉
        pass

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
    if not academic_class_is_visible(current_user, rec.grade, rec.class_name):
        flash('该记录不在你的可见范围内', 'danger')
        return redirect(url_for('academic.inspection_page'))
    db.session.delete(rec)
    db.session.commit()
    log_operation(current_user, '删除', '查课记录', rid,
                  f'{rec.inspect_date} {rec.teacher_name}', module='academic')
    flash('查课记录已删除', 'success')
    return redirect(url_for('academic.inspection_page'))


# ══════════════════════════════════════════════════════════════════════
# 查课联动 · 实时课表（v1.16.0，基于 timetable.db；仅增量添加，不改动上方逻辑）
# ══════════════════════════════════════════════════════════════════════

def _resolve_live_params():
    """解析实时课表公共参数：返回 (schedule, periods, target_date, period, grade, current_period)。"""
    sched = swap_service.get_active_schedule()
    periods = swap_service.get_periods(sched.id) if sched else []
    date_str = (request.args.get('date') or '').strip()
    target_date = _parse_iso_date(date_str) or date.today()
    current_period = swap_service.current_period_number(periods)
    period = request.args.get('period', type=int)
    if not period:
        period = current_period or (periods[0].period_number if periods else 1)
    grade = (request.args.get('grade') or '').strip()
    return sched, periods, target_date, period, grade, current_period


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
    看完还得回记录页手工补录（没法标记 正常/迟到/缺课/调课）。现在与全校总课表
    同款版式 —— 一天所有节次一次铺开、班级占第一列、格内上学科下教师，点格子即
    弹出标记面板（正常/迟到/缺课/调课/其他 + 备注），状态写回 inspection_records，
    顶部实时显示"已查/应查"。
    """
    from app.modules.academic.services import schedule_service, term_service

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
    data = schedule_service.get_day_matrix(
        sched.id, target_date, week=week, grade=grade_filter,
        allowed_classes=class_scope)
    _attach_checks(data, target_date, current_user)

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
                           can_mark=current_user.has_perm('academic.edit'),
                           now_str=datetime.now().strftime('%H:%M'))


@bp.route('/api/inspection/live-schedule')
@login_required
@perm_required('academic.view')
def api_inspection_live_schedule():
    """实时课表 JSON：供前端切换节次/年级时无刷新局部加载。"""
    sched, periods, target_date, period, grade, current_period = _resolve_live_params()
    if not sched:
        return jsonify({'success': False, 'message': '尚未建立学期课表'}), 400
    visible = visible_academic_grades(current_user)
    class_scope = visible_academic_class_scope(current_user)
    if visible is not None and grade and grade not in visible:
        abort(403)
    grade_filter = grade or (sorted(visible) if visible is not None else None)
    data = swap_service.build_live_schedule(
        sched.id, target_date, period, grade_filter,
        allowed_classes=class_scope)
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

    ug = visible_academic_grades(current_user)
    class_visible = academic_class_authorizer(current_user)
    updated = cleared = skipped = 0
    touched = []
    for cell in cells[:500]:             # 单次上限：挡住异常/恶意的大批量提交
        if not isinstance(cell, dict):
            skipped += 1
            continue
        grade = (cell.get('grade') or '').strip()
        class_name = (cell.get('class_name') or '').strip() or None
        try:
            period = int(cell.get('period_number'))
        except (TypeError, ValueError):
            period = None
        if not grade or not period:
            skipped += 1
            continue
        if ug is not None and grade not in ug:
            skipped += 1
            continue
        if not class_visible(grade, class_name):
            skipped += 1
            continue
        result = (cell.get('result') or '').strip()
        if result and result not in _RESULT_KEYS:
            skipped += 1
            continue

        teacher_uid = (cell.get('teacher_uid') or '').strip()
        teacher_name = (cell.get('teacher_name') or '').strip()
        subject = (cell.get('subject') or '').strip()
        entry_id = str(cell.get('entry_id') or '').strip()
        if entry_id.isdigit():
            # 教师/学科以课表条目为准（前端只传 entry_id），避免标记与课表对不上
            from app.models.timetable import ScheduleEntry
            be = db.session.get(ScheduleEntry, int(entry_id))
            if be:
                teacher_uid = be.teacher_uid or teacher_uid
                teacher_name = be.teacher_name or teacher_name
                subject = be.subject or subject
                class_name = be.class_name or class_name
                period = be.period_number or period

        rec = (InspectionRecord.query
               .filter_by(inspect_date=target_date, grade=grade,
                          class_name=class_name, period=period)
               .order_by(InspectionRecord.id.desc()).first())
        if not result:
            if rec:
                db.session.delete(rec)
                cleared += 1
                touched.append({'grade': grade, 'class_name': class_name,
                                'period_number': period, 'result': ''})
            continue
        if rec:
            rec.result = result
            rec.teacher_uid = teacher_uid or rec.teacher_uid
            rec.teacher_name = teacher_name or rec.teacher_name
            rec.subject = subject or rec.subject
            rec.inspector_id = current_user.id
            if 'note' in cell:
                rec.note = (cell.get('note') or '').strip() or None
        else:
            db.session.add(InspectionRecord(
                inspect_date=target_date, grade=grade, period=period,
                teacher_uid=teacher_uid or None, teacher_name=teacher_name or None,
                class_name=class_name, subject=subject or None, result=result,
                inspector_id=current_user.id,
                note=(cell.get('note') or '').strip() or None))
        updated += 1
        touched.append({'grade': grade, 'class_name': class_name,
                        'period_number': period, 'result': result,
                        'label': dict(INSPECTION_RESULTS).get(result, result)})
    db.session.commit()
    if updated or cleared:
        log_operation(current_user, '查课标记', '查课记录', 0,
                      f'{target_date} 标记 {updated} 格 / 撤销 {cleared} 格',
                      module='academic')
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
    from app.models import User
    from app.models.permission_group import PermissionGroup
    groups = PermissionGroup.query.filter(
        PermissionGroup.menu_keys.like('%"academic.timetable"%')).all()
    gids = [g.id for g in groups if g.id]
    conds = [User.role == 'admin']
    if gids:
        conds.append(User.permission_group_id.in_(gids))
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

    uids = _timetable_leaders()
    if not uids:
        return jsonify({'success': False,
                        'message': '没有找到具备「课表管理」权限的领导账号，无法通知'}), 400

    grade_text = f'（{grade}）' if grade else ''
    miss_text = ('；未查：' + '、'.join(names[:30]) + ('等' if len(names) > 30 else '')) if names else ''
    title = f'查课提醒：{target_date} 第{period}节{grade_text}有 {len(names) or (expected - checked)} 个班未查'
    content = (f'{current_user.real_name} 于 {datetime.now().strftime("%Y-%m-%d %H:%M")} 提交查课：'
               f'{target_date} 第{period}节{grade_text}，应查 {expected} 个班，已查 {checked} 个，'
               f'未查 {len(names) if names else (expected - checked)} 个{miss_text}。')
    ok, msg, data = notification_service.notify_users(
        uids, title, content, category='academic', biz_type='inspection',
        creator_id=current_user.id, creator_name=current_user.real_name,
        priority='high')
    if not ok:
        return jsonify({'success': False, 'message': msg}), 400
    log_operation(current_user, '通知', '查课提醒', 0,
                  f'{target_date} 第{period}节：通知 {data.get("notified", 0)} 人',
                  module='academic')
    return jsonify({'success': True, 'message': msg, 'data': data})
