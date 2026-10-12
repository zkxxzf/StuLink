# -*- coding: utf-8 -*-
"""备课组长工具（2026-10-11 第 2 批）：保存 / 删除。

权限 `academic.edit`（与 duty.py 两条写路由一致）。
无年级/班级数据范围校验——网页端也没有，此处保持一致（不擅自加严或放宽）。

save_leader 以 (学年, 学期, 年级, 学科) 四元组幂等：命中即更新，未命中新建。
必填：school_year / subject / leader_name；term 留空=整学年，grade 留空=全校/综合组。
"""
from ..core import respond, toolkit

_LEADER_FIELDS = ('id', 'school_year', 'term', 'grade', 'subject',
                  'leader_name', 'leader_uid', 'members', 'duty', 'sort_order')


def _save(user, school_year, subject, leader_name, term, grade, leader_uid,
          members, duty):
    from app.modules.academic.services import subject_leader_service
    from app.utils.helpers import log_operation

    data = {
        'school_year': (school_year or '').strip(),
        'term': (term or '').strip(),
        'grade': (grade or '').strip(),
        'subject': (subject or '').strip(),
        'leader_name': (leader_name or '').strip(),
        'leader_uid': (leader_uid or '').strip(),
        'members': (members or '').strip(),
        'duty': (duty or '').strip(),
    }
    leader, created = subject_leader_service.save_leader(data, operator_id=user.id)
    log_operation(user, '新增' if created else '修改', '备课组长', leader.id,
                  f'{leader.school_year} {grade or "全校"}{subject} '
                  f'{leader.leader_name}（MCP）', module='academic')
    return {('created' if created else 'updated'): True,
            'leader': respond.pick(leader, _LEADER_FIELDS),
            'message': f'已{"新增" if created else "更新"}备课组长：'
                       f'{leader.school_year} {grade or "全校"}{subject} '
                       f'{leader.leader_name}'}


def _delete(user, leader_id):
    from app.modules.academic.services import subject_leader_service
    from app.utils.helpers import log_operation

    info = subject_leader_service.delete_leader(int(leader_id))
    if info is None:
        raise ValueError(f'备课组长记录不存在：{leader_id}')
    log_operation(user, '删除', '备课组长', leader_id, f'{info}（MCP）',
                  module='academic')
    return {'deleted': True, 'leader_id': leader_id,
            'message': f'已删除备课组长：{info}'}


def register(server):
    """把本模块的工具注册到 MCP 服务。"""

    @server.tool(name='save_subject_leader',
                 description='【写操作】新增或更新备课组长'
                             '（按 学年+学期+年级+学科 幂等，命中即更新）。'
                             '必填 school_year(如 2026-2027) / subject / leader_name；'
                             'term 留空=整学年，grade 留空=全校或综合组')
    async def save_subject_leader(school_year: str, subject: str,
                                  leader_name: str, term: str = '',
                                  grade: str = '', leader_uid: str = '',
                                  members: str = '', duty: str = '') -> str:
        params = {'school_year': school_year, 'subject': subject,
                  'leader_name': leader_name, 'term': term, 'grade': grade}
        return await toolkit.db_write(
            lambda u: _save(u, school_year, subject, leader_name, term, grade,
                            leader_uid, members, duty),
            tool='save_subject_leader', params=params, perm='academic.edit')

    @server.tool(name='delete_subject_leader',
                 description='【写操作】删除备课组长记录（按 id）')
    async def delete_subject_leader(leader_id: int) -> str:
        return await toolkit.db_write(lambda u: _delete(u, leader_id),
                                      tool='delete_subject_leader',
                                      params={'leader_id': leader_id},
                                      perm='academic.edit')
