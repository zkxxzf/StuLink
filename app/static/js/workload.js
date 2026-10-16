/* StuLink 课时量统计与导出（2026-10-13 用户口径）
 *
 * 宿主页面：**查课统计页** inspection.html 里的 #workloadModal（统计页负责统计与
 * 导出课时量）。接口来自模板里 #inspectionData JSON 的 workload 字段：
 *
 *     configUrl / rulesUrl / examAddUrl / examDelTpl / exportUrl
 *
 * 三个页签：
 *   导出课时量  —— 选区间（+年级/班级）后跳导出接口下载 Excel
 *   课型折算规则 —— 每个节次一行，单独设置「一节课折算几课时」；另有「监考（每场）」
 *   考试期间    —— 某班某段时间的标记；期间不计工作量、不计违纪/迟到
 *
 * 说明：只依赖 jQuery（页面已加载），导出走服务端 openpyxl 生成；
 * 日期默认值取本机当前日期（本月 1 日 ~ 今天）。
 */
(function () {
    'use strict';
    var $ = window.jQuery;
    if (!$) { return; }

    var cfg = {};
    try {
        cfg = JSON.parse(document.getElementById('inspectionData').textContent || '{}');
    } catch (e) { cfg = {}; }
    var wl = cfg.workload || {};
    if (!wl.configUrl) { return; }        // 未注入（无编辑权限等）→ 不做任何绑定

    var state = { loaded: false };

    function esc(v) {
        return String(v == null ? '' : v).replace(/[&<>"]/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
        });
    }
    function token() { var el = document.getElementById('wlCsrf'); return el ? el.value : ''; }
    function say(msg, bad) {
        var $m = $('#wlMsg');
        if (!$m.length) { return; }
        $m.text(msg || '').removeClass('text-danger text-success')
          .addClass(bad ? 'text-danger' : 'text-success');
        if (msg) { window.setTimeout(function () { $m.text(''); }, 4500); }
    }
    function pad(n) { return (n < 10 ? '0' : '') + n; }
    function todayISO() {
        var d = new Date();
        return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
    }
    function firstOfMonthISO() {
        var d = new Date();
        return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-01';
    }

    function postJSON(url, payload, ok) {
        $.ajax({
            url: url, type: 'POST', contentType: 'application/json',
            data: JSON.stringify(payload),
            headers: { 'X-CSRFToken': token() }
        }).done(function (res) {
            if (res && res.success) {
                say(res.message || '已保存');
                if (ok) { ok(res); }
                refresh();
            } else {
                say((res && res.message) || '操作失败', true);
            }
        }).fail(function (xhr) {
            var m = '操作失败';
            try { m = (xhr.responseJSON && xhr.responseJSON.message) || m; } catch (e) { /* 保持默认文案 */ }
            say(m + '（HTTP ' + (xhr.status || '') + '）', true);
        });
    }

    function renderRules(d) {
        var html = '';
        (d.periods || []).forEach(function (p) {
            html += '<tr data-kind="period" data-number="' + p.number + '">'
                + '<td class="fw-bold">' + esc(p.name) + '</td>'
                + '<td><input type="number" step="0.1" min="0" '
                + 'class="form-control form-control-sm wl-factor" value="' + esc(p.factor) + '"></td>'
                + '<td class="small text-muted">学校作息第 ' + p.number + ' 节</td>'
                + '</tr>';
        });
        $('#wlRuleBody').html(html
            || '<tr><td colspan="3" class="text-muted">本学期还没配置作息节次'
             + '（请到「课表 → 作息」先维护节次）</td></tr>');
        $('#wlInvigFactor').val(d.invigilation_factor);
        $('#wlTerm').text(d.term_name ? ('· ' + d.term_name) : '');
    }

    function renderExam(rows) {
        if (!rows.length) {
            $('#wlExamBody').html('<tr><td colspan="6" class="text-muted">暂无考试期间标记</td></tr>');
            return;
        }
        $('#wlExamBody').html(rows.map(function (r) {
            var days = Math.round((Date.parse(r.end_date) - Date.parse(r.start_date)) / 86400000) + 1;
            return '<tr data-id="' + r.id + '">'
                + '<td>' + esc(r.grade) + '</td>'
                + '<td>' + esc(r.class_name || '（全年级）') + '</td>'
                + '<td>' + esc(r.start_date) + ' ~ ' + esc(r.end_date) + '</td>'
                + '<td>' + days + '</td>'
                + '<td class="small text-muted">' + esc(r.note) + '</td>'
                + '<td><button type="button" class="btn btn-sm btn-outline-danger py-0 px-1 '
                + 'js-wl-exam-del" title="删除"><i class="bi bi-trash"></i></button></td>'
                + '</tr>';
        }).join(''));
    }

    function refresh() {
        $.getJSON(wl.configUrl, function (res) {
            var d = (res && res.data) || {};
            renderRules(d);
            renderExam(d.exam_periods || []);
        }).fail(function () { say('配置加载失败', true); });
    }

    function saveRules() {
        var items = [];
        $('#wlRuleBody tr[data-kind]').each(function () {
            var raw = $(this).find('.wl-factor').val();
            items.push({
                kind: 'period', period_number: Number($(this).data('number')),
                factor: (raw === '' ? null : Number(raw))
            });
        });
        var iv = $('#wlInvigFactor').val();
        items.push({ kind: 'invigilation', period_number: 0,
                     factor: (iv === '' ? null : Number(iv)) });
        postJSON(wl.rulesUrl, { items: items });
    }

    function addExam() {
        var payload = {
            grade: $('#wlExGrade').val(), class_name: $('#wlExClass').val(),
            start_date: $('#wlExFrom').val(), end_date: $('#wlExTo').val(),
            note: $('#wlExNote').val()
        };
        if (!payload.grade) { say('请选择年级', true); return; }
        if (!payload.start_date || !payload.end_date) { say('请选择起止日期', true); return; }
        postJSON(wl.examAddUrl, payload, function () {
            $('#wlExClass').val('');
            $('#wlExFrom').val('');
            $('#wlExTo').val('');
            $('#wlExNote').val('');
        });
    }

    $(function () {
        $('#wlFrom').val(firstOfMonthISO());
        $('#wlTo').val(todayISO());
        $('#workloadModal').on('shown.bs.modal', function () {
            if (!state.loaded) { state.loaded = true; refresh(); }
        });
        $('#btnWlSaveRules').on('click', saveRules);
        $('#btnWlExamAdd').on('click', addExam);
        $('#wlExamBody').on('click', '.js-wl-exam-del', function () {
            var $tr = $(this).closest('tr'), id = $tr.data('id');
            if (!window.confirm('删除这条考试期间标记？删除后该班按常规计算课时量与异常。')) {
                return;
            }
            postJSON(String(wl.examDelTpl).replace(/\/0\/delete$/, '/' + id + '/delete'), {});
        });
        $('#btnWlExport').on('click', function () {
            var qs = $.param({
                date_from: $('#wlFrom').val(), date_to: $('#wlTo').val(),
                grade: $('#wlGrade').val(), class_name: $('#wlClass').val()
            });
            window.location.href = wl.exportUrl + '?' + qs;
        });
    });
})();
