/* StuLink v1.18.8.0 · 查课核对页（2026-10-09 改版）
 * 页面版式：与全校总课表同款矩阵 —— 行＝班级、列＝节次，一天所有节次一次铺开。
 * 交互：点格子弹出标记面板（正常/迟到/缺课/调课/其他 + 备注）→
 *       POST /inspection/mark 写回 inspection_records，就地更新徽标与"覆盖率"。
 *       支持"未标记的全部正常"一键巡课；筛选（日期/年级）走服务端渲染整页刷新。
 */
(function () {
    'use strict';
    var cfg = {};
    try { cfg = JSON.parse(document.getElementById('liveData').textContent || '{}'); } catch (e) { cfg = {}; }
    var $ = window.jQuery;
    if (!$) { return; }

    var RESULT_TEXT = { normal: '正常', late: '迟到', absent: '缺课', swap: '调课', other: '其他' };

    function notify(msg, type) {
        if (typeof window.toast === 'function') { window.toast(msg, type || 'success'); }
        else { window.alert(msg); }
    }

    /* ── 时钟 ── */
    function pad(n) { return (n < 10 ? '0' : '') + n; }
    function tickClock() {
        var el = document.getElementById('clockHM');
        if (el) {
            var d = new Date();
            el.textContent = pad(d.getHours()) + ':' + pad(d.getMinutes());
        }
    }
    tickClock();
    window.setInterval(tickClock, 30000);

    /* ── 筛选：日期 / 年级（服务端渲染，页面本身只有几条 SQL）── */
    function nav(params) {
        var q = [];
        Object.keys(params).forEach(function (k) {
            if (params[k] !== '' && params[k] !== null && params[k] !== undefined) {
                q.push(encodeURIComponent(k) + '=' + encodeURIComponent(params[k]));
            }
        });
        window.location.href = cfg.pageUrl + (q.length ? '?' + q.join('&') : '');
    }
    function navNow(extra) {
        var p = { date: $('#datePick').val() || cfg.today, grade: $('#gradePick').val() || '' };
        if (extra) { Object.keys(extra).forEach(function (k) { p[k] = extra[k]; }); }
        nav(p);
    }
    $('#datePick').on('change', function () { navNow(); });
    $('#gradePick').on('change', function () { navNow(); });
    $('#btnToday').on('click', function () { navNow({ date: cfg.today }); });
    $('#btnPrint').on('click', function () { window.print(); });

    /* ── 覆盖率计数 ── */
    function recount() {
        var total = $('#liveContainer .ovw-item[data-check]').length;
        var done = $('#liveContainer .ovw-item[data-check!=""]').length;
        $('#statChecked').text(done);
        $('#statExpected').text(total);
        $('#statRate').text(total ? Math.floor(done * 100 / total) + '%' : '0%');
    }

    /* ── 就地更新一格 ── */
    function cellSelector(c) {
        return '#liveContainer .ovw-item[data-grade="' + c.grade + '"]'
            + '[data-class="' + c.class_name + '"]'
            + '[data-period="' + c.period_number + '"]';
    }
    function applyResult($el, result, note) {
        var label = RESULT_TEXT[result] || '';
        $el.attr('data-check', result || '');
        $el.find('.ovw-chk').attr('class', 'ovw-chk ovw-chk-' + (result || 'none'))
            .text(label || '未查');
        var t = ($el.data('subject') || '') + ' ' + ($el.data('teacher') || '');
        t += result ? (' · 已标记' + label + (note ? '（' + note + '）' : ''))
            : ' · 点此标记查课结果';
        $el.attr('title', t);
    }

    function post(cells, ok) {
        if (!cells || !cells.length) { return; }
        $.ajax({
            url: cfg.markUrl,
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ inspect_date: cfg.date, cells: cells })
        }).done(function (res) {
            if (res && res.success) {
                (res.cells || []).forEach(function (c) {
                    var $el = $(cellSelector(c));
                    if ($el.length) { applyResult($el, c.result, c.note); }
                });
                recount();
                if (ok) { ok(res); }
            } else {
                notify((res && res.message) || '标记失败', 'danger');
            }
        }).fail(function (xhr) {
            var msg = '标记失败';
            try { msg = JSON.parse(xhr.responseText).message || msg; } catch (e) { /* 忽略解析失败 */ }
            notify(msg, 'danger');
        });
    }

    /* ── 标记面板 ── */
    var $current = null;
    function openMark($el) {
        $current = $el;
        var meta = ($el.data('subject') || '未填学科') + ' · ' + ($el.data('teacher') || '未指定教师');
        $('#markMeta').html('<div class="fw-bold">' + $el.data('grade') + $el.data('class')
            + ' · 第 ' + $el.data('period') + ' 节</div><div class="text-muted">' + meta + '</div>');
        var cur = $el.attr('data-check') || '';
        $('#markHint').text(cur ? ('当前：' + RESULT_TEXT[cur] + '（点下面按钮可改）')
            : '把这次巡课看到的实际情况点一下即可');
        if (String($el.data('temp')) === '1') {
            $('#markHint').append(' · 该节课表上是当天临时调课');
        }
        $('#markNote').val($el.data('note') || '');
        var el = document.getElementById('markModal');
        if (el && window.bootstrap && window.bootstrap.Modal) {
            window.bootstrap.Modal.getOrCreateInstance(el).show();
        }
    }

    $('#liveContainer').on('click', '.ovw-item[data-check]', function () { openMark($(this)); });

    $('#markModal .mark-btn').on('click', function () {
        if (!$current || !$current.length) { return; }
        var result = $(this).data('result');
        var note = ($('#markNote').val() || '').trim();
        var cell = {
            grade: $current.data('grade'), class_name: $current.data('class'),
            period_number: $current.data('period'), entry_id: $current.data('entry'),
            subject: $current.data('subject'), teacher_uid: $current.data('uid'),
            teacher_name: $current.data('teacher'), result: result, note: note
        };
        post([cell], function () {
            $current.data('note', note);
            var el = document.getElementById('markModal');
            if (el && window.bootstrap && window.bootstrap.Modal) {
                window.bootstrap.Modal.getOrCreateInstance(el).hide();
            }
            notify('已标记：' + (RESULT_TEXT[result] || result));
        });
    });

    $('#btnClearMark').on('click', function () {
        if (!$current || !$current.length) { return; }
        var cell = {
            grade: $current.data('grade'), class_name: $current.data('class'),
            period_number: $current.data('period'), entry_id: $current.data('entry'),
            result: ''
        };
        post([cell], function () {
            $current.data('note', '');
            var el = document.getElementById('markModal');
            if (el && window.bootstrap && window.bootstrap.Modal) {
                window.bootstrap.Modal.getOrCreateInstance(el).hide();
            }
            notify('已撤销该格标记', 'warning');
        });
    });

    /* ── 一键：当前页面未标记的全部正常（巡课一圈点一下）── */
    $('#btnMarkAllNormal').on('click', function () {
        var cells = [];
        $('#liveContainer .ovw-item[data-check=""]').each(function () {
            var $el = $(this);
            cells.push({
                grade: $el.data('grade'), class_name: $el.data('class'),
                period_number: $el.data('period'), entry_id: $el.data('entry'),
                subject: $el.data('subject'), teacher_uid: $el.data('uid'),
                teacher_name: $el.data('teacher'), result: 'normal'
            });
        });
        if (!cells.length) { notify('当前页面上没有未标记的格子', 'secondary'); return; }
        if (!window.confirm('把当前页面上 ' + cells.length + ' 个未标记格子全部标为「正常」？')) { return; }
        post(cells, function (res) {
            notify('已标记 ' + (res.updated || 0) + ' 格为正常');
        });
    });

    recount();
})();
