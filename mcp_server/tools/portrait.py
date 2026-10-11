# -*- coding: utf-8 -*-
"""学生画像工具：画像详情、评语与事件查询、新增评语/事件（写）。

写操作沿用网页端同一套规则（评语类型白名单、长度上限、日期格式校验），
并在写前做学号数据范围校验，保证"AI 能写的范围 = 网页上能写的范围"。
"""
from ..core import guards, respond, toolkit

_COMMENT_TYPES = ('学期评语', '操行评语', '班主任评语')
_MAX_CONTENT = 2000


def _detail(user, student_no):
    from app.modules.portrait.services import portrait_service
    guards.find_student(student_no)
    guards.student_scope(user, student_no)
    data = portrait_service.get_student_detail(student_no)
    if not data:
        raise ValueError(f'学生画像不存在：{student_no}')
    return data


def _comments(user, student_no):
    from app.modules.portrait.services import portrait_aggregation as agg
    guards.find_student(student_no)
    guards.student_scope(user, student_no)
    rows = agg.get_portrait_comments(student_no)
    return {'student_no': student_no, 'count': len(rows or []),
            'comments': respond.limit_rows(rows or [])}


def _events(user, student_no):
    from app.modules.portrait.services import portrait_aggregation as agg
    guards.find_student(student_no)
    guards.student_scope(user, student_no)
    rows = agg.get_portrait_events(student_no)
    return {'student_no': student_no, 'count': len(rows or []),
            'events': respond.limit_rows(rows or [])}


def _add_comment(user, student_no, content, comment_type, term):
    from app.modules.portrait.services import portrait_service
    guards.find_student(student_no)
    guards.student_scope(user, student_no)
    content = (content or '').strip()
    if not content:
        raise ValueError('评语内容不能为空')
    if len(content) > _MAX_CONTENT:
        raise ValueError(f'评语内容超长（{len(content)} 字），上限 {_MAX_CONTENT} 字')
    ctype = (comment_type or '').strip() or _COMMENT_TYPES[0]
    if ctype not in _COMMENT_TYPES:
        raise ValueError(f'comment_type 非法，可选：{"、".join(_COMMENT_TYPES)}')
    result = portrait_service.add_comment(
        student_no=student_no, teacher_id=user.id, comment_type=ctype,
        content=content, term=(term or '').strip()[:32])
    return {'created': True, 'comment': result}


def _add_event(user, student_no, title, event_type, description, event_date):
    from app.utils.helpers import to_date
    from app.modules.portrait.services import portrait_service
    guards.find_student(student_no)
    guards.student_scope(user, student_no)
    title = (title or '').strip()
    if not title:
        raise ValueError('事件标题不能为空')
    parsed = to_date((event_date or '').strip())
    if not parsed:
        raise ValueError('event_date 格式应为 YYYY-MM-DD')
    result = portrait_service.add_event(
        student_no=student_no, event_type=(event_type or '其他').strip(),
        title=title, description=(description or '').strip(),
        event_date=parsed, created_by=user.id)
    return {'created': True, 'event': result}


def register(server):
    """把本模块的工具注册到 MCP 服务。"""

    @server.tool(name='get_student_portrait',
                 description='查询学生画像完整数据：画像评分、成绩趋势、积分、住宿、'
                             '考勤、评语、事件')
    async def get_student_portrait(student_no: str) -> str:
        return await toolkit.db_call(lambda u: _detail(u, student_no),
                                     tool='get_student_portrait', perm='portrait.view',
                                     params={'student_no': student_no})

    @server.tool(name='list_portrait_comments',
                 description='查询学生的评语列表（含撰写教师与时间）')
    async def list_portrait_comments(student_no: str) -> str:
        return await toolkit.db_call(lambda u: _comments(u, student_no),
                                     tool='list_portrait_comments', perm='portrait.view',
                                     params={'student_no': student_no})

    @server.tool(name='list_portrait_events',
                 description='查询学生的画像事件列表（奖惩、活动、谈话记录等）')
    async def list_portrait_events(student_no: str) -> str:
        return await toolkit.db_call(lambda u: _events(u, student_no),
                                     tool='list_portrait_events', perm='portrait.view',
                                     params={'student_no': student_no})

    @server.tool(name='add_portrait_comment',
                 description='【写操作】给学生新增评语。comment_type 可选'
                             '学期评语/操行评语/班主任评语（默认学期评语），'
                             'term 为学期如 "2025-2026第一学期"，内容上限 2000 字')
    async def add_portrait_comment(student_no: str, content: str,
                                   comment_type: str = '', term: str = '') -> str:
        params = {'student_no': student_no, 'comment_type': comment_type, 'term': term}
        return await toolkit.db_write(
            lambda u: _add_comment(u, student_no, content, comment_type, term),
            tool='add_portrait_comment', params=params, perm='portrait.edit')

    @server.tool(name='add_portrait_event',
                 description='【写操作】给学生新增画像事件。event_date 格式 YYYY-MM-DD，'
                             'event_type 如 奖励/违纪/活动/谈话/其他')
    async def add_portrait_event(student_no: str, title: str, event_date: str,
                                 event_type: str = '其他',
                                 description: str = '') -> str:
        params = {'student_no': student_no, 'title': title,
                  'event_type': event_type, 'event_date': event_date}
        return await toolkit.db_write(
            lambda u: _add_event(u, student_no, title, event_type, description, event_date),
            tool='add_portrait_event', params=params, perm='portrait.edit')