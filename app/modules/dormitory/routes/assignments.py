# StuLink v1.8.0 2026-08-13（并发安全 + 合班床位份额过滤）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for, abort
from flask_login import login_required, current_user
from datetime import datetime
from app.extensions import db
from app.models import Room, BedAssignment, Student, StudentAccommodation, UserClassLink
from app.utils.decorators import perm_required
from app.utils.helpers import get_dict_values, get_active_grades, log_operation, get_graduated_grades
# 2026-10-11 审计：宿舍数据范围唯一口径（班级归属 / 学生归属 / 房间与学生查询）
from app.utils.dormitory_scope import (class_in_scope, resolve_scope,
                                       room_scope_query, student_in_dorm_scope,
                                       student_scope_query)
from app.services.history_service import record_assignment
from sqlalchemy import and_, exists
import threading
import json
from functools import wraps

bp = Blueprint('assignments', __name__, url_prefix='/assignments')

# 床位写操作互斥锁：串行化"读-检查-写"，防止多用户并发操作同一床位产生覆盖
# （Waitress 单进程多线程部署下有效；配合原子条件 UPDATE 双保险）
_bed_write_lock = threading.Lock()


def _bed_write_lock_decorator(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        with _bed_write_lock:
            # 结束锁外（权限检查等）建立的旧事务快照，确保锁内读取到最新数据
            # （SQLite 快照在事务内首次查询时固定，不重置会导致并发覆盖）
            db.session.rollback()
            return f(*args, **kwargs)
    return wrapper


def _homeroom_class_or_default(grade, class_name):
    """班主任场景的 (grade, class_name)：传入优先，缺失回落本人管辖班级。

    2026-10-12 P1：manage/clear_class/auto_assign 原各自内联
    `if role == 'homeroom_teacher': grade = user.grade`（强制覆盖传入值，
    漏 UserClassLink 多班与字段过期场景）；收敛为一个 helper。
    范围校验仍走 dormitory_scope.class_in_scope（唯一口径，不外移判断）。
    """
    if current_user.role == 'homeroom_teacher' and (not grade or not class_name):
        link = UserClassLink.query.filter_by(user_id=current_user.id).first()
        if link:
            grade = grade or link.grade
            class_name = class_name or link.class_name
        else:
            grade = grade or (current_user.grade or '')
            class_name = class_name or (current_user.class_name or '')
    return grade, class_name


def _unassigned_stats():
    """可见范围内「未分配床位的住校生」按年级+班级+性别聚合。

    2026-10-14 第三轮审计（SEC-01）：原实现写在 `abort(403)` 之后的死代码分支里，
    且用**全校**范围统计 → 不带参数的越权请求能直接读到它。现在统一走
    `student_scope_query`（无范围即空集，fail-closed）。

    返回 (stats, total, total_male, total_female)。
    """
    graduated = get_graduated_grades()
    boarding_ids = [sa.student_id for sa in StudentAccommodation.query.filter(
        StudentAccommodation.boarding_type == '住校'
    ).all()]
    assigned_ids = [b.student_id for b in BedAssignment.query.filter(
        BedAssignment.student_id.isnot(None)
    ).all()]
    query = student_scope_query(Student.query.filter(
        Student.id.in_(boarding_ids) if boarding_ids else False
    ))
    if graduated:
        query = query.filter(~Student.grade.in_(graduated))
    if assigned_ids:
        query = query.filter(~Student.id.in_(assigned_ids))
    rows = query.order_by(
        Student.grade.desc(), Student.class_name, Student.gender.desc()
    ).all()

    grouped = {}
    total = total_male = total_female = 0
    for s in rows:
        key = (s.grade, s.class_name)
        if key not in grouped:
            grouped[key] = {'male': 0, 'female': 0}
        if s.gender == '男':
            grouped[key]['male'] += 1
            total_male += 1
        else:
            grouped[key]['female'] += 1
            total_female += 1
        total += 1

    def _sort_key(x):
        g = (x[0][0] or '').replace('级', '')
        return (-int(g) if g.isdigit() else 0, x[0][1])

    stats = [{'grade': g, 'class_name': c, 'male': n['male'], 'female': n['female'],
              'total': n['male'] + n['female']}
             for (g, c), n in sorted(grouped.items(), key=_sort_key)]
    return stats, total, total_male, total_female


@bp.route('/manage')
@perm_required('dormitory.beds')
def manage():
    grades = get_active_grades()
    graduated = get_graduated_grades()
    grades = [g for g in grades if g not in graduated]

    # 2026-10-14 第三轮审计修复（SEC-01）：
    # ① 范围解析前置：无任何宿舍范围 → 403（fail-closed，与 /statistics/ 同口径）；
    # ② 非全校账号的年级下拉只列可见年级（原先列全校，选到范围外才 403）。
    kind, scope_grades, scope_pairs = resolve_scope(current_user)
    if kind == 'none':
        abort(403)
    if kind != 'school':
        _allowed = ({g for g, _ in (scope_pairs or set())} if kind == 'class'
                    else set(scope_grades or ()))
        grades = [g for g in grades if g in _allowed]

    # 2026-10-12 P1：班级来源与范围校验统一收敛（原班主任分支只信
    # user.grade/class_name 字段且不经 class_in_scope，非班主任走 URL 参数）
    grade, class_name = _homeroom_class_or_default(
        request.args.get('grade', ''), request.args.get('class_name', ''))
    if grade and class_name and not class_in_scope(current_user, grade, class_name):
        abort(403)

    # 2026-10-14 第三轮审计修复（SEC-01）：这段「非班主任必须指定班级」原先写在
    # abort(403) 之后 → 永不执行的死代码；于是不带参数的请求会直落下方全库查询，
    # 越界读「全校房间 + 全校未分配住校生名单」。现在与 abort 同级、真实可达，
    # 且名单与统计都按可见范围收敛（_unassigned_stats → student_scope_query）。
    if not grade or not class_name:
        stats, total, total_male, total_female = _unassigned_stats()
        return render_template('dormitory/assignments/manage.html',
                               rooms_data=[], unassigned_students=[],
                               current_grade='', current_class='',
                               grades=grades, need_filter=True,
                               unassigned_stats=stats,
                               total_unassigned=total,
                               total_unassigned_male=total_male,
                               total_unassigned_female=total_female)

    # 查找已分配给该班级的宿舍
    # 2026-10-14（SEC-01）：套 room_scope_query，与 rooms / statistics 同口径
    room_query = room_scope_query(Room.query.filter_by(is_active=True))
    if grade:
        room_query = room_query.filter_by(grade=grade)
    if class_name:
        room_query = room_query.filter(
            db.or_(
                Room.class_name == class_name,
                Room.class_name.like(class_name + '+%'),
                Room.class_name.like('%+' + class_name + '+%'),
                Room.class_name.like('%+' + class_name),
                Room.combined_class.like(class_name + '+%'),
                Room.combined_class.like('%+' + class_name + '+%'),
                Room.combined_class.like('%+' + class_name)
            )
        )
    rooms = room_query.order_by(
        Room.gender.desc(),  # 女先男后
        Room.building,
        Room.room_number
    ).all()

    # 确保这些房间的 BedAssignment 记录完整
    if rooms:
        from app.modules.dormitory.services.room_assignment_v7 import _ensure_room_beds
        _ensure_room_beds([r.id for r in rooms])

    # 检查班级是否有宿舍分配
    no_rooms = len(rooms) == 0
    total_capacity = sum(r.capacity for r in rooms)
    graduated = get_graduated_grades()
    
    boarding_ids = [sa.student_id for sa in StudentAccommodation.query.filter(
        StudentAccommodation.boarding_type == '住校'
    ).all()]
    
    base_student_query = Student.query.filter(
        Student.grade == grade,
        Student.class_name == class_name,
        Student.id.in_(boarding_ids) if boarding_ids else False
    )
    if graduated:
        base_student_query = base_student_query.filter(~Student.grade.in_(graduated))
    total_students_needed = base_student_query.count()
    beds_insufficient = (not no_rooms) and total_capacity < total_students_needed

    # 构建宿舍和床位数据
    # 2026-10-10 架构审计修复：原实现「每房间查床位 → 每床位再查学生」（双重 N+1），
    # 一栋楼几百间房时是首屏慢的主因。改为：一次性取全部床位 + 一次性取涉及学生，
    # 组装结果与原来完全一致（bed 顺序按 bed_number，student 为 None 仍保留）。
    _room_ids = [r.id for r in rooms]
    _beds_by_room = {}
    if _room_ids:
        _all_beds = BedAssignment.query.filter(
            BedAssignment.room_id.in_(_room_ids)
        ).order_by(BedAssignment.bed_number).all()
        for _b in _all_beds:
            _beds_by_room.setdefault(_b.room_id, []).append(_b)
        _student_ids = {_b.student_id for _b in _all_beds if _b.student_id}
        _student_map = {}
        if _student_ids:
            _student_map = {s.id: s for s in
                            Student.query.filter(Student.id.in_(_student_ids)).all()}
    else:
        _student_map = {}

    rooms_data = []
    for room in rooms:
        bed_list = []
        for bed in _beds_by_room.get(room.id, []):
            student = _student_map.get(bed.student_id) if bed.student_id else None
            bed_list.append({'bed': bed, 'student': student})
        rooms_data.append({'room': room, 'beds': bed_list})

    # 未分配床位的学生
    # 2026-10-14（SEC-01）：再套一层可见范围（上面 grade/class 已过 class_in_scope，
    # 这里是纵深防御，口径统一走 student_scope_query）
    student_query = student_scope_query(Student.query.filter(
        Student.id.in_(boarding_ids) if boarding_ids else False
    ))
    if graduated:
        student_query = student_query.filter(~Student.grade.in_(graduated))
    if grade:
        student_query = student_query.filter_by(grade=grade)
    # 班级必须与年级成对使用（防御：入口已校验，此处兜底防直接 URL 绕过）
    if class_name and grade:
        student_query = student_query.filter_by(class_name=class_name)

    # 排除已有床位的学生（分两步避免跨库子查询）
    assigned_ids = [b.student_id for b in BedAssignment.query.filter(
        BedAssignment.student_id.isnot(None)
    ).all()]
    unassigned_students = student_query.filter(
        ~Student.id.in_(assigned_ids) if assigned_ids else True
    ).order_by(
        Student.gender.desc(),  # 女先男后
        Student.student_number
    ).all()

    # 人数统计
    gender_base = Student.query.filter(
        Student.grade == grade,
        Student.class_name == class_name,
        Student.id.in_(boarding_ids) if boarding_ids else False
    )
    if graduated:
        gender_base = gender_base.filter(~Student.grade.in_(graduated))
    total_male = gender_base.filter_by(gender='男').count()
    total_female = gender_base.filter_by(gender='女').count()
    unassigned_male = len([s for s in unassigned_students if s.gender == '男'])
    unassigned_female = len([s for s in unassigned_students if s.gender == '女'])
    assigned_male = total_male - unassigned_male
    assigned_female = total_female - unassigned_female
    total_all = total_male + total_female
    assigned_total = assigned_male + assigned_female
    need_filter = False

    return render_template('dormitory/assignments/manage.html',
                           rooms_data=rooms_data,
                           unassigned_students=unassigned_students,
                           current_grade=grade,
                           current_class=class_name,
                           need_filter=need_filter,
                           no_rooms=no_rooms,
                           total_capacity=total_capacity,
                           total_students_needed=total_students_needed,
                           beds_insufficient=beds_insufficient,
                           total_male=total_male, total_female=total_female,
                           assigned_male=assigned_male, assigned_female=assigned_female,
                           assigned_total=assigned_total, total_all=total_all,
                           grades=grades,
                           classes=get_dict_values('class'))


@bp.route('/clear-class', methods=['POST'])
@perm_required('dormitory.beds')
@_bed_write_lock_decorator
def clear_class():
    """清除本班全部已分配床位"""
    data = request.get_json() or {}
    grade, class_name = _homeroom_class_or_default(
        data.get('grade', ''), data.get('class_name', ''))

    if not grade or not class_name:
        return jsonify({'success': False, 'message': '请指定年级和班级'}), 400

    # 2026-10-11 审计修复：清空班级床位前校验班级归属范围
    if not class_in_scope(current_user, grade, class_name):
        return jsonify({'success': False, 'message': '无权操作该班级'}), 403

    graduated = get_graduated_grades()
    if grade in graduated:
        return jsonify({'success': False, 'message': f'{grade}已毕业，无法操作床位分配'}), 400
    
    # 找到该班级有床位的所有学生
    student_ids = [s.id for s in Student.query.filter_by(grade=grade, class_name=class_name).all()]
    if not student_ids:
        return jsonify({'success': False, 'message': '该班级没有住校生'}), 400
    
    cleared_beds = BedAssignment.query.filter(
        BedAssignment.student_id.in_(student_ids)
    ).all()
    count = len(cleared_beds)
    # 2026-10-10 架构审计修复：原实现每个床位都查一次 Student + Room（双重 N+1）。
    # 清空一个班（几十人）就会几十次往返。改为一次性批量取出，
    # record_assignment 的调用对象与顺序完全不变。
    _stu_ids = {b.student_id for b in cleared_beds if b.student_id}
    _room_ids = {b.room_id for b in cleared_beds if b.room_id}
    _stu_map = {s.id: s for s in
                Student.query.filter(Student.id.in_(_stu_ids)).all()} if _stu_ids else {}
    _room_map = {r.id: r for r in
                 Room.query.filter(Room.id.in_(_room_ids)).all()} if _room_ids else {}
    for bed in cleared_beds:
        student = _stu_map.get(bed.student_id)
        room = _room_map.get(bed.room_id)
        if student and room:
            record_assignment('clear', student, room, bed.bed_number, current_user)
    if cleared_beds:
        BedAssignment.query.filter(
            BedAssignment.student_id.in_(student_ids)
        ).update({'student_id': None, 'assigned_by': None, 'assigned_at': None}, synchronize_session=False)

    log_operation(current_user, '清空班级', '床位分配', None,
                  f'{grade}{class_name} 清除 {count} 个床位', module='dormitory', severity='WARNING')
    db.session.commit()
    
    return jsonify({'success': True, 'message': f'已清除 {count} 个床位分配', 'count': count})


@bp.route('/assign', methods=['POST'])
@perm_required('dormitory.beds')
@_bed_write_lock_decorator
def assign():
    data = request.get_json()
    student_id = data.get('student_id')
    bed_id = data.get('bed_id')

    bed = BedAssignment.query.get(bed_id)
    student = Student.query.get(student_id)

    if not bed or not student:
        return jsonify({'success': False, 'message': '数据不存在'}), 400

    if bed.student_id:
        return jsonify({'success': False, 'message': '该床位已有人'}), 400

    graduated = get_graduated_grades()
    if student.grade in graduated:
        return jsonify({'success': False, 'message': f'{student.name}所在{student.grade}已毕业，无法分配床位'}), 400

    # 检查学生是否已分配床位
    existing = BedAssignment.query.filter_by(student_id=student_id).filter(
        BedAssignment.student_id.isnot(None)).first()
    if existing:
        return jsonify({'success': False, 'message': '该学生已有床位'}), 400

    # 2026-10-11 审计修复：数据范围校验（原仅约束班主任，年级长/宿管可越界分配他班学生）
    if not student_in_dorm_scope(current_user, student):
        return jsonify({'success': False, 'message': '无权分配该学生'}), 403

    # 性别校验：学生性别必须与房间性别匹配
    room = Room.query.get(bed.room_id)
    if room and room.gender and room.gender != '不限':
        if student.gender != room.gender:
            return jsonify({
                'success': False,
                'message': f'性别不匹配：{student.name}是{student.gender}生，该房间是{room.gender}生宿舍'
            }), 400

    # 跨班防护：非合班宿舍只能分配给本班学生
    is_combined = room.is_combined
    if not is_combined:
        # 检查学生是否属于该房间的班级
        room_classes = (room.class_name or '').replace('+', ' ').split()
        if student.class_name not in room_classes:
            return jsonify({
                'success': False,
                'message': f'班级不匹配：{student.name}是{student.class_name}，该房间属于{room.class_name}'
            }), 400

    # 原子占用：床位仍为空且学生仍无床时才写入成功（防并发冲突）
    updated = db.session.query(BedAssignment).filter(
        and_(
            BedAssignment.id == bed.id,
            BedAssignment.student_id.is_(None),
            ~exists().where(BedAssignment.student_id == student_id)
        )
    ).update({
        'student_id': student_id,
        'assigned_by': current_user.id,
        'assigned_at': datetime.now()
    }, synchronize_session=False)
    if updated != 1:
        db.session.rollback()
        return jsonify({'success': False, 'message': '床位状态已变化（可能已被其他用户操作），请刷新页面后重试'}), 409

    record_assignment('assign', student, room, bed.bed_number, current_user)
    log_operation(current_user, '分配床位', '床位分配', bed.id,
                  f'{student.name} → {room.display_name} {bed.bed_number}床', module='dormitory')
    db.session.commit()

    return jsonify({'success': True, 'message': f'{student.name} 已分配到 {bed.bed_number}床'})


@bp.route('/unassign', methods=['POST'])
@perm_required('dormitory.beds')
@_bed_write_lock_decorator
def unassign():
    data = request.get_json()
    bed_id = data.get('bed_id')

    bed = BedAssignment.query.get(bed_id)
    if not bed or not bed.student_id:
        return jsonify({'success': False, 'message': '该床位无人'}), 400

    student = Student.query.get(bed.student_id)
    # 2026-10-11 审计修复：取消分配前校验学生归属范围
    if student and not student_in_dorm_scope(current_user, student):
        return jsonify({'success': False, 'message': '无权操作该学生床位'}), 403
    if student:
        graduated = get_graduated_grades()
        if student.grade in graduated:
            return jsonify({'success': False, 'message': f'{student.name}所在{student.grade}已毕业，无法操作床位'}), 400

    student_name = student.name if student else ''
    room = Room.query.get(bed.room_id)
    student = Student.query.get(bed.student_id)
    # 原子移除：床位仍被占用时才写入成功（防并发冲突）
    updated = db.session.query(BedAssignment).filter(
        and_(
            BedAssignment.id == bed.id,
            BedAssignment.student_id.isnot(None)
        )
    ).update({
        'student_id': None,
        'assigned_by': None,
        'assigned_at': None
    }, synchronize_session=False)
    if updated != 1:
        db.session.rollback()
        return jsonify({'success': False, 'message': '床位状态已变化（可能已被其他用户操作），请刷新页面后重试'}), 409
    if student and room:
        record_assignment('unassign', student, room, bed.bed_number, current_user)
    log_operation(current_user, '取消分配', '床位分配', bed.id,
                  f'{student_name} 从 {room.display_name if room else "?"} {bed.bed_number}床移除', module='dormitory')
    db.session.commit()

    return jsonify({'success': True, 'message': f'{student_name} 已从该床位移除'})


@bp.route('/move', methods=['POST'])
@perm_required('dormitory.beds')
@_bed_write_lock_decorator
def move():
    """搬移学生从一个床位到另一个空床位"""
    data = request.get_json()
    student_id = data.get('student_id')
    from_bed_id = data.get('from_bed_id')
    to_bed_id = data.get('to_bed_id')

    from_bed = BedAssignment.query.get(from_bed_id)
    to_bed = BedAssignment.query.get(to_bed_id)
    student = Student.query.get(student_id)

    if not from_bed or not to_bed or not student:
        return jsonify({'success': False, 'message': '数据不存在'}), 400

    # 2026-10-11 审计修复：搬移前校验学生归属范围
    if not student_in_dorm_scope(current_user, student):
        return jsonify({'success': False, 'message': '无权搬移该学生床位'}), 403

    if from_bed.student_id != student_id:
        return jsonify({'success': False, 'message': '该学生不在此床位'}), 400

    if to_bed.student_id:
        return jsonify({'success': False, 'message': '目标床位已有人'}), 400

    graduated = get_graduated_grades()
    if student.grade in graduated:
        return jsonify({'success': False, 'message': f'{student.name}所在{student.grade}已毕业，无法搬移床位'}), 400

    # 性别校验
    room = Room.query.get(to_bed.room_id)
    if room and room.gender and room.gender != '不限':
        if student.gender != room.gender:
            return jsonify({'success': False, 'message': f'性别不匹配：{student.name}是{student.gender}生'}), 400

    # 跨班防护：非合班宿舍只能进本班学生
    is_combined = room.is_combined
    if not is_combined:
        room_classes = (room.class_name or '').replace('+', ' ').split()
        if student.class_name not in room_classes:
            return jsonify({'success': False, 'message': f'禁止跨班转移：{student.name}是{student.class_name}，目标房间属于{room.class_name}'}), 400

    # 防御：目标床与旧床不能是同一张床
    if from_bed_id == to_bed_id:
        return jsonify({'success': False, 'message': '目标床位与当前床位相同'}), 400

    # 原子搬移：目标床必须仍为空、旧床必须仍是该学生，任一不满足则整体回滚（防并发冲突）
    from_room = Room.query.get(from_bed.room_id)
    now = datetime.now()
    r1 = db.session.query(BedAssignment).filter(
        and_(BedAssignment.id == to_bed.id, BedAssignment.student_id.is_(None))
    ).update({
        'student_id': student_id,
        'assigned_by': current_user.id,
        'assigned_at': now
    }, synchronize_session=False)
    r2 = db.session.query(BedAssignment).filter(
        and_(BedAssignment.id == from_bed.id, BedAssignment.student_id == student_id)
    ).update({
        'student_id': None,
        'assigned_by': None,
        'assigned_at': None
    }, synchronize_session=False)
    if r1 != 1 or r2 != 1:
        db.session.rollback()
        return jsonify({'success': False, 'message': '床位状态已变化（可能已被其他用户操作），请刷新页面后重试'}), 409

    if from_room:
        record_assignment('move_out', student, from_room, from_bed.bed_number,
                          current_user, {'to_room': room.display_name, 'to_bed': to_bed.bed_number})
    record_assignment('move_in', student, room, to_bed.bed_number,
                      current_user, {'from_room': from_room.display_name if from_room else '?', 'from_bed': from_bed.bed_number})

    log_operation(current_user, '搬移床位', '床位分配', to_bed.id,
                  f'{student.name} {from_room.display_name if from_room else "?"}{from_bed.bed_number}床 → {room.display_name}{to_bed.bed_number}床', module='dormitory')
    db.session.commit()
    return jsonify({'success': True, 'message': f'{student.name} 已搬到 {to_bed.bed_number}床'})


@bp.route('/swap', methods=['POST'])
@perm_required('dormitory.beds')
@_bed_write_lock_decorator
def swap():
    """互换两个座位上的学生"""
    data = request.get_json()
    sid_a = data.get('student_id_a')
    bid_a = data.get('bed_id_a')
    sid_b = data.get('student_id_b')
    bid_b = data.get('bed_id_b')

    bed_a = BedAssignment.query.get(bid_a)
    bed_b = BedAssignment.query.get(bid_b)
    stu_a = Student.query.get(sid_a)
    stu_b = Student.query.get(sid_b)

    if not bed_a or not bed_b or not stu_a or not stu_b:
        return jsonify({'success': False, 'message': '数据不存在'}), 400

    if bed_a.student_id != sid_a or bed_b.student_id != sid_b:
        return jsonify({'success': False, 'message': '床位信息不匹配'}), 400

    # 2026-10-11 审计修复：互换前校验两名学生均在本账号范围内
    if not (student_in_dorm_scope(current_user, stu_a)
            and student_in_dorm_scope(current_user, stu_b)):
        return jsonify({'success': False, 'message': '无权互换该床位'}), 403

    graduated = get_graduated_grades()
    if stu_a.grade in graduated or stu_b.grade in graduated:
        return jsonify({'success': False, 'message': '已毕业年级的学生无法互换床位'}), 400

    # 性别校验（互换后各自性别匹配）
    room_a = Room.query.get(bed_a.room_id)
    room_b = Room.query.get(bed_b.room_id)
    for room, stu, name in [(room_b, stu_a, 'A'), (room_a, stu_b, 'B')]:
        if room and room.gender and room.gender != '不限':
            if stu.gender != room.gender:
                return jsonify({'success': False, 'message': f'性别不匹配：{stu.name}是{stu.gender}生，无法换到{room.gender}生宿舍'}), 400

    # 跨班防护：非合班宿舍只能进本班学生
    def check_cross_class(stu, target_room, label):
        is_comb = target_room.is_combined
        if not is_comb:
            room_classes = (target_room.class_name or '').replace('+', ' ').split()
            if stu.class_name not in room_classes:
                return f'禁止跨班互换：{stu.name}是{stu.class_name}，目标房间属于{target_room.class_name}'
        return None
    
    err = check_cross_class(stu_a, room_b, 'A')
    if err: return jsonify({'success': False, 'message': err}), 400
    err = check_cross_class(stu_b, room_a, 'B')
    if err: return jsonify({'success': False, 'message': err}), 400

    # 防御：两个床位不能是同一张床
    if bid_a == bid_b:
        return jsonify({'success': False, 'message': '不能在同一张床上互换'}), 400

    # 原子互换：两个床位的当前学生必须与请求一致，任一不满足则整体回滚（防并发冲突）
    now = datetime.now()
    r1 = db.session.query(BedAssignment).filter(
        and_(BedAssignment.id == bed_a.id, BedAssignment.student_id == sid_a)
    ).update({
        'student_id': sid_b,
        'assigned_by': current_user.id,
        'assigned_at': now
    }, synchronize_session=False)
    r2 = db.session.query(BedAssignment).filter(
        and_(BedAssignment.id == bed_b.id, BedAssignment.student_id == sid_b)
    ).update({
        'student_id': sid_a,
        'assigned_by': current_user.id,
        'assigned_at': now
    }, synchronize_session=False)
    if r1 != 1 or r2 != 1:
        db.session.rollback()
        return jsonify({'success': False, 'message': '床位状态已变化（可能已被其他用户操作），请刷新页面后重试'}), 409

    record_assignment('swap', stu_a, room_b, bed_b.bed_number,
                      current_user, {'pair_student': stu_b.name, 'pair_bed': bed_a.bed_number})
    record_assignment('swap', stu_b, room_a, bed_a.bed_number,
                      current_user, {'pair_student': stu_a.name, 'pair_bed': bed_b.bed_number})

    log_operation(current_user, '互换床位', '床位分配', bed_a.id,
                  f'{stu_a.name} ⇄ {stu_b.name}', module='dormitory')
    db.session.commit()
    return jsonify({'success': True, 'message': f'{stu_a.name} 与 {stu_b.name} 互换成功'})


@bp.route('/auto-assign', methods=['POST'])
@perm_required('dormitory.beds')
@_bed_write_lock_decorator
def auto_assign():
    """一键自动为本班学生分配床位"""
    data = request.get_json() or {}
    # 班主任自动获取自己的班级（UserClassLink 优先，老数据回落 user 字段）
    grade, class_name = _homeroom_class_or_default(
        data.get('grade', ''), data.get('class_name', ''))

    if not grade or not class_name:
        return jsonify({'success': False, 'message': '请先选择年级和班级'}), 400

    # 2026-10-11 审计修复：自动分配前校验班级归属范围
    if not class_in_scope(current_user, grade, class_name):
        return jsonify({'success': False, 'message': '无权操作该班级'}), 403

    # 1. 找到该班级的房间（包括合班宿舍，合班班级不分主次）
    # 独享房必须同年级（不同年级的同名班级是不同班级）；
    # 合班房不限年级（跨年级合班时，份额在 combined_details 中带年级，匹配时精确校验）
    rooms = Room.query.filter(
        Room.is_active == True,
        db.or_(
            db.and_(Room.grade == grade, db.or_(Room.combined_details.is_(None), Room.combined_details == '')),
            db.and_(Room.combined_details.isnot(None), Room.combined_details != ''),
        )
    ).filter(
        db.or_(
            Room.class_name == class_name,
            Room.class_name.like(class_name + '+%'),
            Room.class_name.like('%+' + class_name + '+%'),
            Room.class_name.like('%+' + class_name),
            Room.combined_class.like(class_name + '+%'),
            Room.combined_class.like('%+' + class_name + '+%'),
            Room.combined_class.like('%+' + class_name)
        )
    ).order_by(Room.building, Room.room_number).all()
    
    if not rooms:
        return jsonify({'success': False, 'message': '该班级尚未分配宿舍房间'}), 400
    
    # 1.1 确保这些房间的 BedAssignment 记录完整
    from app.modules.dormitory.services.room_assignment_v7 import _ensure_room_beds
    _ensure_room_beds([r.id for r in rooms])

    # 2. 收集所有空床位（合班宿舍按 combined_details 份额过滤，防止抢占其他班床位）
    available_beds = []
    room_bed_limits = {}  # {room_id: max_beds_for_this_class}
    for room in rooms:
        beds_in_room = BedAssignment.query.filter(
            BedAssignment.room_id == room.id,
            BedAssignment.student_id.is_(None)
        ).order_by(BedAssignment.bed_number).all()

        # 合班宿舍：只取本班份额内的空床（份额按 grade + class_name 精确匹配）
        if room.combined_details:
            import json as _json
            try:
                details = _json.loads(room.combined_details)
                class_limit = 0
                for d in details:
                    if d.get('class_name') == class_name and d.get('grade', room.grade) == grade:
                        class_limit = d.get('count', 0)
                        break
                if class_limit > 0:
                    room_bed_limits[room.id] = min(class_limit, len(beds_in_room))
                    for i, bed in enumerate(beds_in_room):
                        if i < room_bed_limits[room.id]:
                            available_beds.append(bed)
                elif details:
                    # 合班房已有份额明细但不含本班（含跨年级合班）→ 本班无份额，跳过该房间
                    # （否则会抢占其他班份额，导致后续班级无床）
                    continue
                else:
                    for bed in beds_in_room:
                        available_beds.append(bed)
            except (_json.JSONDecodeError, TypeError):
                for bed in beds_in_room:
                    available_beds.append(bed)
        else:
            for bed in beds_in_room:
                available_beds.append(bed)
    
    if not available_beds:
        return jsonify({'success': False, 'message': '该班级所有床位已满'}), 400
    
    # 3. 获取未分配床位的学生（分两步避免跨库子查询）
    assigned_ids = [b.student_id for b in BedAssignment.query.filter(
        BedAssignment.student_id.isnot(None)
    ).all()]
    
    boarding_ids = [sa.student_id for sa in StudentAccommodation.query.filter(
        StudentAccommodation.boarding_type == '住校'
    ).all()]
    
    unassigned = Student.query.filter(
        Student.id.in_(boarding_ids),
        Student.grade == grade,
        Student.class_name == class_name,
        # 排除非在读学生（学籍已转出/借读后离校）
        db.or_(
            Student.enrollment_status.is_(None),
            ~Student.enrollment_status.in_(['学籍已转出', '借读后离校'])
        )
    )
    if assigned_ids:
        unassigned = unassigned.filter(~Student.id.in_(assigned_ids))
    graduated = get_graduated_grades()
    if graduated:
        unassigned = unassigned.filter(~Student.grade.in_(graduated))
    unassigned = unassigned.order_by(
        Student.gender.desc(),  # 女生优先
        Student.student_number
    ).all()
    
    if not unassigned:
        return jsonify({'success': False, 'message': '该班级所有学生已分配完毕'}), 400
    
    # 4. 按性别分组学生和床位，确保性别匹配
    male_students = [s for s in unassigned if s.gender == '男']
    female_students = [s for s in unassigned if s.gender == '女']
    
    # 4. 按房间分组空床位（用于均衡分配）
    bed_groups = {}  # {room_id: [bed, ...]}
    for bed in available_beds:
        bed_groups.setdefault(bed.room_id, []).append(bed)
    
    # 按性别分房间
    male_rooms = []
    female_rooms = []
    unisex_rooms = []
    for rid, beds in bed_groups.items():
        room = Room.query.get(rid)
        if room:
            if room.gender == '男':
                male_rooms.append(beds)
            elif room.gender == '女':
                female_rooms.append(beds)
            else:
                unisex_rooms.append(beds)
        else:
            unisex_rooms.append(beds)
    
    # 5. 均衡分配：轮询各房间，每轮每人放入一个房间的一个床位
    assigned_names = []
    now = datetime.now()
    
    def distribute_least_filled(students, rooms_list):
        """均衡分配：每个学生放入当前人最少的房间"""
        assigned = []
        if not rooms_list or not students:
            return assigned
        # 每个房间的当前人数 + 床位列表
        room_data = [{'beds': beds, 'count': 0} for beds in rooms_list]
        total_beds = sum(len(rd['beds']) for rd in room_data)
        
        for student in students:
            # 找当前人数最少且有空床的房间
            best = None
            best_count = 9999
            for rd in room_data:
                if rd['count'] < len(rd['beds']):  # 还有空床
                    if rd['count'] < best_count:
                        best = rd
                        best_count = rd['count']
            if not best:
                break  # 无空床
            # 找第一个空床位
            for bed in best['beds']:
                if bed.student_id is None:
                    bed.student_id = student.id
                    bed.assigned_by = current_user.id
                    bed.assigned_at = now
                    best['count'] += 1
                    assigned.append(student.name)
                    # 记录历史
                    r = Room.query.get(bed.room_id)
                    if r:
                        record_assignment('auto_assign', student, r, bed.bed_number, current_user)
                    break
        return assigned
    
    # 男生均衡分配：男寝+不限性别
    assigned_names += distribute_least_filled(male_students, male_rooms + unisex_rooms)
    # 女生均衡分配：女寝+不限性别（跳过已被男生占用的床）
    assigned_names += distribute_least_filled(female_students, female_rooms + unisex_rooms)
    
    db.session.commit()
    
    log_operation(current_user, '自动分配', '床位分配', None,
                  f'{grade}{class_name} 自动分配 {len(assigned_names)} 人', module='dormitory')
    
    remaining_male = len([s for s in unassigned if s.gender == '男' and s.name not in assigned_names])
    remaining_female = len([s for s in unassigned if s.gender == '女' and s.name not in assigned_names])
    remaining = remaining_male + remaining_female
    msg = f'已分配 {len(assigned_names)} 名学生'
    if remaining > 0:
        detail = []
        if remaining_male: detail.append(f'男生{remaining_male}人')
        if remaining_female: detail.append(f'女生{remaining_female}人')
        msg += f'，剩余 {remaining} 名（{"、".join(detail)}）'
    
    return jsonify({'success': True, 'message': msg, 'count': len(assigned_names), 'remaining': remaining})


@bp.route('/auto-assign-all', methods=['POST'])
@perm_required('dormitory.beds')
@_bed_write_lock_decorator
def auto_assign_all():
    """
    一键为全校所有未分配住校生自动分配床位
    
    v4.1 合班宿舍床位分配逻辑:
    - 对每个班级，查找匹配的房间（非合班: class_name匹配; 合班: combined_class包含班级名）
    - 合班宿舍检查 combined_details 字段，按班级人数限额分配床位
    - 防止一个班级抢占合班宿舍的所有床位
    - 使用"最少填充"策略分配：优先将学生分配到当前已分配人数最少的房间
    """
    graduated = get_graduated_grades()

    # 0. 确保所有已分配宿舍的房间都有 BedAssignment 记录
    from app.modules.dormitory.services.room_assignment_v7 import _ensure_room_beds
    all_assigned_rooms = Room.query.filter(
        Room.class_name.isnot(None),
        Room.class_name != ''
    ).all()
    if all_assigned_rooms:
        _ensure_room_beds([r.id for r in all_assigned_rooms])

    # 1. 获取所有已分配床位的student_id
    assigned_ids = [b.student_id for b in BedAssignment.query.filter(
        BedAssignment.student_id.isnot(None)
    ).all()]

    # 2. 获取所有住校学生ID
    boarding_ids = [sa.student_id for sa in StudentAccommodation.query.filter(
        StudentAccommodation.boarding_type == '住校'
    ).all()]

    # 3. 获取所有未分配床位的住校学生（与分宿舍统计口径一致：排除学籍已转出/借读后离校）
    unassigned_query = Student.query.filter(
        Student.id.in_(boarding_ids) if boarding_ids else False,
        db.or_(
            Student.enrollment_status.is_(None),
            ~Student.enrollment_status.in_(['学籍已转出', '借读后离校'])
        )
    )
    if graduated:
        unassigned_query = unassigned_query.filter(~Student.grade.in_(graduated))
    if assigned_ids:
        unassigned_query = unassigned_query.filter(~Student.id.in_(assigned_ids))

    unassigned_all = unassigned_query.order_by(
        Student.grade.desc(),
        Student.class_name,
        Student.gender.desc(),
        Student.student_number
    ).all()

    if not unassigned_all:
        return jsonify({'success': False, 'message': '所有住校学生已分配完毕'}), 400

    # 4. 按年级+班级分组
    grade_class_map = {}
    for s in unassigned_all:
        key = (s.grade, s.class_name)
        if key not in grade_class_map:
            grade_class_map[key] = []
        grade_class_map[key].append(s)

    # 按年级降序、班级升序排列
    sorted_keys = sorted(grade_class_map.keys(), key=lambda x: (
        -int(x[0].replace('级', '')) if x[0].replace('级', '').isdigit() else 0,
        x[1]
    ))

    now = datetime.now()
    total_assigned = 0
    total_remaining = 0
    class_results = []

    for grade, class_name in sorted_keys:
        students = grade_class_map[(grade, class_name)]

        # 找到该班级的房间：
        # 1. 非合班宿舍：class_name 匹配（必须同年级，不同年级的同名班级是不同班级）
        # 2. 合班宿舍：class_name 是主班级名 OR combined_class 包含当前班级名
        #    （合班房不限年级：跨年级合班时份额在 combined_details 中带年级，匹配时精确校验）
        rooms = Room.query.filter(
            Room.is_active == True,
            db.or_(
                db.and_(Room.grade == grade, db.or_(Room.combined_details.is_(None), Room.combined_details == '')),
                db.and_(Room.combined_details.isnot(None), Room.combined_details != ''),
            )
        ).filter(
            db.or_(
                Room.class_name == class_name,
                Room.class_name.like(class_name + '+%'),
                Room.class_name.like('%+' + class_name + '+%'),
                Room.class_name.like('%+' + class_name),
                Room.combined_class.like(class_name + '+%'),
                Room.combined_class.like('%+' + class_name + '+%'),
                Room.combined_class.like('%+' + class_name)
            )
        ).order_by(Room.building, Room.room_number).all()

        # 不再过滤合班宿舍，让所有相关班级都能看到合班宿舍
        # 通过空床位查询来确保不会重复分配（只有 student_id is None 的床位会被使用）

        if not rooms:
            total_remaining += len(students)
            class_results.append({
                'grade': grade, 'class_name': class_name,
                'assigned': 0, 'remaining': len(students),
                'reason': '无宿舍房间'
            })
            continue

        # 收集空床位（合班宿舍按分配限额过滤）
        available_beds = []
        # 预计算：每个房间当前班级的可用床位数
        room_bed_limits = {}  # {room_id: max_beds_for_this_class}
        
        for room in rooms:
            beds_in_room = BedAssignment.query.filter(
                BedAssignment.room_id == room.id,
                BedAssignment.student_id.is_(None)
            ).order_by(BedAssignment.bed_number).all()
            
            # 检查是否为合班宿舍
            if room.combined_details:
                import json
                try:
                    details = json.loads(room.combined_details)
                    # 找到当前班级的分配限额（按 grade + class_name 精确匹配）
                    class_limit = 0
                    for d in details:
                        if d.get('class_name') == class_name and d.get('grade', room.grade) == grade:
                            class_limit = d.get('count', 0)
                            break
                    if class_limit > 0:
                        room_bed_limits[room.id] = min(class_limit, len(beds_in_room))
                        # 只加入限额内的床位
                        for i, bed in enumerate(beds_in_room):
                            if i < room_bed_limits[room.id]:
                                available_beds.append(bed)
                    elif details:
                        # 合班房已有份额明细但不含本班（含跨年级合班）→ 本班无份额，跳过该房间
                        # （否则会抢占其他班份额，导致后续班级无床）
                        continue
                    else:
                        # 无分配限额，使用全部
                        for bed in beds_in_room:
                            available_beds.append(bed)
                except (json.JSONDecodeError, TypeError):
                    for bed in beds_in_room:
                        available_beds.append(bed)
            else:
                for bed in beds_in_room:
                    available_beds.append(bed)

        if not available_beds:
            total_remaining += len(students)
            class_results.append({
                'grade': grade, 'class_name': class_name,
                'assigned': 0, 'remaining': len(students),
                'reason': '床位已满'
            })
            continue

        # 按性别分组
        male_students = [s for s in students if s.gender == '男']
        female_students = [s for s in students if s.gender == '女']

        # 按性别分房间
        bed_groups = {}
        for bed in available_beds:
            bed_groups.setdefault(bed.room_id, []).append(bed)

        # 按性别分房间
        # 2026-10-10 架构审计修复：原实现在循环内逐个 Room.query.get(rid)（N+1），
        # 房间规模下是首屏慢的主因之一。改为一次性批量取出，行为完全一致。
        male_rooms = []
        female_rooms = []
        unisex_rooms = []
        room_ids = list(bed_groups.keys())
        room_gender = {}
        if room_ids:
            rows = (db.session.query(Room.id, Room.gender)
                    .filter(Room.id.in_(room_ids)).all())
            room_gender = {rid: gender for rid, gender in rows}
        for rid, beds in bed_groups.items():
            if rid not in room_gender:
                unisex_rooms.append(beds)
                continue
            gender = room_gender[rid]
            if gender == '男':
                male_rooms.append(beds)
            elif gender == '女':
                female_rooms.append(beds)
            else:
                unisex_rooms.append(beds)

        assigned_names = []

        def distribute_least_filled(students_list, rooms_list):
            assigned = []
            if not rooms_list or not students_list:
                return assigned
            room_data = [{'beds': beds, 'count': 0} for beds in rooms_list]

            for student in students_list:
                best = None
                best_count = 9999
                for rd in room_data:
                    if rd['count'] < len(rd['beds']):
                        if rd['count'] < best_count:
                            best = rd
                            best_count = rd['count']
                if not best:
                    break
                for bed in best['beds']:
                    if bed.student_id is None:
                        bed.student_id = student.id
                        bed.assigned_by = current_user.id
                        bed.assigned_at = now
                        best['count'] += 1
                        assigned.append(student.name)
                        r = Room.query.get(bed.room_id)
                        if r:
                            record_assignment('auto_assign', student, r, bed.bed_number, current_user)
                        break
            return assigned

        assigned_names += distribute_least_filled(male_students, male_rooms + unisex_rooms)
        assigned_names += distribute_least_filled(female_students, female_rooms + unisex_rooms)

        remaining = len(students) - len(assigned_names)
        total_assigned += len(assigned_names)
        total_remaining += remaining

        class_results.append({
            'grade': grade, 'class_name': class_name,
            'assigned': len(assigned_names), 'remaining': remaining
        })

    db.session.commit()

    log_operation(current_user, '全部自动分配', '床位分配', None,
                  f'全校自动分配 {total_assigned} 人，剩余 {total_remaining} 人', module='dormitory')

    msg = f'已分配 {total_assigned} 名学生'
    if total_remaining > 0:
        msg += f'，剩余 {total_remaining} 名未分配（可能床位不足或无宿舍）'

    return jsonify({
        'success': True,
        'message': msg,
        'total_assigned': total_assigned,
        'total_remaining': total_remaining,
        'class_results': class_results
    })


