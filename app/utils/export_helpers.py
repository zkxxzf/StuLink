"""学生列表导出工具"""
import io
from flask import send_file
from app.models import Student, Room, BedAssignment, StudentAccommodation
from app.utils.helpers import get_graduated_grades
from app.extensions import db


def do_export_students(args):
    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

    from flask_login import current_user

    q = Student.query
    gds = get_graduated_grades()
    if gds:
        q = q.filter(~Student.grade.in_(gds))
    if current_user.role == 'homeroom_teacher':
        q = q.filter_by(grade=current_user.grade, class_name=current_user.class_name)
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

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '学生列表'
    hd = ['学号', '姓名', '性别', '年级', '班级', '选科', '毕业学校', '毕业学校代码',
          '联系方式1', '联系方式2', '民族', '学籍情况']
    hf = Font(bold=True, color='FFFFFF')
    hfl = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
    tb = Border(left=Side(style='thin'), right=Side(style='thin'),
                top=Side(style='thin'), bottom=Side(style='thin'))

    for ci, h in enumerate(hd, 1):
        c = ws.cell(row=1, column=ci, value=h)
        c.font = hf
        c.fill = hfl
        c.alignment = Alignment(horizontal='center')
        c.border = tb

    for ri, s in enumerate(students, 2):
        d = [s.student_number or '', s.name, s.gender or '', s.grade or '', s.class_name or '',
             s.subject_selection or '', s.graduation_school or '', s.graduation_school_code or '',
             s.phone1 or '', s.phone2 or '', s.ethnicity or '', s.enrollment_status or '']
        for ci, v in enumerate(d, 1):
            c = ws.cell(row=ri, column=ci, value=v)
            c.border = tb

    for i, w in enumerate([15, 10, 6, 10, 8, 10, 25, 12, 15, 15, 8, 12], 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return send_file(out, as_attachment=True, download_name='学生列表.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


def do_export_student_accommodation(args):
    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

    from flask_login import current_user

    q = Student.query
    gds = get_graduated_grades()
    if gds:
        q = q.filter(~Student.grade.in_(gds))

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

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '学生住宿'
    hd = ['学号', '姓名', '性别', '年级', '班级', '选科', '住校/走读', '出门权限', '宿舍', '床位', '联系方式1', '联系方式2']
    hf = Font(bold=True, color='FFFFFF')
    hfl = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
    tb = Border(left=Side(style='thin'), right=Side(style='thin'),
                top=Side(style='thin'), bottom=Side(style='thin'))

    for ci, h in enumerate(hd, 1):
        c = ws.cell(row=1, column=ci, value=h)
        c.font = hf
        c.fill = hfl
        c.alignment = Alignment(horizontal='center')
        c.border = tb

    for ri, s in enumerate(students, 2):
        room_info = '-'
        bed_info = '-'
        if s.bed_assignment and s.bed_assignment.room:
            room_info = f"{s.bed_assignment.room.building} {s.bed_assignment.room.room_number}"
            bed_info = f"{s.bed_assignment.bed_number}床"
        acc = s.accommodation
        boarding_type = acc.boarding_type if acc else '-'
        day_student_type = acc.day_student_type if acc else '-'
        d = [s.student_number or '', s.name, s.gender or '', s.grade or '', s.class_name or '',
             s.subject_selection or '', boarding_type, day_student_type,
             room_info, bed_info, s.phone1 or '', s.phone2 or '']
        for ci, v in enumerate(d, 1):
            c = ws.cell(row=ri, column=ci, value=v)
            c.border = tb

    for i, w in enumerate([15, 10, 6, 10, 8, 10, 10, 10, 15, 8, 15, 15], 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return send_file(out, as_attachment=True, download_name='学生住宿.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
