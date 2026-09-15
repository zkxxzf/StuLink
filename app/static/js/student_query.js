/* StuLink 个人成绩查询与分析前端 v1.10.0
 * 表/图渲染复用 grades.js 的同一套 CSS 类与 ECharts 配置生成器，保证风格一致
 */
(function () {
var PALETTE = ['#3b82f6', '#8b5cf6', '#10b981', '#f59e0b', '#ef4444',
               '#06b6d4', '#ec4899', '#84cc16', '#f97316', '#6366f1'];
var state = {student_no: '', examId: '', certExams: [], certRepIds: [], certChosen: []};
var charts = {};
var opts = null;

function fmt(v) {
    if (v === null || v === undefined || v === '') return '—';
    return String(v);
}
function escHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
        return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
    });
}

/* ---------- 表格 / 图表渲染（与 grades.js 一致） ---------- */
function renderTables(area, tables) {
    var wrap = $('<div>');
    Object.keys(tables).forEach(function (key) {
        var t = tables[key];
        var card = $('<div class="card tbl-card mb-3">');
        var head = $('<div class="card-header d-flex justify-content-between align-items-center">')
            .append('<h6 class="mb-0"><i class="bi bi-table me-1"></i>' + escHtml(t.title || key) + '</h6>')
            .append('<button class="btn btn-sm btn-outline-secondary tbl-fold">折叠</button>');
        card.append(head);
        var body = $('<div class="card-body p-0 tbl-wrap">');
        var table = $('<table class="table table-hover g-table mb-0">');
        var thead = $('<thead><tr></tr></thead>');
        (t.columns || []).forEach(function (c) {
            thead.find('tr').append('<th' + (c.type !== 'text' ? ' class="num"' : '') + '>' + c.label + '</th>');
        });
        table.append(thead);
        var tbody = $('<tbody>');
        (t.rows || []).forEach(function (r) {
            var tr = $('<tr>');
            (t.columns || []).forEach(function (c) {
                var val = r[c.key];
                var td = $('<td' + (c.type !== 'text' ? ' class="num"' : '') + '>').text(fmt(val));
                if (c.key === 'rank_move' || c.key === 'move' || c.key === 'diff') {
                    // 名次：正值=名次数字变大=后退（红）
                    var n = Number(val);
                    if (!isNaN(n)) {
                        td.addClass(n > 0 ? 'text-danger' : n < 0 ? 'text-success' : '');
                    }
                } else if (c.key === 'score_diff') {
                    // 分数：正值=提高（绿）
                    var sd = Number(val);
                    if (!isNaN(sd)) {
                        td.addClass(sd > 0 ? 'text-success' : sd < 0 ? 'text-danger' : '');
                    }
                }
                tr.append(td);
            });
            tbody.append(tr);
        });
        table.append(tbody);
        body.append(table);
        card.append(body);
        head.find('.tbl-fold').on('click', function () {
            body.toggle();
            $(this).text(body.is(':visible') ? '折叠' : '展开');
        });
        wrap.append(card);
    });
    area.append(wrap);
}

function renderCharts(area, chartsData) {
    var wrap = $('<div class="row g-3">');
    Object.keys(chartsData).forEach(function (key, idx) {
        var c = chartsData[key];
        var col = $('<div class="col-xl-6">');
        var card = $('<div class="card chart-card mb-0">');
        card.append('<div class="card-header py-2"><h6 class="mb-0 small"><i class="bi bi-bar-chart me-1"></i>'
            + (c.title || key) + '</h6></div>');
        var box = $('<div class="chart-box' + (c.type === 'heatmap' ? ' short' : '') + '"></div>');
        card.append('<div class="card-body p-2">').append(box);
        col.append(card);
        wrap.append(col);
        setTimeout(function () { renderChart(box[0], key, c); }, 30 * idx);
    });
    area.append(wrap);
}

function renderChart(dom, key, c) {
    var chart = echarts.init(dom);
    charts[key] = chart;
    var option;
    switch (c.type) {
        case 'bar': option = barOption(c, false); break;
        case 'groupbar': option = barOption(c, true); break;
        case 'stack': option = stackOption(c); break;
        case 'boxplot': option = boxOption(c); break;
        case 'line': option = lineOption(c); break;
        case 'dual': option = dualOption(c); break;
        case 'radar': option = radarOption(c); break;
        case 'heatmap': option = heatOption(c); break;
        default: option = {};
    }
    chart.setOption(option, true);
}

function baseGrid() { return {left: 60, right: 40, top: 50, bottom: 40, containLabel: true}; }
function colorOf(i) { return PALETTE[i % PALETTE.length]; }
function yFmt() { return {type: 'value', axisLabel: {formatter: '{value}'}}; }

function barOption(c, grouped) {
    var series = (c.series || []).map(function (s, i) {
        var item = {name: s.name, type: 'bar', data: s.data, itemStyle: {color: colorOf(i)}};
        if (!grouped && i === 0) {
            var ml = [];
            if (c.markLine != null) {
                ml.push({yAxis: c.markLine, lineStyle: {color: '#f59e0b', type: 'dashed'},
                    label: {formatter: '该生 {c}'}});
            }
            (c.bandLines || []).forEach(function (b) {
                ml.push({yAxis: b.value, lineStyle: {color: '#ef4444', type: 'dashed'},
                    label: {formatter: b.name + ' {c}'}});
            });
            if (ml.length) { item.markLine = {silent: true, symbol: 'none', data: ml}; }
        }
        return item;
    });
    return {color: PALETTE, tooltip: {trigger: 'axis'}, legend: grouped ? {top: 0} : undefined,
        grid: baseGrid(), xAxis: {type: 'category', data: c.xAxis || [], axisLabel: {interval: 0}},
        yAxis: yFmt(), series: series};
}
function stackOption(c) {
    return {tooltip: {trigger: 'axis'}, legend: {top: 0, type: 'scroll'},
        grid: baseGrid(),
        xAxis: {type: 'category', data: c.xAxis || []},
        yAxis: yFmt(),
        series: (c.series || []).map(function (s, i) {
            return {name: s.name, type: 'bar', stack: 'total', data: s.data, itemStyle: {color: colorOf(i)}};
        })};
}
function boxOption(c) {
    return {tooltip: {trigger: 'item'}, grid: baseGrid(),
        xAxis: {type: 'category', data: c.xAxis || []},
        yAxis: yFmt(),
        series: (c.series || []).map(function (s) {
            return {name: s.name || '总分分布', type: 'boxplot', data: s.data || [], itemStyle: {color: '#3b82f6'}};
        })};
}
function lineOption(c) {
    var sel = c.selected || {};
    var selected = {};
    (c.series || []).forEach(function (s) { selected[s.name] = !!sel[s.name]; });
    // inverse=true：Y 轴反向（排名用，名次 1 显示在最上方）
    var yAxis = yFmt();
    if (c.inverse) { yAxis.inverse = true; }
    return {color: PALETTE, tooltip: {trigger: 'axis'},
        legend: {top: 0, type: 'scroll', selected: selected},
        grid: baseGrid(),
        xAxis: {type: 'category', data: c.xAxis || []},
        yAxis: yAxis,
        series: (c.series || []).map(function (s) {
            return {name: s.name, type: 'line', data: s.data, connectNulls: false, smooth: true, symbolSize: 5};
        })};
}
function dualOption(c) {
    return {color: PALETTE, tooltip: {trigger: 'axis'},
        legend: {top: 0},
        grid: baseGrid(),
        xAxis: {type: 'category', data: c.xAxis || []},
        yAxis: [{type: 'value', name: '均分'}, {type: 'value', name: '%', max: 100}],
        series: (c.series || []).map(function (s) {
            return {name: s.name, type: 'line', data: s.data, yAxisIndex: s.yAxis || 0, smooth: true, symbolSize: 5};
        })};
}
function radarOption(c) {
    return {color: PALETTE, tooltip: {},
        legend: {top: 0},
        radar: {indicator: (c.indicators || []), radius: '65%'},
        series: [{type: 'radar', data: (c.series || []).map(function (s) {
            return {name: s.name, value: s.data || []};
        })}]};
}
function heatOption(c) {
    var xs = c.xAxis || [], ys = c.yAxis || [], m = c.data || [];
    var cells = [], min = null, max = null;
    ys.forEach(function (y, i) {
        (m[i] || []).forEach(function (v, j) {
            if (v === null || v === undefined) return;
            cells.push([j, i, v]);
            if (min === null || v < min) min = v;
            if (max === null || v > max) max = v;
        });
    });
    if (min === max) max = min + 1;
    return {tooltip: {position: 'top',
                formatter: function (p) { return xs[p.value[0]] + ' · ' + ys[p.value[1]] + '<br/>平均分：' + p.value[2]; }},
        grid: {left: 60, right: 40, top: 40, bottom: 80},
        xAxis: {type: 'category', data: xs, axisLabel: {interval: 0, rotate: 30}},
        yAxis: {type: 'category', data: ys},
        visualMap: {min: Math.floor(min), max: Math.ceil(max), calculable: true,
            orient: 'horizontal', left: 'center', bottom: 0,
            inRange: {color: ['#eff6ff', '#93c5fd', '#3b82f6', '#1d4ed8']}},
        series: [{type: 'heatmap', data: cells, label: {show: true, fontSize: 10}}]};
}

/* ---------- 成绩认定矩阵（考试 × 科目，与纸质证明同版式） ---------- */
function renderCertMatrix(area, m) {
    if (!m || !m.cols || !m.cols.length) return;
    var card = $('<div class="card tbl-card mb-3">');
    card.append('<div class="card-header d-flex justify-content-between align-items-center flex-wrap">'
        + '<h6 class="mb-0"><i class="bi bi-grid-3x3 me-1"></i>成绩认定表（每次考试 × 各科满分）</h6>'
        + '<span class="text-muted small">未选＝不在选科内；未参加＝在选科内但无成绩</span></div>');
    var body = $('<div class="card-body p-0 tbl-wrap">');
    var table = $('<table class="table table-hover g-table mb-0">');
    var h = $('<tr>').append($('<th>').text('考试'));
    (m.cols || []).forEach(function (c) {
        h.append($('<th>').html(c.label + '<br><small class="text-muted">(满分' + c.full + ')</small>'));
    });
    table.append($('<thead>').append(h));

    var tbody = $('<tbody>');
    (m.rows || []).forEach(function (r) {
        var tr = $('<tr>').append($('<td class="fw-semibold">').text(r.exam_label));
        (m.cols || []).forEach(function (c) {
            var v = r[c.key];
            var td = $('<td>').text(v === null || v === undefined ? '–' : String(v));
            if (typeof v === 'string' && (v === '未选' || v === '未参加')) td.addClass('text-muted');
            tr.append(td);
        });
        tbody.append(tr);
    });
    table.append(tbody);
    body.append(table);
    card.append(body);
    area.append(card);
}

/* ---------- 页面逻辑 ---------- */
function init() {
    $.getJSON('/grades/api/student-query/options', function (res) {
        opts = res.data || {};
        fillSelect('#sqTerm', opts.terms);
        fillSelect('#sqType', opts.exam_types);
        fillSelect('#sqSubject', opts.subjects);
        $('#sqEmpty').removeClass('d-none');
    }).fail(function () { $('#sqEmpty').removeClass('d-none'); });

    bindSearch();
    $('#sqQuery').on('click', function () { state.certChosen = []; loadData(); });
    $('#sqExam').on('change', function () { state.examId = $(this).val(); loadData(); });
    $('#sqSubject').on('change', loadData);
    $('#sqCert').on('click', generateCert);
    $(window).on('resize', function () { Object.keys(charts).forEach(function (k) { charts[k].resize(); }); });

    // 从证明打印页「返回」时带 ?student_no=学号，自动定位并查询该学生，无需重新搜索
    autoOpenFromUrl();
}

// 读取 URL 上的 student_no 参数，精确匹配后自动选中并加载数据
function autoOpenFromUrl() {
    var no = new URLSearchParams(window.location.search).get('student_no');
    if (!no) return;
    $.getJSON('/grades/api/student-query/search?q=' + encodeURIComponent(no), function (res) {
        var hit = (res.data || []).filter(function (s) { return String(s.no) === String(no); })[0];
        if (!hit) { $('#sqEmpty').removeClass('d-none'); return; }
        state.student_no = hit.no;
        $('#sqStudent').val(hit.name + '（' + hit.no + '）');
        loadData();
    });
}

function fillSelect(sel, items) {
    var el = $(sel);
    items = items || [];
    items.forEach(function (v) { el.append('<option value="' + escHtml(v) + '">' + escHtml(v) + '</option>'); });
}

function bindSearch() {
    var timer = null;
    $('#sqStudent').on('input', function () {
        var q = $(this).val().trim();
        clearTimeout(timer);
        if (q.length < 1) { $('#sqStudentMenu').addClass('d-none').empty(); return; }
        timer = setTimeout(function () { doSearch(q); }, 250);
    });
    $('#sqSearchBtn').on('click', function () {
        var q = $('#sqStudent').val().trim();
        if (q) doSearch(q);
    });
    $(document).on('click', function (e) {
        if (!$(e.target).closest('#sqStudent, #sqStudentMenu').length) {
            $('#sqStudentMenu').addClass('d-none');
        }
    });
    $('#sqStudentMenu').on('click', 'li', function () {
        var no = $(this).data('no'), name = $(this).data('name');
        state.student_no = no;
        state.certChosen = [];   // 换学生时清空证明自选口径
        $('#sqStudent').val(name + '（' + no + '）');
        $('#sqStudentMenu').addClass('d-none').empty();
        loadData();
    });
}

function doSearch(q) {
    $.getJSON('/grades/api/student-query/search?q=' + encodeURIComponent(q), function (res) {
        var menu = $('#sqStudentMenu').empty();
        var list = res.data || [];
        if (!list.length) {
            menu.append('<li class="list-group-item text-muted small">无匹配学生（或超出您的权限范围）</li>');
        } else {
            list.forEach(function (s) {
                menu.append('<li class="list-group-item list-group-item-action" data-no="' + escHtml(s.no)
                    + '" data-name="' + escHtml(s.name) + '">' + escHtml(s.name)
                    + ' <span class="text-muted">(' + escHtml(s.no) + ' · ' + escHtml(s.grade)
                    + ' ' + escHtml(s.class_name) + ')</span></li>');
            });
        }
        menu.removeClass('d-none');
    }).fail(function () {
        $('#sqStudentMenu').removeClass('d-none').empty()
            .append('<li class="list-group-item text-danger small">搜索失败</li>');
    });
}

function buildQuery() {
    var qs = [];
    if (state.student_no) qs.push('student_no=' + encodeURIComponent(state.student_no));
    var term = $('#sqTerm').val(), type = $('#sqType').val();
    var from = $('#sqFrom').val(), to = $('#sqTo').val(), subj = $('#sqSubject').val();
    if (term) qs.push('term=' + encodeURIComponent(term));
    if (type) qs.push('exam_type=' + encodeURIComponent(type));
    if (from) qs.push('date_from=' + from);
    if (to) qs.push('date_to=' + to);
    if (subj) qs.push('subject=' + encodeURIComponent(subj));
    if (state.examId) qs.push('exam_id=' + state.examId);
    // v1.12.2 页面矩阵与证明保持同一自选口径
    if (state.certChosen && state.certChosen.length) {
        qs.push('cert_exam_ids=' + state.certChosen.join(','));
    }
    return qs.length ? '?' + qs.join('&') : '';
}

function loadData() {
    if (!state.student_no) { alert('请先选择学生'); return; }
    var area = $('#sqResult').empty();
    $('#sqEmpty').addClass('d-none');
    area.append('<div class="text-center text-muted py-4"><div class="spinner-border text-primary"></div><div>加载中…</div></div>');
    $.getJSON('/grades/api/student-query/data' + buildQuery(), function (res) {
        area.empty();
        if (!res.success) { showEmpty('查询失败：' + (res.message || '')); return; }
        var d = res.data || {};
        renderStudentInfo(d.student, d.meta);
        renderCertMatrix(area, d.cert_matrix);
        // v1.12.2 缓存考试清单与默认代表考试，供「生成成绩证明」选择框使用
        state.certExams = d.exams || [];
        state.certRepIds = (d.cert_matrix && d.cert_matrix.rep_ids) || [];
        // 单场选择器
        fillExamSelect(d.exams, d.selected_exam_id);
        if (d.meta && d.meta.empty) {
            showEmpty(d.meta.message || '暂无数据');
            return;
        }
        if (d.meta && d.meta.selected_exam) {
            area.append($('<div class="alert alert-light border py-2 small">').append(
                '<i class="bi bi-info-circle"></i> 当前单场展示：<strong>'
                + escHtml(d.meta.selected_exam) + '</strong>'
                + (d.meta.direction ? ' · 方向：' + escHtml(d.meta.direction) : '')
                + (d.meta.class_name ? ' · 班级：' + escHtml(d.meta.class_name) : '')));
        }
        renderTables(area, d.tables || {});
        renderCharts(area, d.charts || {});
    }).fail(function () {
        area.empty();
        showEmpty('数据加载失败（或无权查看该学生）');
    });
}

function renderStudentInfo(stu, meta) {
    if (!stu) { $('#sqStudentInfo').addClass('d-none'); return; }
    var html = '<i class="bi bi-person-badge"></i> 当前学生：<strong>' + escHtml(stu.name)
        + '</strong> · 学号 ' + escHtml(stu.no) + ' · ' + escHtml(stu.grade)
        + ' ' + escHtml(stu.class_name)
        + (stu.selection ? ' · 选科 ' + escHtml(stu.selection) : '');
    $('#sqStudentInfo').removeClass('d-none').html(html);
}

function fillExamSelect(exams, selId) {
    var el = $('#sqExam').empty().append('<option value="">指定单场（默认最新）</option>');
    (exams || []).forEach(function (e) {
        el.append('<option value="' + e.id + '">' + escHtml(e.name) + '（' + e.date + '）</option>');
    });
    if ((exams || []).length > 1) {
        el.removeClass('d-none');
        el.val(selId || '');
        state.examId = selId || '';
    } else {
        el.addClass('d-none');
    }
}

function showEmpty(msg) {
    $('#sqEmpty').removeClass('d-none').find('p').text(msg);
}

/* ---------- v1.12.2 成绩证明考试选择框 ----------
   按「学年-学期」分组列出该生全部考试，默认勾选每学期代表考试（优先期末），
   用户可自由增删：没有期中/期末时可选任意月考等代替，同一学期也可多选。 */
function openCertPicker(cb) {
    var exams = state.certExams || [];
    if (!exams.length) { alert('请先查询该学生成绩'); return; }
    var rep = {};
    (state.certRepIds || []).forEach(function (id) { rep[id] = true; });
    // 分组：学年 + 学期
    var groups = {};
    exams.forEach(function (e) {
        var k = (e.term || '未分学年') + '|' + (e.semester || '');
        (groups[k] = groups[k] || []).push(e);
    });
    var keys = Object.keys(groups).sort();
    var html = '<div class="modal fade" id="certPickModal" tabindex="-1"><div class="modal-dialog modal-lg modal-dialog-scrollable">'
        + '<div class="modal-content"><div class="modal-header py-2">'
        + '<h6 class="modal-title"><i class="bi bi-list-check"></i> 选择纳入成绩证明的考试</h6>'
        + '<button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>'
        + '<div class="modal-body py-2">'
        + '<div class="text-muted small mb-2">默认已勾选每学期代表考试（优先期末）。'
        + '若无严格期中/期末，可改选月考等任意考试；同一学期可勾选多场（各占一列）。</div>'
        + '<div class="d-flex gap-2 mb-2">'
        + '<button class="btn btn-sm btn-outline-secondary" id="cpDefault">恢复默认</button>'
        + '<button class="btn btn-sm btn-outline-secondary" id="cpAll">全选</button>'
        + '<button class="btn btn-sm btn-outline-secondary" id="cpNone">清空</button></div>';
    keys.forEach(function (k) {
        var parts = k.split('|');
        html += '<div class="fw-semibold small mt-2 mb-1">' + escHtml(parts[0]) + ' ' + escHtml(parts[1]) + '</div>';
        groups[k].forEach(function (e) {
            html += '<label class="form-check small d-flex align-items-center gap-2">'
                + '<input type="checkbox" class="form-check-input cp-box" value="' + e.id + '"'
                + (rep[e.id] ? ' checked' : '') + '>'
                + '<span>' + escHtml(e.name) + '</span>'
                + '<span class="text-muted">' + e.date + (e.type ? ' · ' + escHtml(e.type) : '') + '</span></label>';
        });
    });
    // v1.12.3 证明底部说明可自行编辑，默认预填系统标准文案
    html += '<div class="mt-3"><label class="form-label small fw-semibold mb-1" for="cpNote">证明说明（打印在证明底部，可自行修改）</label>'
        + '<textarea class="form-control form-control-sm" id="cpNote" rows="3" maxlength="500">'
        + escHtml((opts && opts.cert_note_default) || '') + '</textarea></div>';
    html += '</div><div class="modal-footer py-1">'
        + '<span class="me-auto text-muted small" id="cpCount"></span>'
        + '<button type="button" class="btn btn-sm btn-secondary" data-bs-dismiss="modal">取消</button>'
        + '<button type="button" class="btn btn-sm btn-primary" id="cpOk">生成成绩证明</button>'
        + '</div></div></div></div>';
    var $m = $(html);
    $('#certPickModal').remove();
    $('body').append($m);
    function count() { $('#cpCount').text('已选 ' + $m.find('.cp-box:checked').length + ' 场'); }
    $m.find('.cp-box').on('change', count);
    $m.find('#cpDefault').on('click', function () {
        $m.find('.cp-box').each(function () { this.checked = !!rep[this.value]; }); count();
    });
    $m.find('#cpAll').on('click', function () { $m.find('.cp-box').prop('checked', true); count(); });
    $m.find('#cpNone').on('click', function () { $m.find('.cp-box').prop('checked', false); count(); });
    $m.find('#cpOk').on('click', function () {
        var ids = $m.find('.cp-box:checked').map(function () { return +this.value; }).get();
        if (!ids.length) { alert('请至少勾选一场考试'); return; }
        var note = $m.find('#cpNote').val();
        state.certChosen = ids;   // 生成后页面矩阵按同一口径刷新
        var bs = bootstrap.Modal.getInstance($m[0]) || new bootstrap.Modal($m[0]);
        bs.hide();
        $m.on('hidden.bs.modal', function () { $m.remove(); });
        cb(ids, note);
    });
    count();
    new bootstrap.Modal($m[0]).show();
}

function generateCert() {
    if (!state.student_no) { alert('请先选择学生'); return; }
    openCertPicker(function (ids, note) { doGenerateCert(ids, note); });
}

function doGenerateCert(certExamIds, certNote) {
    var payload = {
        student_no: String(state.student_no || ''),
        term: $('#sqTerm').val(), exam_type: $('#sqType').val(),
        date_from: $('#sqFrom').val(), date_to: $('#sqTo').val(),
        subject: $('#sqSubject').val(), exam_id: state.examId,
        cert_exam_ids: certExamIds || [],
        cert_note: certNote || '',
    };
    $('#sqCert').prop('disabled', true).html('<span class="spinner-border spinner-border-sm me-1"></span>生成中…');
    $.ajax({
        url: '/grades/api/student-query/cert',
        method: 'POST',
        contentType: 'application/json',
        data: JSON.stringify(payload)
    }).done(function (res) {
        $('#sqCert').prop('disabled', false).html('<i class="bi bi-file-earmark-check"></i> 生成成绩证明');
        if (!res.success) { alert(res.message || '生成失败'); return; }
        var d = res.data || {};
        // 当前标签页直接进入打印页（不再新开标签）；打印页「返回成绩查询」会带学号自动恢复查询
        if (d.print_url) {
            alert('成绩证明已生成，防伪码：' + d.code + '\n即将进入打印页，可打印/另存 PDF。');
            window.location.href = d.print_url;
        }
        loadData();   // 刷新页面矩阵为所选考试口径，与证明一致（跳转前执行，返回时页面已是最新）
    }).fail(function (x) {
        $('#sqCert').prop('disabled', false).html('<i class="bi bi-file-earmark-check"></i> 生成成绩证明');
        alert(x.responseJSON && x.responseJSON.message || '生成失败');
    });
}

$(function () { init(); });
})();
