# StuLink v1.4.6 2026-06-30
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0
from flask import Blueprint, render_template, redirect, url_for, flash, request, send_file
from flask_login import login_required, current_user
from app.extensions import db
from app.models import Student, BedAssignment
from app.utils.crypto import encrypt as _encrypt_id
from app.forms.student_forms import StudentForm
from app.utils.decorators import role_required
from app.utils.helpers import get_dict_values, log_operation
import os
import io
import re

bp = Blueprint('students', __name__, url_prefix='/students')


@bp.route('/')
@login_required
def list_students():
    query = Student.query
    # 班主任只能看自己�?
    if current_user.role == 'homeroom_teacher':
        query = query.filter_by(grade=current_user.grade, class_name=current_user.class_name)
    
    # 分页参数
    page = request.args.get('page', 1, type=int)
    per_page = 50  # 每页显示50�?
    
    # 使用分页查询
    pagination = query.order_by(Student.grade, Student.class_name, Student.student_number).paginate(
        page=page, per_page=per_page, error_out=False
    )
    students = pagination.items
    
    grades = get_dict_values('grade')
    classes = get_dict_values('class')
    return render_template('system/students/list.html', 
                         students=students, 
                         grades=grades, 
                         classes=classes,
                         pagination=pagination)


@bp.route('/create', methods=['GET', 'POST'])
@role_required('admin', 'homeroom_teacher')
def create():
    form = StudentForm()
    if form.validate_on_submit():
        # 1. 验证身份证号格式
        id_card = form.id_card_number.data
        if id_card and not _validate_id_card(id_card):
            flash('身份证号格式不正�?, 'danger')
            return render_template('system/students/form.html', form=form, title='新增学生')
        
        # 2. 检查身份证号是否重�?
        if id_card and Student.query.filter_by(_id_card_encrypted=_encrypt_id(id_card)).first():
            flash('该身份证号已存在', 'danger')
            return render_template('system/students/form.html', form=form, title='新增学生')
        
        # 3. 检查学号唯一性（学号不为空时�?
        if form.student_number.data:
            if Student.query.filter_by(student_number=form.student_number.data).first():
                flash('该学号已存在', 'danger')
                return render_template('system/students/form.html', form=form, title='新增学生')
        
        student = Student()
        _populate_student(student, form)
        db.session.add(student)
        db.session.commit()
        log_operation(current_user, '创建', '学生', student.id, f'{student.name} {student.grade}{student.class_name}')
        flash('学生信息已添�?, 'success')
        return redirect(url_for('students.list_students'))
    # 班主任默认填入自己管理的年级班级
    if current_user.role == 'homeroom_teacher':
        form.grade.data = form.grade.data or current_user.grade
        form.class_name.data = form.class_name.data or current_user.class_name
    return render_template('system/students/form.html', form=form, title='新增学生')


@bp.route('/<int:id>')
@login_required
def detail(id):
    student = Student.query.get_or_404(id)
    return render_template('system/students/detail.html', student=student)


@bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@role_required('admin', 'homeroom_teacher')
def edit(id):
    student = Student.query.get_or_404(id)
    # 班主任只能编辑自己班学生
    if current_user.role == 'homeroom_teacher':
        if student.grade != current_user.grade or student.class_name != current_user.class_name:
            flash('无权编辑该学�?, 'danger')
            return redirect(url_for('students.list_students'))
    form = StudentForm(obj=student)
    if form.validate_on_submit():
        # 1. 验证身份证号格式
        id_card = form.id_card_number.data
        if id_card and not _validate_id_card(id_card):
            flash('身份证号格式不正�?, 'danger')
            return render_template('system/students/form.html', form=form, title='编辑学生')
        
        # 2. 检查身份证号是否重复（排除自身�?
        if id_card:
            existing_id = Student.query.filter(
                Student._id_card_encrypted == _encrypt_id(id_card),
                Student.id != student.id
            ).first()
            if existing_id:
                flash('该身份证号已存在', 'danger')
                return render_template('system/students/form.html', form=form, title='编辑学生')
        
        # 3. 检查学号唯一性（排除自身，学号不为空时）
        if form.student_number.data:
            existing_sn = Student.query.filter(
                Student.student_number == form.student_number.data,
                Student.id != student.id
            ).first()
            if existing_sn:
                flash('该学号已存在', 'danger')
                return render_template('system/students/form.html', form=form, title='编辑学生')
        
        _populate_student(student, form)
        db.session.commit()
        log_operation(current_user, '更新', '学生', student.id, f'{student.name} {student.grade}{student.class_name}')
        flash('学生信息已更�?, 'success')
        return redirect(url_for('students.detail', id=student.id))
    return render_template('system/students/form.html', form=form, title='编辑学生')


@bp.route('/<int:id>/delete', methods=['POST'])
@role_required('admin')
def delete(id):
    student = Student.query.get_or_404(id)
    # 先删除床位分�?
    if student.bed_assignment:
        student.bed_assignment.student_id = None
    db.session.delete(student)
    db.session.commit()
    log_operation(current_user, '删除', '学生', id, f'{student.name} {student.grade}{student.class_name}')
    flash(f'学生 {student.name} 已删�?, 'success')
    return redirect(url_for('students.list_students'))


@bp.route('/search')
@login_required
def search():
    query = Student.query
    # 获取搜索条件
    name = request.args.get('name', '').strip()
    student_number = request.args.get('student_number', '').strip()
    grade = request.args.get('grade', '')
    class_name = request.args.get('class_name', '')
    gender = request.args.get('gender', '')
    boarding_type = request.args.get('boarding_type', '')
    day_student_type = request.args.get('day_student_type', '')
    subject_selection = request.args.get('subject_selection', '')

    if name:
        query = query.filter(Student.name.contains(name))
    if student_number:
        query = query.filter(Student.student_number.contains(student_number))
    if grade:
        query = query.filter_by(grade=grade)
    if class_name:
        query = query.filter_by(class_name=class_name)
    if gender:
        query = query.filter_by(gender=gender)
    if boarding_type:
        query = query.filter_by(boarding_type=boarding_type)
    if day_student_type:
        query = query.filter_by(day_student_type=day_student_type)
    if subject_selection:
        query = query.filter_by(subject_selection=subject_selection)

    # 分页参数
    page = request.args.get('page', 1, type=int)
    per_page = 50  # 每页显示50�?
    
    # 使用分页查询
    pagination = query.order_by(Student.grade, Student.class_name, Student.student_number).paginate(
        page=page, per_page=per_page, error_out=False
    )
    students = pagination.items
    
    grades = get_dict_values('grade')
    classes = get_dict_values('class')
    boarding_types = get_dict_values('boarding_type')
    day_student_types = get_dict_values('day_student_type')
    subjects = get_dict_values('subject')
    return render_template('system/students/search.html', 
                         students=students, 
                         grades=grades, 
                         classes=classes,
                         boarding_types=boarding_types, 
                         day_student_types=day_student_types, 
                         subjects=subjects,
                         pagination=pagination)


def _populate_student(student, form):
    """从表单填充学生对�?""
    for field_name in ['name', 'gender', 'student_number', 'id_card_number', 'ethnicity',
                       'phone1', 'phone2', 'grade', 'class_name', 'original_class',
                       'subject_selection', 'boarding_type', 'day_student_type',
                       'enrollment_status', 'textbook', 'teacher_notes', 'enrollment_notes',
                       'graduation_school_code', 'graduation_school']:
        setattr(student, field_name, getattr(form, field_name).data or None)


def _validate_id_card(id_card):
    """验证身份证号是否有效�?8位）"""
    if not id_card:
        return False
    id_card = str(id_card).strip()
    if len(id_card) != 18:
        return False
    if not id_card[:17].isdigit():
        return False
    if not (id_card[17].isdigit() or id_card[17].upper() == 'X'):
        return False
    weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    check_codes = '10X98765432'
    total = sum(int(id_card[i]) * weights[i] for i in range(17))
    if check_codes[total % 11] != id_card[17].upper():
        return False
    return True


@bp.route('/download-template')
@role_required('admin', 'homeroom_teacher')
def download_template():
    """下载导入模板"""
    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '学生导入模板'

    # 新顺序：与编辑页面一致，添加毕业学校信息
    headers = ['姓名', '性别', '民族', '身份证号', '学号', '学籍情况', '学籍备注',
               '年级', '班级', '原班�?, '选科', '住校/走读', '出门权限',
               '联系方式 1', '联系方式 2', '课本', '班主任备�?,
               '毕业学校代码', '毕业学校']

    header_fill = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
    header_font = Font(color='FFFFFF', bold=True, size=11)
    required_fill = PatternFill(start_color='ED7D31', end_color='ED7D31', fill_type='solid')
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin'))

    required_cols = {0, 1, 3, 4}  # 姓名、性别、身份证号、学�?

    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = header_font
        cell.fill = required_fill if (col_idx - 1) in required_cols else header_fill
        cell.alignment = Alignment(horizontal='center')
        cell.border = thin_border

    widths = [10, 8, 8, 22, 15, 10, 20, 10, 8, 10, 10, 10, 10, 15, 15, 10, 25, 12, 25]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w

    ws.append(['张三', '�?, '汉族', '110101200801011234', '20251001', '借读', '',
               '2025 �?, '01 �?, '', '史政�?, '住校', '', '13800138000', '', '', '',
               '0440', '郑州市管城回族区第二中学'])

    # �?2 行：填写说明（合并单元格�?
    ws.merge_cells('A2:S2')
    instr_cell = ws.cell(row=2, column=1, value='说明：橙色列必填 | 性别�?�?| 身份�?18 �?| 住校填住�?男走�?女走�?离校 | 出门权限填晚走读/午晚走读/艺术�?| 毕业学校代码与名称关�?| �?3 行起填数据，删除本行和示�?)
    instr_cell.font = Font(color='FF0000', bold=True, size=10)
    instr_cell.alignment = Alignment(horizontal='left')
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True,
                     download_name='学生导入模板.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@bp.route('/import', methods=['POST'])
@role_required('admin', 'homeroom_teacher')
def import_students():
    """批量导入学生"""
    file = request.files.get('file')
    if not file or not file.filename.endswith(('.xlsx', '.xls')):
        flash('请上�?.xlsx 格式的Excel文件', 'danger')
        return redirect(url_for('students.list_students'))

    try:
        import openpyxl.worksheet.datavalidation as dv
        _orig_init = dv.DataValidation.__init__
        def _patched_init(self, *a, **kw):
            kw.pop('id', None)
            _orig_init(self, *a, **kw)
        dv.DataValidation.__init__ = _patched_init

        import openpyxl
        wb = openpyxl.load_workbook(file, data_only=True)
        ws = wb.active

        # 读取表头映射
        header_map = {}
        for col in range(1, ws.max_column + 1):
            val = ws.cell(row=1, column=col).value
            if val:
                header_map[str(val).strip()] = col

        # Excel列名 �?Student模型字段
        field_mapping = {
            '姓名': 'name', '性别': 'gender', '学号': 'student_number',
            '身份证号': 'id_card_number', '年级': 'grade',
            '班级': 'class_name', '新班�?: 'class_name',
            '民族': 'ethnicity', '联系方式1': 'phone1', '联系方式2': 'phone2',
            '原班�?: 'original_class', '选科': 'subject_selection',
            '走读/住校': 'boarding_type', '走读类型': 'day_student_type',
            '学籍情况': 'enrollment_status', '课本': 'textbook',
            '班主任备�?: 'teacher_notes', '学籍备注': 'enrollment_notes',
            '毕业学校代码': 'graduation_school_code', '毕业学校': 'graduation_school',
        }

        col_map = {}
        for excel_name, model_field in field_mapping.items():
            if excel_name in header_map and model_field not in col_map:
                col_map[model_field] = header_map[excel_name]

        # 检查必填列
        required_fields = {'name': '姓名', 'gender': '性别',
                           'id_card_number': '身份证号', 'student_number': '学号'}
        missing = [v for k, v in required_fields.items() if k not in col_map]
        if missing:
            flash(f'Excel缺少必填列：{", ".join(missing)}', 'danger')
            return redirect(url_for('students.list_students'))

        # 已有数据
        existing_snums = set(
            r[0] for r in db.session.query(Student.student_number).all() if r[0])
        existing_ids = set(
            r[0] for r in db.session.query(Student._id_card_encrypted).all() if r[0])

        errors = []
        students_to_add = []
        seen_snums = set()
        seen_ids = set()

        for row_idx in range(2, ws.max_row + 1):
            row_data = {}
            for model_field, col_idx in col_map.items():
                val = ws.cell(row=row_idx, column=col_idx).value
                row_data[model_field] = str(val).strip() if val is not None else ''

            name = row_data.get('name', '')
            if not name:
                continue

            row_errors = []
            row_label = f'第{row_idx}�?
            gender = row_data.get('gender', '')
            snum = row_data.get('student_number', '')
            id_card = row_data.get('id_card_number', '')

            if not gender:
                row_errors.append('性别为空')
            elif gender not in ('�?, '�?):
                row_errors.append(f'性别"{gender}"无效')
            if not id_card:
                row_errors.append('身份证号为空')
            if not snum:
                row_errors.append('学号为空')

            if snum:
                if snum in existing_snums:
                    row_errors.append(f'学号"{snum}"已存�?)
                elif snum in seen_snums:
                    row_errors.append(f'学号"{snum}"文件内重�?)
                else:
                    seen_snums.add(snum)

            if id_card:
                if not _validate_id_card(id_card):
                    row_errors.append(f'身份证号无效')
                elif _encrypt_id(id_card) in existing_ids:
                    row_errors.append(f'身份证号已存�?)
                elif _encrypt_id(id_card) in seen_ids:
                    row_errors.append(f'身份证号文件内重�?)
                else:
                    seen_ids.add(_encrypt_id(id_card))

            if row_errors:
                errors.append(f'{row_label}（{name}）：{"; ".join(row_errors)}')
                continue

            student = Student(
                name=name, gender=gender, student_number=snum or None,
                id_card_number=id_card,
                grade=row_data.get('grade') or '',
                class_name=row_data.get('class_name') or '',
                ethnicity=row_data.get('ethnicity') or '汉族',
                phone1=row_data.get('phone1') or None,
                phone2=row_data.get('phone2') or None,
                original_class=row_data.get('original_class') or None,
                subject_selection=row_data.get('subject_selection') or None,
                boarding_type=row_data.get('boarding_type') or '住校',
                day_student_type=row_data.get('day_student_type') or None,
                enrollment_status=row_data.get('enrollment_status') or None,
                textbook=row_data.get('textbook') or None,
                teacher_notes=row_data.get('teacher_notes') or None,
                enrollment_notes=row_data.get('enrollment_notes') or None,
                graduation_school_code=row_data.get('graduation_school_code') or None,
                graduation_school=row_data.get('graduation_school') or None,
            )
            students_to_add.append(student)

        if errors and students_to_add:
            # 部分导入：正确的行导入，错误的行报告
            db.session.add_all(students_to_add)
            db.session.commit()
            log_operation(current_user, '导入', '学生', None, f'部分导入 {len(students_to_add)} 名，{len(errors)} 条失�?)
            error_summary = f'成功导入 {len(students_to_add)} 名学生，但有 {len(errors)} 条数据未导入：\n' + '\n'.join(errors[:20])
            if len(errors) > 20:
                error_summary += f'\n...还有 {len(errors) - 20} 条错�?
            flash(error_summary, 'warning')
        elif errors:
            # 全部错误，无有效数据
            error_summary = f'发现 {len(errors)} 条错误，未导入任何数据：\n' + '\n'.join(errors[:20])
            if len(errors) > 20:
                error_summary += f'\n...还有 {len(errors) - 20} 条错�?
            flash(error_summary, 'danger')
        elif not students_to_add:
            flash('Excel中没有有效的学生数据', 'warning')
        else:
            db.session.add_all(students_to_add)
            db.session.commit()
            log_operation(current_user, '导入', '学生', None, f'批量导入 {len(students_to_add)} 名学�?)
            flash(f'成功导入 {len(students_to_add)} 名学�?, 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'导入失败：{str(e)}', 'danger')

    return redirect(url_for('students.list_students'))


@bp.route('/batch-delete', methods=['POST'])
@role_required('admin')
def batch_delete():
    """批量删除学生"""
    ids = request.form.getlist('student_ids')
    if not ids:
        flash('未选择任何学生', 'warning')
        return redirect(url_for('students.list_students'))

    try:
        id_list = [int(i) for i in ids]
    except ValueError:
        flash('参数错误', 'danger')
        return redirect(url_for('students.list_students'))

    students = Student.query.filter(Student.id.in_(id_list)).all()
    if not students:
        flash('未找到选中的学�?, 'warning')
        return redirect(url_for('students.list_students'))

    count = len(students)
    for s in students:
        if s.bed_assignment:
            s.bed_assignment.student_id = None
        db.session.delete(s)
    db.session.commit()
    log_operation(current_user, '删除', '学生', None, f'批量删除 {count} 名学�?)
    flash(f'已删�?{count} 名学�?, 'success')
    return redirect(url_for('students.list_students'))


@bp.route('/<int:id>/transfer', methods=['POST'])
@role_required('admin', 'homeroom_teacher')
def transfer(id):
    """单个学生调班调年�?""
    student = Student.query.get_or_404(id)
    new_grade = request.form.get('new_grade', '').strip()
    new_class = request.form.get('new_class', '').strip()

    if not new_grade or not new_class:
        flash('年级和班级不能为�?, 'danger')
        return redirect(url_for('students.list_students'))

    old_info = f'{student.grade} {student.class_name}'
    student.grade = new_grade
    student.class_name = new_class
    db.session.commit()
    log_operation(current_user, '更新', '学生', student.id, f'{student.name} {old_info}→{new_grade}{new_class}')
    flash(f'学生 {student.name} 已从 {old_info} 调至 {new_grade} {new_class}', 'success')
    return redirect(url_for('students.list_students'))


@bp.route('/download-transfer-template')
@role_required('admin', 'homeroom_teacher')
def download_transfer_template():
    """下载调班模板（学号版或身份证号版�?""
    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

    tpl_type = request.args.get('type', 'student_number')

    wb = openpyxl.Workbook()
    ws = wb.active

    if tpl_type == 'id_card':
        ws.title = '身份证号调班模板'
        headers = ['身份证号', '新年�?, '新班�?]
        filename = '调班模板_身份证号�?xlsx'
        example_row = ['110101200801011234', '2025�?, '02�?]
        widths = [22, 12, 10]
    else:
        ws.title = '学号调班模板'
        headers = ['学号', '新年�?, '新班�?]
        filename = '调班模板_学号�?xlsx'
        example_row = ['20251001', '2025�?, '02�?]
        widths = [15, 12, 10]

    header_fill = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
    header_font = Font(color='FFFFFF', bold=True, size=11)
    required_fill = PatternFill(start_color='ED7D31', end_color='ED7D31', fill_type='solid')
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin'))

    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = header_font
        cell.fill = required_fill
        cell.alignment = Alignment(horizontal='center')
        cell.border = thin_border

    for i, w in enumerate(widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w

    ws.append(example_row)

    ws.cell(row=4, column=1, value='说明�?).font = Font(bold=True, color='FF0000')
    ws.cell(row=5, column=1, value='1. 所有列均为必填�?)
    if tpl_type == 'id_card':
        ws.cell(row=6, column=1, value='2. 身份证号必须与系统中已有学生匹配')
    else:
        ws.cell(row=6, column=1, value='2. 学号必须与系统中已有学生匹配')
    ws.cell(row=7, column=1, value='3. 新年级格式如�?025�?)
    ws.cell(row=8, column=1, value='4. 新班级格式如�?1�?)
    ws.cell(row=9, column=1, value='5. 导入时请删除本说明和示例数据')

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True,
                     download_name=filename,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@bp.route('/batch-transfer', methods=['POST'])
@role_required('admin', 'homeroom_teacher')
def batch_transfer():
    """批量调班"""
    file = request.files.get('file')
    tpl_type = request.form.get('tpl_type', 'student_number')

    if not file or not file.filename.endswith(('.xlsx', '.xls')):
        flash('请上�?.xlsx 格式的Excel文件', 'danger')
        return redirect(url_for('students.list_students'))

    try:
        import openpyxl.worksheet.datavalidation as dv
        _orig_init = dv.DataValidation.__init__
        def _patched_init(self, *a, **kw):
            kw.pop('id', None)
            _orig_init(self, *a, **kw)
        dv.DataValidation.__init__ = _patched_init

        import openpyxl
        wb = openpyxl.load_workbook(file, data_only=True)
        ws = wb.active

        header_map = {}
        for col in range(1, ws.max_column + 1):
            val = ws.cell(row=1, column=col).value
            if val:
                header_map[str(val).strip()] = col

        # 确定匹配�?
        if tpl_type == 'id_card':
            match_col = header_map.get('身份证号')
            match_label = '身份证号'
        else:
            match_col = header_map.get('学号')
            match_label = '学号'

        grade_col = header_map.get('新年�?)
        class_col = header_map.get('新班�?)

        if not match_col:
            flash(f'Excel缺少"{match_label}"�?, 'danger')
            return redirect(url_for('students.list_students'))
        if not grade_col or not class_col:
            flash('Excel缺少"新年�?�?新班�?�?, 'danger')
            return redirect(url_for('students.list_students'))

        errors = []
        updated_count = 0

        for row_idx in range(2, ws.max_row + 1):
            match_val = ws.cell(row=row_idx, column=match_col).value
            new_grade = ws.cell(row=row_idx, column=grade_col).value
            new_class = ws.cell(row=row_idx, column=class_col).value

            if match_val is None:
                continue

            match_val = str(match_val).strip()
            if not match_val:
                continue

            new_grade = str(new_grade).strip() if new_grade else ''
            new_class = str(new_class).strip() if new_class else ''

            row_label = f'第{row_idx}�?

            if not new_grade or not new_class:
                errors.append(f'{row_label}（{match_val}）：新年级或新班级为�?)
                continue

            if tpl_type == 'id_card':
                student = Student.query.filter_by(_id_card_encrypted=_encrypt_id(match_val)).first()
            else:
                student = Student.query.filter_by(student_number=match_val).first()

            if not student:
                errors.append(f'{row_label}（{match_val}）：未找到该学生')
                continue

            student.grade = new_grade
            student.class_name = new_class
            updated_count += 1

        if errors:
            error_summary = f'调班完成，但�?{len(errors)} 条未处理：\n' + '\n'.join(errors[:20])
            if len(errors) > 20:
                error_summary += f'\n...还有 {len(errors) - 20} �?
            if updated_count > 0:
                db.session.commit()
                flash(f'成功调班 {updated_count} 名学生。{error_summary}', 'warning')
            else:
                db.session.rollback()
                flash(error_summary, 'danger')
        elif updated_count > 0:
            db.session.commit()
            flash(f'成功调班 {updated_count} 名学�?, 'success')
        else:
            flash('Excel中没有有效的调班数据', 'warning')

    except Exception as e:
        db.session.rollback()
        flash(f'调班导入失败：{str(e)}', 'danger')

    return redirect(url_for('students.list_students'))
