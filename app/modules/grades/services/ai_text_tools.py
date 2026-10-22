# StuLink v1.19.62.65 2026-10-22
# 文本态工具调用解析：模型把工具调用写成 DSML 标记时兜底
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""解析模型以**纯文本**形式吐出的工具调用。

背景（2026-10-22 用户截图）：`custom` 中转的 `cn:deepseek-v4.1-flash`
不走标准 function calling，把调用写成 DeepSeek 的 DSML 文本：

    <｜｜DSML｜｜ calls>
    <｜｜DSML｜｜ invoke name="layer_online">
    <｜｜DSML｜｜ parameter name="exam_id" string="false">24</｜｜DSML｜｜ parameter>
    </｜｜DSML｜｜ invoke>

旧逻辑只看 `message.tool_calls`，于是把这堆标签当成报告正文原样渲染。
这里把常见写法（半角/全角竖线、calls/tool_calls、结尾缺 `>`）统一解析成
OpenAI 的 tool_calls 结构，并把标签从正文里剔除。

对外：
    parse(text)            -> (calls, cleaned_text)
    has_markup(text)       -> bool
    strip_markup(text)     -> str
    results_message(items) -> str  （文本模式续跑时回灌给模型的 user 消息）
"""
import json
import re

_PIPES = r'(?:[|｜]\s*){1,2}'
_OPEN = r'<\s*' + _PIPES + r'\s*DSML\s*' + _PIPES
_ANY = r'<\s*/?\s*' + _PIPES + r'\s*DSML\s*' + _PIPES
_TOKEN_RE = re.compile(_ANY + r'[^>\n]*>?', re.I)
_BLOCK_OPEN_RE = re.compile(_OPEN + r'\s*(?:tool_)?calls\b[^>\n]*>?', re.I)
_BLOCK_CLOSE_RE = re.compile(
    r'<\s*/\s*' + _PIPES + r'\s*DSML\s*' + _PIPES
    + r'\s*(?:tool_)?calls\b[^>\n]*>?', re.I)
_INVOKE_RE = re.compile(_OPEN + r'\s*invoke\b([^>]*)>?', re.I)
_PARAM_RE = re.compile(_OPEN + r'\s*parameter\b([^>]*)>?', re.I)
_ATTR_RE = re.compile(
    r'([A-Za-z_]\w*)\s*=\s*["\'“”]([^"\'“”]*)["\'“”]')
_PIPE_FIX_RE = re.compile(_PIPES + r'DSML\s*' + _PIPES)
_JSON_CALL_RE = re.compile(r'<tool_call>\s*(\{.*?\})\s*</tool_call>',
                           re.S | re.I)


def _normalize(text):
    """全角竖线（｜）写法统一成半角，便于后续正则匹配。"""
    return _PIPE_FIX_RE.sub('|DSML|', str(text or ''))


def _attrs(text):
    """取标签属性：name="x" string="false"（单双引号都认）。"""
    return {k.lower(): v for k, v in _ATTR_RE.findall(text or '')}


def _coerce(value, is_string):
    """string="false" 的值按 JSON 解析（24 → int），其余保持字符串。"""
    if is_string == 'true':
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _make_call(name, args, idx):
    """拼成 OpenAI 协议的 tool_call 结构（id 只在本次进程内唯一）。"""
    return {'id': 'textcall_%d' % (idx + 1), 'type': 'function',
            'function': {'name': name,
                         'arguments': json.dumps(args, ensure_ascii=False)}}


def _parse_params(block):
    """解析一个 invoke 块里的 parameter 子标签 → 参数字典。"""
    args, starts = {}, list(_PARAM_RE.finditer(block))
    for i, m in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(block)
        attrs = _attrs(m.group(1))
        key = attrs.get('name', '').strip()
        if not key:
            continue
        value = _TOKEN_RE.sub('', block[m.end():end]).strip()
        args[key] = _coerce(value, attrs.get('string', '').lower())
    if not args:
        # 兜底：invoke 里直接放了一个 JSON 对象（不用 parameter 子标签）
        body = _TOKEN_RE.sub('', block).strip()
        if body.startswith('{'):
            try:
                data = json.loads(body)
            except ValueError:
                data = None
            if isinstance(data, dict):
                return data
    return args


def _parse_invokes(raw):
    """解析所有 invoke 块 → tool_calls 列表（每个 invoke 一个调用）。"""
    calls, starts = [], list(_INVOKE_RE.finditer(raw))
    for i, m in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(raw)
        name = _attrs(m.group(1)).get('name', '').strip()
        if name:
            args = _parse_params(raw[m.end():end])
            calls.append(_make_call(name, args, len(calls)))
    return calls


def _parse_json_calls(raw):
    """兼容 <tool_call>{"name":…,"arguments":{…}}</tool_call> 写法。"""
    calls = []
    for m in _JSON_CALL_RE.finditer(raw):
        try:
            obj = json.loads(m.group(1))
        except ValueError:
            continue
        name = str(obj.get('name') or '').strip()
        if name:
            calls.append(_make_call(name, obj.get('arguments') or {},
                                    len(calls)))
    return calls


def _strip_calls(raw):
    """整块剔除工具调用（含 parameter 的值），只留模型说的人话。"""
    spans, pos, found = [], 0, False
    while True:
        block = _BLOCK_OPEN_RE.search(raw, pos)
        if not block:
            break
        found = True
        close = _BLOCK_CLOSE_RE.search(raw, block.end())
        end = close.end() if close else len(raw)
        spans.append((block.start(), end))
        pos = end
    if not found:
        invokes = list(_INVOKE_RE.finditer(raw))
        blocks = invokes or list(_PARAM_RE.finditer(raw))
        for i, m in enumerate(blocks):
            end = blocks[i + 1].start() if i + 1 < len(blocks) else len(raw)
            spans.append((m.start(), end))
    for start, end in sorted(spans, reverse=True):
        raw = raw[:start] + raw[end:]
    return _JSON_CALL_RE.sub('', _TOKEN_RE.sub('', raw))


def parse(text):
    """解析文本态工具调用，返回 (calls, 去掉标签后的正文)。"""
    raw = _normalize(text)
    if not raw:
        return [], ''
    calls = _parse_invokes(raw)
    if not calls:
        calls = _parse_json_calls(raw)
    cleaned = re.sub(r'[ \t]+\n', '\n', _strip_calls(raw))
    return calls, cleaned.strip()


def has_markup(text):
    """正文里是否含 DSML / <tool_call> 标记（用于"别再原样显示"判断）。"""
    raw = _normalize(text)
    return bool(_TOKEN_RE.search(raw) or _JSON_CALL_RE.search(raw))


def strip_markup(text):
    """去掉所有工具调用标记，返回可展示的正文。"""
    return _strip_calls(_normalize(text)).strip()


def results_message(items, limit=16000):
    """把 [(name, args, ok, text)] 拼成一条 user 消息，供文本模式续跑。

    文本模式没有真正的 tool_call_id，回灌 `role=tool` 多数中转会报错，
    因此统一用一条说明清楚的 user 消息；总长按 limit 字符截断。
    """
    lines = ['【工具执行结果】系统已代为执行下列调用；下面的内容是真实取数结果，'
             '请直接引用，**不要再输出工具调用标签**。']
    used = 0
    for i, item in enumerate(items, 1):
        name, args, ok, text = item
        head = '%d. %s(%s) → %s' % (
            i, name,
            json.dumps(args or {}, ensure_ascii=False, sort_keys=True),
            '成功' if ok else '失败')
        room = max(0, limit - used - len(head))
        body = str(text or '')[:room]
        lines.append(head + '\n' + body)
        used += len(head) + len(body)
        if used >= limit:
            break
    return '\n\n'.join(lines)
