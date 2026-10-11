# -*- coding: utf-8 -*-
"""课表工具：学期课表清单、班级课表、教师课表、某日课表。

说明：网页端课表查看类页面对所有登录账号开放（路由层 _view_scope() 目前
直接返回 (None, None)，即不做班级范围过滤），MCP 侧保持一致——
不额外加范围限制，避免"网页能看、AI 不能看"的口径分歧。
"""
from ..core import guards, respond, toolkit


def _schedules(_user):
    from app.modules.academic.services import schedule_service
    rows = schedule_service.list_schedules()
    fields = ('id', 'name', 'school_year', 'term', 'status', 'is_current',
              'start_date', 'end_date', 'total_weeks')
    items = [respond.pick(s, fields) for s in rows]
    return {'count': len(items), 'schedules': items}


def _class_view(_user, schedule_id, grade, class_name, week, weekday):
    from app.modules.academic.services import schedule_service
    if not grade or not class_name:
        raise ValueError('必须提供 grade 与 class_name')
    data = schedule_service.get_class_view(
        int(schedule_id), grade, class_name,
        weekday=int(weekday) or None if weekday else None,
        week=int(week) or None if week else None)
    return _condense(data, f'{grade}{class_name}')


def _teacher_view(_user, schedule_id, teacher_uid, week, weekday):
    from app.modules.academic.services import schedule_service
    if not teacher_uid:
        raise ValueError('必须提供 teacher_uid')
    data = schedule_service.get_teacher_view(
        int(schedule_id), int(teacher_uid),
        weekday=int(weekday) or None if weekday else None,
        week=int(week) or None if week else None)
    return _condense(data, f'教师{teacher_uid}')


def _day_view(_user, schedule_id, target_date, grade, class_name):
    from datetime import date
    from app.modules.academic.services import schedule_service
    try:
        parsed = date.fromisoformat(str(target_date)[:10])
    except (TypeError, ValueError):
        raise ValueError(f'target_date 格式应为 YYYY-MM-DD，收到：{target_date}')
    data = schedule_service.get_day_matrix(
        int(schedule_id) if schedule_id else None, parsed,
        grade=grade or None, class_name=class_name or None)
    return {
        'date': data.get('date'), 'weekday_text': data.get('weekday_text'),
        'week': data.get('week'), 'current_period_number': data.get('current_period_number'),
        'total': data.get('total'),
        'entries': respond.limit_rows(_flatten_entries(data.get('entries')), 400),
    }


def _condense(data, who):
    """把课表视图压缩成"节次×星期"的扁平条目列表，便于模型阅读。"""
    return {'who': who, 'total': data.get('total'),
            'statistics': data.get('stats'),
            'entries': respond.limit_rows(_flatten_entries(data.get('entries')), 400)}


def _flatten_entries(entries):
    """统一条目字段（不同视图的 entry 结构基本同构，缺字段补 None）。"""
    fields = ('weekday', 'weekday_text', 'period_number', 'period_name',
              'subject', 'grade', 'class_name', 'teacher_name', 'room',
              'week_range', 'week', 'date')
    out = []
    for item in entries or []:
        if not isinstance(item, dict):
            continue
        out.append({key: item.get(key) for key in fields if item.get(key) is not None})
    return out


def register(server):
    """把本模块的工具注册到 MCP 服务。"""

    @server.tool(name='list_schedules',
                 description='列出所有学期课表（含 id、学年、学期、状态、是否为当前学期）')
    async def list_schedules() -> str:
        return await toolkit.db_call(_schedules, tool='list_schedules')

    @server.tool(name='get_class_schedule',
                 description='查询某学期某班级的课表；week 为教学周序号（留空为全部周），'
                             'weekday 为星期几 1-7（留空为整周）')
    async def get_class_schedule(schedule_id: int, grade: str, class_name: str,
                                 week: int = 0, weekday: int = 0) -> str:
        params = {'schedule_id': schedule_id, 'grade': grade, 'class_name': class_name,
                  'week': week, 'weekday': weekday}
        return await toolkit.db_call(
            lambda u: _class_view(u, schedule_id, grade, class_name, week, weekday),
            tool='get_class_schedule', params=params)

    @server.tool(name='get_teacher_schedule',
                 description='查询某教师在某学期课表的全部任课条目；teacher_uid 为账号 id，'
                             'week/weekday 可留空')
    async def get_teacher_schedule(schedule_id: int, teacher_uid: int,
                                   week: int = 0, weekday: int = 0) -> str:
        params = {'schedule_id': schedule_id, 'teacher_uid': teacher_uid,
                  'week': week, 'weekday': weekday}
        return await toolkit.db_call(
            lambda u: _teacher_view(u, schedule_id, teacher_uid, week, weekday),
            tool='get_teacher_schedule', params=params)

    @server.tool(name='get_daily_schedule',
                 description='查询某一天的全校课表矩阵（可按年级/班级筛选）；'
                             'target_date 格式 YYYY-MM-DD')
    async def get_daily_schedule(target_date: str, schedule_id: int = 0,
                                 grade: str = '', class_name: str = '') -> str:
        params = {'target_date': target_date, 'schedule_id': schedule_id,
                  'grade': grade, 'class_name': class_name}
        return await toolkit.db_call(
            lambda u: _day_view(u, schedule_id, target_date, grade, class_name),
            tool='get_daily_schedule', params=params)