"""年级管理：毕�?存档/历史查询"""
import os
import shutil
import sqlite3
from datetime import datetime
from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for
from flask_login import login_required, current_user
from app.extensions import db
from app.models import Room, BedAssignment, Student, GradeSetting
from app.utils.decorators import role_required
from app.utils.helpers import log_operation

bp = Blueprint('grade_mgmt', __name__, url_prefix='/grade-mgmt')

GRADES = ['2023�?, '2024�?, '2025�?]


@bp.route('/')
@login_required
@role_required('admin')
def index():
    """年级管理页面"""
# StuLink v1.4.6 2026-06-30
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0
    settings = {}
    for grade in GRADES:
        gs = GradeSetting.query.filter_by(grade=grade).first()
        settings[grade] = {
            'is_graduated': gs.is_graduated if gs else False,
        }
    
    return render_template('system/grade_mgmt/index.html', settings=settings)


@bp.route('/graduate', methods=['POST'])
@login_required
@role_required('admin')
def graduate():
    """设置年级毕业"""
    data = request.get_json() or {}
    grade = data.get('grade', '')
    
    if grade not in GRADES:
        return jsonify({'success': False, 'message': '无效的年�?}), 400
    
    gs = GradeSetting.query.filter_by(grade=grade).first()
    if not gs:
        gs = GradeSetting(grade=grade)
        db.session.add(gs)
    
    if gs.is_graduated:
        return jsonify({'success': False, 'message': f'{grade} 已经毕业'}), 400
    
    # 1. 备份数据�?
    from config import BASE_DIR
    db_path = os.path.join(BASE_DIR, 'data', 'dormitory.db')
    backups_dir = os.path.join(BASE_DIR, 'data', 'backups')
    os.makedirs(backups_dir, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_name = f'graduate_{grade}_{timestamp}.db'
    backup_path = os.path.join(backups_dir, backup_name)
    shutil.copy2(db_path, backup_path)
    # 同时备份历史�?
    history_db_path = os.path.join(BASE_DIR, 'data', 'history.db')
    if os.path.exists(history_db_path):
        history_backup_name = f'history_{grade}_{timestamp}.db'
        history_backup_path = os.path.join(backups_dir, history_backup_name)
        shutil.copy2(history_db_path, history_backup_path)
    
    # 2. 获取该年级所有房间ID（清空前查）
    rooms = Room.query.filter(Room.grade == grade).all()
    room_ids = [r.id for r in rooms]
    
    # 3. 清空房间分配
    rooms_updated = len(rooms)
    for r in rooms:
        r.grade = None
        r.class_name = None
        r.combined_class = None
    
    # 4. 清空床位（按学生年级查）
    student_sub = db.session.query(Student.id).filter(Student.grade == grade).subquery()
    beds_cleared = BedAssignment.query.filter(
        BedAssignment.student_id.in_(student_sub)
    ).update({'student_id': None, 'assigned_by': None, 'assigned_at': None}, synchronize_session=False)
    
    # 4. 标记毕业
    gs.is_graduated = True
    gs.graduated_at = datetime.now()
    gs.backup_path = backup_path
    gs.graduated_by = current_user.id
    db.session.commit()

    log_operation(current_user, '毕业', '年级', None, f'{grade} 毕业，清空{rooms_updated}间房、{beds_cleared}个床�?)

    return jsonify({
        'success': True,
        'message': f'{grade} 已毕业！备份：{backup_name}，清空{rooms_updated}间房、{beds_cleared}个床�?,
        'backup': backup_name
    })


# ==================== 历史查询 ====================

@bp.route('/history')
@login_required
@role_required('admin')
def history():
    """历史数据查询页面"""
    from config import BASE_DIR
    backups_dir = os.path.join(BASE_DIR, 'data', 'backups')
    os.makedirs(backups_dir, exist_ok=True)
    
    # 列出所有备份文�?
    backups = []
    for f in sorted(os.listdir(backups_dir), reverse=True):
        if f.endswith('.db'):
            fpath = os.path.join(backups_dir, f)
            size_kb = os.path.getsize(fpath) // 1024
            mtime = datetime.fromtimestamp(os.path.getmtime(fpath))
            # 从文件名提取年级
            grade = ''
            for g in GRADES:
                if g in f:
                    grade = g
                    break
            backups.append({
                'filename': f,
                'size': f'{size_kb} KB',
                'date': mtime.strftime('%Y-%m-%d %H:%M'),
                'grade': grade,
            })
    
    return render_template('system/grade_mgmt/history.html', backups=backups)


@bp.route('/history/view/<filename>')
@login_required
@role_required('admin')
def history_view(filename):
    """查看历史备份数据"""
    from config import BASE_DIR
    # 防止路径穿越攻击
    safe_filename = os.path.basename(filename)
    backup_path = os.path.join(BASE_DIR, 'data', 'backups', safe_filename)
    
    if not os.path.exists(backup_path):
        flash('备份文件不存�?, 'danger')
        return redirect(url_for('grade_mgmt.history'))
    
    # �?sqlite3 直接读取备份
    conn = sqlite3.connect(backup_path)
    conn.row_factory = sqlite3.Row
    
    # 学生统计
    students = conn.execute("SELECT grade, gender, COUNT(*) as cnt FROM students WHERE boarding_type='住校' GROUP BY grade, gender ORDER BY grade, gender").fetchall()
    
    # 房间分配统计
    rooms = conn.execute("SELECT grade, gender, COUNT(*) as cnt, SUM(capacity) as cap FROM rooms WHERE grade IS NOT NULL AND class_name IS NOT NULL AND class_name != '' GROUP BY grade, gender ORDER BY grade, gender").fetchall()
    
    # 床位使用统计
    beds = conn.execute("""
        SELECT r.grade, r.gender, COUNT(*) as cnt
        FROM bed_assignments ba
        JOIN rooms r ON ba.room_id = r.id
        WHERE ba.student_id IS NOT NULL
        GROUP BY r.grade, r.gender
        ORDER BY r.grade, r.gender
    """).fetchall()
    
    # 班级明细
    class_detail = conn.execute("""
        SELECT r.grade, r.class_name, r.building, r.room_number, r.capacity, r.gender,
               COUNT(ba.student_id) as occupied
        FROM rooms r
        LEFT JOIN bed_assignments ba ON ba.room_id = r.id AND ba.student_id IS NOT NULL
        WHERE r.class_name IS NOT NULL AND r.class_name != ''
        GROUP BY r.id
        ORDER BY r.grade, r.class_name, r.building, r.room_number
    """).fetchall()
    
    conn.close()
    
    # 学生统计整理
    student_stats = {}
    for row in students:
        g = row['grade'] or '未知'
        if g not in student_stats:
            student_stats[g] = {'male': 0, 'female': 0}
        if row['gender'] == '�?:
            student_stats[g]['male'] = row['cnt']
        else:
            student_stats[g]['female'] = row['cnt']
    
    return render_template('system/grade_mgmt/history_view.html',
                           filename=filename,
                           student_stats=student_stats,
                           rooms=rooms, beds=beds,
                           class_detail=class_detail)
