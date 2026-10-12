# -*- coding: utf-8 -*-
"""表单收集工具（2026-10-11 新增）：模板管理 / 轮次管理 / 审核 / 填写提交。

权限严格以**网页端路由装饰器**为准（不是 permission_map 里的 academic.forms /
academic.forms_view —— 那两个是死键，表单路由从未使用）：
  · 管理端（建/改/发布/关闭/删除/轮次/审核/催交）→ academic.edit
  · 填写端提交答案                              → academic.view（与网页端一致）
  · 表单汇总「催交」                            → academic.edit

form_service 是教务模块唯一完整的 service（清晰签名 + 统一 ValueError + 内部
commit），本模块基本是薄封装；主要补齐三件事：
  1. 时间参数从字符串解析成 datetime（网页端由表单提交，MCP 只能收字符串）；
  2. 返回值 ORM → 显式字段 dict（不把整对象丢给模型）；
  3. 写操作统一 log_operation 留痕。

刻意未开放：删除整场考试式的破坏性操作；`reopen_form(mode='new')` 已由
`start_form_round` 覆盖，故未单独暴露重开模板入口。
"""
from datetime import datetime

from ..core import respond, toolkit

_TPL_FIELDS = ('id', 'title', 'description', 'category', 'target_type',
               'status', 'start_time', 'deadline', 'allow_multiple',
               'max_file_size_mb', 'to_achievement', 'created_by', 'created_at')
_ROUND_FIELDS = ('id', 'template_id', 'round_no', 'name', 'term', 'status',
                 'start_time', 'deadline')
_SUB_FIELDS = ('id', 'template_id', 'round_id', 'submitter_type',
               'submitter_id', 'submitter_name', 'submitter_uid', 'status',
               'reviewed_by', 'review_note', 'submitted_at')


def _dt(value):
    """'YYYY-MM-DDTHH:MM' / 'YYYY-MM-DD HH:MM:SS' / ISO → datetime；空为 None。"""
    text = (value or '').strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(f'时间格式应为 YYYY-MM-DDTHH:MM，收到：{text}')


def _create(user, title, description, category, target_type, target_scope,
            start_time, deadline, max_file_size, allow_multiple, questions,
            publish):
    from app.modules.academic.services import form_service
    from app.utils.helpers import log_operation
    title = (title or '').strip()
    if not title:
        raise ValueError('请填写表单标题')
    tpl = form_service.create_form(
        title=title, description=(description or '').strip() or None,
        category=(category or '').strip() or None,
        target_type=(target_type or 'all').strip(),
        target_scope=(target_scope or '').strip() or None,
        start_time=_dt(start_time), deadline=_dt(deadline),
        max_file_size=max_file_size or 10,
        allow_multiple=bool(allow_multiple),
        questions_data=questions or [], created_by=user.id)
    log_operation(user, '新增', '表单', tpl.id,
                  f'创建表单：{title}（MCP）', module='academic')
    if publish:
        form_service.publish_form(tpl.id)
        log_operation(user, '发布', '表单', tpl.id, f'发布表单：{title}（MCP）',
                      module='academic')
    return {'created': True, 'form': respond.pick(tpl, _TPL_FIELDS),
            'published': bool(publish),
            'message': ('表单已发布' if publish else '表单已保存为草稿')}


def _update(user, form_id, title, description, category, target_type,
            target_scope, start_time, deadline, max_file_size,
            allow_multiple, questions):
    from app.modules.academic.services import form_service
    from app.utils.helpers import log_operation
    title = (title or '').strip()
    if not title:
        raise ValueError('请填写表单标题')
    tpl = form_service.update_form(
        form_id=form_id, title=title,
        description=(description or '').strip() or None,
        category=(category or '').strip() or None,
        target_type=(target_type or 'all').strip(),
        target_scope=(target_scope or '').strip() or None,
        start_time=_dt(start_time), deadline=_dt(deadline),
        max_file_size=max_file_size or 10,
        allow_multiple=bool(allow_multiple), questions_data=questions or [])
    log_operation(user, '修改', '表单', form_id, f'编辑表单：{title}（MCP）',
                  module='academic')
    return {'updated': True, 'form': respond.pick(tpl, _TPL_FIELDS),
            'message': '表单已更新（仅草稿可编辑；题目会整体重建）'}


def _set_status(user, form_id, action):
    """发布 / 关闭 / 删除（service 内部各自校验状态并 commit）。"""
    from app.modules.academic.services import form_service
    from app.utils.helpers import log_operation
    handler = {'publish': form_service.publish_form,
               'close': form_service.close_form,
               'delete': form_service.delete_form}.get(action)
    if handler is None:
        raise ValueError('action 只能是 publish / close / delete')
    result = handler(form_id)
    label = {'publish': '发布', 'close': '关闭', 'delete': '删除'}[action]
    log_operation(user, label, '表单', form_id, f'{label}表单 #{form_id}（MCP）',
                  module='academic')
    if action == 'delete':
        return {'deleted': True, 'form_id': form_id, 'message': '表单已删除'}
    return {f'{action}ed': True, 'form': respond.pick(result, _TPL_FIELDS),
            'message': f'表单已{label}'}


def _start_round(user, form_id, name, term, start_time, deadline):
    from app.modules.academic.services import form_service
    from app.utils.helpers import log_operation
    rnd = form_service.start_new_round(
        form_id, name=(name or '').strip() or None,
        term=(term or '').strip() or None, deadline=_dt(deadline),
        created_by=user.id, start_time=_dt(start_time))
    log_operation(user, '新增', '表单轮次', rnd.id,
                  f'表单 #{form_id} 发起第{rnd.round_no}轮（MCP）', module='academic')
    return {'created': True, 'round': respond.pick(rnd, _ROUND_FIELDS),
            'message': f'已发起第 {rnd.round_no} 轮'}


def _round_op(user, round_id, action, start_time, deadline, clear_start,
              clear_deadline):
    """结束轮次 / 重开轮次 / 调整时间窗。"""
    from app.modules.academic.services import form_service
    from app.utils.helpers import log_operation
    if action == 'close':
        rnd = form_service.close_round(round_id)
        log_operation(user, '关闭', '表单轮次', round_id,
                      f'结束轮次 #{round_id}（MCP）', module='academic')
        return {'closed': True, 'round': respond.pick(rnd, _ROUND_FIELDS),
                'message': '该轮次已结束，不再接受提交'}
    if action == 'reopen':
        rnd = form_service.reopen_round(
            round_id, start_time=_dt(start_time), deadline=_dt(deadline),
            clear_deadline=bool(clear_deadline))
        log_operation(user, '重开', '表单轮次', round_id,
                      f'重开轮次 #{round_id}（MCP）', module='academic')
        return {'reopened': True, 'round': respond.pick(rnd, _ROUND_FIELDS),
                'message': '该轮次已重新开放'}
    if action == 'window':
        rnd = form_service.update_round_window(
            round_id, start_time=_dt(start_time), deadline=_dt(deadline),
            clear_start=bool(clear_start), clear_deadline=bool(clear_deadline))
        log_operation(user, '修改', '表单轮次', round_id,
                      f'调整轮次 #{round_id} 时间窗（MCP）', module='academic')
        return {'updated': True, 'round': respond.pick(rnd, _ROUND_FIELDS),
                'message': '时间窗已调整（None=不修改，clear_*=清空）'}
    raise ValueError('action 只能是 close / reopen / window')


def _review(user, submission_id, new_status, note):
    """审核提交：approved 会自动按模板映射计入教师业绩库（幂等）。"""
    from app.modules.academic.services import form_service
    from app.utils.helpers import log_operation
    if new_status not in ('approved', 'rejected'):
        raise ValueError('new_status 只能是 approved 或 rejected')
    sub = form_service.review_submission(submission_id, new_status, user.id,
                                         note=(note or '').strip() or None)
    log_operation(user, '审核', '表单提交', submission_id,
                  f'{new_status} 提交 #{submission_id}（MCP）', module='academic')
    return {'reviewed': True, 'submission': respond.pick(sub, _SUB_FIELDS),
            'message': '审核已处理'
                       + ('；审核通过会自动按模板映射计入教师业绩库'
                          if new_status == 'approved' else '')}


def _current_user_subject(user):
    """当前账号的任教学科（非教师/未绑定 → None）。对齐网页端 _current_user_subject。"""
    try:
        from app.modules.academic.services import teacher_service
        teacher = teacher_service.teacher_of_user(user)
        return (teacher.subject or None) if teacher else None
    except Exception:  # noqa: BLE001  学科取不到不阻断提交
        return None


def _submit(user, form_id, answers, round_id):
    """填写端提交答案（权限 academic.view，与网页端一致）。"""
    from app.modules.academic.services import form_service
    from app.utils.helpers import log_operation
    if not isinstance(answers, dict) or not answers:
        raise ValueError('answers 必须是非空对象（key 为题目 id）')
    # 2026-10-12 审计修复：提交者类型按角色判定（原硬编码 'teacher'，
    # 学生账号可提交「仅面向教师」的表单）；并补全 grade/class/subject，
    # 否则「按年级/班级/学科定向」的表单会因缺字段被判不在范围内
    submitter_type = 'teacher' if user.role in ('teacher', 'admin') else 'student'
    submitter = {
        'submitter_type': submitter_type,
        'submitter_id': user.id,
        'submitter_name': user.real_name or user.username,
        'submitter_uid': (getattr(user, 'teacher_uid', None)
                          or getattr(user, 'student_number', None)
                          or str(user.id)),
        'submitter_grade': getattr(user, 'grade', None),
        'submitter_class': getattr(user, 'class_name', None),
        'submitter_subject': _current_user_subject(user),
        'round_id': round_id or None,
    }
    sub = form_service.submit_form(
        form_id, submitter_info=submitter,
        answers_dict={str(k): v for k, v in answers.items()}, files={})
    log_operation(user, '提交', '表单提交', sub.id,
                  f'提交表单 #{form_id}（MCP）', module='academic')
    return {'submitted': True, 'submission': respond.pick(sub, _SUB_FIELDS),
            'message': '已提交，状态为「待审核」'}


def _remind(user, form_id, uids, all_missing, grade, class_name):
    """催交未提交者（发站内通知；service 内部已做未读去重防刷屏）。"""
    from app.modules.academic.services import form_summary_service
    from app.utils.helpers import log_operation
    ok, msg, data = form_summary_service.remind_submitters(
        form_id, uids=uids or None, all_missing=bool(all_missing),
        operator=user, grade=(grade or '').strip() or None,
        class_name=(class_name or '').strip() or None)
    if not ok:
        raise ValueError(msg)
    log_operation(user, '催交', '表单', form_id,
                  f'催交 #{form_id}：通知 {data.get("notified", 0)} 人（MCP）',
                  module='academic')
    return {'reminded': True, 'data': data, 'message': msg}


def register(server):
    """把本模块的工具注册到 MCP 服务。"""

    @server.tool(name='create_form',
                 description='【写操作】创建收集表单（草稿）。questions 为题目数组，'
                             '每项含 question_type(text/textarea/single_choice/'
                             'multi_choice/file/date/number) 与 title，可选 '
                             'options_json/required/file_types；'
                             'publish=true 时创建后立即发布')
    async def create_form(title: str, questions: list = None,
                          description: str = '', category: str = '',
                          target_type: str = 'all', target_scope: str = '',
                          start_time: str = '', deadline: str = '',
                          max_file_size: int = 10,
                          allow_multiple: bool = False,
                          publish: bool = False) -> str:
        params = {'title': title, 'target_type': target_type, 'publish': publish}
        return await toolkit.db_write(
            lambda u: _create(u, title, description, category, target_type,
                              target_scope, start_time, deadline, max_file_size,
                              allow_multiple, questions or [], publish),
            tool='create_form', params=params, perm='academic.edit')

    @server.tool(name='update_form',
                 description='【写操作】编辑表单（仅草稿状态）；题目会整体重建，'
                             '已产生提交的表单改回草稿编辑会使历史答案悬空，慎用')
    async def update_form(form_id: int, title: str, questions: list = None,
                          description: str = '', category: str = '',
                          target_type: str = 'all', target_scope: str = '',
                          start_time: str = '', deadline: str = '',
                          max_file_size: int = 10,
                          allow_multiple: bool = False) -> str:
        params = {'form_id': form_id, 'title': title}
        return await toolkit.db_write(
            lambda u: _update(u, form_id, title, description, category,
                              target_type, target_scope, start_time, deadline,
                              max_file_size, allow_multiple, questions or []),
            tool='update_form', params=params, perm='academic.edit')

    @server.tool(name='set_form_status',
                 description='【写操作】发布/关闭/删除表单：action 取 publish(草稿→进行中)'
                             ' / close(进行中→已关闭) / delete(仅草稿可删)')
    async def set_form_status(form_id: int, action: str) -> str:
        return await toolkit.db_write(
            lambda u: _set_status(u, form_id, action), tool='set_form_status',
            params={'form_id': form_id, 'action': action}, perm='academic.edit')

    @server.tool(name='start_form_round',
                 description='【写操作】为进行中的表单发起新一轮（上一轮自动结束）；'
                             'deadline 格式 YYYY-MM-DDTHH:MM，留空为不限截止')
    async def start_form_round(form_id: int, name: str = '', term: str = '',
                               start_time: str = '', deadline: str = '') -> str:
        params = {'form_id': form_id, 'name': name, 'deadline': deadline}
        return await toolkit.db_write(
            lambda u: _start_round(u, form_id, name, term, start_time, deadline),
            tool='start_form_round', params=params, perm='academic.edit')

    @server.tool(name='manage_form_round',
                 description='【写操作】管理轮次：action=close(结束该轮) / '
                             'reopen(重开已结束轮) / window(调整时间窗，'
                             'clear_start / clear_deadline 为 true 表示清空)')
    async def manage_form_round(round_id: int, action: str,
                                start_time: str = '', deadline: str = '',
                                clear_start: bool = False,
                                clear_deadline: bool = False) -> str:
        params = {'round_id': round_id, 'action': action}
        return await toolkit.db_write(
            lambda u: _round_op(u, round_id, action, start_time, deadline,
                                clear_start, clear_deadline),
            tool='manage_form_round', params=params, perm='academic.edit')

    @server.tool(name='review_form_submission',
                 description='【写操作】审核表单提交：new_status 取 approved / rejected；'
                             '通过后会自动按模板映射计入教师业绩库（幂等）')
    async def review_form_submission(submission_id: int, new_status: str,
                                     note: str = '') -> str:
        params = {'submission_id': submission_id, 'new_status': new_status}
        return await toolkit.db_write(
            lambda u: _review(u, submission_id, new_status, note),
            tool='review_form_submission', params=params, perm='academic.edit')

    @server.tool(name='submit_form',
                 description='【写操作】以当前账号身份填写并提交表单（权限 academic.view，'
                             '与网页端填写端一致）。answers 为 {题目id: 答案}；'
                             '表单须处于进行中且在时间窗内，且不可重复提交本轮')
    async def submit_form(form_id: int, answers: dict,
                          round_id: int = 0) -> str:
        params = {'form_id': form_id, 'round_id': round_id}
        return await toolkit.db_write(
            lambda u: _submit(u, form_id, answers, round_id),
            tool='submit_form', params=params, perm='academic.view')

    @server.tool(name='remind_form_submitters',
                 description='【写操作】向本轮未提交者推送催交通知。all_missing=true '
                             '催全部未交者，或用 uids 指定；已有未读催交通知的人会被跳过'
                             '（防重复刷屏）')
    async def remind_form_submitters(form_id: int, all_missing: bool = True,
                                     uids: list = None, grade: str = '',
                                     class_name: str = '') -> str:
        params = {'form_id': form_id, 'all_missing': all_missing}
        return await toolkit.db_write(
            lambda u: _remind(u, form_id, uids, all_missing, grade, class_name),
            tool='remind_form_submitters', params=params, perm='academic.edit')
