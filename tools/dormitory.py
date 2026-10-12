# -*- coding: utf-8 -*-
"""宿舍工具：宿舍楼与房间清单、宿舍统计、学生住宿信息。

宿舍模块没有 service 层（只有分配算法），因此这里直接查模型；
床位占用数一律用一次 GROUP BY 批量取回，避免房间列表的 N+1 查询。

数据范围（2026-10-11 三轮审计修正）：房间/楼/统计一律走
app.utils.dormitory_scope.room_scope_query（宿舍范围唯一口径，与网页端
/dormitory/rooms/、宿舍统计页同源）。此前房间与楼清单无任何范围过滤
（持 dormitory.view 即可枚举全校房间），统计另用一份 class_allowed 自拼实现、
对任课教师比网页端更宽——两处均为越权，本批已统一。
"""
from sqlalchemy import func

from ..core import guards, respond, toolkit


def _scoped_rooms(user):
    """按账号宿舍范围收窄的 Room 基础查询（唯一口径，禁止再自拼判定）。"""
    from app.models.room import Room
    from app.utils.dormitory_scope import room_scope_query
    return room_scope_query(Room.query.filter(Room.is_active.is_(True)), user)

_ROOM_FIELDS = ('id', 'building', 'room_number', 'floor', 'gender',
                'capacity', 'grade', 'class_name', 'is_active')


def _occupancy_map():
    """{room_id: 已入住人数}——一次聚合查询取回全部房间占用数。"""
    from app.extensions import db
    from app.models.room import BedAssignment
    rows = (db.session.query(BedAssignment.room_id, func.count(BedAssignment.id))
            .filter(BedAssignment.student_id.isnot(None))
            .group_by(BedAssignment.room_id).all())
    return {room_id: count for room_id, count in rows}


def _buildings(user):
    from app.models.room import Room
    rows = (_scoped_rooms(user).with_entities(Room.building, func.count(Room.id))
            .group_by(Room.building).order_by(Room.building).all())
    return {'count': len(rows),
            'buildings': [{'building': b or '(未命名)', 'room_count': n} for b, n in rows]}


def _rooms(user, building, grade, class_name, gender, page, per_page):
    from app.models.room import Room
    query = _scoped_rooms(user)
    if building:
        query = query.filter(Room.building == building)
    if grade:
        query = query.filter(Room.grade == grade)
    if class_name:
        query = query.filter(Room.class_name.like(f'%{class_name}%'))
    if gender:
        query = query.filter(Room.gender == gender)
    total = query.count()
    per_page = max(1, min(int(per_page or 50), 200))
    page = max(1, int(page or 1))
    rooms = (query.order_by(Room.building, Room.floor, Room.room_number)
             .offset((page - 1) * per_page).limit(per_page).all())
    occ = _occupancy_map()
    items = []
    for room in rooms:
        item = respond.pick(room, _ROOM_FIELDS)
        item['occupied'] = occ.get(room.id, 0)
        items.append(item)
    return {'total': total, 'page': page,
            'pages': (total + per_page - 1) // per_page if per_page else 0,
            'rooms': items}


def _stats(user):
    """宿舍总览统计（限定在账号可见房间范围内）。

    2026-10-11 三轮审计：原先自行用 class_allowed 拼装范围判定，对任课教师
    按"任教年级"放行整年级，比网页端统计页（只认 UserClassLink 班对）更宽；
    现统一走 room_scope_query + scope_kind，口径与网页端一致。
    """
    from app.models.room import BedAssignment, Room
    from app.utils.dormitory_scope import scope_kind
    scoped = _scoped_rooms(user)
    total_rooms = scoped.count()
    total_beds = int(scoped.with_entities(
        func.coalesce(func.sum(Room.capacity), 0)).scalar() or 0)
    # 已住床位：在可见房间内 join 床位分配统计（一次查询，避免逐个房间 N+1）
    occupied = (scoped.join(BedAssignment, BedAssignment.room_id == Room.id)
                .filter(BedAssignment.student_id.isnot(None)).count())
    return {'scope': '全校' if scope_kind(user) == 'school' else '仅本人数据范围',
            'total_rooms': total_rooms, 'total_beds': total_beds,
            'occupied_beds': occupied, 'empty_beds': max(total_beds - occupied, 0),
            'occupancy_rate': round(occupied / total_beds * 100, 1) if total_beds else 0.0}


def _accommodation(user, student_no):
    from app.models.room import BedAssignment, Room, StudentAccommodation
    student = guards.find_student(student_no)
    # 2026-10-12 审计修复：住宿信息属宿舍数据，范围与网页端住宿检索页同源
    # （app/utils/student_scope.apply_student_scope），不再用成绩口径 student_scope
    guards.student_list_scope(user, student_no)
    acc = StudentAccommodation.query.filter_by(student_id=student.id).first()
    bed = BedAssignment.query.filter_by(student_id=student.id).first()
    result = {
        'student': respond.pick(student, ('student_number', 'name', 'grade', 'class_name')),
        'accommodation': respond.pick(acc, ('boarding_type', 'day_student_type',
                                            'textbook', 'teacher_notes')) if acc else None,
        'bed': None,
    }
    if bed:
        room = Room.query.filter_by(id=bed.room_id).first()
        result['bed'] = {
            'bed_number': bed.bed_number,
            'assigned_at': bed.assigned_at.isoformat() if bed.assigned_at else None,
            'room': respond.pick(room, _ROOM_FIELDS) if room else None,
        }
    return result


def register(server):
    """把本模块的工具注册到 MCP 服务。"""

    @server.tool(name='list_dorm_buildings',
                 description='列出宿舍楼清单及各楼房间数')
    async def list_dorm_buildings() -> str:
        return await toolkit.db_call(_buildings, tool='list_dorm_buildings',
                                     perm='dormitory.view')

    @server.tool(name='list_dorm_rooms',
                 description='查询宿舍房间（分页）：可按宿舍楼 building、年级 grade、'
                             '班级 class_name、性别 gender(男/女) 筛选，返回容量与已住人数')
    async def list_dorm_rooms(building: str = '', grade: str = '', class_name: str = '',
                              gender: str = '', page: int = 1, per_page: int = 50) -> str:
        params = {'building': building, 'grade': grade, 'class_name': class_name,
                  'gender': gender, 'page': page, 'per_page': per_page}
        return await toolkit.db_call(
            lambda u: _rooms(u, building, grade, class_name, gender, page, per_page),
            tool='list_dorm_rooms', params=params, perm='dormitory.view')

    @server.tool(name='get_dorm_stats',
                 description='宿舍总览统计：房间总数、床位总数、已住/空余床位、入住率')
    async def get_dorm_stats() -> str:
        return await toolkit.db_call(_stats, tool='get_dorm_stats',
                                     perm='statistics.view')

    @server.tool(name='get_student_accommodation',
                 description='查询单个学生的住宿情况：住宿类型、宿舍楼/房间/床位号')
    async def get_student_accommodation(student_no: str) -> str:
        return await toolkit.db_call(lambda u: _accommodation(u, student_no),
                                     tool='get_student_accommodation', perm='dormitory.view',
                                     params={'student_no': student_no})