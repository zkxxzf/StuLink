/* StuLink v1.19.47.29 成绩汇报区 · AI 一句话分析（任务模式）
 * 2026-10-13：后端改为「服务端任务」——POST /grades/ai/ask 只建任务并返回 task_id，
 * 生成在后台线程继续；这里用 AiTaskUI.subscribe(task_id)（EventSource，自带断线重连）
 * 看进度。**切页/刷新不中断生成**：回到本页会自动恢复该考试最近一条任务（含进度与报告）。
 * 完整历史与跨考试的提问在「AI 分析工作台」/grades/ai。
 * 安全：报告正文由后端 app/utils/html_guard.py 白名单消毒后下发，
 *       前端只经 AiTaskUI.renderReport 插入（服务端那一份，绝不拼接）。
 * Key 来自 window.StuLinkAI.payload()（浏览器本地 localStorage），服务器不留存。
 * Copyright (c) 2026 zkxxzf. Apache License 2.0 */
(function () {
    'use strict';
    if (!window.jQuery) { return; }
    var $ = window.jQuery;
    var UI = window.AiTaskUI;
    if (!UI) { return; }        // 共享模块缺失时不启用（避免出现半残交互）

    var HISTORY = [];           // 本次页面会话的多轮上下文（[{role,content}]）
    var state = { id: null, close: null };

    /* ---------- 取页面上下文 ---------- */
    function examId() {
        var cur = ($('#rpExam').val() || '').trim();
        if (cur) { return cur; }
        var app = document.getElementById('reportApp');
        return app ? ($(app).data('exam-id') || '') : '';
    }
    function curDir() { return ($('#rpDir').val() || '').trim(); }
    function curLayer() { return ($('#rpLayer').val() || '').trim(); }
    function aiPayload() {
        if (window.StuLinkAI && typeof window.StuLinkAI.payload === 'function') {
            return window.StuLinkAI.payload();
        }
        return {};
    }

    /* ---------- 进度区 ---------- */
    function setStage(text) {
        $('#rpAiStage').removeClass('rp-ai-stage-err').addClass('text-muted').text(text || '');
    }
    function setErr(text) {
        $('#rpAiStage').removeClass('text-muted').addClass('rp-ai-stage-err')
            .text('分析失败：' + (text || '未知错误'));
    }
    function setBusy(on) {
        $('#rpAiRun').prop('disabled', on);
        var $label = $('#rpAiRun .rp-ai-run-label');
        if (on) {
            if (!$('#rpAiRun .spinner-border').length) {
                $('#rpAiRun').prepend($('<span class="spinner-border spinner-border-sm me-1"></span>'));
            }
            $label.text('分析中…');
        } else {
            $('#rpAiRun .spinner-border').remove();
            $label.text('AI 分析');
        }
    }
    function stopStream() {
        if (state.close) {
            try { state.close(); } catch (e) { /* 已关闭 */ }
            state.close = null;
        }
    }
    /* 任务被服务重启打断时：问题已经回填在输入框里，这里加个「重跑」按钮，
     * 点一下就用原问题重新提交（省得再翻历史）。元素用 jQuery 创建，不拼 innerHTML。 */
    function addRerunHint(question) {
        var $btn = $('<button type="button" class="btn btn-sm btn-outline-primary ms-2">' +
            '<i class="bi bi-arrow-clockwise"></i> 重跑</button>');
        $btn.on('click', function () {
            if (question && !($('#rpAiQ').val() || '').trim()) { $('#rpAiQ').val(question); }
            submit();
        });
        $('#rpAiStage').append($btn);
    }

    /* ---------- 订阅任务进度 ---------- */
    function subscribe(taskId) {
        stopStream();
        state.close = UI.subscribe(taskId, {
            onStage: setStage,
            onTool: function (ev) { UI.applyEvents($('#rpAiStage'), $('#rpAiTools'), [ev]); },
            onHtml: function (html) { UI.renderReport($('.rp-ai-report'), html); },
            onSaved: function (rid) {
                setStage('报告已保存到「AI 分析工作台」'
                    + (rid === undefined || rid === null ? '' : '（编号 ' + rid + '）'));
            },
            onError: setErr,
            onDone: function () { setBusy(false); }
        });
    }

    /* ---------- 提交一句话分析 ---------- */
    function submit() {
        var q = ($('#rpAiQ').val() || '').trim();
        if (!q) { $('#rpAiQ').trigger('focus'); return; }
        var eid = examId();
        $('#rpAiOut').removeClass('d-none');
        if (eid === '' || eid === undefined || eid === null) {
            setErr('未确定考试（请先选择一场考试）');
            return;
        }
        if (window.StuLinkAI && typeof window.StuLinkAI.ensure === 'function'
                && !window.StuLinkAI.ensure()) {
            return;                 // 本机未配 Key：已给出引导
        }
        setBusy(true);
        $('#rpAiTools').empty();
        UI.renderReport($('.rp-ai-report'), '');
        setStage('正在提交…');
        $.ajax({
            url: '/grades/ai/ask',
            method: 'POST',
            contentType: 'application/json',
            headers: { 'X-CSRFToken': (typeof window.csrfToken === 'string' ? window.csrfToken : '') },
            data: JSON.stringify({
                exam_id: eid, question: q, direction: curDir(), layer: curLayer(),
                history: HISTORY.slice(), ai: aiPayload()
            })
        }).done(function (res) {
            if (!res || !res.success) {
                setErr((res && res.message) || '提交失败');
                setBusy(false);
                return;
            }
            state.id = res.data.task_id;
            setStage('已提交，正在生成…（切走或刷新本页也不会中断）');
            HISTORY.push({ role: 'user', content: q });
            subscribe(state.id);
        }).fail(function (x) {
            var msg = '提交失败';
            try { msg = (x.responseJSON && x.responseJSON.message) || msg; }
            catch (e) { /* 忽略解析失败 */ }
            setErr(msg);
            setBusy(false);
        });
    }

    /* ---------- 回到本页时恢复该考试最近一条任务 ---------- */
    function restore() {
        var eid = examId();
        if (!eid) { return; }
        $.getJSON('/grades/ai/tasks?limit=1&exam_id=' + encodeURIComponent(eid), function (res) {
            var rows = (res && res.data) || [];
            if (!rows.length) { return; }
            var t = rows[0];
            state.id = t.id;
            $('#rpAiOut').removeClass('d-none');
            if (t.question) { $('#rpAiQ').val(t.question); }
            $.getJSON('/grades/ai/tasks/' + encodeURIComponent(t.id), function (r2) {
                var d = (r2 && r2.data) || {};
                UI.applyEvents($('#rpAiStage'), $('#rpAiTools'), d.events || []);
                if (d.md) {
                    // 旧口径 Markdown 报告（后端用负数 id 包装的历史条目）
                    var $body = $('.rp-ai-report').empty();
                    if (window.renderGradeMd) { $body.append(window.renderGradeMd(d.md)); }
                    else { $body.text(d.md); }
                } else {
                    UI.renderReport($('.rp-ai-report'), d.html);
                }
                if (d.status === 'running') {
                    setStage(d.stage || '生成中…');
                    setBusy(true);
                    subscribe(d.id);            // 断线续播：进度从服务端补发
                } else if (d.status === 'done') {
                    setStage('上次分析（' + (d.finished_at || d.created_at || '') + '）');
                } else if (d.status === 'error') {
                    setErr(d.error || '生成失败');
                } else if (d.status === 'interrupted') {
                    setStage('上次任务被中断（服务已重启），问题已填回输入框，可直接重跑');
                    addRerunHint(d.question || t.question);
                }
            });
        });
    }

    /* ---------- 交互绑定 ---------- */
    $(function () {
        $('#rpAiRun').on('click', submit);
        $('#rpAiQ').on('keydown', function (e) {
            if (e.key === 'Enter') { e.preventDefault(); submit(); }
        });
        $('#rpAiChips').on('click', '.rp-ai-chip', function () {
            $('#rpAiQ').val(($(this).text() || '').trim()).trigger('focus');
        });
        $('#rpAiCopy').on('click', function () {
            var txt = UI.plainTextOf($('.rp-ai-report'));
            if (!txt) { setStage('暂无可复制的内容'); return; }
            UI.copyText(txt);
            setStage('已复制报告正文');
        });
        $('#rpAiExport').on('click', function () {
            var html = $('.rp-ai-report').html() || '';
            if (!html) { setStage('暂无可导出的报告'); return; }
            var app = document.getElementById('reportApp');
            var examName = ($('#rpExam option:selected').text() || '').trim();
            var title = (examName || (app ? $(app).data('exam-id') : '') || '成绩汇报')
                + ' · ' + (($('#rpAiQ').val() || '').trim().slice(0, 40) || 'AI 分析');
            if (UI.exportHtml(title, html)) { setStage('已导出独立 HTML 文件'); }
        });
        restore();
    });

    // 供调试/复用
    window.rpAi = { submit: submit, subscribe: subscribe, restore: restore };
})();
