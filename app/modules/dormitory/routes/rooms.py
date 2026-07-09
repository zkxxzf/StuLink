# StuLink v1.6.1 2026-07-09
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0
from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from app.extensions import db
from app.models import Room, BedAssignment, Student, StudentAccommodation
from app.utils.decorators import perm_required
from app.utils.helpers import get_dict_values, log_operation
from sqlalchemy import func
import json

bp = Blueprint('rooms', __name__, url_prefix='/rooms')


@bp.route('/')
@login_required
def list_rooms():
    query = Room.query.filter_by(is_active=True)

    building = request.args.get('building', '')
    gender = request.args.get('gender', '')
    floor = request.args.get('floor', '')
    capacity = request.args.get('capacity', '')

    if building:
        query = query.filter_by(building=building)
    if gender:
        query = query.filter_by(gender=gender)
    if floor:
        # 去掉可能的"层"字，提取数字
        floor_num = floor.replace('层', '').strip()
        try:
            query = query.filter_by(floor=int(floor_num))
        except ValueError:
            pass  # 如果转换失败，忽略该筛选条件
    if capacity:
        try:
            query = query.filter_by(capacity=int(capacity))
        except ValueError:
            pass

    rooms = query.order_by(Room.building, Room.floor, Room.room_number).all()

    # 优化：使用单次聚合查询获取所有房间的入住人数，避免 N+1 查询
    occupancy_data = db.session.query(
        BedAssignment.room_id,
        func.count(BedAssignment.id).label('count')
    ).filter(
        BedAssignment.student_id.isnot(None)
    ).group_by(BedAssignment.room_id).all()
    
    # 转换为字典便于查找
    occupancy_map = {row.room_id: row.count for row in occupancy_data}

    room_data = []
    for room in rooms:
        # 构建班级显示信息
        class_info = None
        if room.grade and room.class_name:
            class_info = f"{room.grade} {room.class_name}"
        elif room.grade:
            class_info = room.grade
        elif room.class_name:
            class_info = room.class_name
        
        room_data.append({
            'room': room,
            'occupancy': occupancy_map.get(room.id, 0),
            'class_info': class_info,
        })

    return render_template('dormitory/rooms/list.html', room_data=room_data,
                           buildings=get_dict_values('building'),
                           floors=get_dict_values('floor'))


@bp.route('/<int:id>')
@login_required
def detail(id):
    room = Room.query.get_or_404(id)
    beds = BedAssignment.query.filter_by(room_id=room.id).order_by(
        BedAssignment.bed_number).all()
    
    # 优化：批量获取所有学生信息，避免 N+1 查询
    student_ids = [bed.student_id for bed in beds if bed.student_id]
    if student_ids:
        students = Student.query.filter(Student.id.in_(student_ids)).all()
        student_map = {s.id: s for s in students}
    else:
        student_map = {}
    
    for bed in beds:
        bed.student_info = student_map.get(bed.student_id) if bed.student_id else None
    
    return render_template('dormitory/rooms/detail.html', room=room, beds=beds)


@bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@perm_required('dormitory.manage')
def edit(id):
    room = Room.query.get_or_404(id)
    if request.method == 'POST':
        new_gender = request.form.get('gender', room.gender)
        new_capacity = int(request.form.get('capacity', room.capacity))
        new_grade = request.form.get('grade', '') or None
        new_class = request.form.get('class_name', '') or None
        new_combined = request.form.get('combined_class', '') or None
        new_notes = request.form.get('notes', '') or None

        old_capacity = room.capacity
        if new_capacity != old_capacity:
            _adjust_beds(room, old_capacity, new_capacity)

        room.gender = new_gender
        room.capacity = new_capacity
        room.grade = new_grade
        room.class_name = new_class
        room.combined_class = new_combined
        room.notes = new_notes
        db.session.commit()
        flash(f'{room.display_name} 信息已更新', 'success')
        return redirect(url_for('rooms.detail', id=room.id))

    return render_template('dormitory/rooms/form.html', room=room, title='编辑宿舍',
                           grades=get_dict_values('grade'), classes=get_dict_values('class'),
                           buildings=get_dict_values('building'), floors=get_dict_values('floor'))


@bp.route('/create', methods=['GET', 'POST'])
@perm_required('dormitory.manage')
def create():
    if request.method == 'POST':
        building = request.form.get('building', '').strip()
        room_number = request.form.get('room_number', '').strip()
        floor = request.form.get('floor', '').strip()
        gender = request.form.get('gender', '男')
        capacity = int(request.form.get('capacity', 8))
        notes = request.form.get('notes', '') or None

        if not room_number or not building:
            flash('请输入宿舍楼和房间号', 'danger')
            return render_template('dormitory/rooms/form.html', room=None, title='新增宿舍',
                                   grades=get_dict_values('grade'), classes=get_dict_values('class'),
                                   buildings=get_dict_values('building'), floors=get_dict_values('floor'))

        if Room.query.filter_by(building=building, room_number=room_number).first():
            flash(f'{building} {room_number} 已存在', 'danger')
            return render_template('dormitory/rooms/form.html', room=None, title='新增宿舍',
                                   grades=get_dict_values('grade'), classes=get_dict_values('class'),
                                   buildings=get_dict_values('building'), floors=get_dict_values('floor'))

        # 优先使用选择的楼层，如果没有则从房间号提取
        floor_num = 1
        if floor:
            try:
                floor_num = int(floor)
            except ValueError:
                for ch in room_number:
                    if ch.isdigit():
                        floor_num = int(ch)
                        break
        else:
            for ch in room_number:
                if ch.isdigit():
                    floor_num = int(ch)
                    break

        room = Room(building=building, room_number=room_number, gender=gender, floor=floor_num,
                    capacity=capacity, notes=notes, is_active=True)
        db.session.add(room)
        db.session.flush()

        db.session.add_all([BedAssignment(room_id=room.id, bed_number=bed_num) for bed_num in range(1, capacity + 1)])

        db.session.commit()
        log_operation(current_user, '创建', '宿舍', room.id, f'{room.display_name} {capacity}人间')
        flash(f'宿舍 {room.display_name}（{capacity}人间）已创建', 'success')
        return redirect(url_for('rooms.detail', id=room.id))

    return render_template('dormitory/rooms/form.html', room=None, title='新增宿舍',
                           grades=get_dict_values('grade'), classes=get_dict_values('class'),
                           buildings=get_dict_values('building'), floors=get_dict_values('floor'))


@bp.route('/<int:id>/delete', methods=['POST'])
@perm_required('dormitory.manage')
def delete(id):
    room = Room.query.get_or_404(id)
    occupied = BedAssignment.query.filter(
        BedAssignment.room_id == room.id,
        BedAssignment.student_id.isnot(None)
    ).count()
    if occupied > 0:
        flash(f'{room.room_number} 还有 {occupied} 名学生入住，无法删除', 'danger')
        return redirect(url_for('rooms.list_rooms'))

    BedAssignment.query.filter_by(room_id=room.id).delete()
    db.session.delete(room)
    db.session.commit()
    log_operation(current_user, '删除', '宿舍', room.id, f'{room.display_name}')
    flash(f'宿舍 {room.room_number} 已删除', 'success')
    return redirect(url_for('rooms.list_rooms'))

@bp.route('/assign-visual')
@perm_required('dormitory.manage')
def assign_visual():
    """可视化宿舍分配页面"""
    from app.utils.helpers import get_graduated_grades
    all_grades = get_dict_values('grade')
    graduated = get_graduated_grades()
    grades = [g for g in all_grades if g not in graduated]
    buildings = get_dict_values('building')
    return render_template('dormitory/rooms/assign_visual.html', grades=grades, buildings=buildings)


@bp.route('/assign-data')
@login_required
def assign_data():
    """获取宿舍分配页面所需的所有数据"""
    from flask import jsonify
    from app.utils.helpers import get_graduated_grades
    
    # 获取所有宿舍
    rooms = Room.query.filter_by(is_active=True).order_by(
        Room.building, Room.floor, Room.room_number
    ).all()
    
    # 获取所有班级和住校学生数（排除已毕业年级）
    from app.models import Student
    all_grades = get_dict_values('grade')
    graduated = get_graduated_grades()
    grades = [g for g in all_grades if g not in graduated]
    classes_list = get_dict_values('class')
    
    classes_data = []
    for grade in grades:
        for cls_name in classes_list:
            male_count = Student.query.filter_by(
                grade=grade, class_name=cls_name,
                gender='男', boarding_type='住校'
            ).filter(~Student.grade.in_(graduated)).count()
            female_count = Student.query.filter_by(
                grade=grade, class_name=cls_name,
                gender='女', boarding_type='住校'
            ).filter(~Student.grade.in_(graduated)).count()
            
            if male_count > 0 or female_count > 0:
                classes_data.append({
                    'grade': grade,
                    'class_name': cls_name,
                    'boarding_male': male_count,
                    'boarding_female': female_count
                })
    
    # 格式化宿舍数据
    rooms_data = []
    for room in rooms:
        rooms_data.append({
            'id': room.id,
            'building': room.building,
            'room_number': room.room_number,
            'floor': room.floor,
            'gender': room.gender,
            'capacity': room.capacity,
            'grade': room.grade,
            'class_name': room.class_name,
            'combined_class': room.combined_class,
            'is_combined': bool(room.combined_class)
        })
    
    return jsonify({
        'rooms': rooms_data,
        'classes': classes_data,
        'buildings': get_dict_values('building'),
        'grades': grades
    })


@bp.route('/assign-room', methods=['POST'])
@perm_required('dormitory.manage')
def assign_room():
    """分配宿舍给班级"""
    from flask import jsonify
    data = request.get_json()
    
    room_id = data.get('room_id')
    grade = data.get('grade')
    class_name = data.get('class_name')
    
    if not room_id or not grade or not class_name:
        return jsonify({'success': False, 'message': '参数不完整'}), 400
    
    room = Room.query.get(room_id)
    if not room:
        return jsonify({'success': False, 'message': '宿舍不存在'}), 404
    
    # 更新宿舍分配信息
    room.grade = grade
    room.class_name = class_name
    db.session.commit()
    
    return jsonify({'success': True, 'message': '分配成功'})


@bp.route('/unassign-room', methods=['POST'])
@perm_required('dormitory.manage')
def unassign_room():
    """取消宿舍分配"""
    from flask import jsonify
    data = request.get_json()
    
    room_id = data.get('room_id')
    
    if not room_id:
        return jsonify({'success': False, 'message': '参数不完整'}), 400
    
    room = Room.query.get(room_id)
    if not room:
        return jsonify({'success': False, 'message': '宿舍不存在'}), 404
    
    # 取消分配
    room.grade = None
    room.class_name = None
    room.combined_class = None
    db.session.commit()
    
    return jsonify({'success': True, 'message': '取消成功'})


@bp.route('/set-combined', methods=['POST'])
@perm_required('dormitory.manage')
def set_combined():
    """设置合班宿舍"""
    from flask import jsonify
    data = request.get_json()
    
    room_id = data.get('room_id')
    is_combined = data.get('is_combined', False)
    
    if not room_id:
        return jsonify({'success': False, 'message': '参数不完整'}), 400
    
    room = Room.query.get(room_id)
    if not room:
        return jsonify({'success': False, 'message': '宿舍不存在'}), 404
    
    # 设置合班标记
    if is_combined:
        room.combined_class = '合班'
    else:
        room.combined_class = None
    db.session.commit()
    
    return jsonify({'success': True, 'message': '设置成功'})


@bp.route('/save-assignments', methods=['POST'])
@perm_required('dormitory.manage')
def save_assignments():
    """批量保存所有分配"""
    from flask import jsonify
    data = request.get_json()
    
    assignments = data.get('assignments', [])
    
    # 如果没有分配数据，清空所有宿舍的分配状态
    if not assignments:
        # 清空所有房间分配
        rooms = Room.query.filter(Room.grade.isnot(None) | Room.class_name.isnot(None)).all()
        room_count = 0
        for room in rooms:
            room.grade = None
            room.class_name = None
            room_count += 1
        # 同时清空所有床位
        bed_count = BedAssignment.query.filter(
            BedAssignment.student_id.isnot(None)
        ).update({'student_id': None, 'assigned_by': None, 'assigned_at': None}, synchronize_session=False)
        db.session.commit()
        return jsonify({'success': True, 'message': f'已清空 {room_count} 间房间分配、{bed_count} 个床位'})
    
    # 保存新的分配数据
    count = 0
    for assign in assignments:
        room = Room.query.get(assign.get('room_id'))
        if room:
            room.grade = assign.get('grade')
            room.class_name = assign.get('class_name')
            count += 1
    
    db.session.commit()
    
    return jsonify({'success': True, 'message': f'已保存 {count} 个分配'})


@bp.route('/class-bed-requirement')
@login_required
def class_bed_requirement():
    """获取某个班级的床位需求和已分配情况"""
    from flask import jsonify
    grade = request.args.get('grade', '')
    class_name = request.args.get('class_name', '')
    
    if not grade or not class_name:
        return jsonify({
            'male': 0, 'female': 0, 'total': 0,
            'assigned_male_rooms': 0, 'assigned_male_beds': 0,
            'assigned_female_rooms': 0, 'assigned_female_beds': 0
        })
    
    # 统计该班级住校学生的性别分布
    male_count = Student.query.filter_by(
        grade=grade, 
        class_name=class_name, 
        gender='男', 
        boarding_type='住校'
    ).count()
    
    female_count = Student.query.filter_by(
        grade=grade, 
        class_name=class_name, 
        gender='女', 
        boarding_type='住校'
    ).count()
    
    # 统计该班级已分配的宿舍 (按性别)
    assigned_rooms = Room.query.filter_by(grade=grade, class_name=class_name, is_active=True).all()
    
    assigned_male_rooms = 0
    assigned_male_beds = 0
    assigned_female_rooms = 0
    assigned_female_beds = 0
    
    for room in assigned_rooms:
        occupancy = BedAssignment.query.filter(
            BedAssignment.room_id == room.id,
            BedAssignment.student_id.isnot(None)
        ).count()
        
        if room.gender == '男':
            assigned_male_rooms += 1
            assigned_male_beds += occupancy
        else:
            assigned_female_rooms += 1
            assigned_female_beds += occupancy
    
    return jsonify({
        'male': male_count,
        'female': female_count,
        'total': male_count + female_count,
        'assigned_male_rooms': assigned_male_rooms,
        'assigned_male_beds': assigned_male_beds,
        'assigned_female_rooms': assigned_female_rooms,
        'assigned_female_beds': assigned_female_beds
    })


@bp.route('/update-room-assignment', methods=['POST'])
@perm_required('dormitory.manage')
def update_room_assignment():
    """更新单个房间的年级班级分配"""
    from flask import jsonify
    data = request.get_json()
    
    room_id = data.get('room_id')
    grade = data.get('grade', '')
    class_name = data.get('class_name', '')
    
    if not room_id:
        return jsonify({'success': False, 'message': '房间 ID 不能为空'}), 400
    
    room = Room.query.get(room_id)
    if not room:
        return jsonify({'success': False, 'message': '房间不存在'}), 404
    
    # 更新房间分配信息
    room.grade = grade or None
    room.class_name = class_name or None
    db.session.commit()
    
    return jsonify({'success': True, 'message': '更新成功'})


@bp.route('/batch-setting', methods=['GET', 'POST'])
@perm_required('dormitory.manage')
def batch_setting():
    if request.method == 'POST':
        room_ids = request.form.getlist('room_ids')
        new_gender = request.form.get('gender', '')
        new_capacity = request.form.get('capacity', '')

        if not room_ids:
            flash('请选择宿舍', 'warning')
            return redirect(url_for('rooms.list_rooms'))

        if not new_gender and not new_capacity:
            flash('请至少选择一项要修改的内容（性别或床位数）', 'warning')
            return redirect(url_for('rooms.list_rooms'))

        count = 0
        for rid in room_ids:
            room = Room.query.get(int(rid))
            if not room:
                continue
            if new_gender:
                room.gender = new_gender
            if new_capacity:
                cap = int(new_capacity)
                if cap != room.capacity:
                    _adjust_beds(room, room.capacity, cap)
                    room.capacity = cap
            count += 1

        db.session.commit()

        msg_parts = []
        if new_gender:
            msg_parts.append(f'性别={new_gender}')
        if new_capacity:
            msg_parts.append(f'床位数={new_capacity}')
        flash(f'已批量设置 {count} 间宿舍（{", ".join(msg_parts)}）', 'success')
        return redirect(url_for('rooms.list_rooms'))

    return redirect(url_for('rooms.list_rooms'))


@bp.route('/batch-add-rooms', methods=['GET', 'POST'])
@perm_required('dormitory.manage')
def batch_add_rooms():
    if request.method == 'POST':
        building = request.form.get('building', '').strip()
        floor = request.form.get('floor', '').strip()
        gender = request.form.get('gender', '男')
        room_count = int(request.form.get('room_count', 0))
        start_room_number = request.form.get('start_room_number', '').strip()
        capacity = int(request.form.get('capacity', 8))

        if not building or not floor or not start_room_number:
            flash('请填写完整信息', 'danger')
            return redirect(url_for('rooms.list_rooms'))

        if room_count <= 0 or room_count > 100:
            flash('房间数量必须在 1-100 之间', 'danger')
            return redirect(url_for('rooms.list_rooms'))

        try:
            start_num = int(start_room_number)
        except ValueError:
            flash('起始房间号必须是数字', 'danger')
            return redirect(url_for('rooms.list_rooms'))

        created_count = 0
        skipped_count = 0

        for i in range(room_count):
            room_num = str(start_num + i)
            existing = Room.query.filter_by(building=building, room_number=room_num).first()
            if existing:
                skipped_count += 1
                continue

            room = Room(
                building=building,
                room_number=room_num,
                gender=gender,
                floor=int(floor),
                capacity=capacity,
                is_active=True
            )
            db.session.add(room)
            db.session.flush()

            for bed_num in range(1, capacity + 1):
                db.session.add(BedAssignment(room_id=room.id, bed_number=bed_num))

            created_count += 1

        db.session.commit()

        msg = f'成功添加 {created_count} 间宿舍'
        if skipped_count > 0:
            msg += f'，跳过 {skipped_count} 间已存在的宿舍'
        flash(msg, 'success')
        return redirect(url_for('rooms.list_rooms'))

    buildings = get_dict_values('building')
    floors = get_dict_values('floor')
    return render_template('dormitory/rooms/batch_add.html', buildings=buildings, floors=floors)


def _adjust_beds(room, old_capacity, new_capacity):
    if new_capacity > old_capacity:
        for bed_num in range(old_capacity + 1, new_capacity + 1):
            existing = BedAssignment.query.filter_by(room_id=room.id, bed_number=bed_num).first()
            if not existing:
                db.session.add(BedAssignment(room_id=room.id, bed_number=bed_num))
    elif new_capacity < old_capacity:
        for bed_num in range(new_capacity + 1, old_capacity + 1):
            bed = BedAssignment.query.filter_by(room_id=room.id, bed_number=bed_num).first()
            if bed:
                if bed.student_id:
                    flash(f'{room.display_name} 的 {bed_num}床 有学生入住，无法删除', 'warning')
                else:
                    db.session.delete(bed)


# ==================== 自动分配宿舍路由 ====================

@bp.route('/assign-auto')
@login_required
@perm_required('dormitory.manage')
def assign_auto():
    """自动分配宿舍向导页面"""
    from app.utils.helpers import get_graduated_grades
    all_grades = get_dict_values('grade')
    graduated = get_graduated_grades()
    grades = [g for g in all_grades if g not in graduated]
    
    # 获取各年级各班级的住校生统计
    grade_class_stats = {}
    for grade in grades:
        from sqlalchemy import case
        
        boarding_ids = [sa.student_id for sa in StudentAccommodation.query.filter(
            StudentAccommodation.boarding_type == '住校'
        ).all()]
        
        classes = db.session.query(
            Student.class_name,
            func.count(Student.id).label('count'),
            func.sum(case((Student.gender == '男', 1), else_=0)).label('male'),
            func.sum(case((Student.gender == '女', 1), else_=0)).label('female')
        ).filter(
            Student.grade == grade,
            Student.id.in_(boarding_ids)
        ).filter(~Student.grade.in_(graduated) if graduated else True
        ).group_by(Student.class_name).order_by(Student.class_name).all()
        
        grade_class_stats[grade] = [
            {
                'class_name': c.class_name,
                'count': c.count,
                'male': c.male or 0,
                'female': c.female or 0
            }
            for c in classes
        ]
    
    # 获取楼栋和楼层选项
    buildings = get_dict_values('building')
    floors = get_dict_values('floor')
    
    return render_template('dormitory/rooms/assign_auto.html',
                         grades=grades,
                         grade_class_stats=grade_class_stats,
                         buildings=buildings,
                         floors=floors)


@bp.route('/assign-auto/stats')
@login_required
def assign_auto_stats():
    """
    获取班级选择统计数据（后端计算，防止篡改）
    接收: ?keys=grade:class_name:gender,...
    返回: 每个组合的4维度信息 + 汇总
    """
    keys_param = request.args.get('keys', '')
    if not keys_param:
        return jsonify({'success': False, 'error': '参数不完整'})
    
    # 解析选中的班级-性别组合
    selected_keys = []
    for key_str in keys_param.split(','):
        parts = key_str.strip().split(':')
        if len(parts) == 3:
            selected_keys.append({
                'grade': parts[0],
                'class_name': parts[1],
                'gender': parts[2]
            })
    
    if not selected_keys:
        return jsonify({'success': False, 'error': '参数不完整'})
    
    # 从字典表获取有效值
    valid_grades = get_dict_values('grade')
    valid_classes = get_dict_values('class')
    
    # 统计每个组合的真实住校生人数
    details = []
    male_class_count = 0
    male_total = 0
    female_class_count = 0
    female_total = 0
    seen_male_classes = set()
    seen_female_classes = set()
    
    for sk in selected_keys:
        grade = sk['grade']
        class_name = sk['class_name']
        gender = sk['gender']
        
        # 验证字典表
        if grade not in valid_grades or class_name not in valid_classes:
            continue
        if gender not in ('男', '女'):
            continue
        
        # 从数据库查询真实人数
        student_count = Student.query.filter_by(
            grade=grade,
            class_name=class_name,
            gender=gender,
            boarding_type='住校'
        ).count()
        
        details.append({
            'grade': grade,
            'class_name': class_name,
            'gender': gender,
            'count': student_count
        })
        
        # 汇总统计
        class_ident = f"{grade}:{class_name}"
        if gender == '男':
            male_total += student_count
            if class_ident not in seen_male_classes:
                seen_male_classes.add(class_ident)
                male_class_count += 1
        else:
            female_total += student_count
            if class_ident not in seen_female_classes:
                seen_female_classes.add(class_ident)
                female_class_count += 1
    
    return jsonify({
        'success': True,
        'details': details,
        'summary': {
            'male_class_count': male_class_count,
            'male_total': male_total,
            'female_class_count': female_class_count,
            'female_total': female_total,
            'total_classes': len(seen_male_classes | seen_female_classes),
            'total_students': male_total + female_total
        }
    })


@bp.route('/assign-auto/preview', methods=['POST'])
@login_required
@perm_required('dormitory.manage')
def assign_auto_preview():
    """预览自动分配方案（不执行）- V4"""
    data = request.json or {}
    
    selected_keys = data.get('selected_keys', [])  # [{grade, class_name, gender}]
    selected_room_ids = data.get('selected_room_ids', [])  # [room_id, ...]
    mode = data.get('mode', 'keep_existing')
    combine_confirmations = data.get('combine_confirmations', [])
    force_full_8 = data.get('force_full_8', False)
    
    if not selected_keys or not selected_room_ids:
        return jsonify({'success': False, 'error': '参数不完整：请选择班级和房间'})
    
    # 检测已有分配的房间
    from app.models import Room as RoomModel, BedAssignment
    rooms_with_assignments = []
    rooms_with_beds = []
    for rid in selected_room_ids:
        room = RoomModel.query.get(rid)
        if not room:
            continue
        if room.class_name and room.class_name.strip():
            rooms_with_assignments.append(f"{room.building} {room.room_number}({room.grade} {room.class_name})")
        bed_count = BedAssignment.query.filter(
            BedAssignment.room_id == rid,
            BedAssignment.student_id.isnot(None)
        ).count()
        if bed_count > 0:
            rooms_with_beds.append(f"{room.building} {room.room_number}({bed_count}床已分配)")
    
    from app.modules.dormitory.services.room_assignment_v4 import auto_assign_preview as do_preview
    
    result = do_preview(
        selected_keys=selected_keys,
        selected_room_ids=selected_room_ids,
        mode=mode,
        dry_run=True,
        combine_confirmations=combine_confirmations,
        force_full_8=force_full_8
    )
    
    # 预览模式回滚
    if result['success']:
        db.session.rollback()
    
    # 附加已有分配信息
    result['has_existing'] = len(rooms_with_assignments) > 0 or len(rooms_with_beds) > 0
    result['rooms_with_assignments'] = rooms_with_assignments
    result['rooms_with_beds'] = rooms_with_beds
    
    return jsonify(result)


@bp.route('/assign-auto/execute', methods=['POST'])
@login_required
@perm_required('dormitory.manage')
def assign_auto_execute():
    """执行自动分配 - V4"""
    data = request.json or {}
    
    selected_keys = data.get('selected_keys', [])  # [{grade, class_name, gender}]
    selected_room_ids = data.get('selected_room_ids', [])  # [room_id, ...]
    mode = data.get('mode', 'keep_existing')
    combine_confirmations = data.get('combine_confirmations', [])
    force_full_8 = data.get('force_full_8', False)
    
    if not selected_keys or not selected_room_ids:
        return jsonify({'success': False, 'error': '参数不完整：请选择班级和房间'})
    
    from app.modules.dormitory.services.room_assignment_v4 import auto_assign_preview as do_preview
    
    result = do_preview(
        selected_keys=selected_keys,
        selected_room_ids=selected_room_ids,
        mode=mode,
        dry_run=False,
        combine_confirmations=combine_confirmations,
        force_full_8=force_full_8
    )
    
    return jsonify(result)


@bp.route('/available-rooms-data')
@login_required
def available_rooms_data():
    """获取可用房间数据（用于自动分配页面）"""
    # 获取所有激活的房间
    rooms = Room.query.filter_by(is_active=True).order_by(
        Room.building, Room.floor, Room.room_number
    ).all()
    
    # 组织成楼栋-楼层-房间的结构
    buildings_data = {}
    
    for room in rooms:
        building = room.building
        floor = room.floor
        
        if building not in buildings_data:
            buildings_data[building] = {}
        
        if floor not in buildings_data[building]:
            buildings_data[building][floor] = []
        
        # 可用床位 = 房间总容量（房间分配阶段不看学生床位）
        buildings_data[building][floor].append({
            'id': room.id,
            'room_number': room.room_number,
            'capacity': room.capacity,
            'occupied': 0,
            'available': room.capacity,
            'gender': room.gender,
            'grade': room.grade,
            'class_name': room.class_name,
            'is_combined': bool(room.combined_class and room.combined_class != '')
        })
    
    return jsonify(buildings_data)


@bp.route('/assign-auto/room-stats', methods=['POST'])
@login_required
def assign_auto_room_stats():
    """
    获取房间选择统计数据（后端计算，防止篡改）
    接收: { room_ids: [1,2,3,...] }
    返回: 男女宿舍/床位统计
    """
    data = request.json or {}
    room_ids = data.get('room_ids', [])
    
    if not room_ids:
        return jsonify({
            'success': True,
            'male_rooms': 0, 'male_beds': 0,
            'female_rooms': 0, 'female_beds': 0,
            'total_beds': 0, 'combined_rooms': 0
        })
    
    # 验证所有room_id有效并从数据库查询
    rooms = Room.query.filter(Room.id.in_(room_ids), Room.is_active == True).all()
    
    if len(rooms) != len(room_ids):
        return jsonify({'success': False, 'error': '部分房间ID无效'})
    
    # 统计
    male_rooms = 0
    male_beds = 0
    female_rooms = 0
    female_beds = 0
    combined_rooms = 0
    
    for room in rooms:
        if room.gender == '男':
            male_rooms += 1
            male_beds += room.capacity
        elif room.gender == '女':
            female_rooms += 1
            female_beds += room.capacity
        else:
            # 不限性别的房间
            male_rooms += 1
            male_beds += room.capacity
            female_rooms += 1
            female_beds += room.capacity
        
        if room.combined_class and room.combined_class != '':
            combined_rooms += 1
    
    return jsonify({
        'success': True,
        'male_rooms': male_rooms,
        'male_beds': male_beds,
        'female_rooms': female_rooms,
        'female_beds': female_beds,
        'total_beds': male_beds + female_beds,
        'combined_rooms': combined_rooms
    })


@bp.route('/report')
@login_required
def report():
    """宿舍报表 → 已合并到 /statistics/?tab=rooms"""
    grade = request.args.get('grade', '')
    url = url_for('statistics.index', tab='rooms')
    if grade:
        url += f'?grade={grade}'
    return redirect(url)


@bp.route('/report/export')
@login_required
def report_export():
    """导出宿舍报表为Excel（保持原路由，由 statistics 页面调用）"""
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
    from openpyxl.utils import get_column_letter
    from collections import OrderedDict

    grade_filter = request.args.get('grade', '')

    query = Room.query.filter(
        Room.is_active == True,
        Room.class_name.isnot(None),
        Room.class_name != ''
    )
    if grade_filter:
        query = query.filter_by(grade=grade_filter)

    rooms = query.order_by(
        Room.grade, Room.gender, Room.class_name,
        Room.building, Room.room_number
    ).all()

    tree = OrderedDict()
    class_totals = {}
    for room in rooms:
        g = room.grade or ''
        gender = room.gender or ''
        cn = room.class_name or ''
        if g not in tree:
            tree[g] = OrderedDict()
        if gender not in tree[g]:
            tree[g][gender] = OrderedDict()
        if cn not in tree[g][gender]:
            tree[g][gender][cn] = []
        tree[g][gender][cn].append(room)

    for g in tree:
        for gender in tree[g]:
            for cn in tree[g][gender]:
                cnt = Student.query.filter_by(
                    grade=g, class_name=cn, gender=gender,
                    boarding_type='住校'
                ).count()
                class_totals[(g, cn, gender)] = cnt

    wb = Workbook()
    ws = wb.active
    ws.title = '宿舍分配报表'

    header_font = Font(bold=True, size=11)
    header_fill = PatternFill(start_color='D9E1F2', end_color='D9E1F2', fill_type='solid')
    title_font = Font(bold=True, size=14)
    subtitle_font = Font(bold=True, size=11, color='1565C0')
    sub_fill = PatternFill(start_color='E3F2FD', end_color='E3F2FD', fill_type='solid')
    sum_font = Font(bold=True, size=10)
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin')
    )
    center_align = Alignment(horizontal='center', vertical='center')
    left_align = Alignment(horizontal='left', vertical='center')

    row = 1
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
    c = ws.cell(row=row, column=1, value='宿舍分配报表')
    c.font = title_font
    c.alignment = Alignment(horizontal='center')
    row += 1

    info = f'已分配 {len(rooms)} 间宿舍 / {sum(r.capacity for r in rooms)} 张床位 / 住校生 {sum(class_totals.values())} 人'
    if grade_filter:
        info += f' / 年级：{grade_filter}'
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
    c = ws.cell(row=row, column=1, value=info)
    c.font = Font(size=10, color='666666')
    c.alignment = Alignment(horizontal='center')
    row += 2

    cols = ['班级', '住校生', '宿舍楼', '房间号', '床位数', '合班标记']
    col_widths = [10, 9, 18, 9, 9, 14]

    for grade, genders in tree.items():
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
        c = ws.cell(row=row, column=1, value=f'▌ {grade}')
        c.font = Font(bold=True, size=12)
        c.alignment = Alignment(horizontal='left')
        row += 1

        for gender, classes in genders.items():
            gender_label = '男生' if gender == '男' else '女生'
            gender_fill = PatternFill(start_color='E3F2FD', end_color='E3F2FD', fill_type='solid') if gender == '男' else PatternFill(start_color='FCE4EC', end_color='FCE4EC', fill_type='solid')

            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
            c = ws.cell(row=row, column=1, value=f'{gender_label}')
            c.font = subtitle_font
            c.fill = gender_fill
            row += 1

            for ci, (col_name, width) in enumerate(zip(cols, col_widths), 1):
                c = ws.cell(row=row, column=ci, value=col_name)
                c.font = header_font
                c.fill = header_fill
                c.border = thin_border
                c.alignment = center_align
                ws.column_dimensions[get_column_letter(ci)].width = width
            row += 1

            for class_name, room_list in classes.items():
                boarders = class_totals.get((grade, class_name, gender), 0)
                room_count = len(room_list)
                start_row_class = row

                for ri, room in enumerate(room_list):
                    r = row
                    if ri == 0:
                        c = ws.cell(row=r, column=1, value=grade + class_name)
                        ws.merge_cells(start_row=r, start_column=1, end_row=r + room_count - 1, end_column=1)
                        c.font = Font(bold=True)
                        c.alignment = center_align
                        c = ws.cell(row=r, column=2, value=boarders)
                        ws.merge_cells(start_row=r, start_column=2, end_row=r + room_count - 1, end_column=2)
                        c.font = Font(bold=True)
                        c.alignment = center_align

                    ws.cell(row=r, column=3, value=room.building).alignment = left_align
                    ws.cell(row=r, column=4, value=room.room_number).alignment = center_align
                    ws.cell(row=r, column=5, value=room.capacity).alignment = center_align
                    combined = room.combined_class if room.combined_class and room.combined_class.strip() else ''
                    ws.cell(row=r, column=6, value=combined).alignment = center_align

                    for ci in range(1, 7):
                        ws.cell(row=r, column=ci).border = thin_border
                    row += 1

                # 小计行
                cls_beds = sum(r.capacity for r in room_list)
                c = ws.cell(row=row, column=4, value=f'小计：{room_count}间')
                c.font = sum_font
                c.alignment = Alignment(horizontal='right')
                c = ws.cell(row=row, column=5, value=f'{cls_beds}床')
                c.font = sum_font
                c.alignment = center_align
                for ci in range(1, 7):
                    ws.cell(row=row, column=ci).border = thin_border
                    ws.cell(row=row, column=ci).fill = PatternFill(start_color='F2F2F2', end_color='F2F2F2', fill_type='solid')
                row += 1

            row += 1  # 性别间空行

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    from flask import send_file
    filename = f'宿舍分配报表_{grade_filter or "全部"}.xlsx'
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                     as_attachment=True, download_name=filename)


