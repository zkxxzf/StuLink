/* StuLink 积分管理：筛选/列表/汇总/录入/可视化 */
(function () {
'use strict';
var opts = null;
var pickedStudent = null;
var curTab = 'records';
var modal = null;
var charts = { trend: null, category: null, ranking: null };

function esc(v) {
    return String(v == null ? '' : v).replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function fmtPts(p) {
    var n = Number(p) || 0;
    var cls = n > 0 ? 'text-success' : n < 0 ? 'text-danger' : '';
    return '<span class="' + cls + ' fw-bold">' + (n > 0 ? '+' : '') + n + '</span>';
}
function qs() {
    var a = [];
    var g = $('#fGrade').val(), c = $('#fClass').val(), kw = $('#fKw').val().trim();
    var d1 = $('#fFrom').val(), d2 = $('#fTo').val();
    if (g) a.push('grade=' + encodeURIComponent(g));
    if (c) a.push('class_name=' + encodeURIComponent(c));
    if (kw) a.push('q=' + encodeURIComponent(kw));
    if (d1) a.push('date_from=' + d1);
    if (d2) a.push('date_to=' + d2);
    return a.length ? '?' + a.join('&') : '';
}

function loadOptions() {
    $.getJSON('/points/api/options', function (res) {
        opts = res.data;
        var $g = $('#fGrade').empty().append('<option value="">全部年级</option>');
        opts.grades.forEach(function (g) { $g.append('<option value="' + esc(g) + '">' + esc(g) + '</option>'); });
        if (opts.grades.length === 1) $g.val(opts.grades[0]);
        fillClass();
        var $c = $('#mCategory').empty();
        opts.categories.forEach(function (c) { $c.append('<option value="' + esc(c) + '">' + esc(c) + '</option>'); });
        if (opts.can_edit) {
            $('#btnAdd').removeClass('d-none');
        }
        // v1.17.0：导入/导出按钮各自按权限显示（与路由 points.import / points.export 对齐）
        if (opts.can_import) {
            $('#btnImport').removeClass('d-none');
        }
        if (opts.can_export) {
            $('#btnExport').removeClass('d-none');
        }
        var scopeText = {school: '范围：全校', grade: '范围：本年级 ' + (opts.grades[0] || ''),
                         class: '范围：所辖班级', none: '未关联管理范围，仅可查看空数据'}[opts.scope] || '';
        $('#scopeHint').text(scopeText);
        $('#mDate').val(opts.today);
        load();
    });
}

function fillClass() {
    var g = $('#fGrade').val();
    var list = (opts && opts.classes && opts.classes[g]) || [];
    if (!g) {
        // 全部年级：合并所有班级
        list = [];
        if (opts && opts.classes) {
            Object.keys(opts.classes).forEach(function (k) {
                opts.classes[k].forEach(function (c) { if (list.indexOf(c) < 0) list.push(c); });
            });
            list.sort();
        }
    }
    if (opts && ('class' === opts.scope)) {
        // 班主任：合并所辖班级
        list = [];
        Object.keys(opts.classes).forEach(function (k) {
            opts.classes[k].forEach(function (c) { if (list.indexOf(c) < 0) list.push(c); });
        });
        list.sort();
    }
    var $c = $('#fClass').empty().append('<option value="">全部班级</option>');
    list.forEach(function (c) { $c.append('<option value="' + esc(c) + '">' + esc(c) + '</option>'); });
    if (list.length === 1) $c.val(list[0]);
}

function load() {
    if (curTab === 'records') loadRecords();
    else if (curTab === 'summary') loadSummary();
    else if (curTab === 'charts') loadCharts();
}

function loadRecords() {
    $('#tbRecords').html('<tr><td colspan="9" class="text-center text-muted py-4">加载中…</td></tr>');
    $.getJSON('/points/api/list' + qs(), function (res) {
        var d = res.data;
        $('#statCount').text(d.stat.count);
        $('#statStudents').text(d.stat.students);
        $('#statPoints').text((d.stat.total_points > 0 ? '+' : '') + d.stat.total_points);
        var $tb = $('#tbRecords').empty();
        if (!d.records.length) {
            $tb.append('<tr><td colspan="9" class="text-center text-muted py-4">暂无积分记录'
                + (opts && opts.can_edit ? '，点击右上角"录入积分"开始记录' : '') + '</td></tr>');
            return;
        }
        d.records.forEach(function (r) {
            var tr = $('<tr>');
            tr.append('<td>' + esc(r.recorded_at) + '</td>')
              .append('<td>' + esc(r.student_name) + ' <span class="text-muted small">' + esc(r.student_no) + '</span></td>')
              .append('<td>' + esc(r.class_name) + '</td>')
              .append('<td class="text-end">' + fmtPts(r.points) + '</td>')
              .append('<td>' + esc(r.category || '—') + '</td>')
              .append('<td>' + esc(r.reason) + '</td>')
              .append('<td class="text-muted small">' + esc(r.remark || '') + '</td>')
              .append('<td class="small">' + esc(r.operator_name) + '</td>');
            var ops = '<td class="text-end">';
            if (opts && opts.can_edit) {
                ops += '<button class="btn btn-sm btn-outline-primary py-0 px-1 me-1 rec-edit" data-id="' + r.id + '">改</button>'
                     + '<button class="btn btn-sm btn-outline-danger py-0 px-1 rec-del" data-id="' + r.id + '">删</button>';
            } else { ops += '—'; }
            ops += '</td>';
            tr.append(ops);
            tr.data('rec', r);
            $tb.append(tr);
        });
    }).fail(function () {
        $('#tbRecords').html('<tr><td colspan="9" class="text-center text-danger py-4">加载失败</td></tr>');
    });
}

function loadSummary() {
    $('#tbSummary').html('<tr><td colspan="6" class="text-center text-muted py-4">加载中…</td></tr>');
    $.getJSON('/points/api/summary' + qs(), function (res) {
        var rows = res.data || [];
        $('#statCount').text(rows.reduce(function (a, r) { return a + r.count; }, 0));
        $('#statStudents').text(rows.length);
        $('#statPoints').text((rows.reduce(function (a, r) { return a + r.points; }, 0) > 0 ? '+' : '')
            + rows.reduce(function (a, r) { return a + r.points; }, 0));
        var $tb = $('#tbSummary').empty();
        if (!rows.length) {
            $tb.append('<tr><td colspan="6" class="text-center text-muted py-4">暂无数据</td></tr>');
            return;
        }
        rows.forEach(function (r, i) {
            $tb.append('<tr><td class="text-end">' + (i + 1) + '</td><td>' + esc(r.student_no)
                + '</td><td>' + esc(r.student_name) + '</td><td>' + esc(r.class_name)
                + '</td><td class="text-end">' + fmtPts(r.points)
                + '</td><td class="text-end">' + r.count + '</td></tr>');
        });
    });
}

/* ---------- 弹窗 ---------- */
function openRec(rec) {
    pickedStudent = null;
    $('#mId').val('');
    $('#recModalTitle').text('录入积分');
    $('#mStudentInput').val('').prop('disabled', false);
    $('#mStudentPicked').text('');
    $('#mPoints').val('');
    $('#mCategory').val(opts.categories[0] || '');
    $('#mDate').val(opts.today);
    $('#mReason').val('');
    $('#mRemark').val('');
    $('#stuDrop').addClass('d-none').empty();
    if (rec) {
        $('#recModalTitle').text('编辑积分');
        $('#mId').val(rec.id);
        pickedStudent = {student_no: rec.student_no, name: rec.student_name,
                         grade: rec.grade, class_name: rec.class_name};
        $('#mStudentInput').val(rec.student_name + '（' + rec.student_no + '）').prop('disabled', true);
        $('#mStudentPicked').text('班级：' + rec.class_name + '（编辑时不可更换学生）');
        $('#mPoints').val(rec.points);
        $('#mCategory').val(rec.category || opts.categories[0] || '');
        $('#mDate').val(rec.recorded_at);
        $('#mReason').val(rec.reason);
        $('#mRemark').val(rec.remark);
    }
    modal.show();
}

function searchStudents(kw) {
    $.getJSON('/points/api/students?q=' + encodeURIComponent(kw), function (res) {
        var rows = res.data || [];
        var $d = $('#stuDrop').empty();
        if (!rows.length) { $d.addClass('d-none'); return; }
        rows.forEach(function (s) {
            $('<button type="button" class="list-group-item list-group-item-action py-1 small">')
                .text(s.name + '（' + s.student_no + '） ' + s.grade + s.class_name)
                .on('click', function () {
                    pickedStudent = s;
                    $('#mStudentInput').val(s.name + '（' + s.student_no + '）');
                    $('#mStudentPicked').text('班级：' + s.grade + ' ' + s.class_name);
                    $d.addClass('d-none');
                })
                .appendTo($d);
        });
        $d.removeClass('d-none');
    });
}

function save() {
    var payload = {
        student_no: pickedStudent ? pickedStudent.student_no : '',
        points: $('#mPoints').val(),
        category: $('#mCategory').val(),
        reason: $('#mReason').val().trim(),
        remark: $('#mRemark').val().trim(),
        recorded_at: $('#mDate').val(),
    };
    if (!payload.student_no) { alert('请先搜索并选择学生'); return; }
    var id = $('#mId').val();
    var url = id ? '/points/api/record/' + id : '/points/api/record';
    $('#btnSave').prop('disabled', true);
    $.post(url, JSON.stringify(payload), function (res) {
        $('#btnSave').prop('disabled', false);
        modal.hide();
        load();
    }).fail(function (x) {
        $('#btnSave').prop('disabled', false);
        alert(x.responseJSON && x.responseJSON.message || '保存失败');
    });
}

/* ---------- 图表 ---------- */
function loadCharts() {
    loadTrendChart();
    loadCategoryChart();
    loadRankingChart();
}

function loadTrendChart() {
    if (!charts.trend) {
        charts.trend = echarts.init(document.getElementById('chartTrend'));
    }
    $.getJSON('/points/api/trend' + qs(), function(res) {
        var data = res.data || [];
        var dates = data.map(function(d) { return d.date; });
        var totals = data.map(function(d) { return d.total; });
        charts.trend.setOption({
            tooltip: { trigger: 'axis' },
            grid: { left: 50, right: 20, top: 20, bottom: 30 },
            xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 11 } },
            yAxis: { type: 'value', axisLabel: { fontSize: 11 } },
            series: [{
                type: 'line',
                data: totals,
                smooth: true,
                lineStyle: { color: '#3b82f6', width: 2 },
                areaStyle: { color: 'rgba(59,130,246,0.1)' },
                itemStyle: { color: '#3b82f6' }
            }]
        });
    });
}

function loadCategoryChart() {
    if (!charts.category) {
        charts.category = echarts.init(document.getElementById('chartCategory'));
    }
    $.getJSON('/points/api/category-distribution' + qs(), function(res) {
        var data = res.data || [];
        var pieData = data.map(function(d) {
            return { name: d.category, value: Math.abs(d.total) };
        });
        charts.category.setOption({
            tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
            legend: { orient: 'vertical', right: 10, top: 'center', textStyle: { fontSize: 11 } },
            series: [{
                type: 'pie',
                radius: ['40%', '70%'],
                center: ['35%', '50%'],
                data: pieData,
                label: { show: false },
                emphasis: { label: { show: true, fontSize: 12 } }
            }]
        });
    });
}

function loadRankingChart() {
    if (!charts.ranking) {
        charts.ranking = echarts.init(document.getElementById('chartRanking'));
    }
    $.getJSON('/points/api/class-ranking' + qs(), function(res) {
        var data = res.data || [];
        var names = data.map(function(d) { return d.grade + d.class_name; });
        var totals = data.map(function(d) { return d.total; });
        charts.ranking.setOption({
            tooltip: { trigger: 'axis' },
            grid: { left: 50, right: 20, top: 20, bottom: 60 },
            xAxis: {
                type: 'category',
                data: names,
                axisLabel: { rotate: 30, fontSize: 11 }
            },
            yAxis: { type: 'value', axisLabel: { fontSize: 11 } },
            series: [{
                type: 'bar',
                data: totals,
                itemStyle: {
                    color: function(params) {
                        return params.value >= 0 ? '#10b981' : '#ef4444';
                    }
                },
                barMaxWidth: 40
            }]
        });
    });
}

$(function () {
    modal = new bootstrap.Modal(document.getElementById('recModal'));
    loadOptions();

    $('#btnSearch').on('click', load);
    $('#fGrade').on('change', function () { fillClass(); load(); });
    $('#fClass').on('change', load);
    $('#fKw').on('keydown', function (e) { if (e.key === 'Enter') load(); });
    $('#fFrom,#fTo').on('change', load);
    $('#btnReset').on('click', function () {
        $('#fGrade,#fClass,#fKw,#fFrom,#fTo').val('');
        load();
    });
    $('#tabRecords').on('click', function () {
        curTab = 'records'; $(this).addClass('active'); $('#tabSummary,#tabCharts').removeClass('active');
        $('#cardRecords').removeClass('d-none'); $('#cardSummary,#cardCharts').addClass('d-none'); load();
    });
    $('#tabSummary').on('click', function () {
        curTab = 'summary'; $(this).addClass('active'); $('#tabRecords,#tabCharts').removeClass('active');
        $('#cardSummary').removeClass('d-none'); $('#cardRecords,#cardCharts').addClass('d-none'); load();
    });
    $('#tabCharts').on('click', function () {
        curTab = 'charts'; $(this).addClass('active'); $('#tabRecords,#tabSummary').removeClass('active');
        $('#cardCharts').removeClass('d-none'); $('#cardRecords,#cardSummary').addClass('d-none'); load();
    });
    $('#btnAdd').on('click', function () { openRec(null); });
    $('#btnSave').on('click', save);

    var timer = null;
    $('#mStudentInput').on('input', function () {
        var kw = $(this).val().trim();
        clearTimeout(timer);
        if (kw.length < 1) { $('#stuDrop').addClass('d-none'); return; }
        timer = setTimeout(function () { searchStudents(kw); }, 300);
    });
    $(document).on('click', function (e) {
        if (!$(e.target).closest('#mStudentInput,#stuDrop').length) $('#stuDrop').addClass('d-none');
    });

    $('#tbRecords').on('click', '.rec-edit', function () {
        var rec = $(this).closest('tr').data('rec');
        openRec(rec);
    });
    $('#tbRecords').on('click', '.rec-del', function () {
        var rec = $(this).closest('tr').data('rec');
        if (!confirm('确认删除「' + rec.student_name + ' ' + (rec.points > 0 ? '+' : '') + rec.points
                + '分 · ' + rec.reason + '」？')) return;
        $.post('/points/api/record/' + rec.id + '/delete', function () { load(); })
            .fail(function (x) { alert(x.responseJSON && x.responseJSON.message || '删除失败'); });
    });
});
})();
