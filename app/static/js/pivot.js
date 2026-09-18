/* StuLink 自由表前端 v1.9.2
 * 行维度 × 列维度 × 指标 自由组合；渲染为交叉表 + （单指标时）分组柱状图
 */
(function () {
var PALETTE = ['#3b82f6', '#8b5cf6', '#10b981', '#f59e0b', '#ef4444',
               '#06b6d4', '#ec4899', '#84cc16', '#f97316', '#6366f1'];
var meta = null;
var chart = null;

function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
        return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
    });
}
function fmt(v) {
    if (v === null || v === undefined || v === '') return '—';
    return String(v);
}
function showEmpty(msg) {
    $('#pvResult').empty();
    $('#pvEmpty').removeClass('d-none').find('p').text(msg || '');
}

/* ---------------- 初始化 ---------------- */
function init() {
    $.getJSON('/grades/api/pivot/meta', function (res) {
        meta = res.data || {};
        fillGrades();
        fillSubject();
        fillDims();
        fillMeasures();
        bind();
    }).fail(function () { showEmpty('加载失败，请刷新页面'); });
}

function fillGrades() {
    var sel = $('#pvGrade').empty();
    var grades = meta.grades || [], exams = meta.exams || [];
    grades.forEach(function (g) { sel.append('<option value="' + esc(g) + '">' + esc(g) + '</option>'); });
    // 默认选中「第一个有考试的年级」，否则会停在无考试的年级上导致考试下拉为空
    var first = grades.filter(function (g) {
        return exams.some(function (e) { return e.grade === g; });
    })[0];
    if (first) { sel.val(first); }
    else if (grades.length) { sel.val(grades[0]); }
    fillExams();
}
function fillExams() {
    var g = $('#pvGrade').val(), sel = $('#pvExam').empty();
    (meta.exams || []).filter(function (e) { return e.grade === g; }).forEach(function (e) {
        sel.append('<option value="' + e.id + '">' + esc(e.name) + '（' + e.date + '）</option>');
    });
}
function fillSubject() {
    var sel = $('#pvSubject').empty().append('<option value="总分">总分</option>');
    (meta.subjects || []).forEach(function (s) { sel.append('<option value="' + esc(s) + '">' + esc(s) + '</option>'); });
}
function fillDims() {
    var row = $('#pvRow').empty(), col = $('#pvCol').empty();
    col.append('<option value="">（不分列）</option>');
    (meta.dims || []).forEach(function (d) {
        row.append('<option value="' + d.key + '">' + esc(d.label) + '</option>');
        col.append('<option value="' + d.key + '">' + esc(d.label) + '</option>');
    });
    row.val('class');
    col.val('layer');    // 默认给「班级 × 层」，直接回答各班各层上线情况
}
function fillMeasures() {
    var box = $('#pvMeasures').empty();
    (meta.measures || []).forEach(function (m) {
        var hint = m.need_layer ? '（需层维度）' : (m.need_line ? '（需单科）' : '');
        box.append('<div class="form-check form-check-inline mb-0">'
            + '<input class="form-check-input pv-m" type="checkbox" value="' + m.key + '" id="pm_' + m.key + '">'
            + '<label class="form-check-label small" for="pm_' + m.key + '">' + esc(m.label) + hint + '</label></div>');
    });
    ['count', 'online_n', 'online_rate'].forEach(function (k) { $('#pm_' + k).prop('checked', true); });
}

function bind() {
    $('#pvGrade').on('change', fillExams);
    $('#pvRun').on('click', run);
    $('#pvRow,#pvCol').on('change', syncMeasureHints);
    $(window).on('resize', function () { if (chart) chart.resize(); });
    syncMeasureHints();
}

/* 行/列不含「层」时，禁用并取消上线/层内类指标 */
function syncMeasureHints() {
    var hasLayer = ($('#pvRow').val() === 'layer') || ($('#pvCol').val() === 'layer');
    (meta.measures || []).forEach(function (m) {
        var $i = $('#pm_' + m.key);
        if (m.need_layer && !hasLayer) { $i.prop('checked', false).prop('disabled', true); }
        else { $i.prop('disabled', false); }
    });
}

/* ---------------- 查询与渲染 ---------------- */
function run() {
    var ms = [];
    $('#pvMeasures input:checked').each(function () { ms.push($(this).val()); });
    if (!ms.length) { showEmpty('请至少勾选一个指标'); return; }
    if (!$('#pvExam').val()) { showEmpty('请先选择考试'); return; }
    var q = {
        exam_id: $('#pvExam').val(), row: $('#pvRow').val(), col: $('#pvCol').val(),
        subject: $('#pvSubject').val(), direction: $('#pvDir').val(), measures: ms.join(',')
    };
    $('#pvResult').empty();
    $('#pvEmpty').addClass('d-none').find('p').text('加载中…').end().removeClass('d-none');
    $.getJSON('/grades/api/pivot', q, function (res) {
        if (!res.success) { showEmpty(res.message || '生成失败'); return; }
        $('#pvEmpty').addClass('d-none');
        render(res.data, ms);
    }).fail(function () { showEmpty('数据加载失败（或无权查看）'); });
}

function render(d, measures) {
    var area = $('#pvResult').empty();
    var rows = d.row_keys || [], cols = d.col_keys || [], ms = d.measures || [];

    var head = $('<div class="alert alert-light border py-2 small">').append(
        '<i class="bi bi-info-circle"></i> 考试：<strong>' + esc(d.exam.name) + '</strong>'
        + ' · 口径：' + esc(d.subject)
        + (d.direction ? ' · 方向：' + esc(d.direction) : '')
        + ' · 行：' + esc(d.row_label) + (d.col_label ? ' · 列：' + esc(d.col_label) : ''));
    area.append(head);

    var card = $('<div class="card tbl-card mb-3">');
    card.append('<div class="card-header py-2"><h6 class="mb-0 small"><i class="bi bi-table me-1"></i>'
        + esc(d.row_label) + ' × ' + esc(d.col_label || '指标') + ' 交叉表</h6></div>');
    var body = $('<div class="card-body p-0 tbl-wrap">');
    var table = $('<table class="table table-hover g-table mb-0">');

    var r1 = $('<tr>');
    r1.append($('<th rowspan="2">').text(d.row_label));
    (d.col_label ? cols : ['']).forEach(function (c) {
        r1.append($('<th' + (d.col_label ? ' colspan="' + ms.length + '"' : '') + '>').text(d.col_label ? c : ''));
    });
    var r2 = $('<tr>');
    cols.forEach(function () { ms.forEach(function (m) { r2.append($('<th class="num">').text(m.label)); }); });
    table.append($('<thead>').append(r1).append(r2));

    var tb = $('<tbody>');
    rows.forEach(function (rk) {
        var tr = $('<tr>').append($('<td class="fw-semibold">').text(rk));
        cols.forEach(function (ck) {
            var cell = (d.cells || {})[rk + '|' + ck] || {};
            ms.forEach(function (m) { tr.append($('<td class="num">').text(fmt(cell[m.key]))); });
        });
        tb.append(tr);
    });
    table.append(tb);
    body.append(table);
    card.append(body);
    area.append(card);

    // 单指标时出图，便于横向比较
    if (ms.length === 1) {
        var cx = $('<div class="card chart-card">');
        cx.append('<div class="card-header py-2"><h6 class="mb-0 small"><i class="bi bi-bar-chart me-1"></i>'
            + esc(ms[0].label) + '（' + esc(d.row_label)
            + (d.col_label ? ' × ' + esc(d.col_label) : '') + '）</h6></div>');
        var box = $('<div class="chart-box"></div>');
        cx.append($('<div class="card-body p-2">').append(box));
        area.append(cx);
        chart = echarts.init(box[0]);
        var series = cols.map(function (ck, i) {
            return {
                name: d.col_label ? String(ck) : ms[0].label, type: 'bar',
                itemStyle: {color: PALETTE[i % PALETTE.length]},
                data: rows.map(function (rk) {
                    var v = ((d.cells || {})[rk + '|' + ck] || {})[ms[0].key];
                    return (v === null || v === undefined) ? 0 : v;
                })
            };
        });
        chart.setOption({
            color: PALETTE, tooltip: {trigger: 'axis'},
            legend: {top: 0, type: 'scroll'},
            grid: {left: 60, right: 40, top: 50, bottom: 60, containLabel: true},
            xAxis: {type: 'category', data: rows, axisLabel: {interval: 0, rotate: rows.length > 12 ? 40 : 0}},
            yAxis: {type: 'value'}, series: series
        }, true);
    }
}

$(function () { init(); });
})();
