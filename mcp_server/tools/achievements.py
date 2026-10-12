# -*- coding: utf-8 -*-
"""业绩库工具（2026-10-11 新增）：录入 / 审核 / 编辑 / 删除 / 标签。

权限：全部 `academic.edit`（与网页端 7 条写路由一致）。业绩库**没有年级/班级
数据范围校验**（网页端也没有），但有"本人"式限制——由 service 真正生效：
  教务（academic.edit）可改任意记录；非教务只能改「本人任教的业绩 + 仍 pending」

⚠️ 重要差异：网页端 achievements_edit 里的同款"只能改自己"判定是**死代码**
（装饰器已强制 academic.edit，is_admin 恒为真，分支永远走不到，见原
achievements.py:390）。本模块调用的 service 已修正为真正生效的判定。

业绩表有三条写入链路，MCP 只覆盖"教务录入 + 审核"，另两条保持原样：
  ① 教务录入（本模块 add）② 教师工作台自助提交 ③ 表单审核自动入账
（② ③ 分属不同库，与业绩表不可 join，审核与入账是两次独立 commit）

刻意未开放：附件上传/删除（涉及文件落盘与 multipart，MCP 无文件上传通道）。
"""
from ..core import respond, toolkit

_ACH_FIELDS = ('id', 'teacher_uid', 'teacher_name', 'category', 'title',
               'level', 'obtain_date', 'issuer', 'note', 'tags', 'status',
               'submitted_by', 'reviewed_by', 'review_note', 'source_type',
               'created_at')


def _date(value):
    """YYYY-MM-DD → date；空/非法返回 None（与网页端"脏日期就清掉"一致）。"""
    from datetime import datetime
    text = (value or '').strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], '%Y-%m-%d').date()
    except ValueError:
        return None


def _add(user, teacher_uid, category, title, level, obtain_date, issuer,
         note, tags):
    from app.extensions import db
    from app.modules.academic.services import achievement_service as ach_svc
    from app.utils.helpers import log_operation
    rec = ach_svc.add_achievement(
        user, teacher_uid=teacher_uid, category=category, title=title,
        level=level, obtain_date=_date(obtain_date), issuer=issuer,
        note=note, tags=tags or '')
    db.session.commit()
    log_operation(user, '新增', '教师业绩', rec.id,
                  f'{rec.teacher_name} {rec.title}（MCP）', module='academic')
    return {'created': True, 'achievement': respond.pick(rec, _ACH_FIELDS),
            'message': f'已录入 {rec.teacher_name} 的业绩：{rec.title}'
                       f'（直接生效，状态 approved）'}


def _review(user, aid, action, note):
    from app.extensions import db
    from app.modules.academic.services import achievement_service as ach_svc
    from app.utils.helpers import log_operation
    rec, status_text = ach_svc.review_achievement(user, aid, action, note)
    db.session.commit()
    log_operation(user, '审核', '教师业绩', rec.id,
                  f'{rec.teacher_name} {rec.title} → {status_text}（MCP）',
                  module='academic')
    return {'reviewed': True, 'achievement': respond.pick(rec, _ACH_FIELDS),
            'status': status_text, 'message': f'已{status_text}：{rec.title}'}


def _edit(user, aid, title, category, level, obtain_date, issuer, note, tags):
    from app.extensions import db
    from app.modules.academic.services import achievement_service as ach_svc
    from app.utils.helpers import log_operation
    rec = ach_svc.edit_achievement(
        user, aid, title=title, category=category or None, level=level,
        obtain_date=_date(obtain_date) if obtain_date is not None else ...,
        issuer=issuer, note=note,
        tags=tags if tags is not None else None)
    db.session.commit()
    log_operation(user, '编辑', '教师业绩', aid,
                  f'{rec.teacher_name} {rec.title}（MCP）', module='academic')
    return {'updated': True, 'achievement': respond.pick(rec, _ACH_FIELDS),
            'message': f'已保存修改：{rec.title}'}


def _delete(user, aid):
    from app.extensions import db
    from app.modules.academic.services import achievement_service as ach_svc
    from app.utils.helpers import log_operation
    rec = ach_svc.delete_achievement(user, aid)
    db.session.commit()
    log_operation(user, '删除', '教师业绩', aid,
                  f'{rec.teacher_name} {rec.title}（MCP）', module='academic')
    return {'deleted': True, 'achievement': respond.pick(rec, _ACH_FIELDS),
            'message': '业绩记录已删除（附件一并清理）'}


def _set_tags(user, aid, tags):
    """单独改标签（service 内部 commit）。"""
    from app.extensions import db
    from app.models.academic import TeacherAchievement
    from app.modules.academic.services import achievement_service as ach_svc
    from app.utils.helpers import log_operation
    rec = db.session.get(TeacherAchievement, aid)
    if rec is None:
        raise ValueError(f'业绩记录不存在：{aid}')
    parsed = ach_svc.set_tags(rec, tags or '')
    log_operation(user, '修改', '教师业绩', aid, f'标签 → {",".join(parsed)}（MCP）',
                  module='academic')
    return {'updated': True, 'aid': aid, 'tags': parsed,
            'message': f'标签已更新为：{"、".join(parsed) or "（空）"}'}


def register(server):
    """把本模块的工具注册到 MCP 服务。"""

    @server.tool(name='add_achievement',
                 description='【写操作】教务录入教师业绩（直接生效，状态 approved）。'
                             'teacher_uid 为教师编号，category 取 certificate(证书)/'
                             'course(课程)/paper(论文)/honor(荣誉)/training(培训)/'
                             'other(其他)')
    async def add_achievement(teacher_uid: str, category: str, title: str,
                              level: str = '', obtain_date: str = '',
                              issuer: str = '', note: str = '',
                              tags: str = '') -> str:
        params = {'teacher_uid': teacher_uid, 'category': category,
                  'title': title, 'obtain_date': obtain_date}
        return await toolkit.db_write(
            lambda u: _add(u, teacher_uid, category, title, level, obtain_date,
                           issuer, note, tags),
            tool='add_achievement', params=params, perm='academic.edit')

    @server.tool(name='review_achievement',
                 description='【写操作】审核教师提交的业绩：action 取 approve / reject，'
                             '驳回必填 review_note（教师可在工作台看到原因）')
    async def review_achievement(aid: int, action: str,
                                 review_note: str = '') -> str:
        params = {'aid': aid, 'action': action}
        return await toolkit.db_write(
            lambda u: _review(u, aid, action, review_note),
            tool='review_achievement', params=params, perm='academic.edit')

    @server.tool(name='update_achievement',
                 description='【写操作】编辑业绩（只改传入的字段）。教务可改任意记录；'
                             '非教务只能改「本人任教且仍待审核」的业绩。'
                             'obtain_date 传空串表示清空')
    async def update_achievement(aid: int, title: str = '', category: str = '',
                                 level: str = '', obtain_date: str = None,
                                 issuer: str = '', note: str = '',
                                 tags: str = None) -> str:
        params = {'aid': aid, 'title': title, 'category': category}
        return await toolkit.db_write(
            lambda u: _edit(u, aid, title or None, category, level or None,
                            obtain_date, issuer or None, note or None, tags),
            tool='update_achievement', params=params, perm='academic.edit')

    @server.tool(name='delete_achievement',
                 description='【写操作】删除业绩记录，附件（记录 + 落盘文件）一并清理，'
                             '不留孤儿文件')
    async def delete_achievement(aid: int) -> str:
        return await toolkit.db_write(lambda u: _delete(u, aid),
                                      tool='delete_achievement',
                                      params={'aid': aid}, perm='academic.edit')

    @server.tool(name='set_achievement_tags',
                 description='【写操作】设置业绩标签（逗号/空格分隔，自动去重）；'
                             '传空串表示清空')
    async def set_achievement_tags(aid: int, tags: str) -> str:
        return await toolkit.db_write(lambda u: _set_tags(u, aid, tags),
                                      tool='set_achievement_tags',
                                      params={'aid': aid, 'tags': tags},
                                      perm='academic.edit')
