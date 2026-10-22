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
import json
import logging
import re
import time

from app.modules.grades.services import ai_service, ai_text_tools, ai_tools
from app.utils.html_guard import sanitize_report_html
from app.utils.md_guard import normalize_report_html

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
    '- **只给片段**：不要 <!DOCTYPE html>、<html>、<head>、<body> 等整篇文档外壳；\n'
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


def _extract_html(text):
    """从模型输出里取 HTML 片段。

    容错四件事（2026-10-22 用户截图反馈后补）：
      1. 模型用 ```html 围栏包起来 → 剥掉围栏；
      2. 模型直接返回 **Markdown**（`# 标题`、`**加粗**`、`| 表 |`）→
         用 `md_guard.markdown_to_html` 转成 HTML（与 grades_md.js 同口径），
         否则会以源码形式显示在报告里；
      3. 纯文本 → 同样走 Markdown 转换（至少分段成 <p>）；
      4. 混进正文的 DSML / <tool_call> 工具调用标记 → 先剔除再排版。
    """
    body = ai_text_tools.strip_markup(text)
    if not body:
        return ''
    m = _FENCE_RE.search(body)
    if m:
        body = m.group(1).strip()
    return normalize_report_html(body)


def _context_note(user, exam, direction='', layer='', scope_desc=''):
    parts = [
        '【本次分析对象】',
        # 2026-10-18：必须写明 exam_id。此前只给考试名，模型会从名字里"猜"数字
        # （2024级202505月考 → exam_id=202505292024），工具回"找不到考试"，整轮作废。
        f'考试：{exam.name}（exam_id={exam.id}；{exam.grade}'
        + (f'，{exam.exam_date.strftime("%Y-%m-%d")}' if exam.exam_date else '') + '）',
        f'调用工具时 exam_id 一律用 {exam.id}（本场考试），不要从考试名、日期或'
        '页面元素里推测数字；要换别的考试，先用 list_exams 拿到真实 id。',
        f'页面当前筛选：方向={direction or "未指定"}，层级={layer or "未指定"}',
        f'你的数据范围：{scope_desc or "由账号权限决定"}',
        '提示：不确定能看什么时可先调用 list_my_score_scope；'
        '不确定这场考试有什么数据时先调用 exam_overview。',
    ]
    return '\n'.join(parts)


def run(cfg, user, exam, question, direction='', layer='', scope_desc='',
        history=None, max_rounds=MAX_ROUNDS, cancel_check=None):
    """跑一轮「一句话分析」，生成事件流（供路由直通 SSE）。

    history：页面内的多轮追问上下文（[{role,content}]，只保留最近几条），
    由前端内存携带；不落库、不跨用户，仅作本会话的对话上下文。

    cancel_check：可调用对象（返回 True = 用户已点「停止」）。在**每一轮模型调用前
    与每次工具调用前**检查一次 —— 模型调用本身不可中断（HTTP 请求在飞），
    所以"停止"的语义是"当前这一步跑完就停"，最多多等一次模型往返/一个工具查询。

    事件：
      {'type': 'stage', 'text': …}      阶段提示
      {'type': 'tool',  'name':…, 'summary':…, 'ok':bool, 'ms':int}
      {'type': 'html',  'text': 消毒后的 HTML, 'trace': [工具轨迹]}
      {'type': 'canceled', 'text': …}   用户点了停止
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
        if cancel_check and cancel_check():
            yield {'type': 'canceled', 'text': '已按你的要求停止'}
            return
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
        if forced:
            # tool_choice=none 对部分中转无效，再用一句话把模型按回收尾
            messages.append({'role': 'user',
                             'content': '【系统】不要再调用任何工具，直接基于'
                                        '已查询到的数据输出最终 HTML 报告。'})
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
        text_mode = False
        if not calls:
            # 2026-10-22：部分中转/模型把工具调用写进 content 的 DSML 文本
            # （<|DSML|invoke name="…">），旧逻辑会当成最终报告原样显示。
            calls, cleaned = ai_text_tools.parse(msg.get('content') or '')
            if calls:
                text_mode = True
                msg['content'] = cleaned
                logger.info('文本态工具调用：%s',
                            ','.join(c.get('function', {}).get('name', '')
                                     for c in calls))
        elif ai_text_tools.has_markup(msg.get('content') or ''):
            # 原生 tool_calls 与文本标记并存时，正文里的标记也要剔掉
            msg['content'] = ai_text_tools.strip_markup(
                msg.get('content') or '')
        if forced and calls:
            # 少数服务商不认 tool_choice='none' → 忽略这轮的工具请求，避免又白白多跑一轮
            calls = []
        if not calls:
            # 收敛：这一轮没有工具调用 → 内容即最终报告
            raw = msg.get('content') or ''
            html = _extract_html(raw)
            if not html:
                if ai_text_tools.has_markup(raw):
                    yield {'type': 'error',
                           'text': ('模型把工具调用写成了文本标签，但格式无法解析；'
                                    '请换用支持 function calling 的模型'
                                    '（如 deepseek-chat / qwen-plus / glm-4-plus）'
                                    '后重试')}
                else:
                    yield {'type': 'error',
                           'text': (f'模型没有返回报告内容'
                                    f'（本轮已查询 {len(trace)} 项数据），'
                                    '请把问题描述得更具体一些后重试')}
                return
            safe = sanitize_report_html(html)
            if not safe.strip():
                # 2026-10-23：模型可能只回了空白/被安全过滤清空的内容，
                # 旧逻辑会把它当"成功报告"落库，页面显示"报告内容已不存在"。
                yield {'type': 'error',
                       'text': '模型返回的报告内容为空（空白片段或被安全过滤'
                               '清空），请重试或换用其他模型'}
                return
            yield {'type': 'html', 'text': safe, 'trace': trace}
            return

        if text_mode:
            # 文本模式：服务商不认 tool_calls，只回灌模型自己说过的话
            messages.append({'role': 'assistant',
                             'content': msg.get('content') or '（已请求取数）'})
        else:
            # 回灌 assistant 的 tool_calls 消息（OpenAI 协议要求）
            messages.append({'role': 'assistant',
                             'content': msg.get('content') or '',
                             'tool_calls': calls})
        results = []
        for call in calls:
            if cancel_check and cancel_check():
                yield {'type': 'canceled', 'text': '已按你的要求停止'}
                return
            if used_calls >= MAX_TOOL_CALLS:
                yield {'type': 'error',
                       'text': f'工具调用次数超过上限（{MAX_TOOL_CALLS} 次），'
                               '请把问题拆小一些再试'}
                return
            used_calls += 1
            fn = call.get('function') or {}
            name = fn.get('name') or ''
            raw_args = fn.get('arguments') or '{}'
            # 注意：不能复用外层 `started`（总计时）——此前这里覆盖它，导致
            # TIME_BUDGET 预算与 stage 里的"已用 N 秒"每次工具调用后归零（恒显示 0 秒）。
            t0 = time.time()
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
            elapsed = int((time.time() - t0) * 1000)
            if okc:
                yield {'type': 'tool', 'name': name, 'summary': summ or '已取数',
                       'ok': True, 'args': args, 'ms': elapsed}
            else:
                yield {'type': 'tool', 'name': name, 'summary': str(text)[:120],
                       'ok': False, 'args': args, 'ms': elapsed}
            trace.append({'tool': name, 'args': args, 'ok': bool(okc),
                          'summary': summ or (str(text)[:160] if not okc else ''),
                          'ms': elapsed})
            if text_mode:
                results.append((name, args, bool(okc), text))
            else:
                messages.append({'role': 'tool',
                                 'tool_call_id': call.get('id') or '',
                                 'content': str(text)[:MAX_TOOL_CHARS]})
        if text_mode:
            messages.append({'role': 'user',
                             'content': ai_text_tools.results_message(results)})

    yield {'type': 'error',
           'text': f'已达到最大分析轮数（{max_rounds} 轮）仍未收敛，'
                   '请把问题描述得更小一些（例如指定某个班或某个科目）'}
