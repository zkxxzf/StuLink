/* StuLink 受限 Markdown 渲染（AI 报告用）：先转义 HTML 再按白名单子集转换，防 XSS */
(function (global) {
    'use strict';
    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    // 行内：**加粗**、`代码`、安全链接
    function inline(s) {
        return esc(s)
            .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
            .replace(/`([^`]+)`/g, '<code>$1</code>')
            .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
                '<a href="$2" target="_blank" rel="noopener">$1</a>');
    }
    function renderMd(text) {
        if (!text) return '<p class="text-muted mb-0">（空报告）</p>';
        var lines = String(text).split(/\r?\n/);
        var html = [];
        var i = 0;
        while (i < lines.length) {
            var line = lines[i];
            var t = line.trim();
            if (t === '') { i++; continue; }
            // 标题
            var m = /^(#{1,4})\s+(.*)$/.exec(t);
            if (m) {
                var level = Math.min(m[1].length + 3, 6); // h4~h6
                html.push('<' + 'h' + level + ' class="ai-h">' + inline(m[2]) + '</' + 'h' + level + '>');
                i++; continue;
            }
            // 分隔线
            if (/^-{3,}$/.test(t)) { html.push('<hr class="ai-hr">'); i++; continue; }
            // 引用
            if (t.indexOf('> ') === 0 || t === '>') {
                var quote = [];
                while (i < lines.length && (lines[i].trim().indexOf('> ') === 0 ||
                       lines[i].trim() === '>')) {
                    quote.push(lines[i].trim().replace(/^>\s?/, ''));
                    i++;
                }
                html.push('<blockquote class="ai-quote">' + inline(quote.join(' ')) + '</blockquote>');
                continue;
            }
            // 表格：当前行含 | 且下一行是分隔行
            if (t.indexOf('|') >= 0 && i + 1 < lines.length &&
                /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(lines[i + 1].trim())) {
                var header = t.replace(/^\||\|$/g, '').split('|');
                i += 2; // 跳过分隔行
                var body = [];
                while (i < lines.length && lines[i].trim().indexOf('|') >= 0) {
                    var cells = lines[i].replace(/^\||\|$/g, '').split('|');
                    body.push('<tr>' + cells.map(function (c) {
                        return '<td>' + inline(c) + '</td>';
                    }).join('') + '</tr>');
                    i++;
                }
                html.push('<div class="table-responsive"><table class="table table-sm table-bordered ai-tbl">' +
                    '<thead><tr>' + header.map(function (c) {
                        return '<th>' + inline(c) + '</th>';
                    }).join('') + '</tr></thead><tbody>' + body.join('') +
                    '</tbody></table></div>');
                continue;
            }
            // 无序列表
            if (/^[-*]\s+/.test(t)) {
                var ul = [];
                while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
                    ul.push('<li>' + inline(lines[i].replace(/^\s*[-*]\s+/, '')) + '</li>');
                    i++;
                }
                html.push('<ul>' + ul.join('') + '</ul>');
                continue;
            }
            // 有序列表
            if (/^\d+[.、]\s+/.test(t)) {
                var ol = [];
                while (i < lines.length && /^\s*\d+[.、]\s+/.test(lines[i])) {
                    ol.push('<li>' + inline(lines[i].replace(/^\s*\d+[.、]\s+/, '')) + '</li>');
                    i++;
                }
                html.push('<ol>' + ol.join('') + '</ol>');
                continue;
            }
            // 段落
            html.push('<p class="ai-p mb-1">' + inline(t) + '</p>');
            i++;
        }
        return html.join('');
    }
    global.renderGradeMd = renderMd;
})(window);
