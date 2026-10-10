/* StuLink 查课统计：ECharts 图表 + 批量录入动态行
 *
 * 页面数据来源：模板 inspection.html 里的
 *     <script id="inspectionData" type="application/json"> … </script>
 * 该 JSON 的字段形状（2026-10-10 起）：
 *     statsUrl : string
 *     teachers : Array<[uid: string, name: string]>   ← 注意是「二元组数组」，不是 [{uid, name}]
 *     grades   : string[]
 *     results  : Array<{key: string, label: string}>
 * 为什么 teachers 用二元组：纯粹为压缩 JSON 体积（省去每项 ~14 字节的 "uid"/"name" 键名，
 * 48 位教师约省 0.7KB）；读取处见 buildRowHTML()：t[0] → uid，t[1] → name。
 * 若将来改回对象数组，请同步修改此处、buildRowHTML() 以及模板 inspection.html 的生成逻辑。
 */
(function () {
'use strict';

var charts = { pie: null, line: null, cls: null, period: null };
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
        // 2026-10-09 新增：班级 / 节次 / 检查人 / 覆盖率
        renderClassChart(res.by_class || []);
        renderPeriodChart(res.by_period || []);
        renderInspectorTable(res.by_inspector || []);
        renderCoverage(res.coverage || {});
        var s = res.summary || {};
        $('#statTotal').text(s.total || 0);
        $('#statNormalRate').text((s.normal_rate || 0) + '%');
        // 填充年份选择器（从月度趋势中提取年份）
        fillYearOptions(res.monthly_trend || []);
    }).fail(function () {
        $('#statTotal').text('-');
        $('#statNormalRate').text('-');
        $('#statTodayChecked').text('-');
        $('#statTodayExpected').text('-');
        $('#statTodayRate').text('-');
        $('#statMonthRate').text('-');
        $('#tableInspector').text('统计加载失败');
    });
}

/* 覆盖率 = 已标记 / 应查（应查 = 当天有课的格子数，按周课表估算） */
function rateOf(checked, expected) {
    return expected ? Math.floor((checked || 0) * 100 / expected) : 0;
}

function renderCoverage(cov) {
    $('#statTodayChecked').text(cov.today_checked || 0);
    $('#statTodayExpected').text(cov.today_expected || 0);
    $('#statTodayRate').text(rateOf(cov.today_checked, cov.today_expected) + '%');
    $('#statMonthRate').text(rateOf(cov.month_checked, cov.month_expected) + '%');
}

/* 异常最多的班级（横向条形，第一名在最上面） */
function renderClassChart(rows) {
    var el = document.getElementById('chartClass');
    if (!el || typeof echarts === 'undefined') return;
    if (!charts.cls) charts.cls = echarts.init(el);
    var data = rows.slice().reverse();
    charts.cls.setOption({
        tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
        grid: { left: 96, right: 34, top: 8, bottom: 20 },
        xAxis: { type: 'value', minInterval: 1, axisLabel: { fontSize: 10 } },
        yAxis: {
            type: 'category', axisLabel: { fontSize: 10 },
            data: data.map(function (d) { return d.label || ''; })
        },
        series: [{
            type: 'bar', barMaxWidth: 14, itemStyle: { color: '#ef4444' },
            label: { show: true, position: 'right', fontSize: 10 },
            data: data.map(function (d) { return d.count || 0; })
        }]
    });
}

/* 各节次已查 / 异常（堆叠柱） */
function renderPeriodChart(rows) {
    var el = document.getElementById('chartPeriod');
    if (!el || typeof echarts === 'undefined') return;
    if (!charts.period) charts.period = echarts.init(el);
    var names = rows.map(function (d) { return '第' + d.period + '节'; });
    var normal = rows.map(function (d) { return Math.max(0, (d.count || 0) - (d.abnormal || 0)); });
    var bad = rows.map(function (d) { return d.abnormal || 0; });
    charts.period.setOption({
        tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
        legend: { data: ['正常', '异常'], top: 0, textStyle: { fontSize: 11 } },
        grid: { left: 36, right: 12, top: 30, bottom: 24 },
        xAxis: { type: 'category', data: names, axisLabel: { fontSize: 10 } },
        yAxis: { type: 'value', minInterval: 1, axisLabel: { fontSize: 10 } },
        series: [
            { name: '正常', type: 'bar', stack: 'p', itemStyle: { color: '#10b981' }, data: normal },
            { name: '异常', type: 'bar', stack: 'p', itemStyle: { color: '#ef4444' }, data: bad }
        ]
    });
}

/* 检查人工作量（谁在查、查出多少异常） */
function renderInspectorTable(rows) {
    var $box = $('#tableInspector');
    if (!rows.length) { $box.html('<div class="text-muted">暂无数据</div>'); return; }
    var max = rows[0].count || 1;
    var html = rows.map(function (r) {
        var pct = Math.max(4, Math.round((r.count || 0) * 100 / max));
        return '<div class="d-flex align-items-center gap-2 mb-1">'
            + '<div style="width:64px" class="text-truncate">' + esc(r.name) + '</div>'
            + '<div class="progress flex-fill" style="height:12px">'
            + '<div class="progress-bar" style="width:' + pct + '%"></div></div>'
            + '<div style="width:96px" class="text-end text-muted">'
            + (r.count || 0) + ' 次 · 异常 ' + (r.abnormal || 0) + '</div></div>';
    }).join('');
    $box.html(html);
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
    // 2026-10-10：模板里的教师表已改为紧凑二元组 [uid, name]，此处同步读取
    config.teachers.forEach(function (t) {
        var uid = t[0], name = t[1];
        teacherOpts += '<option value="' + esc(uid) + '">' + esc(name) + '（' + esc(uid) + '）</option>';
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

/* ========== 查课记录「删除」（2026-10-10 性能优化） ==========
 * 原模板每条记录一个 <form method="POST"> + csrf_token()（30 行 = 30 表单 / 30 份 token）。
 * 现改为：列表按钮 data-url + 页面底部单一隐藏表单 #inspDeleteForm 统一提交。
 * confirm 文案、POST 方法、CSRF 校验、服务端权限判断均保持不变。 */
(function () {
    function bindDelete() {
        var form = document.getElementById('inspDeleteForm');
        if (!form) { return; }
        document.addEventListener('click', function (ev) {
            var btn = ev.target && ev.target.closest ? ev.target.closest('.js-insp-del') : null;
            if (!btn) { return; }
            if (!confirm('确定删除这条查课记录？')) { return; }
            form.action = btn.dataset.url;
            form.submit();
        });
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bindDelete);
    } else {
        bindDelete();
    }
})();
