/* StuLink 查课统计：ECharts 图表 + 批量录入动态行 */
(function () {
'use strict';

var charts = { pie: null, line: null };
var config = null;  // 从模板 JSON 块读取

function esc(v) {
    return String(v == null ? '' : v)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/* ========== 图表 ========== */

function loadStats(year) {
    var url = config.statsUrl;
    if (year) url += '?year=' + encodeURIComponent(year);
    $.getJSON(url, function (res) {
        renderPie(res.result_distribution || []);
        renderLine(res.monthly_trend || []);
        var s = res.summary || {};
        $('#statTotal').text(s.total || 0);
        $('#statNormalRate').text((s.normal_rate || 0) + '%');
        // 填充年份选择器（从月度趋势中提取年份）
        fillYearOptions(res.monthly_trend || []);
    }).fail(function () {
        $('#statTotal').text('-');
        $('#statNormalRate').text('-');
    });
}

function fillYearOptions(trend) {
    var $sel = $('#chartYear');
    if ($sel.find('option').length > 1) return; // 已填充
    var years = {};
    trend.forEach(function (m) {
        var y = (m.month || '').substring(0, 4);
        if (y) years[y] = true;
    });
    var list = Object.keys(years).sort().reverse();
    list.forEach(function (y) {
        $sel.append('<option value="' + y + '">' + y + '年</option>');
    });
}

function renderPie(data) {
    if (!charts.pie) {
        var el = document.getElementById('chartPie');
        if (!el) return;
        charts.pie = echarts.init(el);
    }
    var colorMap = { '正常': '#10b981', '迟到': '#f59e0b', '缺课': '#ef4444', '调课': '#6366f1', '其他': '#94a3b8' };
    var pieData = data.map(function (d) {
        return { name: d.name, value: d.value, itemStyle: { color: colorMap[d.name] || undefined } };
    });
    charts.pie.setOption({
        tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
        legend: { orient: 'vertical', right: 10, top: 'center', textStyle: { fontSize: 11 } },
        series: [{
            type: 'pie',
            radius: ['40%', '72%'],
            center: ['38%', '50%'],
            data: pieData,
            label: { show: false },
            emphasis: { label: { show: true, fontSize: 13, fontWeight: 'bold' } }
        }]
    });
}

function renderLine(trend) {
    if (!charts.line) {
        var el = document.getElementById('chartLine');
        if (!el) return;
        charts.line = echarts.init(el);
    }
    var months = trend.map(function (d) { return d.month; });
    charts.line.setOption({
        tooltip: { trigger: 'axis' },
        legend: { data: ['正常', '迟到', '缺课', '调课'], top: 0, textStyle: { fontSize: 11 } },
        grid: { left: 45, right: 15, top: 35, bottom: 30 },
        xAxis: { type: 'category', data: months, axisLabel: { fontSize: 10, rotate: months.length > 8 ? 30 : 0 } },
        yAxis: { type: 'value', axisLabel: { fontSize: 11 }, minInterval: 1 },
        series: [
            { name: '正常', type: 'line', data: trend.map(function (d) { return d.normal; }),
              smooth: true, lineStyle: { color: '#10b981', width: 2 }, itemStyle: { color: '#10b981' },
              areaStyle: { color: 'rgba(16,185,129,0.08)' } },
            { name: '迟到', type: 'line', data: trend.map(function (d) { return d.late; }),
              smooth: true, lineStyle: { color: '#f59e0b', width: 2 }, itemStyle: { color: '#f59e0b' } },
            { name: '缺课', type: 'line', data: trend.map(function (d) { return d.absent; }),
              smooth: true, lineStyle: { color: '#ef4444', width: 2 }, itemStyle: { color: '#ef4444' } },
            { name: '调课', type: 'line', data: trend.map(function (d) { return d.swap; }),
              smooth: true, lineStyle: { color: '#6366f1', width: 2 }, itemStyle: { color: '#6366f1' } }
        ]
    });
}

/* ========== 批量录入动态行 ========== */

var rowIdx = 0;

function buildRowHTML(idx) {
    var teacherOpts = '<option value="">-- 选择教师 --</option>';
    config.teachers.forEach(function (t) {
        teacherOpts += '<option value="' + esc(t.uid) + '">' + esc(t.name) + '（' + esc(t.uid) + '）</option>';
    });
    var gradeOpts = '<option value="">--</option>';
    config.grades.forEach(function (g) {
        gradeOpts += '<option value="' + esc(g) + '">' + esc(g) + '</option>';
    });
    var resultOpts = '';
    config.results.forEach(function (r) {
        resultOpts += '<option value="' + esc(r.key) + '">' + esc(r.label) + '</option>';
    });
    return '<tr data-row="' + idx + '">'
        + '<td class="text-muted small text-center">' + (idx + 1) + '</td>'
        + '<td><select name="row_grade" class="form-select form-select-sm" required>' + gradeOpts + '</select></td>'
        + '<td><select name="row_teacher_uid" class="form-select form-select-sm" required>' + teacherOpts + '</select></td>'
        + '<td><input name="row_class_name" class="form-control form-control-sm" maxlength="10" placeholder="班级"></td>'
        + '<td><input name="row_subject" class="form-control form-control-sm" maxlength="20" placeholder="科目"></td>'
        + '<td><input name="row_period" type="number" class="form-control form-control-sm" min="1" max="12" placeholder="节次"></td>'
        + '<td><select name="row_result" class="form-select form-select-sm">' + resultOpts + '</select></td>'
        + '<td><input name="row_note" class="form-control form-control-sm" maxlength="200" placeholder="备注"></td>'
        + '<td class="text-center"><button type="button" class="btn btn-sm btn-outline-danger btn-del-row py-0 px-1" title="删除此行"><i class="bi bi-x"></i></button></td>'
        + '</tr>';
}

function addBatchRow() {
    $('#batchBody').append(buildRowHTML(rowIdx));
    rowIdx++;
}

function initBatchModal() {
    // 打开模态框时，如果表格为空则添加 5 行
    $('#batchModal').on('shown.bs.modal', function () {
        if ($('#batchBody tr').length === 0) {
            for (var i = 0; i < 5; i++) addBatchRow();
        }
    });
    // 添加行
    $('#btnAddRow').on('click', function () {
        addBatchRow();
    });
    // 删除行（事件委托）
    $('#batchBody').on('click', '.btn-del-row', function () {
        $(this).closest('tr').remove();
        // 重新编号
        $('#batchBody tr').each(function (i) {
            $(this).find('td:first').text(i + 1);
        });
    });
    // 批量提交
    $('#btnBatchSubmit').on('click', function () {
        var rows = $('#batchBody tr');
        if (rows.length === 0) {
            alert('请至少添加一条记录');
            return;
        }
        // 检查至少有一行教师被选择
        var hasValid = false;
        rows.each(function () {
            if ($(this).find('[name=row_teacher_uid]').val()) hasValid = true;
        });
        if (!hasValid) {
            alert('请至少选择一位教师');
            return;
        }
        if (!$('#batchDate').val()) {
            alert('请选择查课日期');
            return;
        }
        $('#batchForm').submit();
    });
}

/* ========== 初始化 ========== */

$(function () {
    // 读取模板中的配置
    var raw = $('#inspectionData').text();
    try { config = JSON.parse(raw); } catch (e) { config = { statsUrl: '', teachers: [], grades: [], results: [] }; }

    // 加载图表
    loadStats();

    // 年份切换
    $('#chartYear').on('change', function () {
        loadStats($(this).val() || '');
    });

    // 图表区域展开时 resize（ECharts 需要重新计算尺寸）
    $('#chartArea').on('shown.bs.collapse', function () {
        if (charts.pie) charts.pie.resize();
        if (charts.line) charts.line.resize();
    });

    // 窗口 resize
    $(window).on('resize', function () {
        if (charts.pie) charts.pie.resize();
        if (charts.line) charts.line.resize();
    });

    // 批量录入
    initBatchModal();
});

})();

/* ========== 查课 ↔ 实时课表联动（v1.16.0 增量，纯原生 JS，不依赖 jQuery 加载顺序） ========== */
window.StuLinkInspection = window.StuLinkInspection || {};
/**
 * 跳转到实时课表页并定位到指定日期/节次/年级。
 * @param {string} baseUrl  实时课表基础 URL（由模板注入 data-live-url）
 * @param {object} p        { date: 'YYYY-MM-DD', period: number, grade: string }
 */
window.StuLinkInspection.goLiveSchedule = function (baseUrl, p) {
    if (!baseUrl) { return; }
    p = p || {};
    var qs = [];
    if (p.date) { qs.push('date=' + encodeURIComponent(p.date)); }
    if (p.period) { qs.push('period=' + encodeURIComponent(p.period)); }
    if (p.grade) { qs.push('grade=' + encodeURIComponent(p.grade)); }
    window.location.href = baseUrl + (qs.length ? ('?' + qs.join('&')) : '');
};
