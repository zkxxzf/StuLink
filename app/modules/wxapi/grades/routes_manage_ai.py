# StuLink v1.19.49.50 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序 AI 分析（与网页 `/grades/ai` 同一个任务链路）。

网页用 SSE 订阅进度；小程序改成**轮询任务详情**（详情接口本就带进度回放与
报告正文，网页刷新续看也走它）。提交沿用 `ai_task_service.submit`：建任务即返回，
生成在后台线程跑，切页/锁屏不中断。

范围与可见性完全继承网页最终口径：**任务/报告仅生成者本人可见**（管理员也不例外），
数据边界由 `ai_task_service` 内部的 score_guard 兜底。
"""
from flask import g, request

from app.extensions import db
from app.models.grades import AiReport, AiTask, Exam
from app.modules.grades.services import ai_task_service
from app.modules.grades.services import scope as scope_service
from app.modules.wxapi import bp
from app.modules.wxapi.core.guards import wx_perm_required
from app.modules.wxapi.core.response import wx_err, wx_ok
from app.utils.helpers import log_operation

PERM = 'grades.view'
MAX_QUESTION = 300
TASK_LIMIT = 30


def _fmt(dt):
    return dt.strftime('%Y-%m-%d %H:%M') if dt else ''


def _task_json(task, with_body=False):
    exam = db.session.get(Exam, task.exam_id)
    out = {
        'id': task.id,
        'origin': 'task',
        'exam_id': task.exam_id,
        'exam_name': exam.name if exam else '（考试已删除）',
        'grade': (exam.grade if exam else '') or '',
        'question': task.question or '',
        'status': task.status,
        'stage': task.stage or '',
        'error': task.error or '',
        'tool_count': task.tool_count or 0,
        'report_id': task.report_id,
        'kind': 'html',
        'created_at': _fmt(task.created_at),
        'finished_at': _fmt(task.finished_at),
    }
    if with_body:
        out['events'] = [ev for ev in ai_task_service.task_events(task.id)
                         if ev.get('type') in ('stage', 'tool')]
        html_text = None
        if task.report_id:
            report = db.session.get(AiReport, task.report_id)
            if report is not None:
                html_text = report.content
                out['kind'] = report.kind or 'html'
        out['html'] = html_text
        out['md'] = None
    return out


def _report_json(report):
    """旧口径报告（无对应任务）包装成任务结构，id 取负数。"""
    exam = db.session.get(Exam, report.exam_id)
    kind = report.kind or 'md'
    return {
        'id': -report.id,
        'origin': 'report',
        'exam_id': report.exam_id,
        'exam_name': exam.name if exam else '（考试已删除）',
        'grade': report.grade or ((exam.grade if exam else '') or ''),
        'question': '（旧口径 AI 报告）',
        'status': 'done',
        'stage': '已完成',
        'error': '',
        'tool_count': 0,
        'report_id': report.id,
        'kind': kind,
        'created_at': _fmt(report.created_at),
        'finished_at': _fmt(report.created_at),
        'events': [],
        'html': report.content if kind == 'html' else None,
        'md': report.content if kind != 'html' else None,
        'trace': [],
    }


def _my_task(tid):
    task = db.session.get(AiTask, tid)
    if task is None or task.user_id != g.wx_user.id:
        return None
    return task


@bp.route('/grades/ai/tasks')
@wx_perm_required(PERM)
def wx_ai_tasks():
    """我的 AI 分析记录（query: exam_id / limit）。"""
    user = g.wx_user
    exam_id = request.args.get('exam_id', type=int)
    limit = max(1, min(request.args.get('limit', default=TASK_LIMIT, type=int)
                       or TASK_LIMIT, 100))
    tasks = ai_task_service.list_tasks(user, exam_id, limit)
    linked = {t.report_id for t in tasks if t.report_id}
    q = AiReport.query.filter_by(user_id=user.id)
    if exam_id:
        q = q.filter_by(exam_id=exam_id)
    reports = [r for r in q.order_by(AiReport.id.desc()).limit(limit).all()
               if r.id not in linked]
    items = [_task_json(t) for t in tasks] + [_report_json(r) for r in reports]
    items.sort(key=lambda x: (x.get('finished_at') or x.get('created_at') or ''),
               reverse=True)
    return wx_ok(items)


@bp.route('/grades/ai/tasks/<tid>')
@wx_perm_required(PERM)
def wx_ai_task_detail(tid):
    """任务详情（含进度回放与报告正文）；负数 = 旧口径报告。

    用字符串路由再转 int：Flask 的 `<int:>` 不匹配负号，旧报告（负 id）会 404。
    """
    try:
        tid = int(tid)
    except (TypeError, ValueError):
        return wx_err('任务不存在', 404)
    user = g.wx_user
    if tid < 0:
        report = db.session.get(AiReport, -tid)
        if report is None:
            return wx_err('报告不存在', 404)
        if report.user_id != user.id:
            return wx_err('只能查看自己的 AI 报告', 403)
        return wx_ok(_report_json(report))
    task = db.session.get(AiTask, tid)
    if task is None:
        return wx_err('任务不存在', 404)
    if task.user_id != user.id:
        return wx_err('只能查看自己的 AI 分析', 403)
    return wx_ok(_task_json(task, with_body=True))


@bp.route('/grades/ai/ask', methods=['POST'])
@wx_perm_required(PERM)
def wx_ai_ask():
    """提交一句话分析：body {exam_id, question}，返回 task_id。"""
    data = request.get_json(silent=True) or {}
    try:
        exam_id = int(data.get('exam_id') or 0)
    except (TypeError, ValueError):
        return wx_err('参数不正确：exam_id 需为数字')
    question = (data.get('question') or '').strip()[:MAX_QUESTION]
    if not question:
        return wx_err('请输入要分析的问题')
    exam = db.session.get(Exam, exam_id)
    if exam is None:
        return wx_err('考试不存在', 404)
    try:
        scope_service.check_exam_visible(g.wx_user, exam)
    except PermissionError:
        return wx_err('该考试不在你的可见范围内', 403)
    ok, out = ai_task_service.submit(g.wx_user, exam, question)
    if not ok:
        return wx_err(str(out))
    log_operation(g.wx_user, '提交', 'AI 分析', int(out),
                  '%s：%s' % (exam.name, question[:80]), module='grades')
    return wx_ok({'task_id': int(out)}, '已提交，生成中')


@bp.route('/grades/ai/tasks/<int:tid>/stop', methods=['POST'])
@wx_perm_required(PERM)
def wx_ai_task_stop(tid):
    """请求停止进行中的任务。"""
    task = _my_task(tid)
    if task is None:
        return wx_err('任务不存在或无权操作', 403)
    if task.status != 'running':
        return wx_err('任务已结束，无需停止')
    if not ai_task_service.request_stop(tid):
        return wx_err('任务不在当前进程中执行，请刷新列表', 409)
    log_operation(g.wx_user, '停止', 'AI 任务', tid, '', module='grades')
    return wx_ok({'id': tid}, '已请求停止，当前步骤完成即停')


@bp.route('/grades/ai/tasks/<int:tid>/delete', methods=['POST'])
@wx_perm_required(PERM)
def wx_ai_task_delete(tid):
    """删除任务（进行中不可删），连带删掉它生成的报告。"""
    task = _my_task(tid)
    if task is None:
        return wx_err('任务不存在或无权操作', 403)
    if task.status == 'running':
        return wx_err('任务进行中，请等它跑完再删除')
    rid = task.report_id
    if rid:
        rep = db.session.get(AiReport, rid)
        if rep is not None and rep.user_id == g.wx_user.id:
            db.session.delete(rep)
    db.session.delete(task)
    db.session.commit()
    ai_task_service.drop_events(tid)
    log_operation(g.wx_user, '删除', 'AI 任务', tid, '', module='grades')
    return wx_ok({'id': tid}, '已删除')
