/* StuLink · AI 分析工作台（v1.19.50.62 2026-10-14）
 * 对接服务端 AI 任务：
 *   POST /grades/ai/ask                → 建任务，拿 task_id（可附 gen＝生成器选项留档）
 *   GET  /grades/ai/tasks?…            → 我的任务列表
 *   GET  /grades/ai/tasks/<id>         → 任务详情（events / html / trace / gen）
 *   GET  /grades/ai/tasks/<id>/stream  → SSE 进度（由 AiTaskUI.subscribe 封装）
 *   POST /grades/ai/tasks/<id>/stop    → 请求停止（进行中任务，当前步骤完成即停）
 *   POST /grades/ai/reports/<id>/delete→ 删除报告
 * 提示词生成器面板（AiPromptGen）见 ai_prompt_gen.js：范围/侧重/输出格式/附加要求
 * → 拼装提示词 → 存模板 / 复制 / 直接分析；中断或停止后可「重新编辑提示词」回填。
 * 路径从页面 #aiStudioCfg 注入，不在 JS 里硬编码。
 * 安全：报告正文只经 AiTaskUI.renderReport 插入（服务端已消毒），UI 文本一律 .text()。
 * Copyright (c) 2026 zkxxzf. Apache License 2.0 */
(function () {
    'use strict';
    if (!window.jQuery) { return; }
    var $ = window.jQuery;
    var UI = window.AiTaskUI;
    var PG = window.AiPromptGen;

    var cfg = {};
    try { cfg = JSON.parse((document.getElementById('aiStudioCfg') || {}).textContent || '{}'); }
    catch (e) { cfg = {}; }

    var ASK = cfg.ask_url || '/grades/ai/ask';
    var TASKS = cfg.tasks_url || '/grades/ai/tasks';
    var REPORTS = cfg.reports_url || '/grades/ai/reports';
    var OPTS = cfg.exam_opts_url || '/grades/api/options';

    var state = { id: null, task: null, closeStream: null };

    function toast(msg, type) {
        if (typeof window.stuToast === 'function') { window.stuToast(msg, type || 'ok'); }
        else if (type === 'err') { window.alert(msg); }
    }
    function csrf() { return (typeof window.csrfToken === 'string') ? window.csrfToken : ''; }
    function aiPayload() {
        if (window.StuLinkAI && typeof window.StuLinkAI.payload === 'function') {
            return window.StuLinkAI.payload();
        }
        return {};
    }
    function reportBody() { return $('.ai-report-body'); }

    /* ================= 左栏：任务列表 ================= */
    function renderTasks(rows) {
        var $list = $('#aiTaskList').empty();
        if (!rows || !rows.length) {
            $list.append($('<div class="text-center text-muted py-4 small"></div>')
                .text('还没有分析任务。在右侧选考试、输入一句话问题，点「开始分析」即可。'));
            return;
        }
        rows.forEach(function (t) {
            if (!t || t.id === undefined || t.id === null) { return; }
            var meta = UI.statusMeta(t.status);
            var $item = $('<div class="ai-studio-task"></div>').attr('data-id', t.id);
            if (String(t.id) === String(state.id)) { $item.addClass('active'); }

            var $top = $('<div class="d-flex align-items-center gap-2"></div>');
            if (t.status === 'running') {
                $top.append('<span class="spinner-border spinner-border-sm text-warning"></span>');
            }
            $top.append($('<span></span>').addClass('badge ' + meta.cls).text(meta.text));
            $top.append($('<span class="t-meta ms-auto"></span>').text(t.created_at || ''));
            $item.append($top);

            // 提示词生成器拼出来的提问可能很长（提示词正文），列表里只显示开头一段
            var q = t.question || '（无问题）';
            $item.append($('<div class="t-q mt-1"></div>')
                .text(q.length > 70 ? q.slice(0, 70) + '…' : q));
            var sub = (t.exam_name || '') + (t.grade ? ' · ' + t.grade : '');
            $item.append($('<div class="t-meta"></div>').text(sub || '—'));
            if (t.status === 'error' && t.error) {
                $item.append($('<div class="t-meta text-danger"></div>').text('错误：' + t.error));
            }
            // 2026-10-18：列表项可删除（原来只有"当前打开的报告"能删，列表里删不掉）
            // 旧口径报告在列表里是负数 id（-报告id），删除时走报告接口
            $item.append($('<button type="button" class="ai-del-btn" title="删除这条记录">&times;</button>')
                .attr('data-del', t.id).attr('aria-label', '删除这条记录'));
            $list.append($item);
        });
    }

    function loadTasks(done) {
        var url = TASKS + '?limit=30';
        var eid = $('#aiExamSel').val();
        if (eid) { url += '&exam_id=' + encodeURIComponent(eid); }
        return $.getJSON(url, function (res) {
            var rows = (res && res.data) || [];
            renderTasks(rows);
            if (typeof done === 'function') { done(rows); }
        }).fail(function (x) {
            toast('任务列表加载失败' + (x && x.status ? '（' + x.status + '）' : ''), 'err');
            if (typeof done === 'function') { done([]); }
        });
    }

    function markActive() {
        $('#aiTaskList .ai-studio-task').each(function () {
            $(this).toggleClass('active', String($(this).attr('data-id')) === String(state.id));
        });
    }

    /* ================= 右栏：任务详情 / 进度 ================= */
    function setStage(text) {
        $('#aiStage').removeClass('ai-stage-err').addClass('text-muted').text(text || '');
    }
    function setStageErr(text) {
        $('#aiStage').removeClass('text-muted').addClass('ai-stage-err')
            .text('分析失败：' + (text || '未知错误'));
    }
    function refreshDelBtn() {
        var rid = state.task && state.task.report_id;
        $('#aiDel').prop('disabled', rid === undefined || rid === null || rid === '')
            .attr('title', rid ? '删除该报告' : '任务尚未生成报告');
    }
    function showProgress() {
        $('#aiEmpty').addClass('d-none');
        $('#aiProgress').removeClass('d-none');
    }
    /* 进度区状态按钮：进行中→「停止」；结束/失败/中断→「重新编辑提示词」 */
    function setStopVisible(on) {
        $('#aiStop').toggleClass('d-none', !on).prop('disabled', false);
    }
    function setReeditVisible(on) {
        $('#aiReedit').toggleClass('d-none', !on);
    }
    /** 把该任务当时的生成器选项 + 提示词带回生成器（没有 gen 的老任务只回填提示词） */
    function reeditTask(t) {
        if (!t) { return; }
        if (PG && typeof PG.applyTask === 'function') { PG.applyTask(t); }
        else { $('#aiQ').val(t.question || ''); }
        toast('已带回当时的选择与提示词，改完点「开始分析」', 'ok');
    }

    function stopStream() {
        if (state.closeStream) {
            try { state.closeStream(); } catch (e) { /* 已关闭 */ }
            state.closeStream = null;
        }
    }

    function subscribeTask(id) {
        stopStream();
        state.closeStream = UI.subscribe(id, {
            onStage: function (text) { setStage(text || ''); },
            // 复用 applyEvents：单条 tool 事件直接追加一行（不重复实现渲染）
            onTool: function (ev) { UI.applyEvents(null, $('#aiTools'), [ev]); },
            onHtml: function (html) { UI.renderReport(reportBody(), html); },
            onSaved: function (rid) {
                state.task = state.task || {};
                state.task.report_id = rid;
                refreshDelBtn();
                setStage('报告已保存');
            },
            onError: function (text) {
                setStageErr(text);
                setStopVisible(false);
                setReeditVisible(true);
            },
            onCanceled: function (text) {
                // 用户点了「停止」：服务端标记 canceled 后还会补一个 done 帧（见 onDone 保护）
                if (state.task) { state.task.status = 'canceled'; }
                setStopVisible(false);
                setReeditVisible(true);
                setStage(text || '已按你的要求停止');
                loadTasks();
            },
            onDone: function () {
                state.closeStream = null;
                setStopVisible(false);
                if (state.task && state.task.status !== 'canceled') { state.task.status = 'done'; }
                loadTasks();
            }
        });
    }

    /* 中断的任务：一键重跑（沿用原考试 + 原问题）。
     * 中断几乎都来自"生成期间后端重启"，重跑一次基本就出结果，比重新打字省事。 */
    function rerunTask(t) {
        if (!t) { return; }
        if (t.exam_id) { $('#aiExamSel').val(String(t.exam_id)); }
        $('#aiQ').val(t.question || '');
        submit();
    }

    function openTask(id) {
        stopStream();
        state.id = id;
        state.task = null;
        markActive();
        showProgress();
        $('#aiTools').empty();
        UI.renderReport(reportBody(), '');
        setStage('加载任务…');
        refreshDelBtn();
        setStopVisible(false);
        setReeditVisible(false);

        $.getJSON(TASKS + '/' + encodeURIComponent(id), function (res) {
            if (!res || !res.success) {
                setStageErr((res && res.message) || '任务不存在');
                return;
            }
            var t = res.data || {};
            state.task = t;
            $('#aiTools').empty();
            UI.applyEvents($('#aiStage'), $('#aiTools'), t.events || []);
            if (t.md) {
                // 旧口径报告（早期「AI 分析」按钮产出的 Markdown，或已无任务的报告，
                // 后端用负数 id 包装成条目）：走 grades_md.js 白名单渲染
                var $body = reportBody();
                $body.empty();
                if (window.renderGradeMd) { $body.append(window.renderGradeMd(t.md)); }
                else { $body.text(t.md); }
            } else {
                UI.renderReport(reportBody(), t.html);
            }
            if (t.status === 'running') {
                if (!$('#aiStage').text()) { setStage(t.stage || '生成中…'); }
                setStopVisible(true);
                subscribeTask(t.id);
            } else if (t.status === 'done') {
                setStage('分析完成');
            } else if (t.status === 'error') {
                setStageErr(t.error || '生成失败');
                setReeditVisible(true);                 // 失败后：改改提示词再跑，常比原样重跑有用
            } else if (t.status === 'canceled') {
                setStage(t.error || '已按你的要求停止');
                setReeditVisible(true);
            } else if (t.status === 'interrupted') {
                setStage('任务已中断（生成期间服务重启），可就原问题重跑，或改提示词再跑');
                setReeditVisible(true);
                var $again = $('<button type="button" class="btn btn-sm btn-outline-secondary ms-2">' +
                    '<i class="bi bi-arrow-clockwise"></i> 重跑</button>');
                $again.on('click', function () { rerunTask(t); });
                $('#aiStage').append($again);
            }
            refreshDelBtn();
        }).fail(function (x) {
            setStageErr('任务加载失败' + (x && x.status ? '（HTTP ' + x.status + '）' : ''));
        });
    }

    /* 请求停止进行中的任务：当前步骤（模型往返/工具查询）完成即停 */
    function stopTask() {
        var tid = state.id;
        if (tid === null || tid === undefined || String(tid).charAt(0) === '-') { return; }
        if (!window.confirm('停止这个分析任务？（当前步骤完成即停，已生成的进度会保留）')) { return; }
        $('#aiStop').prop('disabled', true);
        $.ajax({
            url: TASKS + '/' + encodeURIComponent(tid) + '/stop',
            method: 'POST',
            headers: { 'X-CSRFToken': csrf() }
        }).done(function (res) {
            if (!res || !res.success) {
                $('#aiStop').prop('disabled', false);
                toast((res && res.message) || '停止失败', 'err');
                return;
            }
            toast(res.message || '已请求停止', 'ok');
            setStage('已请求停止，当前步骤完成即停…');
        }).fail(function (x) {
            $('#aiStop').prop('disabled', false);
            toast((x && x.responseJSON && x.responseJSON.message)
                || ('停止失败' + (x && x.status ? '（HTTP ' + x.status + '）' : '')), 'err');
        });
    }

    /* ================= 右栏：考试下拉 ================= */
    function fillExams() {
        var $sel = $('#aiExamSel').empty();
        $sel.append($('<option value=""></option>').text('— 选择考试 —'));
        $.getJSON(OPTS, function (res) {
            var data = (res && res.data) || {};
            var exams = data.exams || {};
            Object.keys(exams).sort().reverse().forEach(function (g) {
                (exams[g] || []).forEach(function (e) {
                    $sel.append($('<option></option>').attr('value', e.id)
                        .text(g + ' · ' + (e.name || '') + (e.banded === false ? '（未划线）' : '')));
                });
            });
            if (cfg.exam_id !== undefined && cfg.exam_id !== null && cfg.exam_id !== '') {
                $sel.val(String(cfg.exam_id));
                if (!$sel.val()) { $sel.prop('selectedIndex', 0); }
            }
        }).fail(function () {
            toast('考试列表加载失败', 'err');
        });
    }

    /* ================= 提交新任务 ================= */
    function setBusy(on) {
        $('#aiSubmit').prop('disabled', on);
        var $label = $('#aiSubmit .ai-run-label');
        if (on) {
            if (!$('#aiSubmit .spinner-border').length) {
                $('#aiSubmit').prepend($('<span class="spinner-border spinner-border-sm me-1"></span>'));
            }
            $label.text('提交中…');
        } else {
            $('#aiSubmit .spinner-border').remove();
            $label.text('开始分析');
        }
    }

    function submit() {
        var q = ($('#aiQ').val() || '').trim();
        if (!q) { $('#aiQ').trigger('focus'); toast('请先输入一句话问题', 'warn'); return; }
        var eid = $('#aiExamSel').val();
        if (!eid) { toast('请先选择一场考试', 'warn'); $('#aiExamSel').trigger('focus'); return; }
        if (window.StuLinkAI && typeof window.StuLinkAI.ensure === 'function') {
            if (!window.StuLinkAI.ensure()) { return; }   // 未配置 Key：提示并引导去用户设置
        }
        setBusy(true);
        $.ajax({
            url: ASK,
            method: 'POST',
            contentType: 'application/json',
            headers: { 'X-CSRFToken': csrf() },
            data: JSON.stringify({
                exam_id: eid,
                question: q,
                direction: '',
                layer: '',
                history: [],
                // 用过提示词生成器才附 gen（结构化选项留档，供「重新编辑提示词」回填）
                gen: (PG && PG.isUsed()) ? PG.getGen() : null,
                ai: aiPayload()
            })
        }).done(function (res) {
            setBusy(false);
            if (!res || !res.success) {
                toast((res && res.message) || '提交失败', 'err');
                return;
            }
            var tid = ((res.data || {}).task_id);
            toast('任务已创建，正在后台分析…', 'ok');
            // 提示词保留在文本框里（生成器场景常要微调后再跑；一句话场景也便于复用）
            loadTasks(function () { if (tid) { openTask(tid); } });
        }).fail(function (x) {
            setBusy(false);
            var msg = (x && x.responseJSON && x.responseJSON.message)
                || ('提交失败' + (x && x.status ? '（HTTP ' + x.status + '）' : ''));
            toast(msg, 'err');
        });
    }

    /* ================= 复制 / 导出 / 删除 ================= */
    function taskTitle() {
        var t = state.task || {};
        var exam = t.exam_name || ($('#aiExamSel option:selected').text() || '成绩分析');
        var q = t.question || '';
        return (exam + (q ? ' · ' + q : '')).slice(0, 80);
    }

    function copyReport() {
        var txt = UI.plainTextOf(reportBody());
        if (!txt) { toast('暂无可复制的内容', 'warn'); return; }
        UI.copyText(txt);
        toast('已复制报告正文', 'ok');
    }

    function exportReport() {
        var html = reportBody().html() || '';
        if (!html) { toast('暂无可导出的报告', 'warn'); return; }
        if (UI.exportHtml(taskTitle(), html)) { toast('已导出独立 HTML 文件', 'ok'); }
    }

    function deleteReport() {
        var rid = state.task && state.task.report_id;
        if (rid === undefined || rid === null || rid === '') { return; }
        if (!window.confirm('确认删除该报告？（任务记录会保留）')) { return; }
        $.ajax({
            url: REPORTS + '/' + encodeURIComponent(rid) + '/delete',
            method: 'POST',
            headers: { 'X-CSRFToken': csrf() }
        }).done(function (res) {
            if (res && res.success === false) {
                toast(res.message || '删除失败', 'err');
                return;
            }
            toast('报告已删除', 'ok');
            if (state.task) { state.task.report_id = null; }
            refreshDelBtn();
            UI.renderReport(reportBody(), '');
            loadTasks();
        }).fail(function (x) {
            toast('删除失败' + (x && x.status ? '（HTTP ' + x.status + '）' : ''), 'err');
        });
    }

    /** 删除列表里的一条记录（任务 id > 0；旧口径报告是负数 id，走报告接口） */
    function deleteListItem(id) {
        var n = Number(id);
        if (!n) { return; }
        var isReport = n < 0;
        var url = isReport
            ? (REPORTS + '/' + Math.abs(n) + '/delete')
            : (TASKS + '/' + encodeURIComponent(n) + '/delete');
        if (!window.confirm(isReport
                ? '确认删除这条分析记录（含报告正文）？'
                : '确认删除这条分析任务（连同它生成的报告）？')) { return; }
        $.ajax({ url: url, method: 'POST', headers: { 'X-CSRFToken': csrf() } })
            .done(function (res) {
                if (res && res.success === false) {
                    toast(res.message || '删除失败', 'err'); return;
                }
                toast('已删除', 'ok');
                if (String(state.id) === String(id)) { clearStage(); }
                loadTasks();
            })
            .fail(function (x) {
                var msg = '删除失败' + (x && x.status ? '（HTTP ' + x.status + '）' : '');
                if (x && x.responseJSON && x.responseJSON.message) {
                    msg = x.responseJSON.message;
                }
                toast(msg, 'err');
            });
    }

    /* 2026-10-18：一键「清空」入口已移除 —— 用户误点一次把全部分析删光。
       现在只保留逐条删除（列表项右上角 ×，二次确认），后端也要求 confirm=CLEAR 且删前归档。 */

    /** 右侧回到"未选择任务"的空状态 */
    function clearStage() {
        state.id = null;
        state.task = null;
        if (state.closeStream) {
            try { state.closeStream(); } catch (e) { /* 已关闭 */ }
            state.closeStream = null;
        }
        UI.renderReport(reportBody(), '');
        $('#aiProgress').addClass('d-none');
        $('#aiEmpty').removeClass('d-none');
        refreshDelBtn();
    }

    /* ================= 启动 ================= */
    $(function () {
        if (!UI) { return; }

        $('#aiTaskList').on('click', '.ai-studio-task', function () {
            var id = $(this).attr('data-id');
            if (id) { openTask(id); }
        });
        // 列表项上的删除按钮：阻止冒泡，避免顺带打开任务
        $('#aiTaskList').on('click', '.ai-del-btn', function (ev) {
            ev.stopPropagation();
            deleteListItem($(this).attr('data-del'));
        });

        $('#aiRefresh').on('click', function () { loadTasks(); });
        $('#aiExamSel').on('change', function () {
            var eid = $(this).val();
            loadTasks();
            if (PG && typeof PG.onExamChanged === 'function') { PG.onExamChanged(eid); }
        });
        $('#aiSubmit').on('click', submit);
        // 提示词是多行文本（生成器拼装后可继续编辑）：Enter 换行，Ctrl/⌘+Enter 提交
        $('#aiQ').on('keydown', function (e) {
            if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); submit(); }
        });
        $('#aiChips').on('click', '.ai-chip', function () {
            $('#aiQ').val(($(this).text() || '').trim()).trigger('focus');
        });
        $('#aiCopyPrompt').on('click', function () {
            var txt = (PG && PG.getPrompt) ? PG.getPrompt() : ($('#aiQ').val() || '');
            if (!(txt || '').trim()) {
                toast('提示词还是空的：可直接输入，或用「提示词生成器」拼装', 'warn');
                $('#aiQ').trigger('focus');
                return;
            }
            UI.copyText(txt);
            toast('已复制提示词', 'ok');
        });
        $('#aiStop').on('click', stopTask);
        $('#aiReedit').on('click', function () { reeditTask(state.task); });
        $('#aiCopy').on('click', copyReport);
        $('#aiExport').on('click', exportReport);
        $('#aiDel').on('click', deleteReport);

        if (PG && typeof PG.init === 'function') { PG.init(); }

        fillExams();
        // 启动即打开：优先 URL 指定任务，否则最近一条
        loadTasks(function (rows) {
            var want = cfg.task_id;
            if (want === undefined || want === null || want === '') {
                want = (rows && rows[0]) ? rows[0].id : null;
            }
            if (want !== null && want !== undefined && want !== '') { openTask(want); }
        });
    });
})();
