/**
 * StuLink v1.9.3 2026-09-18
 * 表单收集「汇总套件」前端逻辑：看板图表 / 汇总表格 / 未交催交 / 材料清单 / 材料包
 * 单文件按 window.FX_CTX.page 分派，全部本地依赖（jQuery + 本地 ECharts），零 CDN。
 * Copyright (c) 2026 zkxxzf. Apache License 2.0
 */
(function () {
    'use strict';
    var CTX = window.FX_CTX || {};
    var $doc = $(document);

    /* ── 通用工具 ─────────────────────────────────────────── */
    function toast(msg, type) {
        type = type || 'info';
        var $t = $('<div class="fx-toast ' + type + '"></div>').text(msg);
        $('body').append($t);
        setTimeout(function () { $t.fadeOut(280, function () { $t.remove(); }); }, 2800);
    }
    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function getJSON(url, params) {
        return $.ajax({ url: url, data: params || {}, dataType: 'json', method: 'GET' });
    }
    // 年级→班级级联
    function fillClasses(grade, $sel, keep) {
        var opts = CTX.scopeOptions || [];
        $sel.empty().append('<option value="">全部班级</option>');
        for (var i = 0; i < opts.length; i++) {
            if (!grade || opts[i].grade === grade) {
                (opts[i].classes || []).forEach(function (c) {
                    $sel.append($('<option>').val(c).text(c));
                });
            }
        }
        if (keep) { $sel.val(keep); }
    }
    function animateRing(rate) {
        var arc = document.getElementById('fxRingArc');
        var pct = document.getElementById('fxRingPct');
        if (!arc) { return; }
        var C = 289;
        var off = C * (1 - (rate || 0) / 100);
        setTimeout(function () { arc.style.strokeDashoffset = off; }, 60);
        if (pct) {
            var cur = 0, target = Math.round(rate || 0);
            var timer = setInterval(function () {
                cur += Math.max(1, Math.ceil((target - cur) / 8));
                if (cur >= target) { cur = target; clearInterval(timer); }
                pct.textContent = cur + '%';
            }, 40);
        }
    }

    /* ══ 页面：汇总看板 ═════════════════════════════════════ */
    function pageSummary() {
        animateRing(CTX.inlineStats && CTX.inlineStats.rate);
        var cache = { classData: null, qData: null, trend: null };
        var charts = {};

        getJSON(CTX.urls.classBreakdown).done(function (r) {
            if (r.success) { cache.classData = r.data; tryRender('class'); }
        });
        getJSON(CTX.urls.questionStats).done(function (r) {
            if (r.success) { cache.qData = r.data.question_stats; renderQuestionCards(); tryRender('choice'); }
        });
        getJSON(CTX.urls.stats).done(function (r) {
            if (r.success) { cache.trend = r.data.trend || []; tryRender('trend'); }
        });

        var PALETTE = ['#2f5496', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#06b6d4', '#e0699a', '#4a72c4'];

        function initChart(id) {
            var el = document.getElementById(id);
            if (!el || typeof echarts === 'undefined') { return null; }
            if (!charts[id]) { charts[id] = echarts.init(el); $(window).on('resize', function () { charts[id].resize(); }); }
            return charts[id];
        }
        function renderClass() {
            var d = cache.classData; if (!d) { return; }
            var c = initChart('fxChartClass'); if (!c) { return; }
            var rows = d.rows || [];
            var labels = rows.map(function (x) { return x.grade + ' ' + x.class_name; });
            var sub = rows.map(function (x) { return x.submitted; });
            var miss = rows.map(function (x) { return x.not_submitted; });
            c.setOption({
                tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
                legend: { data: ['已交', '未交'], right: 10, top: 0 },
                grid: { left: 8, right: 20, top: 34, bottom: 8, containLabel: true },
                xAxis: { type: 'value', splitLine: { lineStyle: { color: '#eef1f7' } } },
                yAxis: { type: 'category', data: labels, inverse: true, axisLabel: { fontSize: 11 } },
                series: [
                    { name: '已交', type: 'bar', stack: 't', data: sub, itemStyle: { color: '#10b981', borderRadius: [3, 0, 0, 3] }, barMaxWidth: 16 },
                    { name: '未交', type: 'bar', stack: 't', data: miss, itemStyle: { color: '#ef4444', borderRadius: [0, 3, 3, 0] }, barMaxWidth: 16 }
                ]
            });
        }
        function renderStatus() {
            var s = CTX.inlineStats || {};
            var c = initChart('fxChartStatus'); if (!c) { return; }
            c.setOption({
                tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
                legend: { bottom: 0 },
                series: [{
                    type: 'pie', radius: ['42%', '68%'], center: ['50%', '44%'],
                    avoidLabelOverlap: true, itemStyle: { borderColor: '#fff', borderWidth: 2 },
                    label: { formatter: '{b}\n{c}' },
                    data: [
                        { name: '待审核', value: s.pending || 0, itemStyle: { color: '#f59e0b' } },
                        { name: '已通过', value: s.approved || 0, itemStyle: { color: '#10b981' } },
                        { name: '已驳回', value: s.rejected || 0, itemStyle: { color: '#ef4444' } }
                    ]
                }]
            });
        }
        function renderTrend() {
            if (!cache.trend) { return; }
            var c = initChart('fxChartTrend'); if (!c) { return; }
            if (!cache.trend.length) { c.setOption({ title: { text: '暂无提交趋势数据', left: 'center', top: 'center', textStyle: { color: '#8a97b1', fontSize: 13, fontWeight: 'normal' } } }); return; }
            c.setOption({
                tooltip: { trigger: 'axis' },
                grid: { left: 8, right: 20, top: 20, bottom: 8, containLabel: true },
                xAxis: { type: 'category', data: cache.trend.map(function (x) { return x.date.slice(5); }), boundaryGap: false },
                yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: '#eef1f7' } } },
                series: [{
                    type: 'line', smooth: true, data: cache.trend.map(function (x) { return x.count; }),
                    areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: 'rgba(47,84,150,.35)' }, { offset: 1, color: 'rgba(47,84,150,.02)' }]) },
                    lineStyle: { color: '#2f5496', width: 2.5 }, itemStyle: { color: '#2f5496' }
                }]
            });
        }
        var choiceIdx = 0;
        function renderChoiceTabs() {
            var qs = (cache.qData || []).filter(function (q) { return q.type === 'single_choice' || q.type === 'multi_choice'; });
            var $tabs = $('#fxChoiceTabs').empty();
            if (!qs.length) { $('#fxChartChoice').html('<div class="fx-chart-empty">本表单无选择题</div>'); return; }
            qs.forEach(function (q, i) {
                $tabs.append($('<span class="fx-tab' + (i === choiceIdx ? ' active' : '') + '">').text('第' + (i + 1) + '题').on('click', function () {
                    choiceIdx = i; $tabs.find('.fx-tab').removeClass('active'); $(this).addClass('active'); drawChoice(qs[i]);
                }));
            });
            drawChoice(qs[choiceIdx] || qs[0]);
        }
        function drawChoice(q) {
            if (!q) { return; }
            var c = initChart('fxChartChoice'); if (!c) { return; }
            var opts = q.options || [];
            c.setOption({
                title: { text: q.title, left: 'center', top: 0, textStyle: { fontSize: 12, color: '#6b7a99', fontWeight: 'normal' } },
                tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, formatter: function (p) { var o = opts[p[0].dataIndex]; return o.label + ': ' + o.count + ' (' + o.percent + '%)'; } },
                grid: { left: 8, right: 30, top: 30, bottom: 8, containLabel: true },
                xAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: '#eef1f7' } } },
                yAxis: { type: 'category', data: opts.map(function (o) { return o.label; }), inverse: true, axisLabel: { fontSize: 11 } },
                series: [{ type: 'bar', data: opts.map(function (o, i) { return { value: o.count, itemStyle: { color: PALETTE[i % PALETTE.length], borderRadius: [0, 4, 4, 0] } }; }), barMaxWidth: 18, label: { show: true, position: 'right', formatter: '{c}', fontSize: 11 } }]
            }, true);
        }
        function tryRender(kind) {
            var el = document.querySelector('[data-lazy-chart="' + kind + '"]');
            if (!el) { return; }
            if (el.dataset.rendered === '1') { doRender(kind); return; }
            if (!('IntersectionObserver' in window)) { el.dataset.rendered = '1'; doRender(kind); return; }
            var io = new IntersectionObserver(function (entries) {
                entries.forEach(function (e) { if (e.isIntersecting) { el.dataset.rendered = '1'; doRender(kind); io.disconnect(); } });
            }, { threshold: 0.15 });
            io.observe(el);
        }
        function doRender(kind) {
            if (kind === 'class') { renderClass(); }
            else if (kind === 'status') { renderStatus(); }
            else if (kind === 'trend') { renderTrend(); }
            else if (kind === 'choice') { renderChoiceTabs(); }
        }
        // status 图无需异步数据，直接懒渲染
        tryRender('status');

        function renderQuestionCards() {
            var qs = cache.qData || [];
            $('#fxQCount').text('共 ' + qs.length + ' 题');
            var $box = $('#fxQuestionCards').empty();
            if (!qs.length) { $box.html('<div class="col-12 fx-empty"><i class="bi bi-inbox"></i>暂无题目</div>'); return; }
            qs.forEach(function (q, i) {
                var body = '';
                if (q.type === 'single_choice' || q.type === 'multi_choice') {
                    var maxc = Math.max(1, ...q.options.map(function (o) { return o.count; }));
                    body = q.options.map(function (o) {
                        return '<div class="fx-bar-row"><span class="fx-bar-label" title="' + esc(o.label) + '">' + esc(o.label) + '</span>' +
                            '<span class="fx-bar-track"><span class="fx-bar-fill" style="width:' + (o.count / maxc * 100) + '%"></span></span>' +
                            '<span class="fx-bar-val">' + o.count + ' · ' + o.percent + '%</span></div>';
                    }).join('');
                } else if (q.type === 'number') {
                    body = '<div class="d-flex gap-3 flex-wrap"><div><div class="fx-hint">均值</div><b class="fx-num" style="font-size:20px">' + (q.avg == null ? '—' : q.avg) + '</b></div>' +
                        '<div><div class="fx-hint">最小</div><b class="fx-num" style="font-size:20px">' + (q.min == null ? '—' : q.min) + '</b></div>' +
                        '<div><div class="fx-hint">最大</div><b class="fx-num" style="font-size:20px">' + (q.max == null ? '—' : q.max) + '</b></div></div>';
                } else if (q.type === 'file') {
                    body = '<div class="d-flex gap-3 flex-wrap"><div><div class="fx-hint">文件数</div><b class="fx-num" style="font-size:20px">' + q.file_count + '</b></div>' +
                        '<div><div class="fx-hint">总大小</div><b class="fx-num" style="font-size:18px">' + q.file_size_text + '</b></div>' +
                        '<div><div class="fx-hint">缺失人数</div><b class="fx-num" style="font-size:20px;color:var(--fx-bad)">' + q.file_missing + '</b></div></div>';
                } else {
                    body = '<div class="fx-hint">平均字数 <b class="fx-num" style="font-size:18px;color:var(--fx-ink)">' + (q.avg_len || 0) + '</b></div>';
                }
                var col = $('<div class="col-md-6 col-xl-4">');
                col.html('<div class="fx-qcard"><div class="fx-qtitle">' + (i + 1) + '. ' + esc(q.title) + '</div>' +
                    '<div class="fx-qmeta">' + q.type_text + ' · 已答 ' + q.answered_count + ' · 未答 ' + q.blank_count + '</div>' + body + '</div>');
                $box.append(col);
            });
        }
    }

    /* ══ 页面：汇总表格 ═════════════════════════════════════ */
    function pageTable() {
        var state = { page: 1, per_page: 20, status: '', grade: '', class_name: '', keyword: '' };
        var qids = CTX.questions || [];
        var modal = new bootstrap.Modal(document.getElementById('fxCellModal'));

        $('#fxGrade').on('change', function () { fillClasses($(this).val(), $('#fxClass')); });
        fillClasses('', $('#fxClass'));

        function buildQuery(extra) {
            return $.extend({ page: state.page, per_page: state.per_page, status: state.status,
                grade: state.grade, class_name: state.class_name, keyword: state.keyword }, extra || {});
        }
        function load() {
            $('#fxTbody').html('<tr><td colspan="' + (qids.length + 6) + '" class="text-center py-4 text-muted">加载中…</td></tr>');
            getJSON(CTX.urls.tableData, buildQuery()).done(function (r) {
                if (!r.success) { toast(r.message || '加载失败', 'err'); return; }
                render(r.data);
            }).fail(function () { toast('网络错误', 'err'); });
        }
        function render(data) {
            var rows = data.rows || [];
            var $tb = $('#fxTbody').empty();
            if (!rows.length) { $tb.html('<tr><td colspan="' + (qids.length + 6) + '" class="fx-empty"><i class="bi bi-inbox"></i>无符合条件的提交</td></tr>'); }
            rows.forEach(function (row) {
                var $tr = $('<tr class="st-' + row.status + '">');
                $tr.append($('<td class="fx-sticky-1">').text(row.index));
                $tr.append($('<td class="fx-sticky-2 fx-mono">').text(row.uid || '-'));
                $tr.append($('<td class="fx-sticky-3 fw-bold">').text(row.name || '-'));
                $tr.append($('<td>').text((row.grade || '') + ' ' + (row.class_name || '')));
                $tr.append($('<td class="fx-mono" style="font-size:12px">').text(row.submitted_at || '-'));
                $tr.append($('<td>').html('<span class="fx-badge b-' + row.status + '">' + esc(row.status_text) + '</span>'));
                qids.forEach(function (qid) {
                    var cell = (row.answers || {})[String(qid)] || {};
                    $tr.append(buildCell(cell, row));
                });
                $tb.append($tr);
            });
            var p = data.pagination || {};
            $('#fxPageInfo').text('共 ' + (p.total || 0) + ' 条 · 第 ' + (p.page || 1) + '/' + (p.pages || 1) + ' 页');
            renderPager(p);
        }
        function buildCell(cell, row) {
            var $td = $('<td>');
            if (cell.files && cell.files.length) {
                var show = cell.files.slice(0, 2);
                show.forEach(function (f) {
                    $td.append($('<span class="fx-file-chip">').html('<i class="bi bi-paperclip"></i>' + esc(f.filename)).on('click', function () { openFiles(cell.files, row); }));
                });
                if (cell.files.length > 2) { $td.append($('<span class="fx-more">').text(' +' + (cell.files.length - 2)).css('cursor', 'pointer').on('click', function () { openFiles(cell.files, row); })); }
            } else {
                var txt = cell.display || '';
                if (!txt) { $td.text('—').css('color', '#c3cad9'); return $td; }
                if (txt.length > 24) {
                    $td.append($('<span class="fx-cell-text">').text(txt.slice(0, 24) + '…').attr('title', '点击查看全文').on('click', function () { openText(txt); }));
                } else { $td.text(txt); }
            }
            return $td;
        }
        function openText(txt) {
            $('#fxCellTitle').text('答案全文');
            $('#fxCellBody').html('<div style="white-space:pre-wrap;font-size:14px;line-height:1.7">' + esc(txt) + '</div>');
            modal.show();
        }
        function openFiles(files, row) {
            $('#fxCellTitle').text((row.name || '') + ' 的材料（' + files.length + '）');
            var html = files.map(function (f) {
                return '<div class="d-flex align-items-center justify-content-between border-bottom py-2" style="border-color:#eef1f7!important">' +
                    '<span><i class="bi bi-file-earmark-arrow-down text-primary me-2"></i>' + esc(f.filename) + ' <span class="fx-hint">' + esc(f.size_text || '') + '</span></span>' +
                    '<a class="btn btn-sm btn-outline-primary" target="_blank" href="' + esc(f.url) + '"><i class="bi bi-box-arrow-up-right"></i> 打开</a></div>';
            }).join('');
            $('#fxCellBody').html(html || '<div class="fx-empty">无文件</div>');
            modal.show();
        }
        function renderPager(p) {
            var $pg = $('#fxPager').empty();
            function li(label, page, disabled, active) {
                return $('<li class="page-item' + (disabled ? ' disabled' : '') + (active ? ' active' : '') + '">')
                    .append($('<a class="page-link" href="#">').text(label).on('click', function (e) {
                        e.preventDefault(); if (disabled || active) { return; } state.page = page; load();
                    }));
            }
            $pg.append(li('‹', p.prev_num, !p.has_prev));
            var start = Math.max(1, (p.page || 1) - 2), end = Math.min(p.pages || 1, start + 4);
            for (var i = start; i <= end; i++) { $pg.append(li(i, i, false, i === p.page)); }
            $pg.append(li('›', p.next_num, !p.has_next));
        }
        $('#fxApply').on('click', function () {
            state.page = 1; state.status = $('#fxStatus').val(); state.grade = $('#fxGrade').val();
            state.class_name = $('#fxClass').val(); state.keyword = $('#fxKeyword').val().trim(); load();
        });
        $('#fxKeyword').on('keydown', function (e) { if (e.key === 'Enter') { $('#fxApply').click(); } });
        $('#fxPerPage').on('change', function () { state.per_page = parseInt(this.value, 10); state.page = 1; load(); });
        $('#fxReset').on('click', function () {
            $('#fxStatus,#fxGrade').val(''); fillClasses('', $('#fxClass')); $('#fxKeyword').val('');
            state = { page: 1, per_page: state.per_page, status: '', grade: '', class_name: '', keyword: '' }; load();
        });
        $('#fxExport').on('click', function () {
            var qs = $.param({ status: state.status || '', grade: state.grade || '', class_name: state.class_name || '', keyword: state.keyword || '', include_rejected: '1' });
            window.location = CTX.urls.exportSummary + '?' + qs;
            toast('正在导出 Excel…', 'info');
        });
        load();
    }

    /* ══ 页面：未交名单 ═════════════════════════════════════ */
    function pageMissing() {
        animateRing(CTX.inlineStats && CTX.inlineStats.rate);
        var state = { grade: '' };
        function load() {
            $('#fxGroups').html('<div class="fx-empty"><i class="bi bi-hourglass-split"></i>加载中…</div>');
            getJSON(CTX.urls.missingData, { grade: state.grade, per_page: 1000 }).done(function (r) {
                if (!r.success) { toast(r.message || '加载失败', 'err'); return; }
                render(r.data);
            });
        }
        function render(data) {
            var groups = data.groups || [];
            var $box = $('#fxGroups').empty();
            $('#fxMissCount').text((data.pagination && data.pagination.total) || 0);
            if (!groups.length) { $box.html('<div class="fx-empty"><i class="bi bi-check2-circle"></i>太棒了，范围内所有人都已提交！</div>'); return; }
            groups.forEach(function (g, gi) {
                var members = g.members || [];
                var rows = members.map(function (m) {
                    return '<tr><td style="width:40px"><input type="checkbox" class="fx-miss-cb" value="' + esc(m.uid) + '"></td>' +
                        '<td class="fx-mono">' + esc(m.uid) + '</td><td class="fw-bold">' + esc(m.name) + '</td>' +
                        '<td>' + esc(m.class_name) + '</td><td>' + esc(m.grade) + '</td></tr>';
                }).join('');
                var $grp = $('<div class="fx-group">');
                $grp.html(
                    '<div class="fx-group-head" data-bs-toggle="collapse" data-bs-target="#fxg' + gi + '">' +
                    '<div class="fx-group-title"><i class="bi bi-people-fill text-primary"></i>' + esc(g.grade) + ' ' + esc(g.class_name) +
                    ' <span class="fx-group-count">未交 ' + members.length + ' 人</span></div>' +
                    '<i class="bi bi-chevron-down text-muted"></i></div>' +
                    '<div class="collapse show" id="fxg' + gi + '"><div class="table-responsive">' +
                    '<table class="table table-sm mb-0" style="font-size:13px"><thead class="table-light"><tr><th></th><th>学号</th><th>姓名</th><th>班级</th><th>年级</th></tr></thead>' +
                    '<tbody>' + rows + '</tbody></table></div></div>');
                $box.append($grp);
            });
        }
        $('#fxGrade').on('change', function () { state.grade = $(this).val(); load(); });
        $('#fxSelAll').on('change', function () { $('.fx-miss-cb').prop('checked', this.checked); });
        function selectedUids() { return $('.fx-miss-cb:checked').map(function () { return this.value; }).get(); }
        function doRemind(payload, btn) {
            var $b = $(btn); var old = $b.html(); $b.prop('disabled', true).html('<span class="fx-spin"></span> 催交中…');
            $.ajax({ url: CTX.urls.remind, method: 'POST', contentType: 'application/json', data: JSON.stringify(payload), dataType: 'json' })
                .done(function (r) {
                    if (r.success) {
                        toast(r.message, 'ok');
                        if (r.data && r.data.rounds != null) { $('#fxRounds').text(r.data.rounds); }
                    } else { toast(r.message || '催交失败', 'err'); }
                })
                .fail(function () { toast('催交请求失败', 'err'); })
                .always(function () { $b.prop('disabled', false).html(old); });
        }
        $('#fxRemindSel').on('click', function () {
            var uids = selectedUids();
            if (!uids.length) { toast('请先勾选要催交的学生', 'err'); return; }
            if (!confirm('确定对选中的 ' + uids.length + ' 名学生发送催交通知？')) { return; }
            doRemind({ uids: uids }, this);
        });
        $('#fxRemindAll').on('click', function () {
            if (!confirm('确定对全部未提交者发送催交通知？')) { return; }
            doRemind({ all_missing: true, grade: state.grade }, this);
        });
        load();
    }

    /* ══ 页面：材料清单 ═════════════════════════════════════ */
    function pageFiles() {
        var state = { grade: '', class_name: '', question_id: '', only_missing: false };
        var previewModal = new bootstrap.Modal(document.getElementById('fxPreviewModal'));
        $('#fxGrade').on('change', function () { fillClasses($(this).val(), $('#fxClass')); });
        fillClasses('', $('#fxClass'));
        var ICONS = { pdf: 'bi-file-earmark-pdf text-danger', doc: 'bi-file-earmark-word text-primary', docx: 'bi-file-earmark-word text-primary',
            xls: 'bi-file-earmark-excel text-success', xlsx: 'bi-file-earmark-excel text-success', jpg: 'bi-file-earmark-image text-warning',
            jpeg: 'bi-file-earmark-image text-warning', png: 'bi-file-earmark-image text-warning', txt: 'bi-file-earmark-text text-secondary',
            zip: 'bi-file-earmark-zip text-warning' };
        function load() {
            $('#fxTbody').html('<tr><td colspan="10" class="text-center py-4 text-muted">加载中…</td></tr>');
            getJSON(CTX.urls.filesData, { grade: state.grade, class_name: state.class_name, question_id: state.question_id, only_missing: state.only_missing ? 1 : '' }).done(function (r) {
                if (!r.success) { toast(r.message || '加载失败', 'err'); return; }
                render(r.data);
            });
        }
        function render(data) {
            var items = data.items || [];
            $('#fxFileTotal').text(data.total || 0);
            $('#fxFileSize').text(data.total_size_text || '0B');
            $('#fxFileMissing').text(data.missing_count || 0);
            $('#fxFileInfo').text('当前显示 ' + items.length + ' 条' + (state.only_missing ? '（仅缺失）' : ''));
            var $tb = $('#fxTbody').empty();
            if (!items.length) { $tb.html('<tr><td colspan="10" class="fx-empty"><i class="bi bi-folder2-open"></i>暂无材料文件</td></tr>'); return; }
            items.forEach(function (f) {
                var icon = ICONS[f.ext] || 'bi-file-earmark text-muted';
                var $tr = $('<tr>');
                $tr.append($('<td>').html('<i class="bi ' + icon + '" style="font-size:18px"></i>'));
                $tr.append($('<td class="fw-bold">').text(f.filename));
                $tr.append($('<td class="fx-mono" style="font-size:12px">').text(f.size_text));
                $tr.append($('<td class="fx-mono">').text(f.uid || '-'));
                $tr.append($('<td>').text(f.name || '-'));
                $tr.append($('<td>').text(f.grade));
                $tr.append($('<td>').text(f.class_name));
                $tr.append($('<td style="max-width:180px">').text(f.question_title).css({ overflow: 'hidden', 'text-overflow': 'ellipsis' }));
                $tr.append($('<td>').html(f.exists ? '<span class="fx-badge b-approved">正常</span>' : '<span class="fx-badge b-rejected">缺失</span>'));
                var $ops = $('<td class="text-nowrap">');
                if (f.exists) {
                    $ops.append($('<button class="btn btn-sm btn-outline-primary me-1" title="预览">').html('<i class="bi bi-eye"></i>').on('click', function () { openPreview(f); }));
                    $ops.append($('<a class="btn btn-sm btn-outline-secondary" title="下载" target="_blank">').attr('href', f.download_url).html('<i class="bi bi-download"></i>'));
                } else { $ops.append($('<span class="fx-hint">—</span>')); }
                $tr.append($ops);
                $tb.append($tr);
            });
        }
        function openPreview(f) {
            $('#fxPreviewTitle').text(f.filename);
            $('#fxPreviewDownload').attr('href', f.download_url);
            var ext = (f.ext || '').toLowerCase();
            var $body = $('#fxPreviewBody');
            if (['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp'].indexOf(ext) >= 0) {
                $body.html('<img class="fx-preview-img" src="' + esc(f.download_url) + '" alt="' + esc(f.filename) + '">');
            } else if (ext === 'pdf') {
                $body.html('<iframe src="' + esc(f.download_url) + '" style="width:100%;height:64vh;border:none;border-radius:10px"></iframe>');
            } else if (ext === 'txt') {
                $body.html('<div class="fx-hint">文本文件，请下载后查看，或点右上角「下载原文件」。</div>');
            } else {
                $body.html('<div class="fx-empty"><i class="bi bi-file-earmark"></i>该类型不支持在线预览，请下载查看</div>');
            }
            previewModal.show();
        }
        $('#fxApply').on('click', function () {
            state.grade = $('#fxGrade').val(); state.class_name = $('#fxClass').val();
            state.question_id = $('#fxQuestion').val(); state.only_missing = $('#fxOnlyMissing').is(':checked'); load();
        });
        $('#fxOnlyMissing').on('change', function () { state.only_missing = this.checked; load(); });
        load();
    }

    /* ══ 页面：材料包下载 ═══════════════════════════════════ */
    function pagePackage() {
        $('#fxGrade').on('change', function () { fillClasses($(this).val(), $('#fxClass')); });
        fillClasses('', $('#fxClass'));
        $('input[name=fxScope]').on('change', function () {
            var v = $(this).val();
            $('#fxGrade').prop('disabled', !(v === 'grade' || v === 'class'));
            $('#fxClass').prop('disabled', v !== 'class');
            $('#fxQuestion').prop('disabled', v !== 'question');
            $('#fxUid').prop('disabled', v !== 'uid');
        });
        $('#fxStruct').on('click', 'label', function () {
            $('#fxStruct label').removeClass('active'); $(this).addClass('active');
            $(this).find('input[type=radio]').prop('checked', true);
        });
        function collect() {
            var scope = $('input[name=fxScope]:checked').val();
            return {
                scope: scope,
                grade: (scope === 'grade' || scope === 'class') ? $('#fxGrade').val() : '',
                class_name: scope === 'class' ? $('#fxClass').val() : '',
                question_id: scope === 'question' ? $('#fxQuestion').val() : '',
                uid: scope === 'uid' ? $('#fxUid').val().trim() : '',
                structure: $('input[name=fxStructure]:checked').val(),
                approved_only: $('#fxApprovedOnly').is(':checked') ? 1 : ''
            };
        }
        $('#fxPreviewBtn').on('click', function () {
            var p = collect();
            $('#fxPreviewResult').html('<div class="fx-empty" style="padding:26px"><i class="bi bi-arrow-repeat"></i>统计中…</div>');
            getJSON(CTX.urls.preview, p).done(function (r) {
                if (!r.success) { toast(r.message || '预览失败', 'err'); return; }
                var d = r.data;
                var tree = (d.tree || []).join('\n');
                var html = '<div class="row g-2 mb-3 text-center">' +
                    '<div class="col-4"><div class="fx-hint">可打包文件</div><div class="fx-num" style="font-size:24px;font-weight:700">' + d.packable_files + '</div></div>' +
                    '<div class="col-4"><div class="fx-hint">总大小</div><div class="fx-num" style="font-size:24px;font-weight:700">' + esc(d.total_size_text) + '</div></div>' +
                    '<div class="col-4"><div class="fx-hint">缺失跳过</div><div class="fx-num" style="font-size:24px;font-weight:700;color:var(--fx-bad)">' + d.missing_count + '</div></div></div>';
                if (d.warning) { html += '<div class="alert alert-warning py-2 px-3" style="font-size:12.5px"><i class="bi bi-exclamation-triangle"></i> ' + esc(d.warning) + '</div>'; }
                if (d.use_disk) { html += '<div class="alert alert-info py-2 px-3" style="font-size:12.5px"><i class="bi bi-hdd"></i> 文件较大，将采用落盘方式打包，下载完成后自动清理。</div>'; }
                html += '<div class="fx-hint mb-1">目录结构示例（前 3 层）：</div><div class="fx-tree">' + esc(tree || '（无文件）') + '</div>';
                $('#fxPreviewResult').html(html);
                $('#fxDownloadBtn').prop('disabled', d.packable_files === 0);
            }).fail(function () { toast('预览请求失败', 'err'); });
        });
        $('#fxDownloadBtn').on('click', function () {
            var p = collect();
            var $b = $(this); $b.prop('disabled', true).html('<span class="fx-spin"></span> 正在打包，请稍候…');
            var qs = $.param({ scope: p.scope, grade: p.grade, class_name: p.class_name, question_id: p.question_id,
                uid: p.uid, structure: p.structure, approved_only: p.approved_only,
                include_manifest: $('#fxManifest').is(':checked') ? 1 : '' });
            var iframe = document.createElement('iframe');
            iframe.style.display = 'none';
            iframe.src = CTX.urls.download + '?' + qs;
            document.body.appendChild(iframe);
            toast('打包已开始，完成后浏览器将自动下载', 'info');
            setTimeout(function () {
                $b.prop('disabled', false).html('<i class="bi bi-file-earmark-zip"></i> 确认下载材料包');
                $(iframe).remove();
            }, 8000);
        });
    }

    /* ── 分派 ─────────────────────────────────────────────── */
    $doc.ready(function () {
        if (CTX.page === 'summary') { pageSummary(); }
        else if (CTX.page === 'table') { pageTable(); }
        else if (CTX.page === 'missing') { pageMissing(); }
        else if (CTX.page === 'files') { pageFiles(); }
        else if (CTX.page === 'package') { pagePackage(); }
    });
})();
