# StuLink v1.19.47.30 2026-10-13
# 成绩汇报区「AI 一句话分析」：工具调用循环（agent loop）+ HTML 报告产出
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""AI 一句话分析：让模型自己挑工具取数，最后产出可直接渲染的 HTML 报告。

与既有「AI 报告」（`ai_service.build_payload` 把整包成绩塞进 prompt 单次生成）的区别：
  · 旧口径：**先取全量数据**再让模型写 Markdown —— 数据量大、模型只能围绕给到的字段说；
  · 本口径（同 MCP 思路）：只给模型**工具清单**，由它自己决定看什么（各班汇总 / 单科各班 /
    分数段 / 分层上线 / 临界生 / 学生趋势…），取回的是与网页端同源的真实数字，
    最后**直接输出 HTML 片段**（经 `app.utils.html_guard` 白名单消毒后进页面）。

权限：每次工具调用都过 `score_guard`（与 MCP 服务同一份规则），
任课教师只能拿到所教班级里自己所教学科的数据、看不到总分口径。

LLM 侧只用到 `ai_service._chat_completions(..., tools=…)`（OpenAI 兼容 function calling），
Key 仍走 `ai_service.resolve_key`（浏览器本地 / 个人 / 全局），服务器不留存。
"""
import html
import json
import logging
import re
import time

from app.modules.grades.services import ai_service, ai_tools
from app.utils.html_guard import sanitize_report_html

logger = logging.getLogger(__name__)

MAX_ROUNDS = 4           # 最多几轮「模型 → 工具 → 模型」（prompt 已要求 2~4 次取数足够）
MAX_TOOL_CALLS = 12      # 全程工具调用次数上限（防模型刷工具）
MAX_TOOL_CHARS = 12000   # 单次工具结果回灌给模型的最大字符数（ai_tools 已先截断一轮）
# 总时长预算（秒）：多轮工具调用会跑几十秒到几分钟，期间一旦服务重启，任务就断在半路
# （进度在进程内）。因此宁可用"已取到的数据"先出一版报告，也不要无限磨蹭：
# 预算耗尽或轮数用尽时，最后一轮**禁止再调工具**（tool_choice='none'），只准出报告。
TIME_BUDGET = 150

AGENT_SYSTEM_PROMPT = (
    '你是 StuLink 中学成绩分析助手，为中学老师做年级/班级的成绩分析报告。\n'
    '你可以调用一组**只读**工具取真实成绩数据；数据范围已按登录账号的权限收敛'
    '（任课教师只能拿到所教班级里自己所教学科的数据，看不到总分口径），'
    '工具报「权限不足」时不要绕路，如实说明即可。\n\n'
    '【工作方式】\n'
    '1. 先想清楚要回答这个问题需要什么数据，再调用工具；'
    '**报告里出现的每一个数字都必须来自工具返回结果，禁止编造、禁止凭常识推测**。\n'
    '2. 一轮里可以同时调用多个工具；数据不够就再补调用（例如换方向、指定班级或科目）。\n'
    '3. 工具报错或无数据时如实说明（例如"该场考试未划线，无法统计上线率"），'
    '不要用别的口径顶替，也不要把错误当成 0。\n'
    '4. 通常 1~3 轮、2~4 次工具调用就能回答：每轮尽量把需要的工具一次调齐，'
    '不要重复调用同一个工具凑数，尽早给出报告。\n\n'
    '【输出格式】最终**只输出 HTML 片段**（不要 Markdown、不要 JSON、不要解释性前后缀）：\n'
    '- 用 <h4>/<h5> 小标题、<p>、<ul>/<li>、<table class="table table-sm"> 组织内容；\n'
    '- 允许标签：h1~h6、p、br、hr、div、span、small、strong、em、ul、ol、li、'
    'table、thead、tbody、tr、th、td、blockquote、code、a；\n'
    '- 允许 class 属性（页面已加载 Bootstrap），常用：text-danger、text-success、text-muted、'
    'text-end、fw-bold、small、table table-sm、badge bg-danger-subtle、alert alert-warning；\n'
    '- **禁止** style 属性与 script/iframe/img/表单控件（会被安全过滤丢弃）；\n'
    '- 表格只放关键行（不超过 15 行）；名单类只列前若干人并注明"仅列前 N 人"；\n'
    '- 建议结构：结论摘要（3~5 条要点）→ 数据支撑（表格）→ 需要关注的班级/学生'
    '（只陈述客观事实，不作人格化评价）→ 数据口径与限制说明。\n\n'
    '【纪律】保持客观，不评价教师与学校的教学，不给情绪化结论；'
    '涉及个体只列学号/姓名等客观事实。报告最后一行固定写：'
    '<p class="text-muted small">本报告由 AI 依据账号可见范围内的成绩数据生成，仅供参考。</p>'
)

_FENCE_RE = re.compile(r'```(?:html|HTML)?\s*(.*?)```', re.S)
_TAG_RE = re.compile(r'<[a-zA-Z][^>]*>')


def _extract_html(text):
    """从模型输出里取 HTML 片段（容错：模型用 ```html 围栏包起来时剥掉围栏）。"""
    body = str(text or '').strip()
    if not body:
        return ''
    m = _FENCE_RE.search(body)
    if m:
        body = m.group(1).strip()
    return _wrap_plain(body)


def _wrap_plain(text):
    """模型偶尔不按 HTML 输出（直接写纯文本/Markdown）时的兜底：

    没有任何标签就按空行分段包装，至少不会挤成一坨（消毒器会把 Markdown 的
    `|`、`#` 当普通文本，这是可接受的降级：内容仍真实，只是排版朴素）。
    """
    if _TAG_RE.search(text):
        return text
    escaped = html.escape(text, quote=False)
    blocks = [b.strip() for b in re.split(r'\n\s*\n', escaped) if b.strip()]
    return ''.join('<p>%s</p>' % b.replace('\n', '<br>') for b in blocks)


def _context_note(user, exam, direction='', layer='', scope_desc=''):
    parts = [
        '【本次分析对象】',
        f'考试：{exam.name}（{exam.grade}'
        + (f'，{exam.exam_date.strftime("%Y-%m-%d")}' if exam.exam_date else '') + '）',
        f'页面当前筛选：方向={direction or "未指定"}，层级={layer or "未指定"}',
        f'你的数据范围：{scope_desc or "由账号权限决定"}',
        '提示：不确定能看什么时可先调用 list_my_score_scope；'
        '不确定这场考试有什么数据时先调用 exam_overview。',
    ]
    return '\n'.join(parts)


def run(cfg, user, exam, question, direction='', layer='', scope_desc='',
        history=None, max_rounds=MAX_ROUNDS):
    """跑一轮「一句话分析」，生成事件流（供路由直通 SSE）。

    history：页面内的多轮追问上下文（[{role,content}]，只保留最近几条），
    由前端内存携带；不落库、不跨用户，仅作本会话的对话上下文。

    事件：
      {'type': 'stage', 'text': …}      阶段提示
      {'type': 'tool',  'name':…, 'summary':…, 'ok':bool, 'ms':int}
      {'type': 'html',  'text': 消毒后的 HTML, 'trace': [工具轨迹]}
      {'type': 'error', 'text': …}
    """
    messages = [
        {'role': 'system', 'content': AGENT_SYSTEM_PROMPT},
        {'role': 'system', 'content': _context_note(user, exam, direction, layer, scope_desc)},
    ]
    for item in (history or [])[-6:]:
        content = str((item or {}).get('content') or '')[:2000]
        if content:
            messages.append({'role': 'user' if (item or {}).get('role') == 'user'
                                     else 'assistant', 'content': content})
    messages.append({'role': 'user', 'content': question})
    trace, used_calls = [], 0
    started = time.time()

    for round_no in range(1, max_rounds + 1):
        left = TIME_BUDGET - (time.time() - started)
        # 已取到数据 + （最后一轮 或 预算耗尽）→ 收尾轮：只准出报告，不准再调工具
        forced = bool(trace) and (round_no >= max_rounds or left <= 0)
        used = int(time.time() - started)
        if forced:
            yield {'type': 'stage',
                   'text': f'已用 {used} 秒，正在汇总已查到的数据生成报告…'}
        else:
            yield {'type': 'stage',
                   'text': '正在思考需要哪些数据…' if round_no == 1
                           else f'正在整理第 {round_no} 轮分析…（已用 {used} 秒）'}
        ok, payload, sec = ai_service._chat_completions(
            cfg, messages, tools=ai_tools.TOOLS, temperature=0.2,
            tool_choice='none' if forced else None)
        if not ok:
            msg = str(payload)
            if 'tools' in msg.lower() or 'function' in msg.lower():
                msg += ('\n（该模型可能不支持工具调用，请在「用户设置 → AI」里换用支持 '
                        'function calling 的模型，如 deepseek-chat / qwen-plus / glm-4-plus）')
            yield {'type': 'error', 'text': msg}
            return
        try:
            msg = (payload.get('choices') or [{}])[0].get('message') or {}
        except (AttributeError, IndexError, TypeError):
            yield {'type': 'error', 'text': '服务商返回内容无法解析，请稍后重试'}
            return

        calls = msg.get('tool_calls') or []
        if forced and calls:
            # 少数服务商不认 tool_choice='none' → 忽略这轮的工具请求，避免又白白多跑一轮
            calls = []
        if not calls:
            # 收敛：这一轮没有工具调用 → 内容即最终报告
            html = _extract_html(msg.get('content') or '')
            if not html:
                yield {'type': 'error',
                       'text': (f'模型没有返回报告内容（本轮已查询 {len(trace)} 项数据），'
                                '请把问题描述得更具体一些后重试')}
                return
            yield {'type': 'html', 'text': sanitize_report_html(html), 'trace': trace}
            return

        # 回灌 assistant 的 tool_calls 消息（OpenAI 协议要求）
        messages.append({'role': 'assistant',
                         'content': msg.get('content') or '',
                         'tool_calls': calls})
        for call in calls:
            if used_calls >= MAX_TOOL_CALLS:
                yield {'type': 'error',
                       'text': f'工具调用次数超过上限（{MAX_TOOL_CALLS} 次），'
                               '请把问题拆小一些再试'}
                return
            used_calls += 1
            fn = call.get('function') or {}
            name = fn.get('name') or ''
            raw_args = fn.get('arguments') or '{}'
            started = time.time()
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                if not isinstance(args, dict):
                    raise ValueError('参数不是对象')
            except Exception:                                   # noqa: BLE001
                args = {}
                okc, text, summ = (False,
                                   '参数解析失败，请用合法 JSON 重新调用本工具', '')
            else:
                okc, text, summ = ai_tools.dispatch(name, args, user, exam.id)
            elapsed = int((time.time() - started) * 1000)
            if okc:
                yield {'type': 'tool', 'name': name, 'summary': summ or '已取数',
                       'ok': True, 'args': args, 'ms': elapsed}
            else:
                yield {'type': 'tool', 'name': name, 'summary': str(text)[:120],
                       'ok': False, 'args': args, 'ms': elapsed}
            trace.append({'tool': name, 'args': args, 'ok': bool(okc),
                          'summary': summ or (str(text)[:160] if not okc else ''),
                          'ms': elapsed})
            messages.append({'role': 'tool', 'tool_call_id': call.get('id') or '',
                             'content': str(text)[:MAX_TOOL_CHARS]})

    yield {'type': 'error',
           'text': f'已达到最大分析轮数（{max_rounds} 轮）仍未收敛，'
                   '请把问题描述得更小一些（例如指定某个班或某个科目）'}
