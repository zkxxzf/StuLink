"""考勤管理路由"""
from datetime import date

from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for, send_file
from flask_login import login_required, current_user

from app.modules.workbench.services.attendance_service import (
    record_attendance, get_attendance, get_attendance_stats,
    get_class_students, get_teacher_classes, export_attendance,
)
from app.models.academic import ATTENDANCE_STATUS
from app.modules.workbench.services import scope_service
from app.utils.decorators import perm_required

bp = Blueprint('attendance', __name__, url_prefix='/workbench/attendance')


@bp.route('/')
@login_required
@perm_required('workbench.attendance_view')
def attendance_page():
    """考勤管理页面"""
    classes = get_teacher_classes(current_user.id)
    default_grade = classes[0][0] if classes else ''
    default_class = classes[0][1] if classes else ''

    sel_grade = request.args.get('grade', default_grade)
    sel_class = request.args.get('class_name', default_class)
    # 越界（非管辖班级）回退到默认班级
    if sel_class and not scope_service.is_class_in_scope(current_user, sel_grade, sel_class):
        sel_grade, sel_class = default_grade, default_class
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    page = request.args.get('page', 1, type=int)

    pagination = get_attendance(
        class_name=sel_class, grade=sel_grade,
        date_from=date.fromisoformat(date_from) if date_from else None,
        date_to=date.fromisoformat(date_to) if date_to else None,
        page=page, per_page=30,
    )

    students = get_class_students(sel_class, sel_grade) if sel_class else []

    return render_template('workbench/attendance.html',
                           classes=classes,
                           sel_grade=sel_grade,
                           sel_class=sel_class,
                           date_from=date_from,
                           date_to=date_to,
                           pagination=pagination,
                           records=pagination.items,
                           students=students,
                           status_options=ATTENDANCE_STATUS,
                           today=date.today())


@bp.route('/record', methods=['POST'])
@login_required
@perm_required('workbench.attendance')
def attendance_record():
    """批量记录考勤"""
    attend_date = request.form.get('attend_date', '')
    period = request.form.get('period', type=int)
    class_name = request.form.get('class_name', '')
    grade = request.form.get('grade', '')

    if not attend_date or not class_name:
        flash('请选择日期和班级', 'danger')
        return redirect(url_for('attendance.attendance_page'))

    if not scope_service.is_class_in_scope(current_user, grade, class_name):
        flash('无该班级的操作权限（超出管辖范围）', 'danger')
        return redirect(url_for('attendance.attendance_page'))

    records_list = []
    valid_nos = {s.student_number for s in get_class_students(class_name, grade)}
    for key, value in request.form.items():
        if key.startswith('status_'):
            student_no = key[7:]
            if student_no not in valid_nos:
                continue
            student_name = request.form.get(f'name_{student_no}', '')
            remark = request.form.get(f'remark_{student_no}', '').strip()
            records_list.append({
                'student_no': student_no,
                'student_name': student_name,
                'grade': grade,
                'class_name': class_name,
                'attend_date': attend_date,
                'period': period,
                'status': value,
                'remark': remark,
            })

    if records_list:
        record_attendance(records_list, current_user.id)
        flash(f'已记录 {len(records_list)} 条考勤', 'success')
    else:
        flash('未找到考勤数据', 'warning')

    return redirect(url_for('attendance.attendance_page',
                            grade=grade, class_name=class_name))


@bp.route('/export')
@login_required
@perm_required('workbench.attendance_view')
def attendance_export():
    """导出考勤 Excel"""
    class_name = request.args.get('class_name', '')
    grade = request.args.get('grade', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')

    if not class_name:
        flash('请选择班级后再导出', 'warning')
        return redirect(url_for('attendance.attendance_page'))

    if not scope_service.is_class_in_scope(current_user, grade, class_name):
        flash('无该班级的导出权限（超出管辖范围）', 'danger')
        return redirect(url_for('attendance.attendance_page'))

    out = export_attendance(
        class_name=class_name,
        date_from=date.fromisoformat(date_from) if date_from else None,
        date_to=date.fromisoformat(date_to) if date_to else None,
        grade=grade,
    )
    filename = f'考勤记录_{class_name}_{date.today().strftime("%Y%m%d")}.xlsx'
    return send_file(out, as_attachment=True, download_name=filename,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@bp.route('/stats')
@login_required
@perm_required('workbench.attendance_view')
def attendance_stats_api():
    """考勤统计 JSON 接口"""
    class_name = request.args.get('class_name', '')
    grade = request.args.get('grade', '')
    month = request.args.get('month', '')

    if not class_name:
        return jsonify({'error': '请选择班级'}), 400

    if not scope_service.is_class_in_scope(current_user, grade, class_name):
        return jsonify({'error': '无该班级的访问权限（超出管辖范围）'}), 403

    stats = get_attendance_stats(class_name, grade=grade, month=month or None)
    return jsonify(stats)
