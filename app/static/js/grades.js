/* StuLink 成绩管理：分析页前端 v1.9.0
 * 四 tab 联动加载 / 统一表格渲染 / ECharts 图表渲染（无文字报告）
 */
(function(){
var PALETTE = ['#3b82f6', '#8b5cf6', '#10b981', '#f59e0b', '#ef4444',
               '#06b6d4', '#ec4899', '#84cc16', '#f97316', '#6366f1'];
var state = {grade: '', examId: '', tab: 'grade', class_name: '', subject: '', direction: ''};
// tab 业务顺序。注意：Flask jsonify 默认按键名排序，Object.keys(opts.tabs) 的首项会是
// 'class' 而不是 'grade'，因此不能用它决定默认 tab，必须按此固定顺序取第一个可见项。
var TAB_ORDER = ['grade', 'class', 'subject', 'teacher'];
var opts = null;
var charts = {};

// 注意：不要在此处把 $ 重定义为普通函数（如 function $(s){ return jQuery(s); }）。
// 一旦覆盖，$.getJSON / $.post / $.ajax 等 jQuery 静态方法全部不可用，
// 会导致页面初始化第一行就抛 TypeError、年级/考试下拉为空、AI 分析失效。
// 这里直接使用 base.html 已加载的全局 jQuery。

function fmt(v){
    if (v === null || v === undefined || v === '') return '—';
    return String(v);
}

/* ---------------- 初始化 ---------------- */
function init(){
    $.getJSON('/grades/api/options', function(res){
        opts = res.data;
        fillGrades();
        bindTabs();
        bindFilters();
        // 可见 tab
        Object.keys(opts.tabs).forEach(function(t){
            if (!opts.tabs[t]) $('li[data-tab="'+t+'"]').hide();
        });
        var first = TAB_ORDER.filter(function(t){ return opts.tabs[t]; })[0];
        if (!first){ showEmpty('grade', '您没有成绩查看权限'); return; }
        activateTab(first);
        window.addEventListener('resize', function(){ Object.keys(charts).forEach(function(k){ charts[k].resize(); }); });
    }).fail(function(){ showEmpty('grade', '加载失败，请刷新'); });
}

function fillGrades(){
    var sel = $('#gGrade').empty().append('<option value="">年级…</option>');
    opts.grades.forEach(function(g){ sel.append('<option value="'+g+'">'+g+'</option>'); });
    // 锁定年级
    if (opts.locked.grade){
        $('#gGrade').val(opts.locked.grade).prop('disabled', true);
    } else {
        // 未锁定时默认选中「第一个有考试的年级」，
        // 否则停在占位项上，考试下拉会是空的
        var first = (opts.grades || []).filter(function(g){
            return (opts.exams[g] || []).length > 0;
        })[0];
        if (first){ $('#gGrade').val(first); }
        else if ((opts.grades || []).length){ $('#gGrade').val(opts.grades[0]); }
    }
    fillExams();
}

function fillExams(){
    var grade = $('#gGrade').val() || state.grade;
    var list = (opts.exams[grade] || []).slice();
    var sel = $('#gExam').empty().append('<option value="">考试…</option>');
    // 按日期倒序（后端已排）默认选最近一场已导入
    list.forEach(function(e){
        var label = e.name + '（' + e.date + '）' + (e.status === 'imported' ? '' :
                     e.status === 'dirty' ? '·待重算' : '·未导入');
        sel.append('<option value="'+e.id+'" data-status="'+e.status+'">'+label+'</option>');
    });
    var pick = list.filter(function(e){ return e.status === 'imported'; })[0] || list[0];
    if (pick){ sel.val(String(pick.id)); }
    state.examId = sel.val() || '';
    state.grade = grade;
    fillClasses();
}

function fillClasses(){
    var grade = $('#gGrade').val() || state.grade;
    var list = (opts.classes[grade] || []).slice();
    if (opts.locked.classes && opts.locked.classes.length){
        list = list.filter(function(c){
            return opts.locked.classes.some(function(l){ return l.grade === grade && l.class_name === c; });
        });
    }
    var sel = $('#gClass').empty().append('<option value="">班级…</option>');
    list.forEach(function(c){ sel.append('<option value="'+c+'">'+c+'</option>'); });
    if (list.length) sel.val(list[0]);
    state.class_name = sel.val() || '';
    fillSubject();
}

function fillSubject(){
    var sel = $('#gSubject').empty();
    if (state.tab === 'subject'){
        opts.subjects.forEach(function(s){ sel.append('<option value="'+s+'">'+s+'</option>'); });
        if (!state.subject || !opts.subjects.includes(state.subject)) state.subject = opts.subjects[0] || '';
        sel.val(state.subject);
    } else if (state.tab === 'teacher'){
        sel.append('<option value="">全部科目</option>');
        var used = {};
        opts.subjects.forEach(function(s){ used[s] = true; });
        if (opts.teacher_combos && opts.teacher_combos.length){
            opts.subjects.forEach(function(s){
                if (opts.teacher_combos.some(function(c){ return c.subject === s; }))
                    sel.append('<option value="'+s+'">'+s+'（仅我任课）</option>');
            });
        }
        state.subject = '';
    }
}

function bindFilters(){
    $('#gGrade').on('change', fillExams);
    $('#gExam').on('change', function(){
        state.examId = $(this).val();
        loadCurrent();
    });
    $('#gClass').on('change', function(){ state.class_name = $(this).val(); loadCurrent(); });
    $('#gSubject').on('change', function(){
        state.subject = $(this).val();
        if (state.tab === 'teacher' && $(this).val() === '') state.subject = '';
        loadCurrent();
    });
    $('#gDir').on('change', function(){
        state.direction = $(this).val() || '';
        loadCurrent();
    });
    $('#gExport').on('click', function(){
        var q = buildQuery();
        window.location.href = '/grades/export/' + state.tab + q;
    });
    $('#gPrint').on('click', function(){ window.print(); });
}

function bindTabs(){
    $('#gTabs a[data-bs-toggle="tab"]').on('shown.bs.tab', function(e){
        var t = $(e.target).closest('li').data('tab');
        activateTab(t, true);
    });
}

function activateTab(tab, fromClick){
    state.tab = tab;
    // 筛选器显隐：方向仅年级/学科分析可用；班级仅班级分析；科目按 tab
    $('#gClass').toggleClass('d-none', tab !== 'class');
    $('#gDir').toggleClass('d-none', tab !== 'grade' && tab !== 'subject');
    // 修复：原条件写反——学科/教师分析 tab 需要显示科目筛选器，年级/班级 tab 隐藏
    $('#gSubject').toggleClass('d-none', tab !== 'subject' && tab !== 'teacher');
    if (tab === 'subject' || tab === 'teacher') fillSubject();
    $('#gTabs a[href="#pane-'+tab+'"]').tab('show');
    // 班级下拉只随 tab 显隐，**绝不清空其值**：一旦清空，切回班级分析时
    // state.class_name 读到空 → 后端查不到班级 → 前端误报"该考试尚未导入成绩"。
    // （buildQuery 仅在 tab==='class' 时带 class_name，其他 tab 保留该值无副作用）
    state.class_name = $('#gClass').val() || '';
    loadCurrent();
}

/* ---------------- 数据加载与渲染 ---------------- */
function loadCurrent(){
    var pane = $('#pane-' + state.tab);
    pane.find('.tab-content-area').empty().addClass('d-none');
    pane.find('.empty-state').addClass('d-none');
    // 班级分析未选班级时直接提示，不发无意义请求（否则后端返回空，会误报成"未导入成绩"）
    if (state.tab === 'class' && !state.class_name){
        showEmpty(state.tab, '请先在顶部筛选栏选择班级');
        return;
    }
    pane.find('.tab-pane-loading').removeClass('d-none');
    var url = '/grades/api/analysis/' + state.tab + buildQuery();
    $.getJSON(url, function(res){
        pane.find('.tab-pane-loading').addClass('d-none');
        var data = res.data || {};
        var area = pane.find('.tab-content-area').removeClass('d-none');
        if (!data || data.meta && data.meta.empty || !Object.keys(data.tables || {}).length){
            showEmpty(state.tab, '暂无数据：该考试尚未导入成绩（或本场无参考学生）');
            return;
        }
        area.empty();
        var info = buildExamInfo(data);
        if (info) area.append(info);
        renderTables(area, data.tables || {});
        renderCharts(area, data.charts || {});
    }).fail(function(){
        pane.find('.tab-pane-loading').addClass('d-none');
        showEmpty(state.tab, '数据加载失败（或无权查看该数据）');
    });
}

// 修复：拼接进 HTML 的服务端字符串统一转义，防止存储型 XSS（考试名/班级/科目等）
function escHtml(s){
    return String(s == null ? '' : s).replace(/[&<>"']/g, function(c){
        return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
    });
}

function buildExamInfo(data){
    if (!data.exam) return null;
    var wrap = $('<div>');
    wrap.append($('<div class="alert alert-light border py-2 small mb-2">').append(
        '<i class="bi bi-info-circle"></i> ' +
        '当前考试：<strong>' + escHtml(data.exam.name) + '</strong> · ' +
        escHtml(data.exam.grade) + ' · ' + escHtml(data.exam.date) +
        (data.class_name ? ' · 班级：' + escHtml(data.class_name) : '') +
        (data.subject ? ' · 科目：' + escHtml(data.subject) : '')
    ));
    // 分批导入（一次导一科）时，总分只是「已导入科目合计」，必须显式提示避免误读
    var p = data.meta && data.meta.partial;
    if (p){
        wrap.append($('<div class="alert alert-warning py-2 small mb-2">').append(
            // 注：expected 是全年级选科并集（可能 9 科），不是某个学生的应考科，
            // 所以这里不写「x/y 科」的比例，避免误以为学生要考 9 科
            '<i class="bi bi-exclamation-triangle"></i> <b>分批导入中</b>：本场已导入 '
            + escHtml((p.imported || []).join('、') || '—') + '；'
            + '本年级涉及的科目中尚未导入：<b>' + escHtml((p.missing || []).join('、')) + '</b>。'
            + '此时<b>总分、总分排名、分数段/名次段及基于总分的上线统计</b>都只是'
            + '「已导入科目合计」的口径，全部科目导完后才准确；'
            + '单科分析（科目均分、及格率、单科排名）不受影响。'
        ));
    }
    return wrap;
}

function showEmpty(tab, msg){
    var pane = $('#pane-' + tab);
    pane.find('.tab-pane-loading').addClass('d-none');
    pane.find('.tab-content-area').empty().addClass('d-none');
    pane.find('.empty-state p').text(msg);
    pane.find('.empty-state').removeClass('d-none');
}

function renderTables(area, tables){
    var wrap = $('<div>');
    Object.keys(tables).forEach(function(key){
        var t = tables[key];
        var card = $('<div class="card tbl-card mb-3">');
        var head = $('<div class="card-header d-flex justify-content-between align-items-center">')
            .append('<h6 class="mb-0"><i class="bi bi-table me-1"></i>' + escHtml(t.title || key) + '</h6>')
            .append('<button class="btn btn-sm btn-outline-secondary tbl-fold">折叠</button>');
        card.append(head);
        var body = $('<div class="card-body p-0 tbl-wrap">');
        var table = $('<table class="table table-hover g-table mb-0">');
        var thead = $('<thead><tr></tr></thead>');
        (t.columns || []).forEach(function(c){
            thead.find('tr').append('<th' + (c.type !== 'text' ? ' class="num"' : '') + '>' + c.label + '</th>');
        });
        table.append(thead);
        var tbody = $('<tbody>');
        (t.rows || []).forEach(function(r){
            var tr = $('<tr>');
            (t.columns || []).forEach(function(c){
                var val = r[c.key];
                var td = $('<td' + (c.type !== 'text' ? ' class="num"' : '') + '>').text(fmt(val));
                // 变动列着色（客观展示，无文字评价）
                if (c.key === 'rank_move' || c.key === 'score_move' || c.key === 'diff'){
                    var n = Number(val);
                    if (!isNaN(n)){
                        td.addClass(n > 0 ? 'text-danger' : n < 0 ? 'text-success' : '');
                    }
                }
                tr.append(td);
            });
            tbody.append(tr);
        });
        table.append(tbody);
        body.append(table);
        card.append(body);
        head.find('.tbl-fold').on('click', function(){
            body.toggle();
            $(this).text(body.is(':visible') ? '折叠' : '展开');
        });
        wrap.append(card);
    });
    area.append(wrap);
}

function renderCharts(area, chartsData){
    var wrap = $('<div class="row g-3">');
    Object.keys(chartsData).forEach(function(key, idx){
        var c = chartsData[key];
        var col = $('<div class="col-xl-6">');
        var card = $('<div class="card chart-card mb-0">');
        card.append('<div class="card-header py-2"><h6 class="mb-0 small"><i class="bi bi-bar-chart me-1"></i>'
            + (c.title || key) + '</h6></div>');
        var box = $('<div class="chart-box' + (c.type === 'heatmap' ? ' short' : '') + '"></div>');
        card.append('<div class="card-body p-2">').append(box);
        col.append(card);
        wrap.append(col);
        setTimeout(function(){ renderChart(box[0], key, c); }, 30 * idx);
    });
    area.append(wrap);
}

function renderChart(dom, key, c){
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

function baseGrid(){ return {left: 60, right: 40, top: 50, bottom: 40, containLabel: true}; }
function colorOf(i){ return PALETTE[i % PALETTE.length]; }
function yFmt(){ return {type: 'value', axisLabel: {formatter: '{value}'}}; }

function barOption(c, grouped){
    var series = (c.series || []).map(function(s, i){
        var item = {name: s.name, type: 'bar', data: s.data, itemStyle: {color: colorOf(i)}};
        if (!grouped && i === 0){
            // 年级均值参考线 + 该科各层划线参考线
            var ml = [];
            if (c.markLine != null){
                ml.push({yAxis: c.markLine, lineStyle: {color: '#f59e0b', type: 'dashed'},
                    label: {formatter: '年级均值 {c}'}});
            }
            (c.bandLines || []).forEach(function(b){
                ml.push({yAxis: b.value, lineStyle: {color: '#ef4444', type: 'dashed'},
                    label: {formatter: b.name + ' {c}'}});
            });
            if (ml.length){ item.markLine = {silent: true, symbol: 'none', data: ml}; }
        }
        return item;
    });
    return {color: PALETTE, tooltip: {trigger: 'axis'}, legend: grouped ? {top: 0} : undefined,
        grid: baseGrid(), xAxis: {type: 'category', data: c.xAxis || [], axisLabel: {interval: 0}},
        yAxis: yFmt(), series: series};
}

function stackOption(c){
    return {tooltip: {trigger: 'axis'}, legend: {top: 0, type: 'scroll'},
        grid: baseGrid(),
        xAxis: {type: 'category', data: c.xAxis || []},
        yAxis: yFmt(),
        series: (c.series || []).map(function(s, i){
            return {name: s.name, type: 'bar', stack: 'total', data: s.data,
                itemStyle: {color: colorOf(i)}};
        })};
}

function boxOption(c){
    return {tooltip: {trigger: 'item'}, grid: baseGrid(),
        xAxis: {type: 'category', data: c.xAxis || []},
        yAxis: yFmt(),
        series: (c.series || []).map(function(s){
            return {name: s.name || '总分分布', type: 'boxplot', data: s.data || [],
                itemStyle: {color: '#3b82f6'}};
        })};
}

function lineOption(c){
    var sel = c.selected || {};
    var selected = {};
    (c.series || []).forEach(function(s){ selected[s.name] = !!sel[s.name]; });
    return {color: PALETTE, tooltip: {trigger: 'axis'},
        legend: {top: 0, type: 'scroll', selected: selected},
        grid: baseGrid(),
        xAxis: {type: 'category', data: c.xAxis || []},
        yAxis: yFmt(),
        series: (c.series || []).map(function(s){
            return {name: s.name, type: 'line', data: s.data, connectNulls: false,
                smooth: true, symbolSize: 5};
        })};
}

function dualOption(c){
    return {color: PALETTE, tooltip: {trigger: 'axis'},
        legend: {top: 0},
        grid: baseGrid(),
        xAxis: {type: 'category', data: c.xAxis || []},
        yAxis: [{type: 'value', name: '均分'}, {type: 'value', name: '%', max: 100}],
        series: (c.series || []).map(function(s){
            return {name: s.name, type: 'line', data: s.data, yAxisIndex: s.yAxis || 0,
                smooth: true, symbolSize: 5};
        })};
}

function radarOption(c){
    return {color: PALETTE, tooltip: {},
        legend: {top: 0},
        radar: {indicator: (c.indicators || []), radius: '65%'},
        series: [{type: 'radar', data: (c.series || []).map(function(s){
            return {name: s.name, value: s.data || []};
        })}]};
}

function heatOption(c){
    var xs = c.xAxis || [], ys = c.yAxis || [], m = c.data || [];
    var cells = [], min = null, max = null;
    ys.forEach(function(y, i){
        (m[i] || []).forEach(function(v, j){
            if (v === null || v === undefined){ return; }
            cells.push([j, i, v]);
            if (min === null || v < min) min = v;
            if (max === null || v > max) max = v;
        });
    });
    if (min === max){ max = min + 1; }
    return {tooltip: {position: 'top',
                formatter: function(p){ return xs[p.value[0]] + ' · ' + ys[p.value[1]]
                    + '<br/>平均分：' + p.value[2]; }},
        grid: {left: 60, right: 40, top: 40, bottom: 80},
        xAxis: {type: 'category', data: xs, axisLabel: {interval: 0, rotate: 30}},
        yAxis: {type: 'category', data: ys},
        visualMap: {min: Math.floor(min), max: Math.ceil(max), calculable: true,
            orient: 'horizontal', left: 'center', bottom: 0,
            inRange: {color: ['#eff6ff', '#93c5fd', '#3b82f6', '#1d4ed8']}},
        series: [{type: 'heatmap', data: cells, label: {show: true, fontSize: 10}}]};
}

function buildQuery(){
    var qs = [];
    if (state.examId) qs.push('exam_id=' + state.examId);
    if (state.tab === 'class' && state.class_name) qs.push('class_name=' + encodeURIComponent(state.class_name));
    if ((state.tab === 'subject' || state.tab === 'teacher') && state.subject)
        qs.push('subject=' + encodeURIComponent(state.subject));
    if ((state.tab === 'grade' || state.tab === 'subject') && state.direction)
        qs.push('direction=' + encodeURIComponent(state.direction));
    return qs.length ? '?' + qs.join('&') : '';
}

$(function(){ init(); });

/* ==================== AI 分析（BYOK：个人 Key 优先，全局兜底） ==================== */
$(function(){
    var aiBusy = false;
    var aiModalId = 'aiModal';

    function ensureModal(){
        if ($('#' + aiModalId).length) return;
        $('body').append('<div class="modal fade" id="' + aiModalId + '" tabindex="-1">'
            + '<div class="modal-dialog modal-xl modal-dialog-scrollable"><div class="modal-content">'
            + '<div class="modal-header"><h6 class="modal-title" id="aiModalTitle"></h6>'
            + '<button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>'
            + '<div class="modal-body" id="aiModalBody"></div>'
            + '<div class="modal-footer" id="aiModalFoot"></div></div></div></div>');
    }
    function showAi(title, bodyHtml, footHtml){
        ensureModal();
        $('#aiModalTitle').text(title);
        $('#aiModalBody').html(bodyHtml);
        $('#aiModalFoot').html(footHtml || '');
        var m = new bootstrap.Modal(document.getElementById(aiModalId));
        m.show();
        return m;
    }
    function hideAi(){
        var el = document.getElementById(aiModalId);
        if (el && bootstrap.Modal.getInstance(el)) bootstrap.Modal.getInstance(el).hide();
    }

    function esc(v){
        return String(v == null ? '' : v).replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    // 报告渲染到当前 tab 顶部
    function showReport(content, meta){
        var pane = $('#pane-' + state.tab);
        var card = $('<div class="card chart-card mb-3 ai-report" id="aiReportCard"></div>');
        var head = $('<div class="card-header py-2 d-flex justify-content-between align-items-center">')
            .append('<h6 class="mb-0"><i class="bi bi-stars me-1"></i>AI 报告' +
                (meta ? '<span class="text-muted small ms-2">' + esc(meta) + '</span>' : '') + '</h6>')
            .append('<span>'
                + '<button class="btn btn-sm btn-outline-primary ai-copy me-1">复制</button>'
                + '<button class="btn btn-sm btn-outline-secondary ai-close">关闭</button></span>');
        card.append(head);
        var body = $('<div class="card-body ai-report-body"></div>');
        body.append('<div class="ai-report-content">' + renderGradeMd(content) + '</div>');
        body.append('<div class="alert alert-light border small mt-3 mb-0"><i class="bi bi-info-circle"></i> ' +
            '本报告由 AI 生成，仅供参考，不代表学校评价结论。可到「AI 报告」查看历史。</div>');
        card.append(body);
        pane.find('.tab-content-area').prepend(card);
        $('.ai-close').on('click', function(){ card.remove(); });
        $('.ai-copy').on('click', function(){
            navigator.clipboard && navigator.clipboard.writeText(content);
            alert('报告已复制');
        });
    }

    // ---- 服务商列表（后端注册表驱动）----
    // 结构固定为 {default: 'deepseek', list: [{key, name, base_url, models, default_model, key_hint, docs, note}]}
    var PROVIDERS = {default: 'deepseek', list: []};
    var PROVIDERS_ERR = '';   // 加载失败的原因（供弹窗提示）

    // 列表加载失败时的兜底项，结构与注册表项保持一致
    function fallbackProvider(){
        return [{
            key: 'custom', name: '自定义（OpenAI 兼容）', base_url: '',
            default_model: '', models: [], key_hint: '由服务商提供', docs: '',
            note: '服务商列表加载失败，仅可手动填写接口地址与模型名'
        }];
    }

    function loadProviders(cb){
        if (PROVIDERS.list.length){ cb && cb(); return; }
        $.getJSON('/grades/ai/providers')
            .done(function(res){
                var d = (res && res.data) || {};
                // 显式映射：接口返回 default/providers，前端内部统一用 default/list
                PROVIDERS = {
                    default: d.default || 'deepseek',
                    list: d.providers || []
                };
                PROVIDERS_ERR = PROVIDERS.list.length ? '' : '未获取到可用的服务商';
                if (!PROVIDERS.list.length) PROVIDERS.list = fallbackProvider();
            })
            .fail(function(x){
                PROVIDERS = {default: 'custom', list: fallbackProvider()};
                PROVIDERS_ERR = '服务商列表加载失败'
                    + ((x && x.status) ? '（HTTP ' + x.status + '）' : '')
                    + '，可手动填写接口地址与模型名';
            })
            .always(function(){ cb && cb(); });
    }
    function providerOf(key){
        var p = null;
        (PROVIDERS.list || []).forEach(function(x){ if (x.key === key) p = x; });
        return p || {key: key || 'custom', name: key || '自定义', base_url: '', models: [], default_model: '', key_hint: ''};
    }
    function canManageGlobal(){
        return $('#aiCanGlobal').text().trim() === '1';
    }

    // Key 配置表单（支持全部服务商）
    function keyFormHtml(keyData, isGlobal){
        var k = keyData || {};
        var cur = k.provider || PROVIDERS.default || 'deepseek';
        var html = '';
        if (PROVIDERS_ERR){
            html += '<div class="alert alert-warning small py-2"><i class="bi bi-exclamation-triangle"></i> '
                + esc(PROVIDERS_ERR) + '</div>';
        }
        if (k.configured){
            html += '<div class="alert alert-success small py-2 d-flex justify-content-between align-items-center">'
                + '<span>当前已配置：<b>' + esc(k.masked || '') + '</b>'
                + '（' + esc(k.provider_name || cur) + ' / ' + esc(k.model || '') + '）</span>'
                + '<button class="btn btn-sm btn-outline-danger" id="aiKeyDel">清除</button></div>';
        }
        html += '<div class="row g-2">'
            + '<div class="col-md-6"><label class="form-label small mb-1">服务商</label>'
            + '<select class="form-select form-select-sm" id="aiProvider">';
        (PROVIDERS.list || []).forEach(function(p){
            html += '<option value="' + esc(p.key) + '"' + (p.key === cur ? ' selected' : '') + '>'
                + esc(p.name) + '</option>';
        });
        html += '</select><div class="form-text small" id="aiProviderNote"></div></div>'
            + '<div class="col-md-6"><label class="form-label small mb-1">模型</label>'
            + '<input class="form-control form-control-sm" id="aiModel" list="aiModelList" '
            + 'value="' + esc(k.model || providerOf(cur).default_model || '') + '" placeholder="如 deepseek-chat">'
            + '<datalist id="aiModelList"></datalist>'
            + '<div class="form-text small">可从下拉选择，也可手工填写厂商模型名</div></div></div>'
            + '<div class="mb-2"><label class="form-label small mb-1">API Key</label>'
            + '<input type="password" class="form-control form-control-sm" id="aiApiKey" '
            + 'placeholder="' + esc(providerOf(cur).key_hint || 'sk-...') + '" autocomplete="off">'
            + '<div class="form-text small" id="aiKeyHint"></div></div>'
            + '<div class="mb-2"><label class="form-label small mb-1">接口地址（OpenAI 兼容，一般无需修改）</label>'
            + '<input class="form-control form-control-sm" id="aiBaseUrl" placeholder="https://..." value="'
            + esc(k.base_url || '') + '">'
            + '<div class="form-text small">留空表示使用该服务商官方地址；中转站或私有部署可自行填写。</div></div>'
            + '<div id="aiTestResult" class="small mb-2"></div>'
            + '<div class="form-text small">Key 经加密存储，仅本人可见；每次调用使用您自己的额度。</div>';
        return html;
    }

    // 服务商切换：自动填充默认地址/模型与提示
    function syncProviderUI(){
        var p = providerOf($('#aiProvider').val());
        var $list = $('#aiModelList').empty();
        (p.models || []).forEach(function(m){
            $list.append('<option value="' + esc(m) + '"></option>');
        });
        if (p.default_model && !$('#aiModel').val()) $('#aiModel').val(p.default_model);
        $('#aiApiKey').attr('placeholder', p.key_hint || 'sk-...');
        var note = '';
        if (p.key_hint) note += 'Key 形如 <code>' + esc(p.key_hint) + '</code>；';
        if (p.note) note += esc(p.note) + '；';
        if (p.docs) note += '<a href="' + esc(p.docs) + '" target="_blank" rel="noopener">获取 Key</a>';
        $('#aiKeyHint').html(note);
        $('#aiProviderNote').html(p.base_url ? '官方地址：<code>' + esc(p.base_url) + '</code>' : '需填写接口地址');
        if (!$('#aiBaseUrl').val()) $('#aiBaseUrl').attr('placeholder', p.base_url || 'https://...');
    }

    // 连通性测试
    function runKeyTest(isGlobal){
        var $btn = $('#aiKeyTest').prop('disabled', true);
        var $box = $('#aiTestResult').html('<span class="text-muted">正在测试连接…</span>');
        var payload = {
            scope: isGlobal ? 'global' : 'personal',
            provider: $('#aiProvider').val(),
            model: $('#aiModel').val().trim(),
            base_url: $('#aiBaseUrl').val().trim(),
            api_key: $('#aiApiKey').val().trim() || 'keep'
        };
        ajaxJson('/grades/ai/key/test', payload, function(res){
            $box.html('<span class="text-success"><i class="bi bi-check-circle"></i> ' + esc(res.message) + '</span>');
        }, function(x){
            var msg = (x.responseJSON && x.responseJSON.message) || '测试失败';
            $box.html('<span class="text-danger"><i class="bi bi-x-circle"></i> ' + esc(msg) + '</span>');
        }, function(){ $btn.prop('disabled', false); });
    }

    // 统一 JSON 提交（必须显式 contentType，否则后端 get_json 取不到）
    function ajaxJson(url, payload, ok, fail, always){
        return $.ajax({
            url: url, method: 'POST', contentType: 'application/json',
            dataType: 'json', data: JSON.stringify(payload)
        }).done(function(res){ ok && ok(res); })
          .fail(function(x){ fail && fail(x); })
          .always(function(){ always && always(); });
    }

    // 打开配置弹窗
    function openKeySetup(isGlobal){
        var url = isGlobal ? '/grades/ai/global-key' : '/grades/ai/key';
        loadProviders(function(){
            $.getJSON(url, function(res){
                var k = res.data || {};
                var foot = '<button class="btn btn-sm btn-outline-secondary me-auto" id="aiKeyTest">'
                    + '<i class="bi bi-plug"></i> 测试连接</button>'
                    + '<button class="btn btn-sm btn-secondary" data-bs-dismiss="modal">取消</button>'
                    + '<button class="btn btn-sm btn-primary" id="aiKeySave">保存</button>';
                showAi(isGlobal ? '配置全局公共 AI Key（管理员）' : '配置个人 AI API Key',
                    keyFormHtml(k, isGlobal), foot);
                bindKeyForm(isGlobal);
            });
        });
    }
    function bindKeyForm(isGlobal){
        var $b = $('#aiKeyDel');
        if ($b.length){
            $b.on('click', function(){
                if (!confirm('确定清除已保存的 API Key？')) return;
                $.ajax({url: isGlobal ? '/grades/ai/global-key' : '/grades/ai/key',
                    method: 'DELETE',
                    success: function(){ hideAi(); openKeySetup(isGlobal); },
                    error: function(x){
                        alert((x.responseJSON && x.responseJSON.message) || '清除失败');
                    }});
            });
        }
        $('#aiProvider').on('change', function(){
            $('#aiModel').val(providerOf($(this).val()).default_model || '');
            $('#aiBaseUrl').val('');
            syncProviderUI();
        });
        syncProviderUI();
        $('#aiKeyTest').on('click', function(){ runKeyTest(isGlobal); });
        $('#aiKeySave').on('click', function(){
            var payload = collectKeyPayload();
            if (payload === null) return;
            ajaxJson(isGlobal ? '/grades/ai/global-key' : '/grades/ai/key', payload,
                function(res){ alert(res.message || '已保存'); hideAi(); },
                function(x){ alert((x.responseJSON && x.responseJSON.message) || '保存失败'); });
        });
    }
    function collectKeyPayload(){
        var apiKey = $('#aiApiKey').val().trim();
        var model = $('#aiModel').val().trim();
        if (!model){ alert('请填写模型名'); return null; }
        if (!apiKey && !$('#aiKeyDel').length){ alert('请输入 API Key'); return null; }
        return {
            provider: $('#aiProvider').val(),
            model: model,
            base_url: $('#aiBaseUrl').val().trim(),
            api_key: apiKey || 'keep'   // 为空且已配置过 = 不更换 Key
        };
    }

    // ---- SSE 流式请求（fetch + ReadableStream；EventSource 不支持 POST）----
    function streamPost(url, payload, handlers){
        fetch(url, {
            method: 'POST',
            headers: {'Content-Type': 'application/json',
                      'X-CSRFToken': (typeof csrfToken === 'undefined' ? '' : csrfToken)},
            body: JSON.stringify(payload)
        }).then(function(resp){
            if (!resp.ok || !resp.body){
                return resp.json().catch(function(){ return {}; }).then(function(j){
                    handlers.onError && handlers.onError(j.message || ('HTTP ' + resp.status));
                });
            }
            var reader = resp.body.getReader();
            var decoder = new TextDecoder('utf-8');
            var buf = '';
            function pump(){
                return reader.read().then(function(r){
                    if (r.done){ handlers.onDone && handlers.onDone(); return; }
                    buf += decoder.decode(r.value, {stream: true});
                    var frames = buf.split('\n\n');
                    buf = frames.pop();
                    frames.forEach(function(frame){
                        frame.split('\n').forEach(function(line){
                            if (line.indexOf('data:') !== 0) return;
                            try {
                                var ev = JSON.parse(line.slice(5).trim());
                                handlers.onEvent && handlers.onEvent(ev);
                            } catch (e) { /* 忽略半包 */ }
                        });
                    });
                    return pump();
                });
            }
            return pump();
        }).catch(function(e){
            handlers.onError && handlers.onError((e && e.message) || '网络异常');
        });
    }

    // ---- 图表渲染（ECharts，数据由后端本地统计，非模型生成）----
    var PALETTE = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#06b6d4'];
    function chartCard(title, domId){
        return '<div class="col-md-6"><div class="ai-chart-card">'
            + '<div class="ai-chart-title">' + esc(title) + '</div>'
            + '<div class="ai-chart" id="' + domId + '" style="height:260px;"></div></div></div>';
    }
    function renderCharts(container, charts){
        if (!charts || typeof echarts === 'undefined') return;
        var specs = [];
        var sb = charts.score_bands || {};
        if ((sb.values || []).length)
            specs.push(['sb', '总分分数段分布', 'bar', sb.labels, sb.values, '人数']);
        var ca = charts.class_avg || {};
        if ((ca.values || []).length)
            specs.push(['ca', '各班总分均分', 'bar', ca.labels, ca.values, '均分']);
        var sa = charts.subject_avg || {};
        if ((sa.values || []).length)
            specs.push(['sa', '各学科均分', 'bar', sa.labels, sa.values, '均分']);
        var md = charts.move_dist || {};
        if ((md.values || []).length && md.values.some(function(v){ return v > 0; }))
            specs.push(['md', '进退步分布', 'pie', md.labels, md.values, '人数']);
        var cp = charts.compare || {};
        if ((cp.values || []).length === 2)
            specs.push(['cp', '本次 vs 上次 均分', 'bar', cp.labels, cp.values, '均分']);

        if (!specs.length) return;
        var html = '<div class="row g-3 mt-1">';
        specs.forEach(function(s){ html += chartCard(s[1], 'aiChart_' + s[0]); });
        html += '</div>';
        $(container).html(html);

        specs.forEach(function(s){
            var el = document.getElementById('aiChart_' + s[0]);
            if (!el) return;
            var chart = echarts.init(el);
            var opt;
            if (s[2] === 'pie'){
                opt = {
                    tooltip: {trigger: 'item'},
                    legend: {bottom: 0, itemWidth: 10, itemHeight: 10, textStyle: {fontSize: 11}},
                    color: PALETTE,
                    series: [{
                        type: 'pie', radius: ['40%', '65%'], center: ['50%', '45%'],
                        label: {formatter: '{b}\n{c}人', fontSize: 11},
                        data: s[3].map(function(k, i){ return {name: k, value: s[4][i]}; })
                            .filter(function(x){ return x.value > 0; })
                    }]
                };
            } else {
                opt = {
                    tooltip: {trigger: 'axis', axisPointer: {type: 'shadow'}},
                    grid: {left: 8, right: 12, top: 24, bottom: 24, containLabel: true},
                    xAxis: {type: 'category', data: s[3], axisLabel: {fontSize: 10, interval: 0,
                        rotate: (s[3].length > 8 ? 30 : 0)}},
                    yAxis: {type: 'value', name: s[5], nameTextStyle: {fontSize: 10}},
                    series: [{
                        type: 'bar', data: s[4], barMaxWidth: 28,
                        itemStyle: {color: PALETTE[0], borderRadius: [4, 4, 0, 0]},
                        label: {show: true, position: 'top', fontSize: 10}
                    }]
                };
            }
            chart.setOption(opt);
            window.addEventListener('resize', function(){ chart.resize(); });
        });
    }

    // ---- 对话区 ----
    function chatShellHtml(s){
        return '<div class="alert alert-warning small py-2 mb-2"><i class="bi bi-shield-exclamation"></i> '
            + '成绩数据将发送至 <b>' + esc(s.provider_name || s.provider || '') + '</b>（'
            + esc(s.model || '') + '），离开学校内网。范围：' + esc(s.scope_desc)
            + '（' + s.scope_students + ' 人）。</div>'
            + '<div class="ai-chat" id="aiChat"></div>'
            + '<div id="aiChatCharts"></div>';
    }
    function chatInputHtml(){
        return '<div class="ai-chat-input mt-2">'
            + '<textarea class="form-control form-control-sm" id="aiChatInput" rows="2" '
            + 'placeholder="针对本次考试继续追问，例如：哪些班级的数学需要重点帮扶？"></textarea>'
            + '<div class="d-flex justify-content-between align-items-center mt-2">'
            + '<button class="btn btn-sm btn-outline-secondary" id="aiChatClear">'
            + '<i class="bi bi-eraser"></i> 清空对话</button>'
            + '<button class="btn btn-sm btn-primary" id="aiChatSend">'
            + '<i class="bi bi-send"></i> 发送</button></div></div>';
    }
    function pushMsg(role, html, extraClass){
        var $box = $('#aiChat');
        var $m = $('<div class="ai-msg ' + (role === 'user' ? 'ai-msg-user' : 'ai-msg-ai')
            + (extraClass ? ' ' + extraClass : '') + '"></div>');
        $m.append('<div class="ai-bubble">' + html + '</div>');
        $box.append($m);
        $box.scrollTop($box[0].scrollHeight);
        return $m;
    }
    function pushStage(text){
        var $m = pushMsg('ai', '<span class="ai-stage"><span class="spinner-border '
            + 'spinner-border-sm me-1"></span>' + esc(text) + '</span>');
        return $m;
    }

    // 主流程：打开对话式分析面板
    function openAiFlow(){
        var examId = $('#gExam').val();
        if (!examId){ alert('请先选择要分析的考试'); return; }
        if (aiBusy) return;
        $.getJSON('/grades/ai/scope?exam_id=' + examId, function(res){
            var s = res.data;
            if (!s.key_source){
                // 无 Key：引导配置
                loadProviders(function(){
                    showAi('配置 AI API Key',
                        '<p class="small text-muted">进行 AI 分析需要 API Key：可任选一家服务商（DeepSeek / 通义千问 / 智谱 GLM / '
                        + 'Kimi / 豆包 / 混元 / 文心一言，或自定义 OpenAI 兼容接口），费用走您自己的账户；'
                        + '也可联系管理员配置公共 Key 后免填。</p>' + keyFormHtml(null, false),
                        '<button class="btn btn-sm btn-outline-secondary me-auto" id="aiKeyTest">'
                        + '<i class="bi bi-plug"></i> 测试连接</button>'
                        + '<button class="btn btn-sm btn-secondary" data-bs-dismiss="modal">暂不</button>'
                        + '<button class="btn btn-sm btn-primary" id="aiKeySave">保存并继续</button>');
                    bindKeyForm(false);
                    $('#aiKeySave').off('click').on('click', function(){
                        var payload = collectKeyPayload();
                        if (payload === null) return;
                        ajaxJson('/grades/ai/key', payload, function(){
                            hideAi(); openAiFlow();   // 保存后重新进入
                        }, function(x){
                            alert((x.responseJSON && x.responseJSON.message) || '保存失败');
                        });
                    });
                });
                return;
            }
            openChatPanel(examId, s);
        }).fail(function(x){
            alert(((x.responseJSON && x.responseJSON.message) || '无法获取发送范围（或该考试超出您的权限）'));
        });
    }

    function openChatPanel(examId, s){
        showAi('AI 分析 · ' + (s.exam ? s.exam.name : ''),
            chatShellHtml(s),
            '<button class="btn btn-sm btn-outline-secondary me-auto" id="aiChangeKey">更换/清除 Key</button>'
            + (canManageGlobal() ? '<button class="btn btn-sm btn-outline-dark" id="aiGlobalKey">'
                + '<i class="bi bi-key"></i> 公共 Key</button>' : '')
            + '<button class="btn btn-sm btn-outline-success" id="aiToPage">插入到当前页签</button>'
            + '<button class="btn btn-sm btn-secondary" data-bs-dismiss="modal">关闭</button>');
        $('#aiModalBody').append(chatInputHtml());
        $('#aiChangeKey').on('click', function(){ openKeySetup(false); });
        $('#aiGlobalKey').on('click', function(){ openKeySetup(true); });
        $('#aiToPage').on('click', function(){
            var txt = lastReportText;
            if (!txt){ alert('还没有生成报告'); return; }
            showReport(txt, '已保存到 AI 报告历史');
            hideAi();
        });
        $('#aiChatClear').on('click', function(){
            if (!confirm('清空本次考试的对话记录？')) return;
            $.ajax({url: '/grades/ai/chat/history?exam_id=' + examId, method: 'DELETE',
                success: function(){ $('#aiChat').empty(); lastReportText = ''; }});
        });
        function send(){
            var q = $('#aiChatInput').val().trim();
            if (!q || aiBusy) return;
            $('#aiChatInput').val('');
            askFollowUp(examId, q);
        }
        $('#aiChatSend').on('click', send);
        $('#aiChatInput').on('keydown', function(e){
            if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)){ send(); }
        });
        bindReportActions();

        // 已有历史则回放，否则直接生成首份报告
        $.getJSON('/grades/ai/chat/history?exam_id=' + examId, function(res){
            var rows = res.data || [];
            if (rows.length){
                rows.forEach(function(r){
                    pushMsg(r.role, r.role === 'user' ? esc(r.content)
                        : renderGradeMd(r.content || ''));
                });
            } else {
                runAnalyze(examId);
            }
        });
    }

    var lastReportText = '';
    function bindReportActions(){}

    // 生成首份报告（流式）
    function runAnalyze(examId){
        aiBusy = true;
        $('#gAi').prop('disabled', true);
        var $stage = pushStage('正在准备数据…');
        var $msg = null, $body = null, $think = null;
        var text = '', think = '', timer = null;

        function flush(){
            if ($body) $body.html(renderGradeMd(text) + '<span class="ai-caret"></span>');
            if ($think) $think.text(think);
            var $box = $('#aiChat');
            if ($box.length) $box.scrollTop($box[0].scrollHeight);
        }
        function schedule(){
            if (timer) return;
            timer = setTimeout(function(){ timer = null; flush(); }, 120);
        }

        streamPost('/grades/ai/analyze', {exam_id: examId, stream: 1}, {
            onEvent: function(ev){
                if (ev.type === 'stage'){
                    $stage.find('.ai-stage').html('<span class="spinner-border '
                        + 'spinner-border-sm me-1"></span>' + esc(ev.text));
                } else if (ev.type === 'charts'){
                    renderCharts('#aiChatCharts', ev.data);
                } else if (ev.type === 'reasoning'){
                    think += ev.text;
                    if (!$think){
                        if (!$msg){ $msg = pushMsg('ai', ''); $body = $msg.find('.ai-bubble'); }
                        $think = $('<details class="ai-think" open><summary>'
                            + '<i class="bi bi-lightbulb"></i> 思考过程</summary>'
                            + '<div class="ai-think-body small text-muted"></div></details>');
                        $msg.append($think);
                        $think = $think.find('.ai-think-body');
                    }
                    schedule();
                } else if (ev.type === 'delta'){
                    text += ev.text;
                    if (!$msg){ $msg = pushMsg('ai', ''); $body = $msg.find('.ai-bubble'); }
                    schedule();
                } else if (ev.type === 'saved'){
                    lastReportText = text;
                } else if (ev.type === 'error'){
                    pushMsg('ai', '<span class="text-danger"><i class="bi bi-x-circle"></i> '
                        + esc(ev.text) + '</span>');
                }
            },
            onError: function(msg){
                pushMsg('ai', '<span class="text-danger"><i class="bi bi-x-circle"></i> '
                    + esc(msg) + '</span>');
            },
            onDone: function(){
                if (timer){ clearTimeout(timer); timer = null; }
                flush();
                if ($body) $body.find('.ai-caret').remove();
                $stage.remove();
                if (text) lastReportText = text;
                aiBusy = false;
                $('#gAi').prop('disabled', false);
            }
        });
    }

    // 多轮追问（流式）
    function askFollowUp(examId, question){
        aiBusy = true;
        pushMsg('user', esc(question));
        var $msg = null, $body = null, text = '', timer = null;
        function flush(){
            if ($body) $body.html(renderGradeMd(text) + '<span class="ai-caret"></span>');
            var $box = $('#aiChat');
            if ($box.length) $box.scrollTop($box[0].scrollHeight);
        }
        function schedule(){
            if (timer) return;
            timer = setTimeout(function(){ timer = null; flush(); }, 120);
        }
        streamPost('/grades/ai/chat', {exam_id: examId, message: question}, {
            onEvent: function(ev){
                if (ev.type === 'delta'){
                    text += ev.text;
                    if (!$msg){ $msg = pushMsg('ai', ''); $body = $msg.find('.ai-bubble'); }
                    schedule();
                } else if (ev.type === 'error'){
                    pushMsg('ai', '<span class="text-danger"><i class="bi bi-x-circle"></i> '
                        + esc(ev.text) + '</span>');
                }
            },
            onError: function(msg){
                pushMsg('ai', '<span class="text-danger"><i class="bi bi-x-circle"></i> '
                    + esc(msg) + '</span>');
            },
            onDone: function(){
                if (timer){ clearTimeout(timer); timer = null; }
                flush();
                if ($body) $body.find('.ai-caret').remove();
                aiBusy = false;
            }
        });
    }

    $('#gAi').on('click', openAiFlow);
});
})();
