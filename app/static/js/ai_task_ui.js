/* StuLink · AI 分析工作台 · 共享前端模块（v1.19.47.x 2026-10-13）
 * 成绩汇报区改造 / AI 分析工作台共用：任务进度订阅（SSE）/ 报告渲染 / 导出。
 * 安全约定：报告正文只来自后端 app/utils/html_guard.py 白名单消毒后的下发结果，
 *          本模块只负责「插入这一份」，**绝不拼接**任何动态 HTML；
 *          UI 文本一律走 jQuery .text() 或 esc()（无裸 innerHTML 赋值）。
 * Copyright (c) 2026 zkxxzf. Apache License 2.0 */
(function (window) {
    'use strict';
    if (!window.jQuery) { return; }
    var $ = window.jQuery;

    /* ---------- 转义（优先复用全局 escHtml，见 common.js） ---------- */
    var HTML_MAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
    function esc(s) {
        if (typeof window.escHtml === 'function') { return window.escHtml(s); }
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return HTML_MAP[c]; });
    }

    /* ---------- 状态字典 ---------- */
    var STATUS_META = {
        running: { text: '进行中', cls: 'bg-warning-subtle text-warning-emphasis' },
        done: { text: '已完成', cls: 'bg-success-subtle text-success-emphasis' },
        error: { text: '失败', cls: 'bg-danger-subtle text-danger-emphasis' },
        interrupted: { text: '已中断', cls: 'bg-secondary-subtle text-secondary-emphasis' }
    };
    function statusMeta(status) {
        return STATUS_META[status]
            || { text: status || '未知', cls: 'bg-secondary-subtle text-secondary-emphasis' };
    }

    /* ---------- 工具轨迹行（唯一构造点；全程 .text()，不拼 HTML） ---------- */
    function toolRow(ev) {
        ev = ev || {};
        var $row = $('<div class="ai-tool"></div>');
        var $icon = $('<i class="bi me-1"></i>')
            .addClass(ev.ok === false ? 'bi-x-circle text-danger' : 'bi-tools');
        $row.append($icon);
        $row.append($('<span></span>').text(ev.summary || ev.name || ''));
        var meta = [];
        if (ev.name) { meta.push(ev.name); }
        if (ev.ms !== undefined && ev.ms !== null) { meta.push(ev.ms + 'ms'); }
        if (meta.length) {
            $row.append($('<span class="ms-2 text-muted"></span>').text(meta.join(' · ')));
        }
        return $row;
    }

    /* ---------- SSE 订阅 ----------
     * 用原生 EventSource（GET，自带断线重连）：先补发服务端已产生的事件，再实时推。
     * 帧格式：data: {"type":"stage"|"tool"|"html"|"saved"|"error"|"done", ...}\n\n
     * handlers = {onStage, onTool, onHtml, onSaved, onError, onDone}
     * 返回 close()。收到 done 主动关闭（否则 EventSource 会无限重连、重复补发）。 */
    function subscribe(taskId, handlers) {
        handlers = handlers || {};
        var url = '/grades/ai/tasks/' + encodeURIComponent(taskId) + '/stream';
        var es = new EventSource(url);
        var closed = false;
        function close() {
            closed = true;
            try { es.close(); } catch (e) { /* 已关闭 */ }
        }
        es.onmessage = function (ev) {
            var data;
            try { data = JSON.parse(ev.data); } catch (e) { return; }   // 忽略半包 / 非 JSON 帧
            if (!data || !data.type) { return; }
            if (data.type === 'stage') {
                if (handlers.onStage) { handlers.onStage(data.text || ''); }
            } else if (data.type === 'tool') {
                if (handlers.onTool) { handlers.onTool(data); }
            } else if (data.type === 'html') {
                // data.text 是后端白名单消毒后的 HTML：只转交渲染，不拼接
                if (handlers.onHtml) { handlers.onHtml(data.text || ''); }
            } else if (data.type === 'saved') {
                if (handlers.onSaved) { handlers.onSaved(data.report_id); }
            } else if (data.type === 'error') {
                if (handlers.onError) { handlers.onError(data.text || '未知错误'); }
            } else if (data.type === 'done') {
                if (handlers.onDone) { handlers.onDone(); }
                close();
            }
        };
        // EventSource 的 onerror 只代表「连接异常」，自带重连——不是业务错误
        es.onerror = function () {
            if (!closed && handlers.onStage) { handlers.onStage('连接中断，正在重连…'); }
        };
        return close;
    }

    /* ---------- 报告渲染 / 读取 ---------- */
    /** 报告正文只来自服务端消毒结果：整块插入，绝不拼接 */
    function renderReport($el, html) {
        if (!$el || !$el.length) { return; }
        $el.html(html || '');
    }

    /** 取渲染结果的纯文本（DOMParser，不触发页面执行） */
    function plainTextOf($el) {
        var html = ($el && $el.length) ? ($el.html() || '') : '';
        if (!html) { return ''; }
        var doc = new DOMParser().parseFromString(html, 'text/html');
        return (doc.body.textContent || '').replace(/\n{3,}/g, '\n\n').trim();
    }

    /* ---------- 导出独立 HTML ---------- */
    function exportHtml(title, html) {
        var t = title || 'AI 分析';
        var body = html || '';
        if (!body) { return false; }
        var css = 'body{font-family:"Microsoft YaHei",微软雅黑,system-ui,sans-serif;'
            + 'max-width:900px;margin:32px auto;padding:0 18px;line-height:1.7;color:#1e293b;}'
            + 'h1{font-size:20px;}h5{color:#1d4ed8;margin-top:22px;}table{border-collapse:collapse;}'
            + 'td,th{border:1px solid #cbd5e1;padding:4px 8px;}'
            + '.foot{margin-top:28px;color:#94a3b8;font-size:12px;}';
        var parts = [
            '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            '<title>', esc(t), '</title>',
            '<style>', css, '</style></head><body>',
            '<h1>', esc(t), '</h1>',
            body,
            '<p class="foot">本报告由 AI 生成，仅供参考，不代表学校评价结论。</p>',
            '</body></html>'
        ];
        var blob = new Blob([parts.join('')], { type: 'text/html;charset=utf-8' });
        var url = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url;
        a.download = 'AI分析_' + t.replace(/[\\/:*?"<>|\s]+/g, '') + '_'
            + new Date().toISOString().slice(0, 10) + '.html';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
        return true;
    }

    /* ---------- 复制（clipboard + textarea 兜底） ---------- */
    function copyText(text) {
        var txt = text || '';
        if (!txt) { return false; }
        function fallback() {
            var ta = document.createElement('textarea');
            ta.value = txt;
            ta.setAttribute('readonly', 'readonly');
            ta.style.position = 'fixed';
            ta.style.left = '-9999px';
            document.body.appendChild(ta);
            ta.select();
            var ok = false;
            try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
            document.body.removeChild(ta);
            return ok;
        }
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(txt).catch(function () { fallback(); });
            return true;
        }
        return fallback();
    }

    /* ---------- 回放已有进度（打开任务时用） ----------
     * events: [{type:'stage'|'tool', text?, name?, summary?, ok?, ms?}]
     * stage 事件覆盖式写入；tool 事件追加成行。 */
    function applyEvents($stageEl, $toolsEl, events) {
        if (!events || !events.length) { return; }
        for (var i = 0; i < events.length; i++) {
            var ev = events[i];
            if (!ev || !ev.type) { continue; }
            if (ev.type === 'stage') {
                if ($stageEl && $stageEl.length) {
                    $stageEl.removeClass('ai-stage-err').addClass('text-muted').text(ev.text || '');
                }
            } else if (ev.type === 'tool') {
                if ($toolsEl && $toolsEl.length) { $toolsEl.append(toolRow(ev)); }
            }
        }
    }

    window.AiTaskUI = {
        esc: esc,
        subscribe: subscribe,
        renderReport: renderReport,
        plainTextOf: plainTextOf,
        exportHtml: exportHtml,
        copyText: copyText,
        STATUS_META: STATUS_META,
        statusMeta: statusMeta,
        applyEvents: applyEvents
    };
})(window);
