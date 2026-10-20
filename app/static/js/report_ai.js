/* StuLink v1.19.49.48 成绩汇报区 · AI 一句话分析（任务模式）
 * 2026-10-13：后端改为「服务端任务」——POST /grades/ai/ask 只建任务并返回 task_id，
 * 生成在后台线程继续；这里用 AiTaskUI.subscribe(task_id)（EventSource，自带断线重连）
 * 看进度。**切页/刷新不中断生成**：回到本页会自动恢复该考试最近一条任务（含进度与报告）。
 * 完整历史与跨考试的提问在「AI 分析工作台」/grades/ai。
 * 2026-10-14：接入「提示词生成器」（ai_prompt_gen.js 的工厂实例，范围/侧重/输出格式/
 * 附加要求 → 拼装提示词 → 存模板/复制）；进行中可「停止」，中断/失败/停止后
 * 「重新编辑提示词」把当时的选择与提示词无损带回生成器。
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
    var state = { id: null, close: null, task: null };

    /* 提示词生成器：本页自建实例（DOM 前缀 rp，考试来源＝本页 examId()） */
    var PG = (typeof window.AiPromptGenFactory === 'function')
        ? window.AiPromptGenFactory({
            panel: '#rpGenPanel', toggle: '#rpGenToggle',
            classes: '#rpGenClasses', subjects: '#rpGenSubjects',
            focus: '#rpGenFocus', output: '#rpGenOutput',
            extra: '#rpGenExtra', presets: '#rpGenExtraPresets',
            tplName: '#rpGenTplName', tplSave: '#rpGenTplSave',
            tpl: '#rpGenTpl', tplDel: '#rpGenTplDel', build: '#rpGenBuild',
            prompt: '#rpAiQ',
            examId: examId
        })
        : null;

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

    /* ---------- 状态按钮：进行中→「停止」；结束/失败/停止→「重新编辑提示词」 ---------- */
    function setStopVisible(on) {
        $('#rpAiStop').toggleClass('d-none', !on).prop('disabled', false);
    }
    function setReeditVisible(on) {
        $('#rpAiReedit').toggleClass('d-none', !on);
    }
    /** 把任务当时的生成器选项 + 提示词带回生成器面板（老任务没有 gen 时只回填提示词） */
    function reeditTask(t) {
        if (!t) { return; }
        if (PG && typeof PG.applyTask === 'function') { PG.applyTask(t); }
        else if (t.question) { $('#rpAiQ').val(t.question); }
        setStage('已带回当时的选择与提示词，改完点「AI 分析」');
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
            onError: function (text) {
                setErr(text);
                setBusy(false);
                setStopVisible(false);
                setReeditVisible(true);
            },
            onCanceled: function (text) {
                // 用户点了「停止」：服务端随后仍会补 done 帧（onDone 里做了状态保护）
                if (state.task) { state.task.status = 'canceled'; }
                setStage(text || '已按你的要求停止');
                setBusy(false);
                setStopVisible(false);
                setReeditVisible(true);
            },
            onDone: function () {
                setBusy(false);
                setStopVisible(false);
            }
        });
    }

    /* ---------- 请求停止进行中的任务 ---------- */
    function stopTask() {
        var tid = state.id;
        if (tid === null || tid === undefined || String(tid).charAt(0) === '-') { return; }
        if (!window.confirm('停止这个分析任务？（当前步骤完成即停，已生成的进度会保留）')) { return; }
        $('#rpAiStop').prop('disabled', true);
        $.ajax({
            url: '/grades/ai/tasks/' + encodeURIComponent(tid) + '/stop',
            method: 'POST',
            headers: { 'X-CSRFToken': (typeof window.csrfToken === 'string' ? window.csrfToken : '') }
        }).done(function (res) {
            if (!res || !res.success) {
                $('#rpAiStop').prop('disabled', false);
                setStage((res && res.message) || '停止失败');
                return;
            }
            setStage('已请求停止，当前步骤完成即停…');
        }).fail(function (x) {
            $('#rpAiStop').prop('disabled', false);
            var msg = '停止失败';
            try { msg = (x.responseJSON && x.responseJSON.message) || msg; }
            catch (e) { /* 忽略解析失败 */ }
            setErr(msg);
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
        setReeditVisible(false);
        setStopVisible(false);
        state.task = null;
        $.ajax({
            url: '/grades/ai/ask',
            method: 'POST',
            contentType: 'application/json',
            headers: { 'X-CSRFToken': (typeof window.csrfToken === 'string' ? window.csrfToken : '') },
            data: JSON.stringify({
                exam_id: eid, question: q, direction: curDir(), layer: curLayer(),
                history: HISTORY.slice(),
                // 用过提示词生成器才附 gen（结构化选项留档，供「重新编辑提示词」回填）
                gen: (PG && PG.isUsed()) ? PG.getGen() : null,
                ai: aiPayload()
            })
        }).done(function (res) {
            if (!res || !res.success) {
                setErr((res && res.message) || '提交失败');
                setBusy(false);
                return;
            }
            state.id = res.data.task_id;
            state.task = { id: state.id, exam_id: eid, question: q,
                           gen: (PG && PG.isUsed()) ? PG.getGen() : null };
            setStage('已提交，正在生成…（切走或刷新本页也不会中断）');
            setStopVisible(true);
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
            state.task = t;
            $('#rpAiOut').removeClass('d-none');
            if (t.question) { $('#rpAiQ').val(t.question); }
            $.getJSON('/grades/ai/tasks/' + encodeURIComponent(t.id), function (r2) {
                var d = (r2 && r2.data) || {};
                state.task = d;
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
                    setStopVisible(true);
                    subscribe(d.id);            // 断线续播：进度从服务端补发
                } else if (d.status === 'done') {
                    setStage('上次分析（' + (d.finished_at || d.created_at || '') + '）');
                } else if (d.status === 'error') {
                    setErr(d.error || '生成失败');
                    setReeditVisible(true);
                } else if (d.status === 'canceled') {
                    setStage(d.error || '上次任务已停止，可改提示词后再跑');
                    setReeditVisible(true);
                } else if (d.status === 'interrupted') {
                    setStage('上次任务被中断（服务已重启），问题已填回输入框，可直接重跑');
                    setReeditVisible(true);
                    addRerunHint(d.question || t.question);
                }
            });
        });
    }

    /* ---------- 交互绑定 ---------- */
    $(function () {
        $('#rpAiRun').on('click', submit);
        // 提示词是多行文本（生成器拼装后可继续编辑）：Enter 换行，Ctrl/⌘+Enter 提交
        $('#rpAiQ').on('keydown', function (e) {
            if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); submit(); }
        });
        $('#rpAiChips').on('click', '.rp-ai-chip', function () {
            $('#rpAiQ').val(($(this).text() || '').trim()).trigger('focus');
        });
        $('#rpAiCopyPrompt').on('click', function () {
            var txt = (PG && PG.getPrompt) ? PG.getPrompt() : ($('#rpAiQ').val() || '');
            if (!(txt || '').trim()) {
                setStage('提示词还是空的：可直接输入，或用「提示词生成器」拼装');
                $('#rpAiQ').trigger('focus');
                return;
            }
            UI.copyText(txt);
            setStage('已复制提示词');
        });
        $('#rpAiStop').on('click', stopTask);
        $('#rpAiReedit').on('click', function () { reeditTask(state.task); });
        // 考试切换（report.js 会重载数据）：生成器的班级/学科选择随本场考试作废
        $('#rpExam').on('change', function () {
            if (PG && typeof PG.onExamChanged === 'function') { PG.onExamChanged($(this).val()); }
        });
        if (PG && typeof PG.init === 'function') { PG.init(); }
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
