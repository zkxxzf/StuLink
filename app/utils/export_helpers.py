"""学生列表导出工具 + Excel 导出公共工具"""
import io
import re
from datetime import datetime
from flask import send_file
from app.models import Student, Room, BedAssignment, StudentAccommodation, OperationLog
from app.utils.helpers import get_graduated_grades
from app.utils.student_scope import apply_student_scope
from app.extensions import db

# ── Excel / WPS 公式注入防护 ─────────────────────────────────
# 单元格内容以这些字符开头时，表格软件会把它当成公式求值（= + - @）
# 或触发 DDE/外部链接调用，故前置一个半角单引号强制转为文本。
# 单引号是表格软件的「文本指示符」，打开时不会显示出来。
_FORMULA_RISK_CHARS = ('=', '+', '-', '@', '\t', '\r', '\n')
# 控制字符（Excel 会忽略 \x00-\x08 等，留着只会干扰排查）
_CTRL_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')


def _client_ip():
    """L-1：导出日志的来源 IP 统一取请求真实地址（此前可由调用方参数伪造）"""
    try:
        from flask import request
        return request.remote_addr or ''
    except Exception:  # noqa: BLE001  非请求上下文（脚本/任务）时留空
        return ''


def xl_safe(value):
    """把待写入单元格的值转为安全值。

    - 非 str（int/float/date/None 等）原样返回，统计列仍可被求和/排序；
    - str 先去控制字符，若以 = + - @ / Tab / CR / LF 开头则前置半角单引号。
    """
    if not isinstance(value, str) or not value:
        return value
    value = _CTRL_RE.sub('', value)
    if value and value[0] in _FORMULA_RISK_CHARS:
        return "'" + value
    return value


def xl_row(values):
    """整行转义，返回新 list（配合 ws.append(...) 使用）"""
    return [xl_safe(v) for v in values]


def xl_write_row(ws, row_idx, values, start_col=1):
    """写一行并做公式注入转义，返回写入的 cell 列表（便于调用方继续设样式）"""
    cells = []
    for ci, v in enumerate(values, start_col):
        cells.append(ws.cell(row=row_idx, column=ci, value=xl_safe(v)))
    return cells

BASE_COLUMNS = [
    ('student_number', '学号'),
    ('name', '姓名'),
    ('gender', '性别'),
    ('grade', '年级'),
    ('class_name', '班级'),
    ('subject_selection', '选科'),
    ('ethnicity', '民族'),
]

SENSITIVE_COLUMNS = {
    'id_card_number': {
        'label': '身份证号',
        'perm': 'students.export_id_card',
        'width': 18,
    },
    'phone1': {
        'label': '联系方式1',
        'perm': 'students.export_phone',
        'width': 15,
    },
    'phone2': {
        'label': '联系方式2',
        'perm': 'students.export_phone',
        'width': 15,
    },
    'graduation_school': {
        'label': '毕业学校',
        'perm': 'students.export_graduation_school',
        'width': 25,
    },
    'graduation_school_code': {
        'label': '毕业学校代码',
        'perm': 'students.export_graduation_school',
        'width': 12,
    },
    'enrollment_status': {
        'label': '学籍情况',
        'perm': 'students.export_enrollment',
        'width': 12,
    },
    'enrollment_notes': {
        'label': '学籍备注',
        'perm': 'students.export_enrollment',
        'width': 20,
    },
}


def do_export_students(args):
    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

    from flask_login import current_user

    q = Student.query
    gds = get_graduated_grades()
    if gds:
        q = q.filter(~Student.grade.in_(gds))
    # H-2：统一数据范围口径（此前只处理班主任，任课教师/年级长/无范围账号可导出全校）
    q = apply_student_scope(q)
    # 班级必须与年级成对使用：只按班级名筛选会串到其他年级的同名班级
    if args.get('class_name') and not args.get('grade'):
        args = args.copy()
        args['class_name'] = ''
    for k in ['gender', 'grade', 'class_name', 'subject_selection', 'enrollment_status']:
        v = args.get(k)
        if v:
            q = q.filter_by(**{k: v})
    sch = args.get('graduation_school', '').strip()
    if sch:
        q = q.filter(Student.graduation_school.contains(sch))
    name = args.get('name', '').strip()
    if name:
        q = q.filter(Student.name.contains(name))
    student_number = args.get('student_number', '').strip()
    if student_number:
        q = q.filter(Student.student_number.contains(student_number))

    students = q.order_by(Student.grade, Student.class_name, Student.student_number).all()

    requested_columns = args.getlist('columns')

    selected_columns = []
    selected_widths = []

    base_columns_dict = dict(BASE_COLUMNS)

    selected_columns.append(('student_number', base_columns_dict['student_number']))
    selected_widths.append(10)

    for field in requested_columns:
        if field in base_columns_dict and field != 'student_number':
            selected_columns.append((field, base_columns_dict[field]))
            selected_widths.append(10)
        elif field in SENSITIVE_COLUMNS:
            config = SENSITIVE_COLUMNS[field]
            if current_user.has_perm(config['perm']):
                selected_columns.append((field, config['label']))
                selected_widths.append(config['width'])

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '学生列表'
    hf = Font(bold=True, color='FFFFFF')
    hfl = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
    tb = Border(left=Side(style='thin'), right=Side(style='thin'),
                top=Side(style='thin'), bottom=Side(style='thin'))

    for ci, (field, label) in enumerate(selected_columns, 1):
        c = ws.cell(row=1, column=ci, value=label)
        c.font = hf
        c.fill = hfl
        c.alignment = Alignment(horizontal='center')
        c.border = tb

    for ri, s in enumerate(students, 2):
        row_data = []
        for field, _ in selected_columns:
            if field == 'id_card_number':
                value = s.id_card_number or ''
            else:
                value = getattr(s, field, '') or ''
            row_data.append(value)
        for ci, v in enumerate(row_data, 1):
            c = ws.cell(row=ri, column=ci, value=xl_safe(v))   # M-4：公式注入防护
            c.border = tb

    for i, w in enumerate(selected_widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    download_name = f'学生列表_{timestamp}.xlsx'

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)

    try:
        import json
        log_detail = {
            'columns': [label for _, label in selected_columns],
            'record_count': len(students),
            'file_name': download_name,
            'filters': {
                'gender': args.get('gender'),
                'grade': args.get('grade'),
                'class_name': args.get('class_name'),
                'name': args.get('name'),
                'student_number': args.get('student_number'),
            }
        }
        log = OperationLog(
            user_id=current_user.id,
            action='导出',
            target_type='学生',
            module='system',
            detail=json.dumps(log_detail, ensure_ascii=False),
            ip_address=_client_ip()
        )
        db.session.add(log)
        db.session.commit()
    except Exception:
        db.session.rollback()

    return send_file(out, as_attachment=True, download_name=download_name,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


ACCOMMODATION_BASE_COLUMNS = [
    ('student_number', '学号'),
    ('name', '姓名'),
    ('gender', '性别'),
    ('grade', '年级'),
    ('class_name', '班级'),
    ('subject_selection', '选科'),
    ('boarding_type', '住校/走读'),
    ('day_student_type', '出门权限'),
    ('room', '宿舍'),
    ('bed', '床位'),
]

ACCOMMODATION_SENSITIVE_COLUMNS = {
    'phone1': {
        'label': '联系方式1',
        'perm': 'students.export_phone',
        'width': 15,
    },
    'phone2': {
        'label': '联系方式2',
        'perm': 'students.export_phone',
        'width': 15,
    },
}


def do_export_student_accommodation(args):
    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

    from flask_login import current_user

    q = Student.query
    gds = get_graduated_grades()
    if gds:
        q = q.filter(~Student.grade.in_(gds))
    # H-2：住宿导出同样收敛到可见范围
    q = apply_student_scope(q)

    # 班级必须与年级成对使用：只按班级名筛选会串到其他年级的同名班级
    if args.get('class_name') and not args.get('grade'):
        args = args.copy()
        args['class_name'] = ''
    for k in ['gender', 'grade', 'class_name', 'subject_selection']:
        v = args.get(k)
        if v:
            q = q.filter_by(**{k: v})

    bt = args.get('boarding_type')
    if bt:
        acc_ids = [sa.student_id for sa in StudentAccommodation.query.filter_by(boarding_type=bt).all()]
        if acc_ids:
            q = q.filter(Student.id.in_(acc_ids))
        else:
            q = q.filter(Student.id == -1)

    dst = args.get('day_student_type')
    if dst:
        acc_ids = [sa.student_id for sa in StudentAccommodation.query.filter_by(day_student_type=dst).all()]
        if acc_ids:
            q = q.filter(Student.id.in_(acc_ids))
        else:
            q = q.filter(Student.id == -1)

    name = args.get('name', '').strip()
    if name:
        q = q.filter(Student.name.contains(name))
    student_number = args.get('student_number', '').strip()
    if student_number:
        q = q.filter(Student.student_number.contains(student_number))
    room_number = args.get('room_number', '').strip()
    if room_number:
        bed_sub = db.session.query(BedAssignment.student_id).join(BedAssignment.room).filter(
            Room.room_number.contains(room_number)
        ).filter(BedAssignment.student_id.isnot(None)).all()
        bed_ids = [b[0] for b in bed_sub if b[0]]
        if bed_ids:
            q = q.filter(Student.id.in_(bed_ids))
        else:
            q = q.filter(Student.id == -1)

    students = q.order_by(Student.grade, Student.class_name, Student.student_number).all()

    requested_columns = args.getlist('columns')

    selected_columns = []
    selected_widths = []

    base_columns_dict = dict(ACCOMMODATION_BASE_COLUMNS)

    selected_columns.append(('student_number', base_columns_dict['student_number']))
    selected_widths.append(10)

    for field in requested_columns:
        if field in base_columns_dict and field != 'student_number':
            selected_columns.append((field, base_columns_dict[field]))
            selected_widths.append(10)
        elif field in ACCOMMODATION_SENSITIVE_COLUMNS:
            config = ACCOMMODATION_SENSITIVE_COLUMNS[field]
            if current_user.has_perm(config['perm']):
                selected_columns.append((field, config['label']))
                selected_widths.append(config['width'])

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '学生住宿'
    hf = Font(bold=True, color='FFFFFF')
    hfl = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
    tb = Border(left=Side(style='thin'), right=Side(style='thin'),
                top=Side(style='thin'), bottom=Side(style='thin'))

    for ci, (field, label) in enumerate(selected_columns, 1):
        c = ws.cell(row=1, column=ci, value=label)
        c.font = hf
        c.fill = hfl
        c.alignment = Alignment(horizontal='center')
        c.border = tb

    for ri, s in enumerate(students, 2):
        row_data = []
        room_info = '-'
        bed_info = '-'
        if s.bed_assignment and s.bed_assignment.room:
            room_info = f"{s.bed_assignment.room.building} {s.bed_assignment.room.room_number}"
            bed_info = f"{s.bed_assignment.bed_number}床"
        acc = s.accommodation

        for field, _ in selected_columns:
            if field == 'room':
                value = room_info
            elif field == 'bed':
                value = bed_info
            elif field == 'boarding_type':
                value = acc.boarding_type if acc else '-'
            elif field == 'day_student_type':
                value = acc.day_student_type if acc else '-'
            elif field == 'phone1':
                value = s.phone1 or ''
            elif field == 'phone2':
                value = s.phone2 or ''
            else:
                value = getattr(s, field, '') or ''
            row_data.append(value)

        for ci, v in enumerate(row_data, 1):
            c = ws.cell(row=ri, column=ci, value=xl_safe(v))   # M-4：公式注入防护
            c.border = tb

    for i, w in enumerate(selected_widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    download_name = f'学生住宿_{timestamp}.xlsx'

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)

    try:
        import json
        log_detail = {
            'columns': [label for _, label in selected_columns],
            'record_count': len(students),
            'file_name': download_name,
            'filters': {
                'gender': args.get('gender'),
                'grade': args.get('grade'),
                'class_name': args.get('class_name'),
                'name': args.get('name'),
                'student_number': args.get('student_number'),
                'boarding_type': args.get('boarding_type'),
                'day_student_type': args.get('day_student_type'),
                'room_number': args.get('room_number'),
            }
        }
        log = OperationLog(
            user_id=current_user.id,
            action='导出',
            target_type='学生住宿',
            module='dormitory',
            detail=json.dumps(log_detail, ensure_ascii=False),
            ip_address=_client_ip()
        )
        db.session.add(log)
        db.session.commit()
    except Exception:
        db.session.rollback()

    return send_file(out, as_attachment=True, download_name=download_name,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
