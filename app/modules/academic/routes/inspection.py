# StuLink v1.18.2.0 2026-09-24
# 教务 · 查课统计：记录录入 / 列表筛选 / 月度统计 / 批量录入 / 导出 / 图表API
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import io
from datetime import date, datetime

from flask import render_template, request, redirect, url_for, flash, abort, jsonify, send_file
from flask_login import login_required, current_user
from sqlalchemy import func

from app.extensions import db
from app.models.academic import (InspectionRecord, Teacher, INSPECTION_RESULTS)
from app.models.timetable import WEEKDAY_NAMES
from app.modules.academic import bp
from app.modules.academic.services import swap_service
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation

_RESULT_KEYS = {k for k, _ in INSPECTION_RESULTS}


def _active_teachers():
    return Teacher.query.filter_by(status='active').order_by(Teacher.teacher_uid).all()


def _grade_options():
    """年级字典选项（活跃年级）"""
    from app.models import DictCategory
    cat = DictCategory.query.filter_by(code='grade').first()
    if not cat:
        return []
    return [i.value for i in cat.items.filter_by(is_active=True)
            .order_by('sort_order').all()]


@bp.route('/inspection')
@login_required
@perm_required('academic.view')
def inspection_page():
    """查课记录与统计（按用户数据范围过滤年级）"""
    from app.modules.grades.services.scope import user_grade_scope
    ug = user_grade_scope(current_user)
    teacher_uid = (request.args.get('teacher_uid') or '').strip()
    result_f = (request.args.get('result') or '').strip()
    d_from = (request.args.get('date_from') or '').strip()
    d_to = (request.args.get('date_to') or '').strip()

    q = InspectionRecord.query
    if ug is not None:
        q = q.filter(InspectionRecord.grade.in_(ug))
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
    records = (q.order_by(InspectionRecord.inspect_date.desc(),
                          InspectionRecord.id.desc()).limit(300).all())

    # 本月统计（同样按数据范围）
    today = date.today()
    month_start = today.replace(day=1)
    month_q = InspectionRecord.query.filter(
        InspectionRecord.inspect_date >= month_start)
    if ug is not None:
        month_q = month_q.filter(InspectionRecord.grade.in_(ug))
    month_total = month_q.count()
    month_abnormal = month_q.filter(
        InspectionRecord.result != 'normal').count()
    by_teacher = (InspectionRecord.query.with_entities(
        InspectionRecord.teacher_uid, InspectionRecord.teacher_name,
        func.count(InspectionRecord.id))
        .filter(InspectionRecord.inspect_date >= month_start)
        .group_by(InspectionRecord.teacher_uid, InspectionRecord.teacher_name)
        .order_by(func.count(InspectionRecord.id).desc()).limit(10).all())

    grade_opts = _grade_options()
    if ug is not None:
        grade_opts = [g for g in grade_opts if g in ug]
    return render_template('academic/inspection.html',
                           records=records, teachers=_active_teachers(),
                           results=INSPECTION_RESULTS, grade_opts=grade_opts,
                           f_teacher=teacher_uid, f_result=result_f,
                           f_from=d_from, f_to=d_to,
                           month_total=month_total, month_abnormal=month_abnormal,
                           by_teacher=by_teacher, today=today.isoformat())


@bp.route('/inspection/add', methods=['POST'])
@login_required
@perm_required('academic.edit')
def inspection_add():
    """录入一条查课记录（年级必选；用户级数据范围时仅限授权年级）"""
    from app.modules.grades.services.scope import user_grade_scope
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
    ug = user_grade_scope(current_user)
    if ug is not None and grade not in ug:
        flash('该年级不在你的可见范围内', 'danger')
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
        class_name=(request.form.get('class_name') or '').strip() or None,
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
    from app.modules.grades.services.scope import user_grade_scope
    back = redirect(url_for('academic.inspection_page'))
    date_str = (request.form.get('inspect_date') or '').strip()
    try:
        inspect_date = date.fromisoformat(date_str)
    except ValueError:
        flash('请选择正确的查课日期', 'danger')
        return back
    ug = user_grade_scope(current_user)

    rows = request.form.getlist('row_teacher_uid')
    grades = request.form.getlist('row_grade')
    periods = request.form.getlist('row_period')
    class_names = request.form.getlist('row_class_name')
    subjects = request.form.getlist('row_subject')
    results_list = request.form.getlist('row_result')
    notes = request.form.getlist('row_note')

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
            class_name=(class_names[i] if i < len(class_names) else '').strip() or None,
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
    from app.modules.grades.services.scope import user_grade_scope
    from app.utils.export_helpers import xl_safe

    ug = user_grade_scope(current_user)
    teacher_uid = (request.args.get('teacher_uid') or '').strip()
    result_f = (request.args.get('result') or '').strip()
    d_from = (request.args.get('date_from') or '').strip()
    d_to = (request.args.get('date_to') or '').strip()
    grade_f = (request.args.get('grade') or '').strip()

    q = InspectionRecord.query
    if ug is not None:
        q = q.filter(InspectionRecord.grade.in_(ug))
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
            r.inspector_id or '',
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
    """返回查课统计数据 JSON（供 ECharts 图表使用）"""
    from app.modules.grades.services.scope import user_grade_scope
    ug = user_grade_scope(current_user)
    year = request.args.get('year', type=int)

    base_q = InspectionRecord.query
    if ug is not None:
        base_q = base_q.filter(InspectionRecord.grade.in_(ug))
    if year:
        base_q = base_q.filter(
            func.strftime('%Y', InspectionRecord.inspect_date) == str(year))

    # 结果分布
    dist_rows = (base_q.with_entities(InspectionRecord.result,
                                      func.count(InspectionRecord.id))
                 .group_by(InspectionRecord.result).all())
    result_map = dict(INSPECTION_RESULTS)
    result_distribution = [
        {'name': result_map.get(k, k), 'value': cnt}
        for k, cnt in dist_rows
    ]

    # 月度趋势
    month_rows = (base_q.with_entities(
        func.strftime('%Y-%m', InspectionRecord.inspect_date).label('month'),
        InspectionRecord.result, func.count(InspectionRecord.id))
        .group_by('month', InspectionRecord.result)
        .order_by('month').all())
    monthly_map = {}
    for m, res, cnt in month_rows:
        if m not in monthly_map:
            monthly_map[m] = {'month': m, 'normal': 0, 'late': 0,
                              'absent': 0, 'swap': 0, 'other': 0}
        if res in monthly_map[m]:
            monthly_map[m][res] = cnt
    monthly_trend = sorted(monthly_map.values(), key=lambda x: x['month'])

    # 汇总
    total = base_q.count()
    normal_cnt = base_q.filter_by(result='normal').count()
    normal_rate = round(normal_cnt * 100.0 / total, 1) if total else 0
    today = date.today()
    this_month_start = today.replace(day=1)
    this_month_q = base_q.filter(InspectionRecord.inspect_date >= this_month_start)
    this_month = this_month_q.count()

    return jsonify({
        'result_distribution': result_distribution,
        'monthly_trend': monthly_trend,
        'summary': {
            'total': total,
            'normal_rate': normal_rate,
            'this_month': this_month,
        }
    })


@bp.route('/inspection/<int:rid>/delete', methods=['POST'])
@login_required
@perm_required('academic.edit')
def inspection_delete(rid):
    rec = db.session.get(InspectionRecord, rid)
    if not rec:
        abort(404)
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


@bp.route('/inspection/live-schedule')
@login_required
@perm_required('academic.view')
def inspection_live_schedule():
    """查课实时课表页：某天某节次全校（或指定年级）课表，叠加当天临时调课。"""
    sched, periods, target_date, period, grade, current_period = _resolve_live_params()
    if not sched:
        flash('尚未建立学期课表，无法查看实时课表', 'warning')
        return redirect(url_for('academic.inspection_page'))
    class_opts = swap_service.get_class_options(sched.id)
    data = swap_service.build_live_schedule(sched.id, target_date, period, grade or None)
    return render_template('academic/inspection_live.html',
                           schedule=sched, periods=periods, target_date=target_date,
                           period=period, current_period=current_period,
                           grade=grade, grade_opts=class_opts['grades'], data=data,
                           weekday_names=WEEKDAY_NAMES,
                           now_str=datetime.now().strftime('%H:%M'),
                           weekday=target_date.isoweekday())


@bp.route('/api/inspection/live-schedule')
@login_required
@perm_required('academic.view')
def api_inspection_live_schedule():
    """实时课表 JSON：供前端切换节次/年级时无刷新局部加载。"""
    sched, periods, target_date, period, grade, current_period = _resolve_live_params()
    if not sched:
        return jsonify({'success': False, 'message': '尚未建立学期课表'}), 400
    data = swap_service.build_live_schedule(sched.id, target_date, period, grade or None)
    return jsonify({'success': True, 'message': 'ok', 'data': {
        'date': target_date.isoformat(),
        'period': period,
        'current_period': current_period,
        'weekday': target_date.isoweekday(),
        'weekday_text': WEEKDAY_NAMES.get(target_date.isoweekday(), ''),
        'grades': data,
    }})
