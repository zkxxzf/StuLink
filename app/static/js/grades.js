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
    // v1.13.0 汇报区入口已移至左侧菜单栏（base.html），顶部按钮及相关逻辑同步移除
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
            + '<div class="modal-dialog modal-lg modal-dialog-scrollable"><div class="modal-content">'
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

    // 个人 Key 配置表单
    function keyFormHtml(keyData, isGlobal){
        var k = keyData || {};
        var html = '';
        if (k.configured){
            html += '<div class="alert alert-success small py-2">当前已配置：' + esc(k.masked || '') +
                '（' + esc(k.provider || '') + ' / ' + esc(k.model || '') + '）' +
                '<button class="btn btn-sm btn-outline-danger ms-2" id="aiKeyDel">清除</button></div>';
        }
        html += '<div class="mb-2"><label class="form-label small mb-1">服务商</label>'
            + '<select class="form-select form-select-sm" id="aiProvider">'
            + '<option value="deepseek"' + (!k.provider || k.provider === 'deepseek' ? ' selected' : '') + '>DeepSeek（推荐）</option>'
            + '<option value="custom"' + (k.provider === 'custom' ? ' selected' : '') + '>自定义（OpenAI 兼容）</option></select></div>'
            + '<div class="row"><div class="col-md-6 mb-2"><label class="form-label small mb-1">API Key</label>'
            + '<input type="password" class="form-control form-control-sm" id="aiApiKey" placeholder="sk-..." autocomplete="off"></div>'
            + '<div class="col-md-6 mb-2"><label class="form-label small mb-1">模型</label>'
            + '<input class="form-control form-control-sm" id="aiModel" value="' + esc(k.model || 'deepseek-chat') + '"></div></div>'
            + '<div class="mb-2 d-none" id="aiCustomWrap"><label class="form-label small mb-1">接口地址（OpenAI 兼容，如 https://api.deepseek.com 或中转站）</label>'
            + '<input class="form-control form-control-sm" id="aiBaseUrl" placeholder="https://..." value="' + esc(k.base_url || '') + '"></div>'
            + '<div class="form-text small">Key 经加密存储，仅本人可见；每次调用使用您自己的额度。</div>';
        return html;
    }

    // 打开配置弹窗
    function openKeySetup(isGlobal){
        var url = isGlobal ? '/grades/ai/global-key' : '/grades/ai/key';
        $.getJSON(url, function(res){
            var k = res.data || {};
            showAi(isGlobal ? '配置全局公共 AI Key（管理员）' : '配置个人 AI API Key',
                keyFormHtml(k, isGlobal),
                '<button class="btn btn-sm btn-secondary" data-bs-dismiss="modal">取消</button>'
                + '<button class="btn btn-sm btn-primary" id="aiKeySave">保存</button>');
            if (isGlobal && !(k && k.configured)){
                $('#aiProvider').val('deepseek');
            }
            bindKeyForm(isGlobal);
        });
    }
    function bindKeyForm(isGlobal){
        var $b = $('#aiKeyDel');
        if ($b.length){
            $b.on('click', function(){
                $.ajax({url: isGlobal ? '/grades/ai/global-key' : '/grades/ai/key',
                    method: 'DELETE', success: function(){ hideAi(); openKeySetup(isGlobal); }});
            });
        }
        $('#aiProvider').on('change', function(){
            $('#aiCustomWrap').toggleClass('d-none', $(this).val() !== 'custom');
        });
        $('#aiCustomWrap').toggleClass('d-none', $('#aiProvider').val() !== 'custom');
        $('#aiKeySave').on('click', function(){
            var payload = {
                provider: $('#aiProvider').val(),
                model: $('#aiModel').val().trim(),
                base_url: $('#aiProvider').val() === 'custom' ? $('#aiBaseUrl').val().trim() : '',
                api_key: $('#aiApiKey').val().trim()
            };
            if (!payload.api_key && !($('#aiKeyDel').length)){
                alert('请输入 API Key'); return;
            }
            if (!payload.api_key){ payload.api_key = 'keep'; } // 不更换仅改其它项
            $.post(isGlobal ? '/grades/ai/global-key' : '/grades/ai/key',
                JSON.stringify(payload), function(res){
                    alert(res.message || '已保存');
                    hideAi();
                }).fail(function(x){ alert(x.responseJSON && x.responseJSON.message || '保存失败'); });
        });
    }

    // 主流程
    function openAiFlow(){
        var examId = $('#gExam').val();
        if (!examId){ alert('请先选择要分析的考试'); return; }
        if (aiBusy) return;
        $.getJSON('/grades/ai/scope?exam_id=' + examId, function(res){
            var s = res.data;
            var keyLine = s.key_source === 'personal' ? '个人 Key（' + esc(s.provider) + ' / ' + esc(s.model) + '）'
                : s.key_source === 'global' ? '管理员公共 Key（' + esc(s.provider) + ' / ' + esc(s.model) + '）'
                : null;
            if (!keyLine){
                // 无 Key：引导配置
                showAi('配置 AI API Key',
                    '<p class="small text-muted">进行 AI 分析需要 API Key：您可填写自己的 DeepSeek Key（费用走您自己账户）；'
                    + '也可联系管理员配置公共 Key 后免填。</p>' + keyFormHtml(null, false),
                    '<button class="btn btn-sm btn-secondary" data-bs-dismiss="modal">暂不</button>'
                    + '<button class="btn btn-sm btn-primary" id="aiKeySave">保存并继续</button>');
                bindKeyForm(false);
                $('#aiKeySave').on('click', function(){
                    var payload = {provider: $('#aiProvider').val(),
                        model: $('#aiModel').val().trim(),
                        base_url: $('#aiProvider').val() === 'custom' ? $('#aiBaseUrl').val().trim() : '',
                        api_key: $('#aiApiKey').val().trim()};
                    if (!payload.api_key){ alert('请输入 API Key'); return; }
                    $.post('/grades/ai/key', JSON.stringify(payload), function(){
                        hideAi(); openAiFlow();  // 保存后重新预览
                    }).fail(function(x){ alert(x.responseJSON && x.responseJSON.message || '保存失败'); });
                });
                return;
            }
            // 有 Key：展示发送预览
            var body = '<div class="alert alert-warning small py-2"><i class="bi bi-shield-exclamation"></i> '
                + '成绩数据（含学生学号姓名）将发送至 AI 服务商（' + esc(s.provider || 'deepseek') + '），离开学校内网。请确认符合数据使用规定。</div>'
                + '<table class="table table-sm g-table mb-0">'
                + '<tr><td style="width:110px;">本次考试</td><td>' + esc(s.exam.name) + '（' + esc(s.exam.grade) + ' ' + esc(s.exam.date) + '）</td></tr>'
                + '<tr><td>对比考试</td><td>' + (s.prev ? esc(s.prev.name) + '（' + esc(s.prev.date) + '）' : '无（首场考试，仅本次）') + '</td></tr>'
                + '<tr><td>发送范围</td><td>' + esc(s.scope_desc) + '</td></tr>'
                + '<tr><td>学生人数</td><td>' + s.scope_students + ' 人（本次有效参考）</td></tr>'
                + '<tr><td>使用密钥</td><td>' + keyLine + '</td></tr></table>'
                + '<div class="form-text small mt-2">发送的是本次与上次两次考试的原始成绩（学号/姓名/各科分数/总分/排名/进退步），'
                + '仅包含您有权查看的数据；预计等待 30~90 秒。</div>';
            showAi('AI 分析 · 发送确认', body,
                '<button class="btn btn-sm btn-outline-secondary me-auto" id="aiChangeKey">更换/清除 Key</button>'
                + '<button class="btn btn-sm btn-secondary" data-bs-dismiss="modal">取消</button>'
                + '<button class="btn btn-sm btn-primary" id="aiRun"><i class="bi bi-stars"></i> 生成 AI 报告</button>');
            $('#aiChangeKey').on('click', function(){ openKeySetup(false); });
            $('#aiRun').on('click', function(){ runAnalyze(examId); });
        }).fail(function(){ alert('无法获取发送范围（或该考试超出您的权限）'); });
    }

    function runAnalyze(examId){
        aiBusy = true;
        var btn = $('#aiRun');
        if (btn.length) btn.prop('disabled', true).html('<span class="spinner-border spinner-border-sm me-1"></span>生成中（约 30-90 秒）…');
        $('#gAi').prop('disabled', true);
        $.post('/grades/ai/analyze', JSON.stringify({exam_id: examId}), function(res){
            aiBusy = false;
            $('#gAi').prop('disabled', false);
            hideAi();
            var d = res.data || {};
            showReport(d.content, (res.data && res.data.report_id ? '已保存到 AI 报告历史' : ''));
        }).fail(function(x){
            aiBusy = false;
            $('#gAi').prop('disabled', false);
            alert(x.responseJSON && x.responseJSON.message || '分析失败，请稍后重试');
        });
    }

    $('#gAi').on('click', openAiFlow);
});
})();
