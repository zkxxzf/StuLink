# -*- coding: utf-8 -*-
"""宿舍写工具（2026-10-11 第 2 批）：房间管理 / 床位分配 / 搬移 / 互换。

权限（与网页端路由装饰器一致）：
  · 房间管理（建/改/删/分配班级/合班/批量设置）→ dormitory.manage
  · 床位分配（分配/移除/搬移/互换/清空本班）  → dormitory.beds

范围校验（**写路径必须过**，否则比网页端更宽松）：
  · 房间类：rooms.py 网页端**本身没有**范围校验，但为守住"AI ≤ 网页端"的底线，
    MCP 侧统一用 dormitory_scope.class_in_scope 校验目标年级/班级；
  · 床位类：网页端 assignments.py 的 assign/unassign/move/swap/clear-class
    都用 student_in_dorm_scope（或 class_in_scope）校验，MCP 必须复刻。

业务规则（全部来自网页端，逐条复刻）：
  · 性别：学生性别须与房间性别一致（房间 gender 为「不限」时不校验）；
  · 容量：床位号须在 1..capacity 内；删房时有人不可删；
  · 跨班：非合班宿舍只能放本班学生；
  · 毕业年级：禁止对已毕业年级做床位操作；
  · 并发：用与网页端相同的原子条件 UPDATE（受影响行数 != 1 即失败）。

刻意未开放：全校自动分配（auto-assign-all，网页端无范围校验且影响面过大）、
Excel 导入（需文件通道）、整房间互换（涉及批量床位重排，风险高）。
"""
from datetime import datetime

from ..core import guards, respond, toolkit

_ROOM_FIELDS = ('id', 'building', 'room_number', 'gender', 'floor', 'capacity',
                'grade', 'class_name', 'combined_class', 'notes', 'is_active')
_BED_FIELDS = ('id', 'room_id', 'bed_number', 'student_id', 'assigned_by',
               'assigned_at')


def _room_or_raise(room_id):
    from app.models.room import Room
    room = Room.query.get(int(room_id)) if room_id else None
    if room is None:
        raise ValueError(f'宿舍不存在：{room_id}')
    return room


def _bed_or_raise(bed_id):
    from app.models.room import BedAssignment
    bed = BedAssignment.query.get(int(bed_id)) if bed_id else None
    if bed is None:
        raise ValueError(f'床位不存在：{bed_id}')
    return bed


def _graduated_grades():
    from app.utils.helpers import get_graduated_grades
    return set(get_graduated_grades() or [])


def _check_room_fit(room, student):
    """性别 + 跨班两项校验（与网页端 assign/move 同口径）。"""
    if room.gender and room.gender != '不限' and student.gender != room.gender:
        raise ValueError(
            f'性别不匹配：{student.name} 是{student.gender}生，'
            f'该房间是{room.gender}生宿舍')
    if not room.is_combined:
        room_classes = (room.class_name or '').replace('+', ' ').split()
        if student.class_name not in room_classes:
            raise ValueError(
                f'班级不匹配：{student.name} 是{student.class_name}，'
                f'该房间属于{room.class_name or "未分配"}')


def _check_graduated(student, action='操作'):
    if student.grade in _graduated_grades():
        raise ValueError(f'{student.name} 所在 {student.grade} 已毕业，无法{action}床位')


def _log(user, action, target_id, detail):
    from app.utils.helpers import log_operation
    log_operation(user, action, '床位分配', target_id, detail + '（MCP）',
                  module='dormitory')


def _record(action, student, room, bed_number, user, detail=None):
    """写床位变迁历史（跨库 history，静默失败不阻塞）。"""
    from app.services.history_service import record_assignment
    record_assignment(action, student, room, bed_number, operator=user,
                      detail=detail)


# ══════════════════════════════════════════════════════════════════════
# 床位操作（dormitory.beds）
# ══════════════════════════════════════════════════════════════════════

def _assign(user, student_id, bed_id):
    """学生 → 空床位（原子占用，防并发）。"""
    from app.extensions import db
    from app.models.room import BedAssignment
    from sqlalchemy import and_, exists

    student = guards.dorm_student_scope(user, student_id)
    bed = _bed_or_raise(bed_id)
    if bed.student_id:
        raise ValueError('该床位已有人')
    _check_graduated(student, '分配')
    # 一个学生只能占一张床（网页端同口径）
    existing = BedAssignment.query.filter(
        BedAssignment.student_id == student.id).first()
    if existing:
        existing_room = _room_or_raise(existing.room_id)
        raise ValueError(f'该学生已有床位（{existing_room.display_name} '
                         f'{existing.bed_number}床）')
    room = _room_or_raise(bed.room_id)
    _check_room_fit(room, student)

    updated = db.session.query(BedAssignment).filter(and_(
        BedAssignment.id == bed.id,
        BedAssignment.student_id.is_(None),
        ~exists().where(BedAssignment.student_id == student.id),
    )).update({'student_id': student.id, 'assigned_by': user.id,
               'assigned_at': datetime.now()}, synchronize_session=False)
    if updated != 1:
        db.session.rollback()
        raise ValueError('床位状态已变化（可能已被其他用户操作），请重试')
    _record('assign', student, room, bed.bed_number, user)
    _log(user, '分配床位', bed.id,
         f'{student.name} → {room.display_name} {bed.bed_number}床')
    db.session.commit()
    return {'assigned': True, 'bed': respond.pick(bed, _BED_FIELDS),
            'message': f'{student.name} 已分配到 {room.display_name} '
                       f'{bed.bed_number}床'}


def _unassign(user, student_id):
    """移除学生的床位。"""
    from app.extensions import db
    from app.models.room import BedAssignment

    student = guards.dorm_student_scope(user, student_id)
    bed = BedAssignment.query.filter_by(student_id=student.id).first()
    if bed is None:
        raise ValueError(f'{student.name} 当前没有床位')
    room = _room_or_raise(bed.room_id)
    bed.student_id = None
    bed.assigned_by = None
    bed.assigned_at = None
    _record('unassign', student, room, bed.bed_number, user)
    _log(user, '移除床位', bed.id,
         f'{student.name} 退出 {room.display_name} {bed.bed_number}床')
    db.session.commit()
    return {'unassigned': True, 'bed': respond.pick(bed, _BED_FIELDS),
            'message': f'{student.name} 已退出 {room.display_name} '
                       f'{bed.bed_number}床'}


def _move(user, student_id, to_bed_id):
    """搬移：原床 → 目标空床（两步原子更新）。"""
    from app.extensions import db
    from app.models.room import BedAssignment

    student = guards.dorm_student_scope(user, student_id)
    to_bed = _bed_or_raise(to_bed_id)
    from_bed = BedAssignment.query.filter_by(student_id=student.id).first()
    if from_bed is None:
        raise ValueError(f'{student.name} 当前没有床位，无法搬移')
    if to_bed.student_id:
        raise ValueError('目标床位已有人')
    if from_bed.id == to_bed.id:
        raise ValueError('目标床位与当前床位相同')
    _check_graduated(student, '搬移')
    room = _room_or_raise(to_bed.room_id)
    _check_room_fit(room, student)
    from_room = _room_or_raise(from_bed.room_id)

    now = datetime.now()
    r1 = db.session.query(BedAssignment).filter(and_(
        BedAssignment.id == to_bed.id,
        BedAssignment.student_id.is_(None))).update(
        {'student_id': student.id, 'assigned_by': user.id,
         'assigned_at': now}, synchronize_session=False)
    r2 = db.session.query(BedAssignment).filter(and_(
        BedAssignment.id == from_bed.id,
        BedAssignment.student_id == student.id)).update(
        {'student_id': None, 'assigned_by': None, 'assigned_at': None},
        synchronize_session=False)
    if r1 != 1 or r2 != 1:
        db.session.rollback()
        raise ValueError('床位状态已变化（可能已被其他用户操作），请重试')
    _record('move_out', student, from_room, from_bed.bed_number, user,
            {'to_room': room.display_name, 'to_bed': to_bed.bed_number})
    _record('move_in', student, room, to_bed.bed_number, user,
            {'from_room': from_room.display_name, 'from_bed': from_bed.bed_number})
    _log(user, '搬移床位', to_bed.id,
         f'{student.name} {from_room.display_name}{from_bed.bed_number}床 → '
         f'{room.display_name}{to_bed.bed_number}床')
    db.session.commit()
    return {'moved': True, 'bed': respond.pick(to_bed, _BED_FIELDS),
            'message': f'{student.name} 已搬到 {room.display_name} '
                       f'{to_bed.bed_number}床'}


def _swap_beds(user, student_a_id, student_b_id):
    """两张床互换（双方均须在范围内，且互换后性别/班级仍匹配）。"""
    from app.extensions import db
    from app.models.room import BedAssignment

    a = guards.dorm_student_scope(user, student_a_id)
    b = guards.dorm_student_scope(user, student_b_id)
    if a.id == b.id:
        raise ValueError('不能与同一名学生互换')
    bed_a = BedAssignment.query.filter_by(student_id=a.id).first()
    bed_b = BedAssignment.query.filter_by(student_id=b.id).first()
    if not bed_a or not bed_b:
        raise ValueError('两名学生都必须已有床位才能互换')
    for st in (a, b):
        if st.grade in _graduated_grades():
            raise ValueError(f'{st.name} 所在 {st.grade} 已毕业，无法互换')
    room_a, room_b = _room_or_raise(bed_a.room_id), _room_or_raise(bed_b.room_id)
    _check_room_fit(room_b, a)      # a 换到 b 的房间
    _check_room_fit(room_a, b)      # b 换到 a 的房间

    now = datetime.now()
    r1 = db.session.query(BedAssignment).filter(and_(
        BedAssignment.id == bed_a.id,
        BedAssignment.student_id == a.id)).update(
        {'student_id': b.id, 'assigned_by': user.id, 'assigned_at': now},
        synchronize_session=False)
    r2 = db.session.query(BedAssignment).filter(and_(
        BedAssignment.id == bed_b.id,
        BedAssignment.student_id == b.id)).update(
        {'student_id': a.id, 'assigned_by': user.id, 'assigned_at': now},
        synchronize_session=False)
    if r1 != 1 or r2 != 1:
        db.session.rollback()
        raise ValueError('床位状态已变化（可能已被其他用户操作），请重试')
    _record('swap', a, room_b, bed_b.bed_number, user, {'with': b.name})
    _record('swap', b, room_a, bed_a.bed_number, user, {'with': a.name})
    _log(user, '互换床位', bed_a.id, f'{a.name} ↔ {b.name}')
    db.session.commit()
    return {'swapped': True,
            'message': f'{a.name} 与 {b.name} 的床位已互换'}


def _clear_class(user, grade, class_name):
    """清空本班全部床位（对齐网页端 assignments.py::clear_class）。"""
    from app.extensions import db
    from app.models.room import BedAssignment, Room
    from app.models.student import Student

    guards.dorm_class_scope(user, grade, class_name)
    # 2026-10-12 审计修复：补网页端有的两项——
    # ① 毕业年级禁止操作床位；② 逐床写变迁历史（原实现只清空、无追溯）
    if grade in _graduated_grades():
        raise ValueError(f'{grade} 已毕业，无法操作床位分配')
    students = Student.query.filter_by(grade=grade, class_name=class_name).all()
    ids = [s.id for s in students]
    if not ids:
        raise ValueError(f'班级 {grade}{class_name} 没有学生')
    beds = BedAssignment.query.filter(BedAssignment.student_id.in_(ids)).all()
    if not beds:
        return {'cleared': 0, 'message': f'{grade}{class_name} 当前没有已分配的床位'}
    # 批量取学生与房间，避免逐床查询（网页端同款 N+1 优化）
    stu_ids = {b.student_id for b in beds if b.student_id}
    room_ids = {b.room_id for b in beds if b.room_id}
    stu_map = {s.id: s for s in Student.query.filter(
        Student.id.in_(stu_ids)).all()} if stu_ids else {}
    room_map = {r.id: r for r in Room.query.filter(
        Room.id.in_(room_ids)).all()} if room_ids else {}
    for bed in beds:
        stu, room = stu_map.get(bed.student_id), room_map.get(bed.room_id)
        if stu and room:
            _record('clear', stu, room, bed.bed_number, user)
        bed.student_id = None
        bed.assigned_by = None
        bed.assigned_at = None
    _log(user, '清空班级床位', 0, f'{grade}{class_name} 清空 {len(beds)} 个床位')
    db.session.commit()
    return {'cleared': len(beds),
            'message': f'已清空 {grade}{class_name} 的 {len(beds)} 个床位'}


# ══════════════════════════════════════════════════════════════════════
# 房间管理（dormitory.manage）
# ══════════════════════════════════════════════════════════════════════

def _create_room(user, building, room_number, gender, floor, capacity,
                 grade, class_name, notes):
    from app.extensions import db
    from app.models.room import BedAssignment, Room
    from app.utils.helpers import log_operation

    if not (building or '').strip() or not (room_number or '').strip():
        raise ValueError('楼栋与房间号必填')
    if gender not in ('男', '女', '不限'):
        raise ValueError('gender 只能是 男 / 女 / 不限')
    capacity = int(capacity or 8)
    if not 1 <= capacity <= 20:
        raise ValueError('床位数须在 1~20 之间')
    if grade or class_name:
        guards.dorm_class_scope(user, grade or '', class_name or '')
    exists = Room.query.filter_by(building=building.strip(),
                                  room_number=room_number.strip()).first()
    if exists:
        raise ValueError(f'宿舍已存在：{building}{room_number}')

    room = Room(building=building.strip(), room_number=room_number.strip(),
                gender=gender, floor=int(floor or 1), capacity=capacity,
                grade=(grade or '').strip() or None,
                class_name=(class_name or '').strip() or None,
                notes=(notes or '').strip() or None, is_active=True)
    db.session.add(room)
    db.session.flush()
    # 与网页端 create 一致：自动建床
    for num in range(1, capacity + 1):
        db.session.add(BedAssignment(room_id=room.id, bed_number=num))
    log_operation(user, '新增', '宿舍', room.id,
                  f'新建 {room.display_name}（{capacity}床，MCP）',
                  module='dormitory')
    db.session.commit()
    return {'created': True, 'room': respond.pick(room, _ROOM_FIELDS),
            'message': f'已创建 {room.display_name}，并生成 {capacity} 个床位'}


def _adjust_beds(room, old_capacity, new_capacity):
    """按容量增减床位行（复刻网页端 rooms.py::_adjust_beds，去掉 flash）。

    网页端改容量时会同步调整 BedAssignment 行；MCP 原先漏了这一步，
    会导致「容量 8 但只有 6 个床位行」的脏数据。有学生的床位不删（只提示）。
    """
    from app.extensions import db
    from app.models.room import BedAssignment

    skipped = []
    if new_capacity > old_capacity:
        for bed_num in range(old_capacity + 1, new_capacity + 1):
            exists = BedAssignment.query.filter_by(room_id=room.id,
                                                   bed_number=bed_num).first()
            if not exists:
                db.session.add(BedAssignment(room_id=room.id, bed_number=bed_num))
    elif new_capacity < old_capacity:
        for bed_num in range(new_capacity + 1, old_capacity + 1):
            bed = BedAssignment.query.filter_by(room_id=room.id,
                                                bed_number=bed_num).first()
            if bed is None:
                continue
            if bed.student_id:
                skipped.append(bed_num)      # 有学生，保留（与网页端一致）
            else:
                db.session.delete(bed)
    return skipped


def _update_room(user, room_id, gender, capacity, grade, class_name, notes):
    from app.extensions import db
    from app.models.room import BedAssignment, Room
    from app.utils.helpers import log_operation

    room = _room_or_raise(room_id)
    # 当前归属须在范围内
    if room.grade or room.class_name:
        guards.dorm_class_scope(user, room.grade or '', room.class_name or '')
    # 2026-10-12 审计修复：**新**归属同样须在范围内——否则可绕过
    # assign_room_class 的范围守卫（未分班房间更是零校验）直接把房间改成范围外班级
    if grade is not None or class_name is not None:
        guards.dorm_class_scope(
            user,
            (grade if grade is not None else room.grade) or '',
            (class_name if class_name is not None else room.class_name) or '')

    bed_count = BedAssignment.query.filter(
        BedAssignment.room_id == room.id,
        BedAssignment.student_id.isnot(None)).count()
    has_assignment = bool((room.class_name or '').strip())
    has_beds = bed_count > 0
    gender_changed = bool(gender) and gender != room.gender
    capacity_changed = bool(capacity) and int(capacity) != room.capacity
    # 与网页端 rooms.py:167 一致：**已分班 或 已入住**时禁止改性别/床位数
    # （原实现只看入住人数，已分班但空的房间可改容量 → 比网页端宽松）
    if (has_assignment or has_beds) and (gender_changed or capacity_changed):
        reasons = []
        if has_assignment:
            reasons.append(f'已分配给班级「{room.class_name}」')
        if has_beds:
            reasons.append(f'已有 {bed_count} 名学生入住')
        raise ValueError(f'该宿舍{"、".join(reasons)}，请先清除分配和床位后再修改性别或床位数')

    old_capacity = room.capacity
    skipped_beds = []
    if capacity and int(capacity) != old_capacity:
        skipped_beds = _adjust_beds(room, old_capacity, int(capacity))
    if gender:
        room.gender = gender
    if capacity:
        room.capacity = int(capacity)
    if grade is not None:
        room.grade = (grade or '').strip() or None
    if class_name is not None:
        room.class_name = (class_name or '').strip() or None
    if notes is not None:
        room.notes = (notes or '').strip() or None
    log_operation(user, '修改', '宿舍', room.id,
                  f'编辑 {room.display_name}（MCP）', module='dormitory')
    db.session.commit()
    msg = f'已更新 {room.display_name}'
    if skipped_beds:
        msg += f'；第 {"、".join(map(str, skipped_beds))} 床有学生入住，未删除'
    return {'updated': True, 'room': respond.pick(room, _ROOM_FIELDS),
            'message': msg}


def _delete_room(user, room_id):
    from app.extensions import db
    from app.models.room import BedAssignment, Room
    from app.utils.helpers import log_operation

    room = _room_or_raise(room_id)
    if room.grade or room.class_name:
        guards.dorm_class_scope(user, room.grade or '', room.class_name or '')
    occupied = BedAssignment.query.filter(
        BedAssignment.room_id == room.id,
        BedAssignment.student_id.isnot(None)).count()
    if occupied:
        raise ValueError(f'{room.display_name} 还有 {occupied} 人入住，不能删除')
    BedAssignment.query.filter_by(room_id=room.id).delete()
    db.session.delete(room)
    log_operation(user, '删除', '宿舍', room_id,
                  f'删除 {room.display_name}（MCP）', module='dormitory')
    db.session.commit()
    return {'deleted': True, 'room_id': room_id,
            'message': f'已删除 {room.display_name}'}


def _assign_room_class(user, room_id, grade, class_name):
    """给房间分配年级/班级（留空表示取消分配）。"""
    from app.extensions import db
    from app.utils.helpers import log_operation

    room = _room_or_raise(room_id)
    if grade or class_name:
        guards.dorm_class_scope(user, grade, class_name)
    room.grade = (grade or '').strip() or None
    room.class_name = (class_name or '').strip() or None
    log_operation(user, '分配', '宿舍', room.id,
                  f'{room.display_name} → {room.grade or ""}'
                  f'{room.class_name or "取消分配"}（MCP）', module='dormitory')
    db.session.commit()
    return {'updated': True, 'room': respond.pick(room, _ROOM_FIELDS),
            'message': f'{room.display_name} 已分配至 '
                       f'{room.grade or ""}{room.class_name or "（取消分配）"}'}


def register(server):
    """把本模块的工具注册到 MCP 服务。"""

    # ── 床位（dormitory.beds）────────────────────────────────────────
    @server.tool(name='assign_bed',
                 description='【写操作】把学生分配到指定空床位。'
                             '会校验：学生在你的宿舍管理范围内、未毕业、尚未有床位、'
                             '性别与房间匹配、非合班房须为本班')
    async def assign_bed(student_id: int, bed_id: int) -> str:
        return await toolkit.db_write(
            lambda u: _assign(u, student_id, bed_id), tool='assign_bed',
            params={'student_id': student_id, 'bed_id': bed_id},
            perm='dormitory.beds')

    @server.tool(name='unassign_bed',
                 description='【写操作】让学生退出当前床位（床位释放为空）')
    async def unassign_bed(student_id: int) -> str:
        return await toolkit.db_write(
            lambda u: _unassign(u, student_id), tool='unassign_bed',
            params={'student_id': student_id}, perm='dormitory.beds')

    @server.tool(name='move_bed',
                 description='【写操作】把学生从当前床位搬到另一个空床位（to_bed_id）')
    async def move_bed(student_id: int, to_bed_id: int) -> str:
        return await toolkit.db_write(
            lambda u: _move(u, student_id, to_bed_id), tool='move_bed',
            params={'student_id': student_id, 'to_bed_id': to_bed_id},
            perm='dormitory.beds')

    @server.tool(name='swap_beds',
                 description='【写操作】两名学生的床位互换（双方都须已有床位，'
                             '且互换后性别与班级仍匹配目标房间）')
    async def swap_beds(student_a_id: int, student_b_id: int) -> str:
        return await toolkit.db_write(
            lambda u: _swap_beds(u, student_a_id, student_b_id),
            tool='swap_beds',
            params={'student_a_id': student_a_id, 'student_b_id': student_b_id},
            perm='dormitory.beds')

    @server.tool(name='clear_class_beds',
                 description='【写操作】清空某班全部学生的床位'
                             '（班级须在你的宿舍管理范围内）')
    async def clear_class_beds(grade: str, class_name: str) -> str:
        return await toolkit.db_write(
            lambda u: _clear_class(u, grade, class_name),
            tool='clear_class_beds',
            params={'grade': grade, 'class_name': class_name},
            perm='dormitory.beds')

    # ── 房间（dormitory.manage）──────────────────────────────────────
    @server.tool(name='create_room',
                 description='【写操作】新建宿舍并自动生成床位。'
                             'gender 取 男/女/不限，capacity 1~20')
    async def create_room(building: str, room_number: str, gender: str,
                          floor: int = 1, capacity: int = 8, grade: str = '',
                          class_name: str = '', notes: str = '') -> str:
        params = {'building': building, 'room_number': room_number,
                  'gender': gender, 'capacity': capacity}
        return await toolkit.db_write(
            lambda u: _create_room(u, building, room_number, gender, floor,
                                   capacity, grade, class_name, notes),
            tool='create_room', params=params, perm='dormitory.manage')

    @server.tool(name='update_room',
                 description='【写操作】编辑宿舍（性别/床位数/年级/班级/备注）；'
                             '有人入住或已分配班级时不可改性别与床位数')
    async def update_room(room_id: int, gender: str = '', capacity: int = 0,
                          grade: str = None, class_name: str = None,
                          notes: str = None) -> str:
        params = {'room_id': room_id, 'gender': gender, 'capacity': capacity}
        return await toolkit.db_write(
            lambda u: _update_room(u, room_id, gender, capacity or None, grade,
                                   class_name, notes),
            tool='update_room', params=params, perm='dormitory.manage')

    @server.tool(name='delete_room',
                 description='【写操作】删除宿舍（还有人入住时不可删，床位一并清理）')
    async def delete_room(room_id: int) -> str:
        return await toolkit.db_write(lambda u: _delete_room(u, room_id),
                                      tool='delete_room',
                                      params={'room_id': room_id},
                                      perm='dormitory.manage')

    @server.tool(name='assign_room_class',
                 description='【写操作】给宿舍分配年级/班级；grade 与 class_name '
                             '都留空表示取消分配')
    async def assign_room_class(room_id: int, grade: str = '',
                                class_name: str = '') -> str:
        return await toolkit.db_write(
            lambda u: _assign_room_class(u, room_id, grade, class_name),
            tool='assign_room_class',
            params={'room_id': room_id, 'grade': grade, 'class_name': class_name},
            perm='dormitory.manage')
