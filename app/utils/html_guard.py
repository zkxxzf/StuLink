# StuLink v1.19.47.30 2026-10-13
# AI 报告 HTML 白名单消毒：模型产出的 HTML 片段必须先过这里再进页面
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""AI 报告 HTML 消毒（白名单重建）。

背景：成绩汇报区的「AI 一句话分析」让模型**直接产出 HTML 报告**并插入页面。
模型输出不可信（提示词注入、用户自填的第三方中转站、服务商被投毒，都可能返回
`<script>` / `onerror=` / `javascript:`），因此这里按**白名单重建**，而不是
"黑名单删几个危险标签"。

策略（与前端 `static/js/grades_md.js` 同口径：先丢弃、再转义、绝不透传）：
  1. 只保留 `ALLOWED_TAGS`，其它标签一律丢弃（标签不要，文字留下）；
     `DROP_CONTENT_TAGS`（script/style/iframe/svg/表单控件等）**连内容一起丢**，
     避免脚本源码被当正文渲染出来。
  2. 只保留 `ALLOWED_ATTRS`：`class`（字符集过滤）、`colspan`/`rowspan`（数字）、
     `title`/`align`、`href`（仅 http/https）。**`style` 属性一律丢弃**——
     CSS 不是执行面，但 `position:fixed` 之类可做视觉钓鱼，颜色/字号一律走项目
     Bootstrap 类。
  3. 链接只允许 http(s)（并在解析后校验，防 `java\\tscript:` 之类的实体/空白绕过），
     `javascript:` / `data:` / `vbscript:` 一律丢弃。
  4. 文本节点用 `html.escape` 输出，属性值 `quote=True` 转义。
  5. 输入长度上限 `INPUT_MAX`，超出截断（防一次把几十万字符灌进页面）。

用法：
    from app.utils.html_guard import sanitize_report_html
    safe_html = sanitize_report_html(model_output)
"""
import html
import re
from html.parser import HTMLParser

# 允许的标签（报告常用排版；不含任何可执行/可嵌入外部的标签）
ALLOWED_TAGS = frozenset({
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'p', 'br', 'hr', 'div', 'span', 'small', 'strong', 'b', 'em', 'i', 'u', 's',
    'ul', 'ol', 'li', 'dl', 'dt', 'dd', 'blockquote', 'code', 'pre',
    'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption', 'colgroup', 'col',
    'a',
})

# 连内容一起丢弃的标签（丢标签但留文字会让脚本源码变成正文，很难看也很危险）
DROP_CONTENT_TAGS = frozenset({
    'script', 'style', 'iframe', 'frame', 'frameset', 'object', 'embed', 'applet',
    'svg', 'math', 'template', 'noscript', 'canvas', 'audio', 'video', 'source',
    'track', 'map', 'area', 'form', 'input', 'button', 'select', 'option', 'optgroup',
    'textarea', 'label', 'fieldset', 'legend', 'link', 'meta', 'base', 'html', 'head',
    'body', 'title', 'dialog', 'slot',
})

# 允许带属性的标签 → 允许的属性名（其余一律丢弃）
ALLOWED_ATTRS = {
    'class', 'title', 'align', 'colspan', 'rowspan', 'href',
}

# class 值：只留字母数字下划线连字符与空白，限长（类名不构成执行面，但要防超长注入）
_CLASS_OK = re.compile(r'[^A-Za-z0-9_\-\s]')
_CLASS_MAX = 200

# 输入上限：约 200KB（报告片段远远用不到这么多）
INPUT_MAX = 200_000

# 需要收尾补齐的容器标签（模型偶发漏闭合，会让后续页面结构被吃掉）
_PAIR_TAGS = frozenset({'div', 'span', 'p', 'table', 'thead', 'tbody', 'tfoot', 'tr',
                        'td', 'th', 'ul', 'ol', 'li', 'dl', 'dt', 'dd', 'blockquote',
                        'pre', 'code', 'small', 'strong', 'em', 'b', 'i', 'u', 's',
                        'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'a', 'caption'})
_VOID_TAGS = frozenset({'br', 'hr', 'col'})
# 空元素（没有结束标签）。若这类标签又落在 DROP_CONTENT_TAGS 里（input/meta/link/
# base/source/track/area/embed 等），**不能**递增 drop_depth —— 否则永远等不到结束
# 标签、drop_depth 恒 >0，其后整段报告被吞掉（2026-10-16 审计修复，fail-closed 但
# 会把模型输出的正常内容一并清空）。
_DROP_VOID = frozenset({'area', 'base', 'br', 'col', 'embed', 'hr', 'img',
                        'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'})


def _safe_href(value):
    """只放行 http(s) 链接；其余（javascript:/data:/vbscript: 等）返回空串。

    先按 HTML 实体还原、再去掉所有控制字符与空白后判定前缀，
    避免 `java\\tscript:`、`&#106;avascript:` 这类绕过。
    """
    raw = html.unescape(str(value or '')).strip()
    flat = re.sub(r'[\x00-\x20]+', '', raw).lower()
    if flat.startswith('http://') or flat.startswith('https://'):
        return raw
    return ''


def _clean_class(value):
    out = _CLASS_OK.sub('', str(value or ''))[:_CLASS_MAX].strip()
    return re.sub(r'\s+', ' ', out)


class _ReportSanitizer(HTMLParser):
    """白名单重建：只输出允许的标签与属性，文本转义。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.stack = []       # 已输出的未闭合标签
        self.drop_depth = 0   # >0 表示正处于"连内容一起丢"的标签内部

    # ---------- 标签 ----------
    def handle_starttag(self, tag, attrs):
        tag = (tag or '').lower()
        if tag in DROP_CONTENT_TAGS:
            if tag not in _DROP_VOID:
                self.drop_depth += 1
            return
        if self.drop_depth or tag not in ALLOWED_TAGS:
            return
        out = ['<', tag]
        attr_map = {k.lower(): v for k, v in attrs if k}
        if tag == 'a':
            href = _safe_href(attr_map.get('href'))
            if href:
                out.append(' href="%s"' % html.escape(href, quote=True))
                out.append(' rel="noopener noreferrer" target="_blank"')
        for name in ('class', 'title', 'align', 'colspan', 'rowspan'):
            if name not in attr_map or attr_map[name] in (None, ''):
                continue
            if name == 'class':
                value = _clean_class(attr_map[name])
            elif name in ('colspan', 'rowspan'):
                digits = re.sub(r'\D', '', str(attr_map[name]))[:2]
                value = digits or ''
            else:
                value = str(attr_map[name])[:200]
            if value:
                out.append(' %s="%s"' % (name, html.escape(value, quote=True)))
        out.append('>')
        self.parts.append(''.join(out))
        if tag not in _VOID_TAGS:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        tag = (tag or '').lower()
        # 自闭合写法（如 <script/>）：DROP 集合里的标签整块丢弃，且不得递增 drop_depth，
        # 否则永远等不到匹配的结束标签、其后内容被 handle_data/handle_starttag 一并吞掉。
        if tag in DROP_CONTENT_TAGS:
            return
        self.handle_starttag(tag, attrs)
        if tag in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        tag = (tag or '').lower()
        if tag in DROP_CONTENT_TAGS:
            if self.drop_depth:
                self.drop_depth -= 1
            return
        if self.drop_depth or tag not in ALLOWED_TAGS:
            return
        if tag in self.stack:
            # 从栈里回退到最近的同名标签（顺带补齐中间未闭合的标签）
            while self.stack:
                cur = self.stack.pop()
                self.parts.append('</%s>' % cur)
                if cur == tag:
                    break

    # ---------- 文本 ----------
    def handle_data(self, data):
        if self.drop_depth or not data:
            return
        self.parts.append(html.escape(data, quote=False))

    def handle_comment(self, data):        # 注释一律丢弃
        return

    def handle_decl(self, decl):           # <!DOCTYPE ...> 丢弃（报告只要片段）
        return

    def handle_pi(self, data):
        return

    # ---------- 收尾 ----------
    def close_all(self):
        while self.stack:
            self.parts.append('</%s>' % self.stack.pop())
        return ''.join(self.parts)


def sanitize_report_html(source):
    """把模型返回的 HTML 片段白名单化；返回可直接插入页面的安全片段。"""
    text = str(source or '')
    if not text:
        return ''
    if len(text) > INPUT_MAX:
        text = text[:INPUT_MAX]
    parser = _ReportSanitizer()
    try:
        parser.feed(text)
        parser.close()
    except Exception:      # noqa: BLE001  解析异常不应把报告打没：退化为纯文本
        return html.escape(text, quote=False)
    return parser.close_all()
