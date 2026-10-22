# StuLink v1.19.47.30 2026-10-13
# AI 一句话分析：服务端任务（后台执行 + 进度可订阅/可恢复）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""AI 分析任务：提问即建任务（`AiTask` 落库）→ 后台线程执行 → 进度可随时订阅。

为什么不再"一次 SSE 请求直通"：
  · 一次报告要跑多轮工具调用（几十秒~几分钟），用户切页/刷新就断，看不到进度、也找不到结果；
  · 现在进度事件同时进**进程内缓冲**（可补发）与 `ai_tasks` 表（可跨请求/跨页面恢复），
    完成后写 `AiReport` 并回填 `task.report_id`。

部署前提（重要）：**单进程多线程**（`run.py` 用 waitress，默认 16 线程）。
进度缓冲在**进程内**，因此：
  · 同一进程内，切页/刷新/换标签页都能"续上"进行中的任务；
  · 进程重启后，缓冲丢失 → 库里仍为 `running` 的任务由 `sweep_zombies()` 标记为 `interrupted`
    （用户可重新提问）；已完成的任务与报告不受影响。
  · 若将来改成多进程/多容器部署，需要把缓冲换成 Redis 或改成纯 DB 轮询。
"""
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta

from flask import current_app

from app.extensions import db
from app.models import User
from app.models.grades import AiReport, AiTask, Exam
from app.modules.grades.services import ai_agent, ai_service, score_guard

logger = logging.getLogger(__name__)

MAX_RUNNING_PER_USER = 2                 # 同一用户同时进行的任务上限（防刷）
# P2 M-13/M-22：进程内全局并发上限（单进程 waitress 部署）。
# 这不是持久队列：进程重启仍会中断；只防止“每任务一个线程”把线程打满。
MAX_GLOBAL_RUNNING = max(1, int(os.environ.get('AI_MAX_GLOBAL_TASKS', '6')))
EVENT_KEEP = 300                         # 每任务保留的事件条数
SSE_IDLE_TIMEOUT = 900                   # 订阅最长等待（秒）
SSE_PING_EVERY = 20                      # 空闲 ping 间隔（waitress channel_timeout 默认 120s）
DB_POLL_EVERY = 5.0                      # 订阅时查库兜底的间隔（秒）
MAX_QUESTION = 2000                      # 最终提示词上限（生成器拼装 + 手动编辑）

_BOOT = datetime.now()                   # 进程启动时间：早于它的 running 任务 = 上次运行遗留
_LOCK = threading.RLock()
_EVENTS = {}                             # task_id -> [事件 dict]
_RUNNING = set()                         # 进程内正在执行的任务 id
_STOP = set()                            # 已请求停止的任务 id（2026-10-14「停止」按钮）
_ACTIVE = 0                              # 进程内已占槽任务数（含刚提交未起线程）


def _reserve_slot():
    """占一个全局执行槽；满则拒绝，避免线程/内存被瞬间打满。"""
    global _ACTIVE
    with _LOCK:
        if _ACTIVE >= MAX_GLOBAL_RUNNING:
            return False
        _ACTIVE += 1
        return True


def _release_slot():
    global _ACTIVE
    with _LOCK:
        _ACTIVE = max(0, _ACTIVE - 1)



# ==================== 进程内进度缓冲 ====================

def _push(task_id, ev):
    with _LOCK:
        items = _EVENTS.setdefault(task_id, [])
        items.append(ev)
        if len(items) > EVENT_KEEP:
            del items[:len(items) - EVENT_KEEP]


def _events(task_id):
    with _LOCK:
        return list(_EVENTS.get(task_id) or [])


def _mark_running(task_id, on=True):
    with _LOCK:
        if on:
            _RUNNING.add(task_id)
        else:
            _RUNNING.discard(task_id)


def _live_ids():
    with _LOCK:
        return set(_RUNNING)


def drop_events(task_id):
    """任务与报告都删掉后清缓冲（避免无主事件常驻内存）。"""
    with _LOCK:
        _EVENTS.pop(task_id, None)
        _STOP.discard(task_id)


# ==================== 停止（取消） ====================

def request_stop(task_id):
    """请求停止一个正在跑的任务（进程内标记；执行线程在轮次/工具边界检查）。

    返回 False = 本进程内没在跑（已结束、或服务重启后的孤儿任务），
    调用方应提示用户刷新查看真实状态。
    """
    with _LOCK:
        if task_id in _RUNNING:
            _STOP.add(task_id)
            return True
        return False


def stop_requested(task_id):
    """执行线程查询：本任务是否被请求停止。"""
    with _LOCK:
        return task_id in _STOP


def _clear_stop(task_id):
    with _LOCK:
        _STOP.discard(task_id)



# ==================== 僵尸任务 ====================

def sweep_zombies():
    """把"库里 running、但本进程早就不在跑"的任务标记为 interrupted。

    两种来源：① 服务重启（任务 `created_at` 早于进程启动时间）；
    ② 执行线程异常丢失。判定后用户可重新提问，不会一直显示"进行中"。
    """
    live = _live_ids()
    now = datetime.now()
    changed = 0
    for task in AiTask.query.filter_by(status='running').all():
        if task.id in live:
            continue
        created = task.created_at or task.heartbeat
        if created and created >= _BOOT:
            continue                      # 本进程刚建的：可能正在排队启动
        task.status = 'interrupted'
        task.error = '服务重启或执行线程中断，任务未完成（可重新提问）'
        task.finished_at = now
        changed += 1
    if changed:
        try:
            db.session.commit()
        except Exception:                  # noqa: BLE001  清理失败不影响主流程
            db.session.rollback()
            logger.warning('AI 任务僵尸清理提交失败', exc_info=True)
    return changed


# ==================== 提交任务 ====================

def submit(user, exam, question, direction='', layer='', client_cfg=None, history=None,
           gen=None):
    """创建任务并起后台线程；返回 (ok, task_id 或 失败原因)。

    gen：提示词生成器的结构化选项（dict，可为空）——只做留档/回填，不参与执行；
    真正的提问文本是 `question`（生成器拼装后用户仍可手动编辑）。
    """
    try:
        sweep_zombies()
    except Exception:                      # noqa: BLE001
        logger.warning('AI 任务提交前清理僵尸失败', exc_info=True)
    running = AiTask.query.filter_by(user_id=user.id, status='running').count()
    if running >= MAX_RUNNING_PER_USER:
        return False, f'你还有 {running} 个分析任务在进行中，请等它跑完再提交'
    try:
        cfg = ai_service.resolve_key(user, client_cfg)
    except ValueError as e:
        return False, str(e)
    if not cfg:
        return False, '尚未配置 AI API Key（可配置个人 Key，或联系管理员配置公共 Key）'
    try:
        score_guard.resolve(user)          # 无可见范围直接拒，不浪费一次模型调用
    except PermissionError as e:
        return False, str(e)
    # P2：每日 AI 配额（任务提交即计数，避免后台任务绕过配额）
    from app.utils import ai_quota
    allowed, used, limit = ai_quota.consume(user.id)
    if not allowed:
        return False, f'今日 AI 调用次数已用完（{used}/{limit} 次），请明天再试'
    if not _reserve_slot():
        return False, f'当前 AI 任务较多（上限 {MAX_GLOBAL_RUNNING} 个），请稍后再试'
    gen_text = ''
    if isinstance(gen, dict) and gen:
        try:
            gen_text = json.dumps(gen, ensure_ascii=False)[:4000]
        except Exception:                  # noqa: BLE001  选项留档失败不影响任务
            gen_text = ''
    try:
        task = AiTask(user_id=user.id, exam_id=exam.id,
                      question=question[:MAX_QUESTION], gen=gen_text,
                      direction=(direction or '')[:10],
                      layer=(layer or '')[:30],
                      status='running', stage='已提交，正在准备成绩数据…',
                      tool_count=0, heartbeat=datetime.now(),
                      created_at=datetime.now())
        db.session.add(task)
        db.session.commit()
        _push(task.id, {'type': 'stage', 'text': '已提交，正在准备成绩数据…'})
        _mark_running(task.id)
        app = current_app._get_current_object()
        threading.Thread(
            target=_run, name=f'ai-task-{task.id}', daemon=True,
            args=(app, task.id, user.id, exam.id, question, direction, layer,
                  cfg, list(history or []))).start()
    except Exception as exc:               # noqa: BLE001
        _release_slot()
        db.session.rollback()
        logger.warning('AI 任务提交失败', exc_info=True)
        return False, f'任务提交失败：{exc}'
    return True, task.id


# ==================== 后台执行 ====================

def _run(app, task_id, user_id, exam_id, question, direction, layer, cfg, history):
    """线程体：必须在自己的 app context 内跑（`db.session` 是按 app context 作用域的）。"""
    with app.app_context():
        status, err, html_text, trace, scope_text = 'done', '', '', [], ''
        try:
            user = db.session.get(User, user_id)
            exam = db.session.get(Exam, exam_id)
            if user is None or exam is None:
                raise ValueError('任务对应的账号或考试已不存在')
            desc = score_guard.resolve(user).describe()
            scope_text = '可见年级：%s' % (desc.get('visible_grades'),)
            for ev in ai_agent.run(cfg, user, exam, question, direction=direction,
                                   layer=layer, scope_desc=scope_text, history=history,
                                   cancel_check=lambda: stop_requested(task_id)):
                _push(task_id, ev)
                _touch(task_id, ev)
                if ev.get('type') == 'html':
                    html_text = ev.get('text') or ''
                    trace = ev.get('trace') or []
                elif ev.get('type') == 'canceled':
                    status, err = 'canceled', str(ev.get('text') or '已按你的要求停止')
                    break
                elif ev.get('type') == 'error':
                    status, err = 'error', str(ev.get('text') or '')
                    break
            if status == 'done' and not (html_text or '').strip():
                # 2026-10-23：只有空白字符也算"没有内容"，否则会落一条空报告
                status, err = 'error', '模型没有返回报告内容，请把问题描述得更具体一些'
        except Exception as e:              # noqa: BLE001  线程内异常必须自己兜住
            logger.warning('AI 任务执行异常 task=%s', task_id, exc_info=True)
            status, err = 'error', f'执行异常（{type(e).__name__}）：{e}'
            _push(task_id, {'type': 'error', 'text': err})
        finally:
            try:
                _finish(task_id, status, err, html_text, trace, scope_text, cfg, question)
            except Exception:               # noqa: BLE001
                logger.warning('AI 任务收尾失败 task=%s', task_id, exc_info=True)
                db.session.rollback()
            _mark_running(task_id, False)
            _clear_stop(task_id)
            _release_slot()
            _push(task_id, {'type': 'done'})


def _touch(task_id, ev):
    """心跳 + 阶段落库（工具调用/阶段事件各一次，写频很低）。"""
    try:
        task = db.session.get(AiTask, task_id)
        if task is None:
            return
        task.heartbeat = datetime.now()
        if ev.get('type') == 'stage':
            task.stage = str(ev.get('text') or '')[:200]
        elif ev.get('type') == 'tool':
            task.tool_count = (task.tool_count or 0) + 1
            task.stage = ('已查询：%s' % (ev.get('name') or ''))[:200]
        db.session.commit()
    except Exception:                       # noqa: BLE001
        db.session.rollback()
        logger.warning('AI 任务心跳更新失败 task=%s', task_id, exc_info=True)


def _finish(task_id, status, err, html_text, trace, scope_text, cfg, question):
    """收尾：写报告（成功时）+ 任务状态落库。"""
    task = db.session.get(AiTask, task_id)
    if task is None:
        return
    task.status = status
    task.error = (err or '')[:500]
    task.finished_at = datetime.now()
    task.trace = json.dumps(trace, ensure_ascii=False)
    if status == 'done' and (html_text or '').strip():
        exam = db.session.get(Exam, task.exam_id)
        report = AiReport(user_id=task.user_id, exam_id=task.exam_id,
                          grade=(exam.grade if exam is not None else None),
                          scope_desc=(scope_text or '')[:100],
                          provider=cfg.get('provider'), model=cfg.get('model'),
                          content=html_text, charts=None, kind='html',
                          trace=task.trace)
        db.session.add(report)
        db.session.commit()
        task.report_id = report.id
        task.stage = '已完成'
        try:
            from app.utils.helpers import log_operation
            user = db.session.get(User, task.user_id)
            log_operation(user, '生成', 'AI 报告', report.id,
                          f'{exam.name if exam else ""}：{(question or "")[:60]}'
                          f'（工具 {len(trace)} 次）', module='grades')
        except Exception:                   # noqa: BLE001  留痕失败不影响结果
            logger.warning('AI 任务留痕失败 task=%s', task_id, exc_info=True)
    elif status == 'canceled':
        task.stage = '已停止'
        try:
            from app.utils.helpers import log_operation
            user = db.session.get(User, task.user_id)
            exam = db.session.get(Exam, task.exam_id)
            log_operation(user, '停止', 'AI 任务', task_id,
                          f'{exam.name if exam else ""}：{(question or "")[:60]}', module='grades')
        except Exception:                   # noqa: BLE001  留痕失败不影响结果
            logger.warning('AI 任务停止留痕失败 task=%s', task_id, exc_info=True)
    elif status != 'done':
        task.stage = '已失败'
    db.session.commit()


# ==================== 订阅（SSE 数据源） ====================

def subscribe(task_id, from_index=0):
    """生成事件流：先补发存量事件，再等待新事件；结束（done）即返回。

    路由层把每个事件转成 SSE 帧；`{'type':'ping'}` 由路由转成注释帧（保活）。
    """
    idx = max(0, int(from_index or 0))
    started = time.time()
    last_ping = time.time()
    last_db_poll = 0.0
    while True:
        items = _events(task_id)
        while idx < len(items):
            ev = items[idx]
            idx += 1
            yield ev
            if ev.get('type') == 'done':
                return
            last_ping = time.time()
        if time.time() - started > SSE_IDLE_TIMEOUT:
            yield {'type': 'error',
                   'text': '订阅超时（任务仍在后台运行，刷新页面即可查看最新进度）'}
            return
        now = time.time()
        if now - last_db_poll >= DB_POLL_EVERY:
            last_db_poll = now
            st = _db_status(task_id)
            if st is not None and st != 'running':
                yield {'type': 'done'}      # 进程重启/事件被清：按结束处理（内容走 GET 详情）
                return
        if now - last_ping >= SSE_PING_EVERY:
            yield {'type': 'ping'}
            last_ping = now
        time.sleep(0.3)


def _db_status(task_id):
    try:
        task = db.session.get(AiTask, task_id)
        return task.status if task is not None else 'interrupted'
    except Exception:                       # noqa: BLE001
        db.session.rollback()
        return None


# ==================== 查询 ====================

def list_tasks(user, exam_id=None, limit=30):
    """任务列表（**仅本人**：AI 分析属个人内容，2026-10-18 用户口径）。

    原先有个 `scope_all=True` 的"管理员看全部"分支 —— 已删除：连管理员也不该
    看到别人的 AI 分析（含逐生成绩与评语），需要排查时走操作日志，而不是读正文。
    """
    try:
        sweep_zombies()
    except Exception:                       # noqa: BLE001
        logger.warning('AI 任务列表清理僵尸失败', exc_info=True)
    q = AiTask.query.filter_by(user_id=user.id)
    if exam_id:
        q = q.filter_by(exam_id=int(exam_id))
    return q.order_by(AiTask.id.desc()).limit(max(1, min(int(limit or 30), 100))).all()


def task_events(task_id):
    """已产生的事件（供页面刷新后回放进度）。"""
    return _events(task_id)
