# StuLink v1.7.0 2026-08-02
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import re
from flask import Blueprint, render_template, request, flash, redirect, url_for, send_file
from markupsafe import Markup
from flask_login import login_required, current_user
from app.models import Student, Room, BedAssignment, UserClassLink, StudentAccommodation
from app.extensions import db
from sqlalchemy import func
from app.utils.helpers import (get_dict_values, get_active_grades, get_class_options,
                               is_standard_class, log_operation, get_graduated_grades)
from app.utils.decorators import perm_required
import io
import uuid
import time

bp = Blueprint('statistics', __name__, url_prefix='/statistics')

SCOPE_CLASS = 'class'    # 班主任：只看所管班级
SCOPE_GRADE = 'grade'    # 年级长：只看所管年级
SCOPE_SCHOOL = 'school'  # 全校组/admin：看全部

def _is_valid_class(class_name):
    """判断是否为标准班级名（统一走 helpers.is_standard_class，避免口径漂移）"""
    return is_standard_class(class_name)


def _get_scope():
    """获取当前用户的权限范围"""
    pg = current_user.permission_group
    if not pg:
        return SCOPE_SCHOOL, None
    return pg.scope_type, current_user.grade


def _get_user_class_links():
    """获取当前班主任的所有班级关联"""
    return UserClassLink.query.filter_by(user_id=current_user.id).all()


def _load_stats_base():
    """一次性载入统计基础数据（共 2 条 SQL），供三个 builder 内存聚合。

    v1.16.0 性能改造：旧版每班 ~11 条查询（循环内重复调 get_graduated_grades、
    qs.all() 取全部 id 后跨库大 IN 反模式），整页 400+ 条 SQL；
    现在整页只查 2 次，用 defaultdict 内存分桶，口径与旧版完全一致。

    返回 (students, acc_map)：
    - students: [(id, grade, class_name, gender), ...]，已排除毕业年级
    - acc_map: {student_id: boarding_type}（student_accommodation 全表，student_id 唯一）
    """
    graduated = get_graduated_grades()
    q = db.session.query(Student.id, Student.grade, Student.class_name, Student.gender)
    if graduated:
        q = q.filter(~Student.grade.in_(graduated))
    students = q.all()
    acc_map = {sid: bt for sid, bt in db.session.query(
        StudentAccommodation.student_id, StudentAccommodation.boarding_type).all()}
    return students, acc_map


def _build_per_class_stats(filter_grade=None, filter_classes=None, _base=None):
    """按年级+班级统计（口径同旧版：排除毕业年级与非标准班级）

    _base: 可选的 _load_stats_base() 结果，同一请求内多个 builder 共享，避免重复查询
    """
    students, acc_map = _base if _base is not None else _load_stats_base()
    allowed = {(g, c) for g, c in filter_classes} if filter_classes else None

    buckets = {}
    for sid, grade, class_name, gender in students:
        if filter_grade and grade != filter_grade:
            continue
        # 过滤非标准班级（未分班、不分班、转出等）
        if not _is_valid_class(class_name):
            continue
        if allowed is not None and (grade, class_name) not in allowed:
            continue
        b = buckets.get((grade, class_name))
        if b is None:
            b = buckets[(grade, class_name)] = {
                'total': 0, 'male': 0, 'female': 0,
                'boarding': 0, 'male_boarding': 0, 'female_boarding': 0,
                'day_student': 0, 'male_day': 0, 'female_day': 0,
            }
        b['total'] += 1
        bt = acc_map.get(sid)
        if gender == '男':
            b['male'] += 1
            if bt == '住校':
                b['male_boarding'] += 1
            elif bt == '走读':
                b['male_day'] += 1
        elif gender == '女':
            b['female'] += 1
            if bt == '住校':
                b['female_boarding'] += 1
            elif bt == '走读':
                b['female_day'] += 1
        if bt == '住校':
            b['boarding'] += 1
        elif bt == '走读':
            b['day_student'] += 1

    stats = []
    for (grade, class_name) in sorted(buckets):
        b = buckets[(grade, class_name)]
        stats.append({
            'grade': grade, 'class_name': class_name,
            'total': b['total'], 'male': b['male'], 'female': b['female'],
            'boarding': b['boarding'], 'male_boarding': b['male_boarding'],
            'female_boarding': b['female_boarding'],
            'day_student': b['day_student'], 'male_day': b['male_day'],
            'female_day': b['female_day'],
        })
    return stats


def _build_per_grade_stats(filter_grade=None, _base=None):
    """按年级汇总（在校生口径：不包含“不分班”学生，与旧版一致）"""
    students, acc_map = _base if _base is not None else _load_stats_base()

    per = {}
    for sid, grade, class_name, gender in students:
        if filter_grade and grade != filter_grade:
            continue
        # 在校生口径：不包含“不分班”学生（其余非标准班级照旧计入，与旧版一致）
        if (class_name or '') == '不分班':
            continue
        g = per.get(grade)
        if g is None:
            g = per[grade] = {
                'classes': set(), 'total': 0, 'male': 0, 'female': 0,
                'boarding': 0, 'male_boarding': 0, 'female_boarding': 0,
            }
        g['classes'].add(class_name)
        g['total'] += 1
        bt = acc_map.get(sid)
        if gender == '男':
            g['male'] += 1
            if bt == '住校':
                g['male_boarding'] += 1
        elif gender == '女':
            g['female'] += 1
            if bt == '住校':
                g['female_boarding'] += 1
        if bt == '住校':
            g['boarding'] += 1

    result = []
    for grade in sorted(per):
        g = per[grade]
        result.append({
            'grade': grade, 'class_count': len(g['classes']),
            'total': g['total'], 'male': g['male'], 'female': g['female'],
            'boarding': g['boarding'], 'male_boarding': g['male_boarding'],
            'female_boarding': g['female_boarding'],
        })
    return result


def _build_school_stats(_base=None):
    """全校汇总（在校生口径：排除毕业年级与“不分班”学生，与旧版一致）"""
    students, acc_map = _base if _base is not None else _load_stats_base()

    total = male = female = 0
    boarding = male_boarding = female_boarding = 0
    grade_set = set()
    class_set = set()
    for sid, grade, class_name, gender in students:
        if (class_name or '') == '不分班':
            continue
        total += 1
        grade_set.add(grade)
        class_set.add((grade, class_name))
        bt = acc_map.get(sid)
        if gender == '男':
            male += 1
            if bt == '住校':
                male_boarding += 1
        elif gender == '女':
            female += 1
            if bt == '住校':
                female_boarding += 1
        if bt == '住校':
            boarding += 1

    return {
        'grade_count': len(grade_set), 'class_count': len(class_set),
        'total': total, 'male': male, 'female': female,
        'boarding': boarding, 'male_boarding': male_boarding,
        'female_boarding': female_boarding,
    }


def _dorm_stats():
    total_rooms = Room.query.filter_by(is_active=True).count()
    occupied_beds = BedAssignment.query.filter(BedAssignment.student_id.isnot(None)).count()
    total_beds = BedAssignment.query.count()
    return {
        'total_rooms': total_rooms,
        'total_beds': total_beds,
        'occupied_beds': occupied_beds,
        'empty_beds': total_beds - occupied_beds,
        'occupancy_rate': round(occupied_beds / total_beds * 100, 1) if total_beds else 0,
    }


@bp.route('/')
@login_required
def index():
    scope_type, user_grade = _get_scope()
    tab = request.args.get('tab', scope_type)  # 默认选用户范围对应的tab
    if tab == 'grade':  # 旧链接兼容：年级统计已并入班级统计
        tab = 'class'
    if tab not in ('school', 'class', 'import', 'rooms'):
        tab = 'school'
    sel_grade = request.args.get('grade', user_grade or '')

    # 一次性载入基础数据（2 条 SQL），三个 builder 共享，避免整页 400+ 条查询
    base = _load_stats_base()

    # 班主任：只允许看 class tab
    if scope_type == SCOPE_CLASS:
        tab = 'class'
        links = _get_user_class_links()
        allowed = [(l.grade, l.class_name) for l in links]
        per_class_stats = _build_per_class_stats(filter_classes=allowed, _base=base)
        per_grade_stats = []
        school_stats = {}
        grade_options = list(set(l.grade for l in links))
    # 年级长：只看 class tab，限制年级
    elif scope_type == SCOPE_GRADE:
        if tab == 'school':
            tab = 'class'
        if not sel_grade:
            sel_grade = user_grade or ''
        per_class_stats = _build_per_class_stats(filter_grade=sel_grade, _base=base)
        per_grade_stats = _build_per_grade_stats(filter_grade=user_grade, _base=base)
        school_stats = _build_school_stats(_base=base) if tab == 'school' else {}
        grade_options = [user_grade] if user_grade else []
    else:
        # 全校组/admin：全部数据
        per_class_stats = _build_per_class_stats(filter_grade=sel_grade if tab == 'class' and sel_grade else None, _base=base)
        per_grade_stats = _build_per_grade_stats(_base=base)
        school_stats = _build_school_stats(_base=base)
        grade_options = sorted(get_active_grades(), reverse=True)

    # 宿舍分配明细（原 /rooms/report）：年级 → 性别 → 房间列表（一房一行）
    room_tree = {}
    room_total_beds = 0
    room_total_rooms = 0
    room_student_map = {}
    room_total_occupied = 0
    if tab == 'rooms':
        from collections import OrderedDict
        rq = Room.query.filter(
            Room.is_active == True,
            Room.class_name.isnot(None),
            Room.class_name != ''
        )
        if sel_grade:
            rq = rq.filter_by(grade=sel_grade)
        # 以宿舍为单位：房间按 宿舍楼→楼层→房间号 排列
        rooms = rq.order_by(
            Room.grade, Room.gender, Room.building, Room.floor, Room.room_number
        ).all()
        room_tree = OrderedDict()
        for room in rooms:
            g = room.grade or ''
            gender = room.gender or ''
            if g not in room_tree:
                room_tree[g] = OrderedDict()
            room_tree[g].setdefault(gender, []).append(room)

        room_total_rooms = len(rooms)
        room_total_beds = sum(r.capacity for r in rooms)

        # 每个房间的入住学生名单（按床位号排序）
        room_student_map = {}
        if rooms:
            room_ids = [r.id for r in rooms]
            bed_rows = BedAssignment.query.filter(
                BedAssignment.room_id.in_(room_ids),
                BedAssignment.student_id.isnot(None)
            ).order_by(BedAssignment.room_id, BedAssignment.bed_number).all()
            bed_student_ids = [b.student_id for b in bed_rows]
            _student_map = {}
            if bed_student_ids:
                for s in Student.query.filter(Student.id.in_(bed_student_ids)).all():
                    _student_map[s.id] = s
            for bed in bed_rows:
                stu = _student_map.get(bed.student_id)
                if not stu:
                    continue
                room_student_map.setdefault(bed.room_id, []).append({
                    'bed_number': bed.bed_number,
                    'student_number': stu.student_number or '',
                    'name': stu.name or '',
                    'gender': stu.gender or '',
                    'class_name': stu.class_name or '',
                })
            room_total_occupied = sum(len(v) for v in room_student_map.values())

    return render_template('dormitory/statistics/overview.html',
                           tab=tab,
                           sel_grade=sel_grade,
                           grade_options=grade_options,
                           per_class_stats=per_class_stats,
                           per_grade_stats=per_grade_stats,
                           school_stats=school_stats,
                           dorm_stats=_dorm_stats(),
                           scope_type=scope_type,
                           room_tree=room_tree,
                           room_student_map=room_student_map,
                           room_total_rooms=room_total_rooms,
                           room_total_beds=room_total_beds,
                           room_total_occupied=room_total_occupied)


# ---- 宿舍历史查询 ----

@bp.route('/history')
@login_required
@perm_required('statistics.view')
def dormitory_history():
    """宿舍历史查询"""
    import sqlite3 as _sql
    import os as _os
    from config import BASE_DIR
    history_db_path = _os.path.join(BASE_DIR, 'data', 'history.db')

    graduated_grades = []
    if _os.path.exists(history_db_path):
        try:
            conn = _sql.connect(history_db_path)
            grades = conn.execute(
                'SELECT DISTINCT graduated_grade FROM graduated_rooms ORDER BY graduated_grade'
            ).fetchall()
            graduated_grades = [g[0] for g in grades if g[0]]
            conn.close()
        except Exception:
            pass

    search_grade = request.args.get('grade', '')
    search_room = request.args.get('room_number', '').strip()
    search_building = request.args.get('building', '').strip()
    search_class = request.args.get('class_name', '').strip()

    rooms = []
    beds = []
    if search_grade and _os.path.exists(history_db_path):
        try:
            conn = _sql.connect(history_db_path)
            conn.row_factory = _sql.Row

            room_where = ['graduated_grade = ?']
            room_params = [search_grade]
            if search_room:
                room_where.append('room_number LIKE ?')
                room_params.append(f'%{search_room}%')
            if search_building:
                room_where.append('building LIKE ?')
                room_params.append(f'%{search_building}%')
            if search_class:
                room_where.append('class_name LIKE ?')
                room_params.append(f'%{search_class}%')

            room_sql = f'SELECT * FROM graduated_rooms WHERE {" AND ".join(room_where)} ORDER BY building, room_number LIMIT 200'
            room_rows = conn.execute(room_sql, room_params).fetchall()
            rooms = [dict(r) for r in room_rows]

            room_ids = [r['original_room_id'] for r in rooms if r['original_room_id']]
            if room_ids:
                ph = ','.join('?' * len(room_ids))
                bed_rows = conn.execute(
                    f'SELECT * FROM graduated_beds WHERE original_room_id IN ({ph}) ORDER BY original_room_id, bed_number',
                    room_ids
                ).fetchall()
                beds = [dict(b) for b in bed_rows]

            conn.close()
        except Exception as e:
            flash(f'查询失败：{str(e)}', 'danger')

    return render_template('dormitory/statistics/history.html',
                           graduated_grades=graduated_grades,
                           search_grade=search_grade,
                           search_room=search_room,
                           search_building=search_building,
                           search_class=search_class,
                           rooms=rooms,
                           beds=beds)


@bp.route('/alumni')
@login_required
@perm_required('statistics.view')
def alumni():
    """往届生宿舍查询"""
    import sqlite3 as _sql
    import os as _os
    from config import BASE_DIR
    history_db_path = _os.path.join(BASE_DIR, 'data', 'history.db')

    graduated_grades = []
    if _os.path.exists(history_db_path):
        try:
            conn = _sql.connect(history_db_path)
            grades = conn.execute(
                'SELECT DISTINCT graduated_grade FROM graduated_rooms ORDER BY graduated_grade'
            ).fetchall()
            graduated_grades = [g[0] for g in grades if g[0]]
            conn.close()
        except Exception:
            pass

    search_grade = request.args.get('grade', '')
    search_room = request.args.get('room_number', '').strip()
    search_building = request.args.get('building', '').strip()

    rooms = []
    beds = []
    if search_grade and _os.path.exists(history_db_path):
        try:
            conn = _sql.connect(history_db_path)
            conn.row_factory = _sql.Row

            room_where = ['graduated_grade = ?']
            room_params = [search_grade]
            if search_room:
                room_where.append('room_number LIKE ?')
                room_params.append(f'%{search_room}%')
            if search_building:
                room_where.append('building LIKE ?')
                room_params.append(f'%{search_building}%')

            room_sql = f'SELECT * FROM graduated_rooms WHERE {" AND ".join(room_where)} ORDER BY building, room_number LIMIT 200'
            room_rows = conn.execute(room_sql, room_params).fetchall()
            rooms = [dict(r) for r in room_rows]

            room_ids = [r['original_room_id'] for r in rooms if r['original_room_id']]
            if room_ids:
                ph = ','.join('?' * len(room_ids))
                bed_rows = conn.execute(
                    f'SELECT * FROM graduated_beds WHERE original_room_id IN ({ph}) ORDER BY original_room_id, bed_number',
                    room_ids
                ).fetchall()
                beds = [dict(b) for b in bed_rows]

            conn.close()
        except Exception as e:
            flash(f'查询失败：{str(e)}', 'danger')

    return render_template('dormitory/statistics/alumni.html',
                           graduated_grades=graduated_grades,
                           search_grade=search_grade,
                           search_room=search_room,
                           search_building=search_building,
                           rooms=rooms,
                           beds=beds)


# ---- 宿舍数据导入（宿管专用）----

_import_errors = {}


def _save_import_errors(errors):
    key = uuid.uuid4().hex
    _import_errors[key] = (errors, time.time() + 3600)
    return key


def _clean_expired_errors():
    now = time.time()
    expired = [k for k, v in _import_errors.items() if v[1] < now]
    for k in expired:
        del _import_errors[k]


@bp.route('/download-dormitory-template')
@perm_required('dormitory.import')
def download_dormitory_template():
    """下载宿舍数据导入模板"""
    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '宿舍数据导入'

    headers = ['学号', '姓名', '住校/走读', '出门权限', '课本', '班主任备注']

    header_fill = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
    header_font = Font(color='FFFFFF', bold=True, size=11)
    required_fill = PatternFill(start_color='ED7D31', end_color='ED7D31', fill_type='solid')
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin'))

    required_cols = {0, 1}

    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = header_font
        cell.fill = required_fill if (col_idx - 1) in required_cols else header_fill
        cell.alignment = Alignment(horizontal='center')
        cell.border = thin_border

    widths = [15, 10, 12, 12, 10, 25]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w

    ws.append(['20251001', '张三', '住校', '', '', ''])

    ws.merge_cells('A2:F2')
    instr_cell = ws.cell(row=2, column=1,
        value='说明：橙色列必填 | 住校/走读填住校/男走读/女走读/离校 | 出门权限填晚走读/午晚走读/艺术生 | 第3行起填数据，删除本行和示例')
    instr_cell.font = Font(color='FF0000', bold=True, size=10)
    instr_cell.alignment = Alignment(horizontal='left', vertical='center', wrap_text=True)
    ws.row_dimensions[2].height = 30

    for col in range(1, len(headers) + 1):
        ws.cell(row=3, column=col).border = thin_border
        ws.cell(row=3, column=col).alignment = Alignment(horizontal='center')

    for row in ws.iter_rows(min_row=2, max_row=3):
        for cell in row:
            cell.border = thin_border

    ws.freeze_panes = 'A3'
    ws.auto_filter.ref = f'A1:F{ws.max_row}'

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return send_file(output, as_attachment=True,
                     download_name='宿舍数据导入模板.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@bp.route('/export-bed-detail')
@login_required
@perm_required('statistics.view')
def export_bed_detail():
    """导出宿舍床铺分配明细（A4打印友好，含班级/学号/姓名/宿舍楼/宿舍号/床位号）"""
    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter

    sel_grade = request.args.get('grade', '')

    # 查询所有已分配床位的房间
    rq = Room.query.filter(
        Room.is_active == True,
        Room.class_name.isnot(None),
        Room.class_name != ''
    )
    if sel_grade:
        rq = rq.filter_by(grade=sel_grade)
    rooms = rq.order_by(
        Room.grade, Room.gender, Room.class_name,
        Room.building, Room.room_number
    ).all()

    if not rooms:
        flash('暂无已分配的宿舍数据', 'info')
        return redirect(url_for('statistics.index', tab='rooms'))

    room_ids = [r.id for r in rooms]
    room_map = {r.id: r for r in rooms}

    # 查询所有床位分配（dormitory.db）
    beds = BedAssignment.query.filter(
        BedAssignment.room_id.in_(room_ids),
        BedAssignment.student_id.isnot(None)
    ).order_by(BedAssignment.room_id, BedAssignment.bed_number).all()

    # 批量查询学生信息（system.db）
    student_ids = list(set(b.student_id for b in beds))
    student_map = {}
    if student_ids:
        students = Student.query.filter(Student.id.in_(student_ids)).all()
        student_map = {s.id: s for s in students}

    # 构建数据行：按 年级→班级→宿舍楼→房间号→床位号 排序
    rows = []
    for bed in beds:
        room = room_map.get(bed.room_id)
        if not room:
            continue
        student = student_map.get(bed.student_id)
        if not student:
            continue
        rows.append({
            'grade': student.grade or '',
            'class_name': student.class_name or '',
            'student_number': student.student_number or '',
            'name': student.name or '',
            'gender': student.gender or '',
            'building': room.building or '',
            'room_number': room.room_number or '',
            'bed_number': bed.bed_number or 0,
        })

    # 排序：年级→班级→宿舍楼→房间号→床位号
    rows.sort(key=lambda x: (x['grade'], x['class_name'], x['building'], int(x['room_number']) if x['room_number'].isdigit() else 0, x['bed_number']))

    # 生成 Excel
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '宿舍床铺分配明细'

    headers = ['年级', '班级', '学号', '姓名', '性别', '宿舍楼', '宿舍号', '床位号']
    header_fill = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
    header_font = Font(color='FFFFFF', bold=True, size=11)
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin'))

    # 写表头
    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center', vertical='center')
        cell.border = thin_border

    # 写数据
    for row_idx, row in enumerate(rows, 2):
        values = [row['grade'], row['class_name'], row['student_number'],
                  row['name'], row['gender'], row['building'], row['room_number'], row['bed_number']]
        for col_idx, val in enumerate(values, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.alignment = Alignment(horizontal='center', vertical='center')
            cell.border = thin_border
            cell.font = Font(size=10)

    # 列宽（A4纵向友好）
    widths = [10, 8, 14, 10, 6, 12, 10, 8]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    # 冻结表头
    ws.freeze_panes = 'A2'
    # 自动筛选
    ws.auto_filter.ref = f'A1:H{ws.max_row}'

    # A4 打印设置
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.orientation = ws.ORIENTATION_PORTRAIT
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr = openpyxl.worksheet.properties.PageSetupProperties(fitToPage=True)
    ws.print_title_rows = '1:1'
    ws.page_margins = openpyxl.worksheet.page.PageMargins(left=0.3, right=0.3, top=0.5, bottom=0.5)

    # 生成文件名
    timestamp = time.strftime('%Y%m%d_%H%M%S')
    grade_str = f'_{sel_grade}' if sel_grade else ''
    filename = f'宿舍床铺分配明细{grade_str}_{timestamp}.xlsx'

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    # 记录操作日志
    log_operation(current_user, '导出', '统计报表', None,
                  f'导出宿舍床铺分配明细：{len(rows)}条记录，文件{filename}',
                  module='statistics')

    return send_file(output, as_attachment=True,
                     download_name=filename,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@bp.route('/import-dormitory', methods=['POST'])
@perm_required('dormitory.import')
def import_dormitory():
    """批量导入宿舍数据（学号+姓名双重匹配）"""
    file = request.files.get('file')
    if not file or not file.filename.endswith(('.xlsx', '.xls')):
        flash('请上传 .xlsx 格式的Excel文件', 'danger')
        return redirect(url_for('statistics.index'))

    try:
        import openpyxl
        wb = openpyxl.load_workbook(file, data_only=True)
        ws = wb.active

        header_map = {}
        for col in range(1, ws.max_column + 1):
            val = ws.cell(row=1, column=col).value
            if val:
                header_map[str(val).strip()] = col

        field_mapping = {
            '学号': 'student_number', '姓名': 'name',
            '住校/走读': 'boarding_type', '出门权限': 'day_student_type',
            '课本': 'textbook', '班主任备注': 'teacher_notes',
        }

        col_map = {}
        for excel_name, model_field in field_mapping.items():
            if excel_name in header_map and model_field not in col_map:
                col_map[model_field] = header_map[excel_name]

        required_fields = {'name': '姓名', 'student_number': '学号'}
        missing = [v for k, v in required_fields.items() if k not in col_map]
        if missing:
            flash(f'Excel缺少必填列：{", ".join(missing)}', 'danger')
            return redirect(url_for('statistics.index'))

        all_students = {}
        for s in Student.query.all():
            all_students[s.student_number] = s

        updated_count = 0
        errors = []

        for row_idx in range(2, ws.max_row + 1):
            try:
                row_data = {}
                for field, col in col_map.items():
                    cell = ws.cell(row=row_idx, column=col)
                    v = cell.value
                    if v is not None:
                        v = str(v).strip()
                    row_data[field] = v

                student_number = row_data.get('student_number', '')
                name = row_data.get('name', '')

                if not student_number:
                    continue

                if student_number not in all_students:
                    errors.append(f'第{row_idx}行（{name}）：学号 {student_number} 在基础数据中不存在')
                    continue

                student = all_students[student_number]
                if student.name != name:
                    errors.append(f'第{row_idx}行（{name}）：学号 {student_number} 对应学生姓名为「{student.name}」，与Excel中「{name}」不匹配')
                    continue

                acc = StudentAccommodation.query.filter_by(student_id=student.id).first()
                if not acc:
                    acc = StudentAccommodation(student_id=student.id)
                    db.session.add(acc)
                
                acc.boarding_type = row_data.get('boarding_type') or acc.boarding_type
                acc.day_student_type = row_data.get('day_student_type') or acc.day_student_type
                acc.textbook = row_data.get('textbook') or acc.textbook
                acc.teacher_notes = row_data.get('teacher_notes') or acc.teacher_notes
                updated_count += 1

            except Exception as e:
                errors.append(f'第{row_idx}行处理异常：{str(e)}')
                continue

        if updated_count > 0:
            db.session.commit()
            log_operation(current_user, '导入', '宿舍数据', None, f'批量更新 {updated_count} 名学生宿舍信息')

        if errors:
            ek = _save_import_errors(errors)
            flash(f'成功更新 {updated_count} 名学生宿舍信息，{len(errors)} 条失败', 'warning')
            flash(Markup(f'<a href="/statistics/download-errors/{ek}" class="btn btn-sm btn-outline-danger">下载错误日志 ({len(errors)}条)</a>'), 'warning')
        elif updated_count > 0:
            flash(f'成功更新 {updated_count} 名学生宿舍信息', 'success')
        else:
            flash('Excel中没有有效的宿舍数据', 'warning')

    except Exception as e:
        db.session.rollback()
        flash(f'导入失败：{str(e)}', 'danger')

    return redirect(url_for('statistics.index'))


@bp.route('/download-errors/<key>')
@login_required
def download_import_errors(key):
    """下载导入错误日志"""
    _clean_expired_errors()
    data = _import_errors.pop(key, None)
    if not data:
        flash('错误日志已过期或不存在', 'warning')
        return redirect(url_for('statistics.index'))
    errors, _ = data
    content = '\r\n'.join(errors)
    from io import BytesIO
    buf = BytesIO()
    buf.write(content.encode('utf-8-sig'))
    buf.seek(0)
    return send_file(buf, as_attachment=True,
                     download_name=f'导入错误日志_{key[:8]}.txt',
                     mimetype='text/plain; charset=utf-8')


