# StuLink v1.19.62.65 2026-10-22
# 受限 Markdown → HTML（AI 报告兜底渲染）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""受限 Markdown → HTML：模型不按 HTML 输出时的兜底排版。

背景（2026-10-22 用户截图反馈）：系统提示词要求模型**只输出 HTML 片段**，
但部分服务商/模型仍返回 Markdown（`# 标题`、`**加粗**`、`| 表 |` 原样显示在
报告里，看着就是"生成个这？"）。这里用与 `static/js/grades_md.js` **同一套
规则**把 Markdown 转成安全的 HTML，保证报告至少是排好版的。

规则刻意保持最小（与前端一致）：
  标题（# → h4、## → h5、###+ → h6）、分隔线、引用、表格、无序/有序列表、
  段落；行内只认 `**加粗**`、`` `代码` ``、`[文字](http...)`。
  先 `html.escape` 再转换，任何原始 HTML 都只会变成文本。

用法：
    from app.utils.md_guard import looks_like_html, markdown_to_html
    if not looks_like_html(text):
        text = markdown_to_html(text)
"""
import html
import re

_TAG_RE = re.compile(r'<[a-zA-Z][^>]*>')
_HEAD_RE = re.compile(r'^(#{1,6})\s+(.*)$')
_HR_RE = re.compile(r'^-{3,}$')
_UL_RE = re.compile(r'^\s*[-*]\s+(.*)$')
_OL_RE = re.compile(r'^\s*\d+[.、]\s+(.*)$')
_TABLE_SEP_RE = re.compile(r'^\s*\|?[\s:|-]+\|[\s:|-]*$')
_LINK_RE = re.compile(r'\[([^\]]+)\]\((https?://[^)\s]+)\)')


def looks_like_html(text):
    """内容里是否至少有一个 HTML 标签（没有就按 Markdown/纯文本处理）。"""
    return bool(_TAG_RE.search(str(text or '')))


def _plain_text(html_text):
    """把 HTML 片段还原成纯文本（保留段落/换行），用于识别"被 <p> 包住的 Markdown"。"""
    t = re.sub(r'</p\s*>', '\n\n', str(html_text or ''), flags=re.I)
    t = re.sub(r'<br\s*/?>', '\n', t, flags=re.I)
    t = re.sub(r'<[^>]+>', '', t)
    return html.unescape(t)


def is_markdown_text(text):
    """判断一段内容（可能已被 <p> 包过）是不是 **Markdown**。

    为什么需要：2026-10-22 用户截图里的报告，模型返回 Markdown，旧兜底把它
    包成 `<p># 标题</p><p>**加粗**…</p>` —— 只看"有没有 HTML 标签"会误判成
    HTML，于是 `#`、`**`、`| 表格 |` 就原样显示在页面上。
    这里按"纯文本里至少命中 2 类 Markdown 信号"判定，避免正常报告里的
    单个 `#` 或 `*` 被误转；同时要求**没有块级标签**（h1~h6/table/ul/ol/
    blockquote/div），正常 HTML 报告因此不会被误判。
    """
    raw = str(text or '')
    if re.search(r'<(?:h[1-6]|table|ul|ol|blockquote|div)\b', raw, re.I):
        return False
    plain = _plain_text(raw)
    score = 0
    if re.search(r'(?m)^\s*#{1,6}\s+\S', plain):
        score += 1
    if re.search(r'\*\*[^*\n]{1,80}\*\*', plain):
        score += 1
    if re.search(r'(?m)^\s*\|.+\|\s*$', plain):
        score += 1
    if re.search(r'(?m)^\s*[-*]\s+\S', plain):
        score += 1
    return score >= 2


def normalize_report_html(text):
    """统一入口：把 AI 报告正文变成"可安全渲染的 HTML"。

    三种输入：
      · 正常 HTML → 原样返回；
      · 纯 Markdown / 纯文本 → 转换；
      · 被 `<p>` 包住的历史 Markdown（旧兜底产物）→ 还原成纯文本再转换。
    """
    raw = str(text or '')
    if not raw.strip():
        return ''
    if not looks_like_html(raw):
        return markdown_to_html(raw)
    if is_markdown_text(raw):
        return markdown_to_html(_plain_text(raw))
    return raw


def _inline(s):
    """行内格式：先转义，再只认加粗/代码/安全链接。"""
    s = html.escape(str(s or ''), quote=False)
    s = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', s)
    s = re.sub(r'`([^`]+)`', r'<code>\1</code>', s)
    s = _LINK_RE.sub(
        r'<a href="\2" target="_blank" rel="noopener">\1</a>', s)
    return s


def markdown_to_html(text):
    """把受限 Markdown 转成安全的 HTML 片段（与 grades_md.js 同口径）。"""
    lines = str(text or '').splitlines()
    out = []
    i = 0
    total = len(lines)
    while i < total:
        t = lines[i].strip()
        if not t:
            i += 1
            continue
        # 标题：# → h4，## → h5，### 及以上 → h6
        m = _HEAD_RE.match(t)
        if m:
            level = min(len(m.group(1)) + 3, 6)
            out.append('<h%d class="ai-h">%s</h%d>'
                       % (level, _inline(m.group(2)), level))
            i += 1
            continue
        # 分隔线
        if _HR_RE.match(t):
            out.append('<hr class="ai-hr">')
            i += 1
            continue
        # 引用
        if t.startswith('>'):
            quote = []
            while i < total and lines[i].strip().startswith('>'):
                quote.append(lines[i].strip()[1:].strip())
                i += 1
            out.append('<blockquote class="ai-quote">%s</blockquote>'
                       % _inline(' '.join(quote)))
            continue
        # 表格：当前行含 | 且下一行是分隔行
        if ('|' in t and i + 1 < total
                and _TABLE_SEP_RE.match(lines[i + 1].strip())):
            header = [c.strip() for c in t.strip('|').split('|')]
            i += 2
            rows = []
            while i < total and '|' in lines[i]:
                cells = [c.strip() for c in lines[i].strip().strip('|').split('|')]
                rows.append('<tr>' + ''.join(
                    '<td>%s</td>' % _inline(c) for c in cells) + '</tr>')
                i += 1
            out.append(
                '<div class="table-responsive">'
                '<table class="table table-sm table-bordered ai-tbl">'
                '<thead><tr>' + ''.join('<th>%s</th>' % _inline(c)
                                        for c in header) + '</tr></thead>'
                '<tbody>' + ''.join(rows) + '</tbody></table></div>')
            continue
        # 无序列表
        if _UL_RE.match(t):
            items = []
            while i < total and _UL_RE.match(lines[i].strip()):
                items.append('<li>%s</li>'
                             % _inline(_UL_RE.match(lines[i].strip()).group(1)))
                i += 1
            out.append('<ul>' + ''.join(items) + '</ul>')
            continue
        # 有序列表
        if _OL_RE.match(t):
            items = []
            while i < total and _OL_RE.match(lines[i].strip()):
                items.append('<li>%s</li>'
                             % _inline(_OL_RE.match(lines[i].strip()).group(1)))
                i += 1
            out.append('<ol>' + ''.join(items) + '</ol>')
            continue
        # 段落
        out.append('<p class="ai-p">%s</p>' % _inline(t))
        i += 1
    return ''.join(out)
