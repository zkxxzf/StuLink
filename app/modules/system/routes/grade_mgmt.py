"""年级管理：毕业/存档/历史查询"""
import os
import shutil
import sqlite3
from datetime import datetime
from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for
from flask_login import login_required, current_user
from app.extensions import db
from app.models import Room, BedAssignment, Student, GradeSetting, DictCategory, DictItem
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation, write_change_log, get_dict_values
from app.utils.crypto import decrypt


def _mask_id_card(id_card):
    """身份证号脱敏：显示前6位和后4位，中间用*替换"""
    if not id_card:
        return ''
    id_card = str(id_card).strip()
    if len(id_card) != 18:
        return id_card
    return id_card[:6] + '**********' + id_card[-4:]

bp = Blueprint('grade_mgmt', __name__, url_prefix='/grade-mgmt')


def _get_all_grades():
    """从字典表获取所有年级（与字典表联动）"""
    return get_dict_values('grade')


@bp.route('/')
@login_required
@perm_required('system.grade_mgmt')
def index():
    """年级管理页面"""
    settings = {}
    grades = _get_all_grades()
    for grade in grades:
        gs = GradeSetting.query.filter_by(grade=grade).first()
        settings[grade] = {
            'is_graduated': gs.is_graduated if gs else False,
        }
    
    return render_template('system/grade_mgmt/index.html', settings=settings)


@bp.route('/graduate', methods=['POST'])
@login_required
@perm_required('system.grade_mgmt')
def graduate():
    """设置年级毕业"""
    data = request.get_json() or {}
    grade = data.get('grade', '')
    
    if grade not in _get_all_grades():
        return jsonify({'success': False, 'message': '无效的年级'}), 400
    
    gs = GradeSetting.query.filter_by(grade=grade).first()
    if not gs:
        gs = GradeSetting(grade=grade)
        db.session.add(gs)
    
    if gs.is_graduated:
        return jsonify({'success': False, 'message': f'{grade} 已经毕业'}), 400
    
    # 1. 备份数据库
    from config import BASE_DIR
    db_path = os.path.join(BASE_DIR, 'data', 'dormitory.db')
    system_db_path = os.path.join(BASE_DIR, 'data', 'system.db')
    backups_dir = os.path.join(BASE_DIR, 'data', 'backups')
    os.makedirs(backups_dir, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_name = f'graduate_{grade}_{timestamp}.db'
    backup_path = os.path.join(backups_dir, backup_name)
    shutil.copy2(db_path, backup_path)
    # 同时备份系统库和历史库
    shutil.copy2(system_db_path, os.path.join(backups_dir, f'system_{grade}_{timestamp}.db'))
    history_db_path = os.path.join(BASE_DIR, 'data', 'history.db')
    if os.path.exists(history_db_path):
        history_backup_name = f'history_{grade}_{timestamp}.db'
        history_backup_path = os.path.join(backups_dir, history_backup_name)
        shutil.copy2(history_db_path, history_backup_path)
    
    # 1.5 归档学生数据到历史库
    try:
        hist_conn = sqlite3.connect(history_db_path)
        hist_conn.execute('''
            CREATE TABLE IF NOT EXISTS graduated_students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_id INTEGER,
                student_number TEXT,
                name TEXT NOT NULL,
                gender TEXT,
                ethnicity TEXT,
                grade TEXT,
                class_name TEXT,
                subject_selection TEXT,
                boarding_type TEXT,
                day_student_type TEXT,
                enrollment_status TEXT,
                textbook TEXT,
                teacher_notes TEXT,
                enrollment_notes TEXT,
                graduation_school_code TEXT,
                graduation_school TEXT,
                phone1 TEXT,
                phone2 TEXT,
                id_card_number TEXT,
                graduated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                graduated_grade TEXT
            )
        ''')
        hist_conn.commit()
        sys_conn = sqlite3.connect(system_db_path)
        students = sys_conn.execute(
            'SELECT id,student_number,name,gender,ethnicity,grade,class_name,subject_selection,boarding_type,day_student_type,enrollment_status,textbook,teacher_notes,enrollment_notes,graduation_school_code,graduation_school,phone1,phone2,id_card_number FROM students WHERE grade=?',
            (grade,)
        ).fetchall()
        for s in students:
            hist_conn.execute(
                'INSERT INTO graduated_students (original_id,student_number,name,gender,ethnicity,grade,class_name,subject_selection,boarding_type,day_student_type,enrollment_status,textbook,teacher_notes,enrollment_notes,graduation_school_code,graduation_school,phone1,phone2,id_card_number,graduated_grade) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (*s, grade)
            )
        hist_conn.commit()
        sys_conn.close()
        hist_conn.close()
        student_count = len(students)
    except Exception as e:
        student_count = 0
        print(f'归档学生数据异常: {e}')
    
    # 1.6 归档宿舍数据到历史库
    room_archived = 0
    bed_archived = 0
    try:
        hist_conn = sqlite3.connect(history_db_path)
        # 建表
        hist_conn.execute('''
            CREATE TABLE IF NOT EXISTS graduated_rooms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_room_id INTEGER,
                building TEXT,
                room_number TEXT,
                gender TEXT,
                floor INTEGER,
                capacity INTEGER,
                grade TEXT,
                class_name TEXT,
                combined_class TEXT,
                notes TEXT,
                graduated_grade TEXT,
                graduated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        hist_conn.execute('''
            CREATE TABLE IF NOT EXISTS graduated_beds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_bed_id INTEGER,
                original_room_id INTEGER,
                bed_number INTEGER,
                student_id INTEGER,
                student_number TEXT,
                student_name TEXT,
                student_gender TEXT,
                student_grade TEXT,
                student_class TEXT,
                graduated_grade TEXT,
                graduated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        hist_conn.commit()

        # 归档房间
        dorm_conn = sqlite3.connect(db_path)
        rooms = dorm_conn.execute(
            'SELECT id,building,room_number,gender,floor,capacity,grade,class_name,combined_class,notes FROM rooms WHERE grade=?',
            (grade,)
        ).fetchall()
        for r in rooms:
            hist_conn.execute(
                'INSERT INTO graduated_rooms (original_room_id,building,room_number,gender,floor,capacity,grade,class_name,combined_class,notes,graduated_grade) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (*r, grade)
            )
        room_archived = len(rooms)

        # 归档床位（该年级所有有学生的床位）
        if rooms:
            room_ids = [r[0] for r in rooms]
            placeholders = ','.join('?' * len(room_ids))
            beds = dorm_conn.execute(
                f'SELECT id,room_id,bed_number,student_id FROM bed_assignments WHERE room_id IN ({placeholders}) AND student_id IS NOT NULL',
                room_ids
            ).fetchall()
            if beds:
                # 从 system.db 查学生信息
                sys_conn = sqlite3.connect(system_db_path)
                student_ids = [b[3] for b in beds]
                s_placeholders = ','.join('?' * len(student_ids))
                stu_rows = sys_conn.execute(
                    f'SELECT id,student_number,name,gender,grade,class_name FROM students WHERE id IN ({s_placeholders})',
                    student_ids
                ).fetchall()
                stu_map = {s[0]: s for s in stu_rows}
                for b in beds:
                    stu = stu_map.get(b[3])
                    hist_conn.execute(
                        'INSERT INTO graduated_beds (original_bed_id,original_room_id,bed_number,student_id,student_number,student_name,student_gender,student_grade,student_class,graduated_grade) VALUES (?,?,?,?,?,?,?,?,?,?)',
                        (b[0], b[1], b[2], b[3],
                         stu[1] if stu else '', stu[2] if stu else '',
                         stu[3] if stu else '', stu[4] if stu else '', stu[5] if stu else '',
                         grade)
                    )
                bed_archived = len(beds)
                sys_conn.close()

        dorm_conn.close()
        hist_conn.commit()
        hist_conn.close()
    except Exception as e:
        print(f'归档宿舍数据异常: {e}')

    # 1.7 写入毕业变迁日志
    try:
        grad_students = Student.query.filter(Student.grade == grade).all()
        students_data = [{'id': s.id, 'student_number': s.student_number or '', 'name': s.name} for s in grad_students]
        if students_data:
            write_change_log('graduate', students_data, old_value='在校', new_value='毕业',
                           detail=f'{grade} 毕业归档', operator_name=current_user.real_name)
    except Exception as e:
        print(f'毕业变迁日志异常: {e}')

    # 2. 获取该年级所有房间ID（清空前查）
    rooms = Room.query.filter(Room.grade == grade).all()
    room_ids = [r.id for r in rooms]
    
    # 3. 清空房间分配
    rooms_updated = len(rooms)
    for r in rooms:
        r.grade = None
        r.class_name = None
        r.combined_class = None
    
    # 4. 清空床位（分两步：先从system.db查学生ID，再更新dormitory.db）
    student_ids = [s.id for s in Student.query.filter(Student.grade == grade).all()]
    beds_cleared = 0
    if student_ids:
        beds_cleared = BedAssignment.query.filter(
            BedAssignment.student_id.in_(student_ids)
        ).update({'student_id': None, 'assigned_by': None, 'assigned_at': None}, synchronize_session=False)
    
    # 4. 标记毕业
    gs.is_graduated = True
    gs.graduated_at = datetime.now()
    gs.backup_path = backup_path
    gs.graduated_by = current_user.id
    db.session.commit()

    log_operation(current_user, '毕业', '年级', None, f'{grade} 毕业，归档{student_count}名学生、{room_archived}间房、{bed_archived}个床位，清空{rooms_updated}间房、{beds_cleared}个床位')

    return jsonify({
        'success': True,
        'message': f'{grade} 已毕业！归档{student_count}名学生、{room_archived}间房、{bed_archived}个床位',
        'backup': backup_name
    })


# ==================== 历史查询 ====================


@bp.route('/history')
@login_required
@perm_required('system.grade_mgmt')
def history():
    """基本信息历史查询"""
    from config import BASE_DIR
    history_db_path = os.path.join(BASE_DIR, 'data', 'history.db')

    graduated_grades = []
    if os.path.exists(history_db_path):
        try:
            conn = sqlite3.connect(history_db_path)
            grades = conn.execute(
                'SELECT DISTINCT graduated_grade FROM graduated_students ORDER BY graduated_grade'
            ).fetchall()
            graduated_grades = [g[0] for g in grades if g[0]]
            conn.close()
        except Exception:
            pass

    search_grade = request.args.get('grade', '')
    search_name = request.args.get('name', '').strip()
    search_student_number = request.args.get('student_number', '').strip()
    search_school_code = request.args.get('school_code', '').strip()

    results = []
    if search_grade and os.path.exists(history_db_path):
        try:
            conn = sqlite3.connect(history_db_path)
            conn.row_factory = sqlite3.Row
            where_clauses = ['graduated_grade = ?']
            params = [search_grade]
            if search_name:
                where_clauses.append('name LIKE ?')
                params.append(f'%{search_name}%')
            if search_student_number:
                where_clauses.append('student_number LIKE ?')
                params.append(f'%{search_student_number}%')
            if search_school_code:
                where_clauses.append('graduation_school_code LIKE ?')
                params.append(f'%{search_school_code}%')
            sql = f'SELECT * FROM graduated_students WHERE {" AND ".join(where_clauses)} ORDER BY class_name, name LIMIT 200'
            rows = conn.execute(sql, params).fetchall()
            results = []
            for row in rows:
                r = dict(row)
                if r.get('id_card_number'):
                    r['id_card_decrypted'] = decrypt(r['id_card_number'])
                    r['id_card_masked'] = _mask_id_card(r['id_card_decrypted'])
                else:
                    r['id_card_decrypted'] = ''
                    r['id_card_masked'] = ''
                results.append(r)
            conn.close()
        except Exception as e:
            flash(f'查询失败：{str(e)}', 'danger')

    return render_template('system/grade_mgmt/history.html',
                           graduated_grades=graduated_grades,
                           search_grade=search_grade,
                           search_name=search_name,
                           search_student_number=search_student_number,
                           search_school_code=search_school_code,
                           results=results)


@bp.route('/alumni')
@login_required
@perm_required('system.grade_mgmt')
def alumni():
    """往届生基本信息查询"""
    from config import BASE_DIR
    history_db_path = os.path.join(BASE_DIR, 'data', 'history.db')

    graduated_grades = []
    if os.path.exists(history_db_path):
        try:
            conn = sqlite3.connect(history_db_path)
            grades = conn.execute(
                'SELECT DISTINCT graduated_grade FROM graduated_students ORDER BY graduated_grade'
            ).fetchall()
            graduated_grades = [g[0] for g in grades if g[0]]
            conn.close()
        except Exception:
            pass

    search_grade = request.args.get('grade', '')
    search_name = request.args.get('name', '').strip()
    search_student_number = request.args.get('student_number', '').strip()
    search_school_code = request.args.get('school_code', '').strip()

    results = []
    change_logs = {}
    if search_grade and os.path.exists(history_db_path):
        try:
            conn = sqlite3.connect(history_db_path)
            conn.row_factory = sqlite3.Row
            # 确保表存在
            conn.execute('''
                CREATE TABLE IF NOT EXISTS student_change_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id INTEGER NOT NULL,
                    student_number TEXT,
                    student_name TEXT NOT NULL,
                    change_type TEXT NOT NULL,
                    old_value TEXT,
                    new_value TEXT,
                    detail TEXT,
                    operator TEXT,
                    changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()
            # 查询学生
            where_clauses = ['graduated_grade = ?']
            params = [search_grade]
            if search_name:
                where_clauses.append('name LIKE ?')
                params.append(f'%{search_name}%')
            if search_student_number:
                where_clauses.append('student_number LIKE ?')
                params.append(f'%{search_student_number}%')
            if search_school_code:
                where_clauses.append('graduation_school_code LIKE ?')
                params.append(f'%{search_school_code}%')
            sql = f'SELECT * FROM graduated_students WHERE {" AND ".join(where_clauses)} ORDER BY class_name, name LIMIT 200'
            rows = conn.execute(sql, params).fetchall()
            results = []
            for row in rows:
                r = dict(row)
                if r.get('id_card_number'):
                    r['id_card_decrypted'] = decrypt(r['id_card_number'])
                    r['id_card_masked'] = _mask_id_card(r['id_card_decrypted'])
                else:
                    r['id_card_decrypted'] = ''
                    r['id_card_masked'] = ''
                results.append(r)

            # 查变迁日志
            if results:
                student_ids = [r['original_id'] for r in results if r.get('original_id')]
                if student_ids:
                    ph = ','.join('?' * len(student_ids))
                    logs = conn.execute(
                        f'SELECT * FROM student_change_log WHERE student_id IN ({ph}) ORDER BY student_id, changed_at',
                        student_ids
                    ).fetchall()
                    for log in logs:
                        sid = log['student_id']
                        if sid not in change_logs:
                            change_logs[sid] = []
                        change_logs[sid].append(dict(log))

            conn.close()
        except Exception as e:
            flash(f'查询失败：{str(e)}', 'danger')

    return render_template('system/grade_mgmt/alumni.html',
                           graduated_grades=graduated_grades,
                           search_grade=search_grade,
                           search_name=search_name,
                           search_student_number=search_student_number,
                           search_school_code=search_school_code,
                           results=results,
                           change_logs=change_logs)
