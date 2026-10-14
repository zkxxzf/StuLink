/* StuLink 查课：ECharts 图表 + 批量录入动态行
 *
 * 使用页面（2026-10-13 起）：
 *     · 查课统计页 inspection.html  —— 只做图表统计（statsUrl 有值）
 *     · 查课录入页 inspection_entry.html —— 只做批量录入（statsUrl 为空，
 *       loadStats() 直接返回；批量表格直接铺在「批量录入」页签里）
 *
 * 页面数据来源：模板里的
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
    if (!url) { return; }   // 独立查课录入页没有统计区域（statsUrl 为空）
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

/* 2026-10-14 审计修复：查课状态色改读主题变量（theme.css --sl-chart-*），
   深色下提亮一档（如 #ef4444 → #f87171），色相不变 → 红/黄/绿语义不丢。
   取不到时回落到原字面量。 */
function sem() {
    return (window.stulinkSemantic && window.stulinkSemantic()) ||
        {ok: '#10b981', warn: '#f59e0b', danger: '#ef4444', info: '#3b82f6',
         purple: '#6366f1', cyan: '#0ea5e9', muted: '#94a3b8'};
}
/* 各图表的最后一次数据：主题切换后据此重绘（ECharts 颜色是 JS 字面量，CSS 够不到） */
var _last = {};
function repaintCharts() {
    try {
        if (_last.cls) renderClassChart(_last.cls);
        if (_last.period) renderPeriodChart(_last.period);
        if (_last.pie) renderPie(_last.pie);
        if (_last.trend) renderLine(_last.trend);
    } catch (e) { /* 重绘失败不影响页面使用（刷新即可） */ }
}
if (window.stuOnThemeChange) { window.stuOnThemeChange(repaintCharts); }

/* 异常最多的班级（横向条形，第一名在最上面） */
function renderClassChart(rows) {
    var el = document.getElementById('chartClass');
    if (!el || typeof echarts === 'undefined') return;
    _last.cls = rows;
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
            type: 'bar', barMaxWidth: 14, itemStyle: { color: sem().danger },
            label: { show: true, position: 'right', fontSize: 10 },
            data: data.map(function (d) { return d.count || 0; })
        }]
    });
}

/* 各节次已查 / 异常（堆叠柱） */
function renderPeriodChart(rows) {
    var el = document.getElementById('chartPeriod');
    if (!el || typeof echarts === 'undefined') return;
    _last.period = rows;
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
            { name: '正常', type: 'bar', stack: 'p', itemStyle: { color: sem().ok }, data: normal },
            { name: '异常', type: 'bar', stack: 'p', itemStyle: { color: sem().danger }, data: bad }
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
    _last.pie = data;
    // 2026-10-13：'缺课'统一改为'缺勤'，新增'考试'（与 models/academic.py 的 INSPECTION_RESULTS 同步）
    // 2026-10-14：色值改读主题变量（深色下提亮一档，色相不变）
    var c = sem();
    var colorMap = { '正常': c.ok, '迟到': c.warn, '缺勤': c.danger, '调课': c.purple,
                     '考试': c.cyan, '其他': c.muted };
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
    _last.trend = trend;
    var c = sem();
    var months = trend.map(function (d) { return d.month; });
    charts.line.setOption({
        tooltip: { trigger: 'axis' },
        legend: { data: ['正常', '迟到', '缺勤', '调课', '考试'], top: 0, textStyle: { fontSize: 11 } },
        grid: { left: 45, right: 15, top: 35, bottom: 30 },
        xAxis: { type: 'category', data: months, axisLabel: { fontSize: 10, rotate: months.length > 8 ? 30 : 0 } },
        yAxis: { type: 'value', axisLabel: { fontSize: 11 }, minInterval: 1 },
        series: [
            { name: '正常', type: 'line', data: trend.map(function (d) { return d.normal; }),
              smooth: true, lineStyle: { color: c.ok, width: 2 }, itemStyle: { color: c.ok },
              /* 原 rgba(16,185,129,.08) 是写死的浅绿，深色下完全看不见；
                 改用「主题色 + opacity」，两套主题都随底色自动调整。 */
              areaStyle: { color: c.ok, opacity: 0.12 } },
            { name: '迟到', type: 'line', data: trend.map(function (d) { return d.late; }),
              smooth: true, lineStyle: { color: c.warn, width: 2 }, itemStyle: { color: c.warn } },
            { name: '缺勤', type: 'line', data: trend.map(function (d) { return d.absent; }),
              smooth: true, lineStyle: { color: c.danger, width: 2 }, itemStyle: { color: c.danger } },
            { name: '调课', type: 'line', data: trend.map(function (d) { return d.swap; }),
              smooth: true, lineStyle: { color: c.purple, width: 2 }, itemStyle: { color: c.purple } },
            { name: '考试', type: 'line', data: trend.map(function (d) { return d.exam; }),
              smooth: true, lineStyle: { color: c.cyan, width: 2 }, itemStyle: { color: c.cyan } }
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
    // 2026-10-14：节次与单条同口径 —— 下拉（本学期作息节次定义）；无定义时回落数字输入
    var periodCell;
    if (config.periods && config.periods.length) {
        var periodOpts = '<option value="">节次</option>';
        config.periods.forEach(function (p) {
            periodOpts += '<option value="' + p.number + '">' + esc(p.name) + '</option>';
        });
        periodCell = '<select name="row_period" class="form-select form-select-sm">'
            + periodOpts + '</select>';
    } else {
        periodCell = '<input name="row_period" type="number" class="form-control form-control-sm"'
            + ' min="1" max="12" placeholder="节次">';
    }
    return '<tr data-row="' + idx + '">'
        + '<td class="text-muted small text-center">' + (idx + 1) + '</td>'
        + '<td><select name="row_grade" class="form-select form-select-sm" required>' + gradeOpts + '</select></td>'
        + '<td><select name="row_teacher_uid" class="form-select form-select-sm" required>' + teacherOpts + '</select></td>'
        + '<td><input name="row_class_name" class="form-control form-control-sm" maxlength="10" placeholder="班级"></td>'
        + '<td><input name="row_subject" class="form-control form-control-sm" maxlength="20" placeholder="科目"></td>'
        + '<td>' + periodCell + '</td>'
        + '<td><select name="row_result" class="form-select form-select-sm">' + resultOpts + '</select></td>'
        + '<td><input name="row_note" class="form-control form-control-sm" maxlength="200" placeholder="备注"></td>'
        + '<td class="text-center"><button type="button" class="btn btn-sm btn-outline-danger btn-del-row py-0 px-1" title="删除此行"><i class="bi bi-x"></i></button></td>'
        + '</tr>';
}

function addBatchRow() {
    $('#batchBody').append(buildRowHTML(rowIdx));
    rowIdx++;
}

/* 2026-10-13：批量录入随「查课录入」独立成页（原为统计页的模态框）——
   表格直接铺在录入页的「批量录入」页签里，进入页面即铺好 5 行空行。 */
function initBatchEntry() {
    if ($('#batchBody tr').length === 0) {
        for (var i = 0; i < 5; i++) addBatchRow();
    }
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

/* ========== 查课录入：按教师自动带出班级 / 科目（2026-10-14 用户口径） ==========
 *
 * 需求：录入查课时选中教师后**自动追踪**出班级与科目，仍可手动修改；
 *      批量录入的每一行同样生效。
 * 数据：GET config.lookupUrl?date=&teacher_uid= →
 *      {courses:[{period, grade, class_name, subject, is_temp_swap, …}]}
 *      服务端口径见 app/modules/academic/services/inspection_lookup.py
 *      （常规周课表 + 当天临时调课 / 代课叠加，与查课核对页同源）。
 * 节次：录入页的节次是**下拉**（选项=本学期作息里的节次定义），显示一律用节次名
 *      （period_number 只是行号：1=早读、2=第1节，手填数字极易错位 —— 见
 *      app/utils/period_label.py 的同一口径；无节次定义时才回落「第N节」）。
 * 交互：
 *      · 触发字段：日期 / 节次 / 教师任一变化 → 重新反查；
 *      · 节次有值 → 取该节次的课（无匹配时列出该教师当天的其他课，可点选）；
 *      · 节次空且当天只有 1 节 → 连节次一起带出；多节 → 列出可点选的课次；
 *      · 每次触发都会覆盖班级 / 科目（这是"自动追踪"的语义），改完即止。
 */
var LOOKUP_DEFAULT_HINT = '选择教师后自动带出班级 / 科目，仍可修改';
var courseCache = {};     // 'date|uid' → courses（同一会话内避免重复请求）
var coursePending = {};   // 'date|uid' → 进行中的请求（同一教师的连续触发只发一次）
var lastCourses = [];     // 最近一次单条反查结果（供候选点选）
var singleLookupKey = ''; // 最新单条触发键 date|uid|period（同键不重复查 + 过期结果丢弃）

/* 节次显示名（与后端 app/utils/period_label.py 同口径：优先作息里的节次名） */
function periodLabel(n) {
    if (n === '' || n === null || typeof n === 'undefined') { return ''; }
    var list = (config && config.periods) || [];
    for (var i = 0; i < list.length; i++) {
        if (String(list[i].number) === String(n)) { return list[i].name; }
    }
    return '第' + n + '节';
}

/* 写入节次控件：录入页是下拉（教材外的节次不硬塞），无节次定义时是数字输入 */
function setPeriodValue($el, n) {
    if (!$el.length || n === '' || n === null || typeof n === 'undefined') { return; }
    if ($el.is('select')) {
        var v = String(n);
        var has = $el.find('option').filter(function () { return this.value === v; }).length > 0;
        if (has) { $el.val(v); }
    } else {
        $el.val(n);
    }
}

function coursesOf(dateStr, uid) {
    var key = dateStr + '|' + uid;
    if (Object.prototype.hasOwnProperty.call(courseCache, key)) {
        return $.Deferred().resolve(courseCache[key]).promise();
    }
    if (coursePending[key]) { return coursePending[key]; }   // 进行中的复用，不重复请求
    var req = $.getJSON(config.lookupUrl, { date: dateStr, teacher_uid: uid })
        .then(function (res) {
            var list = (res && res.courses) || [];
            courseCache[key] = list;      // 只缓存成功结果（失败不缓存，下次可重试）
            delete coursePending[key];
            return list;
        }, function () {
            delete coursePending[key];
            return [];                    // 接口失败按"无课"处理，不打断录入
        });
    coursePending[key] = req;
    return req;
}

/* 匹配规则：节次有值取该节次；否则当天唯一一节课可直接带出 */
function pickCourse(list, period) {
    if (!list || !list.length) { return null; }
    if (period) {
        var hit = $.grep(list, function (c) { return String(c.period) === String(period); });
        return hit.length ? hit[0] : null;
    }
    return list.length === 1 ? list[0] : null;
}

function setGradeSelect($sel, grade) {
    if (!grade || !$sel.length) { return; }
    var has = $sel.find('option').filter(function () { return this.value === grade; }).length > 0;
    if (has) { $sel.val(grade); }         // 课表年级不在下拉里（如已停用）就不硬塞
}

function courseText(c) {
    var parts = [periodLabel(c.period), (c.grade || '') + (c.class_name || ''), c.subject || ''];
    var text = $.grep(parts, function (s) { return !!s; }).join(' ');
    return text + (c.is_temp_swap ? '（调课）' : '');
}

/* 当天课次的可点选候选（点一下即填入节次 / 年级 / 班级 / 科目） */
function pickLinks(list) {
    return list.map(function (c, i) {
        return '<a href="javascript:;" class="lookup-pick text-decoration-none"'
            + ' data-i="' + i + '">' + esc(courseText(c)) + '</a>';
    }).join(' <span class="text-muted">/</span> ');
}

/* 把一节课填入单条表单 */
function applySingleCourse(c) {
    setPeriodValue($('#singlePeriod'), c.period);
    setGradeSelect($('#singleGrade'), c.grade);
    $('#singleClass').val(c.class_name || '');
    $('#singleSubject').val(c.subject || '');
    $('#singleLookupHint').html('已自动带出：' + esc(courseText(c)) + '（可修改）');
}

function autofillSingle() {
    if (!config || !config.lookupUrl || !$('#singleLookupHint').length) { return; }
    var $hint = $('#singleLookupHint');
    var uid = $('#singleTeacher').val() || '';
    var dateStr = $('#singleDate').val() || '';
    var period = $('#singlePeriod').val() || '';
    if (!uid || !dateStr) { $hint.text(LOOKUP_DEFAULT_HINT); return; }
    var key = dateStr + '|' + uid + '|' + period;
    if (key === singleLookupKey) { return; }
    singleLookupKey = key;
    coursesOf(dateStr, uid).then(function (list) {
        if (key !== singleLookupKey) { return; }   // 期间触发源又变了 → 丢弃过期结果
        lastCourses = list;
        if (!list.length) {
            $hint.html('<i class="bi bi-info-circle"></i> ' + esc(dateStr)
                + ' 课表里没有该教师的课（可手动填写）');
            return;
        }
        var c = pickCourse(list, period);
        if (c) { applySingleCourse(c); return; }
        if (period) {                 // 指定节次没课 → 不覆盖手填内容，改列出当天其他课
            $hint.html('<i class="bi bi-info-circle"></i> ' + esc(periodLabel(period))
                + ' 没有该教师的课；当天有：' + pickLinks(list) + '（点击可填入）');
            return;
        }
        $hint.html('当天有 ' + list.length + ' 节课，点选自动填入：' + pickLinks(list));
    });
}

function autofillBatchRow($row) {
    if (!config || !config.lookupUrl || !$row.length) { return; }
    var uid = $row.find('[name=row_teacher_uid]').val() || '';
    var dateStr = $('#batchDate').val() || '';
    var period = $row.find('[name=row_period]').val() || '';
    if (!uid || !dateStr) { return; }
    var key = dateStr + '|' + uid + '|' + period;
    if ($row.data('lookupKey') === key) { return; }
    $row.data('lookupKey', key);
    var $class = $row.find('[name=row_class_name]');
    coursesOf(dateStr, uid).then(function (list) {
        if ($row.data('lookupKey') !== key) { return; }   // 期间该行又变了 → 丢弃过期结果
        if (!list.length) {
            $class.attr('title', '课表里没有该教师当天的课，请手动填写');
            return;
        }
        var c = pickCourse(list, period);
        if (!c) {
            $class.attr('title', '该教师当天有 ' + list.length + ' 节课，请先选节次');
            return;
        }
        setPeriodValue($row.find('[name=row_period]'), c.period);
        setGradeSelect($row.find('[name=row_grade]'), c.grade);
        $class.val(c.class_name || '');
        $row.find('[name=row_subject]').val(c.subject || '');
        $class.attr('title', '已按课表带出' + (c.is_temp_swap ? '（当天调课）' : '') + '，可修改');
    });
}

function initEntryAutofill() {
    if (!config || !config.lookupUrl) { return; }   // 统计页不启用
    // 单条录入：教师 / 日期 / 节次 任一变化都重新带出
    $('#singleTeacher, #singleDate, #singlePeriod').on('change', autofillSingle);
    // 点选候选课次（未选节次但当天多节课 / 所选节次没课时）
    $(document).on('click', '.lookup-pick', function () {
        var c = lastCourses[parseInt($(this).attr('data-i'), 10)];
        if (c) { applySingleCourse(c); }
    });
    // 批量录入：每行教师 / 节次变化；日期变化时整表重算
    $('#batchBody').on('change', 'select[name=row_teacher_uid], [name=row_period]',
        function () { autofillBatchRow($(this).closest('tr')); });
    $('#batchDate').on('change', function () {
        $('#batchBody tr').each(function () { autofillBatchRow($(this)); });
    });
    // 浏览器回填了教师（刷新/后退）时也带一次
    autofillSingle();
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

    // 批量录入（独立查课录入页；统计页已不再承载录入）
    initBatchEntry();

    // 2026-10-14：录入页按教师自动带出班级 / 科目（统计页无 lookupUrl，自动跳过）
    initEntryAutofill();
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
