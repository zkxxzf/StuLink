/* StuLink 成绩管理：分析页前端 v1.19.50.63
 * 五 tab 联动加载 / 统一表格渲染 / 每张指标卡「表格 | 图表」双视图 / ECharts 图表渲染
 */
(function(){
/* 2026-10-14 审计修复：调色板改为「实时读主题变量」（theme.css 的 --sl-chart-1..10），
   ECharts 的颜色是 JS 字面量、CSS 兜底够不到，深色下会出现刺眼白块 / 看不见的线。
   取不到（脚本未加载 / 老缓存）时回落到原字面量，保证图表不空、不报错。 */
var PALETTE_FALLBACK = ['#3b82f6', '#8b5cf6', '#10b981', '#f59e0b', '#ef4444',
                        '#06b6d4', '#ec4899', '#84cc16', '#f97316', '#6366f1'];
function palette(){
    return (window.stulinkPalette && window.stulinkPalette()) || PALETTE_FALLBACK;
}
/* 语义单色（参考线、状态色）：红/黄/绿在三套主题下都保持可区分 */
function sem(){
    return (window.stulinkSemantic && window.stulinkSemantic()) ||
           {ok: '#10b981', warn: '#f59e0b', danger: '#ef4444', info: '#3b82f6'};
}
var state = {grade: '', examId: '', tab: 'grade', class_name: '', subject: '', direction: '',
             selection: '', classes: [], denom: 'ref'};   // classes：班级对比 Tab 选中的班级
                                       // selection：选科组合（2026-10-16，如 物化生）
                                       // denom：统计口径（2026-10-17，ref=参考数 / enrolled=班级实际人数）
// tab 业务顺序。注意：Flask jsonify 默认按键名排序，Object.keys(opts.tabs) 的首项会是
// 'class' 而不是 'grade'，因此不能用它决定默认 tab，必须按此固定顺序取第一个可见项。
var TAB_ORDER = ['grade', 'class', 'subject', 'teacher', 'compare'];
// 2026-10-21 用户口径：侧栏五个分析项合并为「详细分析」（data-nav="ana_detail"），
// 页签切换时只需要保持这一项高亮。
var ANA_NAV = 'ana_detail';
var opts = null;
var charts = {};
var chartSpecs = {};   // 图表原始配置（按 key），主题切换重绘时用
// v1.19.50.63：?exam=（本场考试）只在首次填充时应用一次；
// 之后用户手动切换年级/考试不再被拉回本场，也避免「切换年级」被误判为本场不可见。
var initExamApplied = false;

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
        bindClassMulti();
        bindViewToggle();
        syncClassCandidate();
        // 可见 tab（页签与侧栏分析组同一份判定，避免「左边点得进 / 上面没这页」）
        var anyTab = false;
        Object.keys(opts.tabs).forEach(function(t){
            if (!opts.tabs[t]) { $('li[data-tab="'+t+'"]').hide(); }
            else { anyTab = true; }
        });
        if (!anyTab) $('.efn-item[data-nav="' + ANA_NAV + '"]').hide();
        var first = TAB_ORDER.filter(function(t){ return opts.tabs[t]; })[0];
        if (!first){ showEmpty('grade', '您没有成绩查看权限'); return; }
        // v1.19.0 三分栏深链：侧栏「班级分析 / 学科分析」等带 ?tab= 进来的初始页签优先
        var want = ($('#gInitialTab').text() || '').trim();
        activateTab((want && opts.tabs[want]) ? want : first);
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
        // v1.19.50.63 侧栏带进来的本场考试（?exam=）**优先落到它所在的年级**：
        // 此前无脑默认「第一个有考试的年级」（= 年份最大 = 最新年级），
        // 跨年级进入（如在 2024/2025 级考试里点「班级分析 / 学科分析」）时
        // 本场 id 在最新年级的列表里找不到 → fillExams 回落「该年级最近一场已导入」
        // → 页面显示最新年级成绩、syncExamNav 还把侧栏链接改成最新一场，
        // 「来回点」永远跳最新（用户 2026-10-12 复现）。
        var want = ($('#gInitialExam').text() || '').trim();
        var wantGrade = '';
        if (want){
            (opts.grades || []).some(function(g){
                var hit = (opts.exams[g] || []).some(function(e){
                    return String(e.id) === want;
                });
                if (hit){ wantGrade = g; }
                return hit;
            });
        }
        if (wantGrade){
            $('#gGrade').val(wantGrade);
        } else {
            // 未锁定时默认选中「第一个有考试的年级」，
            // 否则停在占位项上，考试下拉会是空的
            var first = (opts.grades || []).filter(function(g){
                return (opts.exams[g] || []).length > 0;
            })[0];
            if (first){ $('#gGrade').val(first); }
            else if ((opts.grades || []).length){ $('#gGrade').val(opts.grades[0]); }
        }
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
    // v1.19.0 优先用侧栏带进来的本场考试（?exam=），否则按原逻辑默认选最近一场已导入
    // v1.19.50.63 only-once：只有首次填充应用 ?exam=，用户手动换年级/考试后不再强制回本场
    var want = '';
    if (!initExamApplied){
        want = ($('#gInitialExam').text() || '').trim();
        initExamApplied = true;
    }
    var pick = want ? list.filter(function(e){ return String(e.id) === want; })[0] : null;
    if (want && !pick){
        // 本场考试不在可见列表（已删除 / 超出数据范围 / 已毕业）：**不能静默**退到最近一场
        // ——否则用户会把最新成绩当成本场数据（与侧栏标题矛盾）。恢复两个下拉并明确提示。
        $('#gGrade, #gExam').removeClass('d-none');
        gradeAppNotice('本场考试不可见（可能已删除或不在您的数据范围），已改为显示最近一场，请重新选择。');
    }
    if (!pick) pick = list.filter(function(e){ return e.status === 'imported'; })[0] || list[0];
    if (pick){ sel.val(String(pick.id)); }
    state.examId = sel.val() || '';
    state.grade = grade;
    syncExamNav();
    fillClasses();
    fillCombos();           // 2026-10-16 组合随考试变（初始化也要填）
}

/* v1.19.50.63 筛选栏上方的警示条（重复调用只保留一条） */
function gradeAppNotice(msg){
    $('#gAppNotice').remove();
    $('<div id="gAppNotice" class="alert alert-warning py-2 small mb-2"></div>')
        .html('<i class="bi bi-exclamation-triangle"></i> ' + msg)
        .insertBefore($('#gradeApp'));
}

/* v1.19.0 侧栏“本场流程”链接跟随当前选中的考试（悬浮流程栏在考试态页面显示时生效）*/
function syncExamNav(){
    var eid = state.examId;
    if (!eid) return;
    var $nav = $('.exam-flow-nav');
    if (!$nav.length) return;
    var urls = {
        detail: '/grades/exams/' + eid,
        affair: '/grades/exams/' + eid + '/affair',
        import: '/grades/exams/' + eid + '/import',
        analysis: '/grades/analysis?exam=' + eid,
        // 2026-10-21：五个分析子项合并为「详细分析」，统一指向分析页（页内页签切换）。
        ana_detail: '/grades/analysis?exam=' + eid,
        // 2026-10-19：原侧栏「AI 智能导入」项已并入「数据导入」（data-nav="import"），
        // 同一页左右两栏，因此这里不再需要 ai 映射；
        // 划线分层也去掉了独立侧栏项（编辑页从导入页卡片 / 考试详情进入），故无 bands 映射。
        near: '/grades/exams/' + eid + '/near-line',
        global: '/grades/global-compare?exam=' + eid
    };
    // v1.19.50.63 侧栏「成绩分析」文字入口（领导=总览（全年级对比）/ 其余=班级分析）此前
    // 未纳入同步：页面内切换考试后点它会回到**进入本页时**的考试，与当前选中不一致。
    // 有无「总览」项即区分领导入口（与 base.html 的 _is_lead 分支一致）。
    urls.ana_home = $nav.find('a[data-nav="global"]').length
        ? '/grades/global-compare?exam=' + eid
        : '/grades/analysis?exam=' + eid + '&tab=class';
    Object.keys(urls).forEach(function(k){
        $nav.find('a[data-nav="' + k + '"]').attr('href', urls[k]);
    });
}

/* v1.19.50.63 班级筛选仅限本场考试：优先用该场考试自己的班级
   （该场学生名册 ∪ 该场任课教师表）；未选考试/旧库无快照时回落年级班级 */
function classListFor(grade){
    var eid = state.examId || $('#gExam').val() || '';
    var m = (opts && opts.classes_by_exam) ? opts.classes_by_exam[String(eid)] : null;
    if (m && m.length) return m.slice();
    return (opts && opts.classes && opts.classes[grade]) ? opts.classes[grade].slice() : [];
}

/* 2026-10-19 用户口径：班级筛选改成和成绩单「按班级查看」一样的一排按钮
   （01班、02班…），点一次即切换，不再用下拉。只在「班级分析」页签显示。 */
function fillClasses(){
    var grade = $('#gGrade').val() || state.grade;
    var list = classListFor(grade);
    if (opts.locked.classes && opts.locked.classes.length){
        list = list.filter(function(c){
            return opts.locked.classes.some(function(l){ return l.grade === grade && l.class_name === c; });
        });
    }
    var bar = $('#gClassBar').empty();
    list.forEach(function(c){
        $('<button type="button" class="btn btn-sm g-class-btn"></button>')
            .attr('data-cls', c).text(c).appendTo(bar);
    });
    if (!list.length){
        bar.append('<span class="text-muted small">本场考试暂无班级</span>');
    }
    // 与原下拉一致：进入/切换考试时默认选第一个班
    state.class_name = list[0] || '';
    paintClassBar();
    fillSubject();
}

/* 班级按钮选中态：当前班=实心主色，其余=描边（与成绩单页按钮同一套观感） */
function paintClassBar(){
    $('#gClassBar .g-class-btn').each(function(){
        var on = $(this).attr('data-cls') === state.class_name;
        $(this).toggleClass('btn-primary', on)
               .toggleClass('btn-outline-secondary', !on)
               .attr('aria-pressed', on ? 'true' : 'false');
    });
}

/* 2026-10-19 用户口径：学科筛选同样改成一排按钮（语文、数学…），
   与班级筛选/成绩单页一致；只在「学科分析」「任课教师」页签显示。 */
function fillSubject(){
    var bar = $('#gSubjectBar').empty();
    var mine = opts.teacher_combos || [];
    var list = [];
    if (state.tab === 'subject'){
        list = opts.subjects.slice();
    } else if (state.tab === 'teacher'){
        // 教师表只做「同学科教师」对比 → 不再提供「全部科目」，
        // 默认落到第一个可选科目；教师角色只列本人任课科目（按钮 title 标注）。
        list = mine.length
            ? opts.subjects.filter(function(s){
                  return mine.some(function(c){ return c.subject === s; });
              })
            : opts.subjects.slice();
    } else {
        return;                       // 其他页签不展示学科筛选
    }
    if (!state.subject || list.indexOf(state.subject) < 0)
        state.subject = list[0] || '';
    list.forEach(function(s){
        var own = (state.tab === 'teacher') && mine.some(function(c){
            return c.subject === s;
        });
        appendSubjectBtn(bar, s, own);
    });
    paintSubjectBar();
}

/* 生成单个学科按钮（own=true 表示教师页签里"仅我任课"的科目） */
function appendSubjectBtn(bar, subject, own){
    $('<button type="button" class="btn btn-sm g-subject-btn"></button>')
        .attr('data-subject', subject)
        .attr('title', own ? '仅我任课的科目' : '')
        .text(subject).appendTo(bar);
}

/* 学科按钮选中态：当前科目=实心主色，其余=描边（与班级按钮同一套观感） */
function paintSubjectBar(){
    $('#gSubjectBar .g-subject-btn').each(function(){
        var on = $(this).attr('data-subject') === state.subject;
        $(this).toggleClass('btn-primary', on)
               .toggleClass('btn-outline-secondary', !on)
               .attr('aria-pressed', on ? 'true' : 'false');
    });
}

/* 2026-10-16 组合维度：本场考试出现过的选科组合 → 「组合」下拉。
   没有选科快照的考试（未分科/老数据）下拉自动隐藏，界面不留无用控件。 */
function fillCombos(){
    var sel = $('#gSel').empty().append('<option value="">全部组合</option>');
    var list = (opts.combos_by_exam && opts.combos_by_exam[String(state.examId)]) || [];
    list.forEach(function(s){ sel.append('<option value="'+s+'">'+s+'</option>'); });
    if (!list.length || !list.includes(state.selection)) state.selection = '';
    sel.val(state.selection);
    syncPopFilter();
}

/* 方向 / 组合的级联：组合本身带方向语义 —— 选了组合就以组合为准，方向下拉置灰；
   取消组合则恢复方向筛选。两者都空 = 全场全部学生。 */
function syncPopFilter(){
    var on = !!state.selection;
    $('#gSel').prop('disabled', false);
    $('#gDir').prop('disabled', on)
              .attr('title', on ? '已按选科组合查看（组合已含方向）' : '方向');
    if (on){ state.direction = ''; $('#gDir').val(''); }
}

function bindFilters(){
    $('#gGrade').on('change', fillExams);
    $('#gExam').on('change', function(){
        state.examId = $(this).val();
        syncExamNav();          // v1.19.0 同步侧栏本场流程链接
        fillClasses();          // v1.19.50.63 班级随考试变：仅列本场考试的班级
        fillCombos();           // 2026-10-16 组合随考试变
        loadCurrent();
    });
    // 班级按钮：点一次即切换（同成绩单页「按班级查看」），重复点当前班不重复请求
    $('#gClassBar').on('click', '.g-class-btn', function(){
        var cls = $(this).attr('data-cls') || '';
        if (!cls || cls === state.class_name) return;
        state.class_name = cls;
        paintClassBar();
        loadCurrent();
    });
    // 学科按钮：点一次即切换（与班级按钮/成绩单页同一交互）
    $('#gSubjectBar').on('click', '.g-subject-btn', function(){
        var sub = $(this).attr('data-subject') || '';
        if (!sub || sub === state.subject) return;
        state.subject = sub;
        paintSubjectBar();
        loadCurrent();
    });
    $('#gDir').on('change', function(){
        state.direction = $(this).val() || '';
        loadCurrent();
    });
    // 2026-10-16 组合筛选：选了组合就以组合为准（方向自动置灰），否则按方向
    $('#gSel').on('change', function(){
        state.selection = $(this).val() || '';
        syncPopFilter();
        loadCurrent();
    });
    // 2026-10-17 统计口径：参考数（实考）↔ 班级实际人数（在籍），切完立即重算
    $('#gDenom').on('change', function(){
        state.denom = $(this).val() || 'ref';
        loadCurrent();
    });
    $('#gExport').on('click', function(){
        var q = buildQuery();
        window.location.href = '/grades/export/' + state.tab + q;
    });
    /* 2026-10-20 一键导出：与当前页签无关 —— 年级/学科/教师一次带走；
       班级逐生明细只在「班级分析」页（明确在看某个班）时才带上。 */
    $('#gExportAll').on('click', function(){
        if (!state.examId){ alert('请先选择考试'); return; }
        var qs = ['exam_id=' + state.examId];
        if (state.tab === 'class' && state.class_name)
            qs.push('class_name=' + encodeURIComponent(state.class_name));
        if (state.classes.length)
            qs.push('classes=' + encodeURIComponent(state.classes.join(',')));
        if (state.direction)
            qs.push('direction=' + encodeURIComponent(state.direction));
        if (state.selection)
            qs.push('selection=' + encodeURIComponent(state.selection));
        if (state.denom && state.denom !== 'ref')
            qs.push('denom=' + state.denom);
        window.location.href = '/grades/export/full?' + qs.join('&');
    });
    // 2026-10-16 汇总打印版：链接跟着当前考试走（考试态 ?exam= 已由服务端带入，
    // 这里兜底用 state.examId；新窗口打开，不影响当前分析页的筛选状态）
    $('#gPrintSummary').attr('href', '/grades/print-summary?exam=' + (state.examId || ''));
    $('#gPrint').on('click', function(){ window.print(); });
    // v1.13.0 汇报区入口已移至左侧菜单栏（base.html），顶部按钮及相关逻辑同步移除
}

function bindTabs(){
    $('#gTabs a[data-bs-toggle="tab"]').on('shown.bs.tab', function(e){
        var t = $(e.target).closest('li').data('tab');
        activateTab(t, true);
    });
}

/* ============ 班级对比：多选控件 ============ */
var MAX_COMPARE = 20;   // 与后端 api_compare_tab 的截断上限一致
// 当前年级的候选班级（优先级同班级分析下拉：本场考试班级）
function classCandidates(){
    var grade = $('#gGrade').val() || state.grade;
    var list = classListFor(grade);
    if (opts.locked.classes && opts.locked.classes.length){
        list = list.filter(function(c){
            return opts.locked.classes.some(function(l){ return l.grade === grade && l.class_name === c; });
        });
    }
    return list;
}

// 默认对比班：本场**全部班级**（2026-10-20 用户口径：对比表要能看全年级，
// 原来是取 1/3 → 11 个班只出 4 个，用户以为数据缺了班级）
function defaultClasses(){
    return classCandidates().slice(0, MAX_COMPARE);
}

// 年级变化时同步候选列表，并把选中值收敛到仍存在的班级
function syncClassCandidate(){
    state.classes = (state.classes || []).filter(function(c){ return classCandidates().indexOf(c) >= 0; });
    if (state.classes.length < 2) state.classes = defaultClasses();
    renderClassList();
}

function renderClassList(){
    var list = classCandidates();
    var $box = $('#gCmList').empty();
    if (!list.length){
        $box.append('<div class="text-muted px-1">本年级暂无班级</div>');
    }
    list.forEach(function(c){
        var checked = state.classes.indexOf(c) >= 0;
        $box.append('<label class="cm-item"><input type="checkbox" value="' + escHtml(c) + '"'
            + (checked ? ' checked' : '') + '><span>' + escHtml(c) + '</span></label>');
    });
    updateClassCount();
}

function updateClassCount(){
    $('#gCmCount').text(state.classes.length);
}

function bindClassMulti(){
    // 复选框变更：保持「候选列表顺序」，避免勾选顺序影响对比表列序；超上限时截断并提示
    $('#gCmList').on('change', 'input[type="checkbox"]', function(){
        var list = classCandidates();
        state.classes = list.filter(function(c){
            return $('#gCmList input[value="' + c + '"]').is(':checked');
        });
        if (state.classes.length > MAX_COMPARE){
            state.classes = state.classes.slice(0, MAX_COMPARE);
            renderClassList();
            alert('一次最多对比 ' + MAX_COMPARE + ' 个班级，已按候选顺序截断');
            return;
        }
        updateClassCount();
        if (state.tab === 'compare') loadCurrent();
    });
    // 快捷操作：只在对比 tab 生效时重新拉数据，避免在其他 tab 触发无意义请求
    [['#gCmAll', function(){ return classCandidates().slice(0, MAX_COMPARE); }],
     ['#gCmClear', function(){ return []; }]].forEach(function(pair){
        $(pair[0]).on('click', function(){
            state.classes = pair[1]();
            renderClassList();
            if (state.tab === 'compare') loadCurrent();
        });
    });
    // 年级切换后候选班变化
    $('#gGrade').on('change', function(){ syncClassCandidate(); });
}

/* ============ 页顶表格 / 图表 总开关 ============ */
function bindViewToggle(){
    try {
        var saved = sessionStorage.getItem('gradesMasterView');
        if (saved === 'chart' || saved === 'table'){
            masterView = saved;
            $('#gViewTable').prop('checked', saved === 'table');
            $('#gViewChart').prop('checked', saved === 'chart');
        }
    } catch (e) {}
    $('#gViewToggle input[name="gView"]').on('change', function(){
        if (this.checked) applyMasterView(this.value);
    });
}

/* 2026-10-19 用户反馈「这俩位置不会同步」：页签 ↔ 侧栏分析组。
   ① 侧栏高亮跟着页签走（此前只有服务端按 ?tab= 渲染一次，页内切页签不动）；
   ② URL 的 tab 跟着页签走（replaceState 不产生历史记录），刷新 / 分享仍停在同一页。
   侧栏不可见（全局态没有分析组）或该页签无权限时直接跳过，不回退到别的项。 */
function syncSidebarTab(tab){
    if (!opts || !opts.tabs || !opts.tabs[tab]) return;
    var $item = $('.efn-item[data-nav="' + ANA_NAV + '"]');
    if (!$item.length) return;
    $('.efn-item[data-nav^="ana_"]').removeClass('active');
    $item.addClass('active');
}

function syncUrlTab(tab){
    try {
        if (!window.history || !window.history.replaceState) return;
        var href = window.location.href;
        var hash = '';
        var hi = href.indexOf('#');
        if (hi >= 0) { hash = href.slice(hi); href = href.slice(0, hi); }
        var qi = href.indexOf('?');
        var base = qi >= 0 ? href.slice(0, qi) : href;
        var parts = qi >= 0 ? href.slice(qi + 1).split('&') : [];
        var out = [], found = false;
        for (var i = 0; i < parts.length; i++) {
            if (!parts[i]) continue;
            if (parts[i].split('=')[0] === 'tab') {
                out.push('tab=' + encodeURIComponent(tab));
                found = true;
            } else {
                out.push(parts[i]);
            }
        }
        if (!found) out.push('tab=' + encodeURIComponent(tab));
        var url = base + '?' + out.join('&') + hash;
        if (url !== window.location.href) {
            window.history.replaceState(null, '', url);
        }
    } catch (e) { /* 隐私模式 / 老浏览器不支持 → 只是地址栏不动，功能不受影响 */ }
}

function activateTab(tab, fromClick){
    state.tab = tab;
    // 筛选器显隐：班级仅班级分析；科目按 tab；班级多选仅对比 tab；
    // 人群（方向/组合）用于年级/学科/班级分析（2026-10-16 班级分析也支持按组合）
    // 班级按钮组（班级分析专用）随 tab 显隐
    $('#gClassBar').toggleClass('d-none', tab !== 'class');
    $('#gClassMulti').toggleClass('d-none', tab !== 'compare');
    var _popOn = (tab === 'grade' || tab === 'subject' || tab === 'class');
    $('#gPopWrap').toggleClass('d-none', !_popOn);
    $('#gSel').toggleClass('d-none', !_popOn);
    // 修复：原条件写反——学科/教师分析 tab 需要显示科目筛选器，年级/班级 tab 隐藏
    $('#gSubjectBar').toggleClass('d-none', tab !== 'subject' && tab !== 'teacher');
    if (tab === 'subject' || tab === 'teacher') fillSubject();
    if (tab === 'compare') renderClassList();
    $('#gTabs a[href="#pane-'+tab+'"]').tab('show');
    // 班级只随 tab 显隐，**绝不清空其值**：清空会让后端查不到班级 →
    // 前端误报"该考试尚未导入成绩"。state.class_name 由 fillClasses/点击按钮维护。
    // （buildQuery 仅在 tab==='class' 时带 class_name，其他 tab 保留该值无副作用）
    syncUrlTab(tab);
    syncSidebarTab(tab);
    loadCurrent();
}

/* ---------------- 数据加载与渲染 ---------------- */
// 重新渲染某 tab 前释放其图表实例：该 tab 的 DOM 已被 empty() 移除，实例留着只会泄漏 + resize 报错
function disposePaneCharts(tab){
    var pane = $('#pane-' + tab);
    var root = pane[0];
    Object.keys(charts).forEach(function(k){
        var inst = charts[k];
        if (!inst) { delete charts[k]; return; }
        try {
            var dom = inst.getDom ? inst.getDom() : null;
            if (!dom || (root && $.contains(root, dom))){
                if (!inst.isDisposed()) inst.dispose();
                delete charts[k];
            }
        } catch (e) { delete charts[k]; }
    });
}

// 2026-10-12 UI 审计 H2：分析页 5 个页签共用一个加载骨架，原先 loadCurrent() 只清
// **当前** tab，快速连点 5 个页签会并发 5 个 $.getJSON，后返回的覆盖先返回的 →
// 出现「图表/表格串 tab」的竞态。这里加递增序号（与 report.js / report_pivot.js 同做法），
// 只有最后一次请求的回调才允许渲染。
var _loadSeq = 0;

function loadCurrent(){
    var pane = $('#pane-' + state.tab);
    disposePaneCharts(state.tab);
    pane.find('.tab-content-area').empty().addClass('d-none');
    pane.find('.empty-state').addClass('d-none');
    // 班级分析未选班级时直接提示，不发无意义请求（否则后端返回空，会误报成"未导入成绩"）
    if (state.tab === 'class' && !state.class_name){
        showEmpty(state.tab, '请先在顶部筛选栏选择班级');
        return;
    }
    // 班级对比至少要有 2 个班
    if (state.tab === 'compare' && state.classes.length < 2){
        showEmpty(state.tab, '请至少选择 2 个班级：点击顶部「选择对比班级」勾选');
        return;
    }
    pane.find('.tab-pane-loading').removeClass('d-none');
    var url = '/grades/api/analysis/' + state.tab + buildQuery();
    var my = ++_loadSeq;
    $.getJSON(url, function(res){
        if (my !== _loadSeq) return;      // 已有更新的请求，丢弃本次结果
        pane.find('.tab-pane-loading').addClass('d-none');
        var data = res.data || {};
        var area = pane.find('.tab-content-area').removeClass('d-none');
        if (!data || data.meta && data.meta.empty || !Object.keys(data.tables || {}).length){
            showEmpty(state.tab, state.tab === 'compare'
                ? '所选班级暂无成绩数据（或本场该班无参考学生）'
                : '暂无数据：该考试尚未导入成绩（或本场无参考学生）');
            return;
        }
        area.empty();
        var info = buildExamInfo(data);
        if (info) area.append(info);
        renderTables(area, data.tables || {});
        renderCharts(area, data.charts || {});
    }).fail(function(xhr){
        if (my !== _loadSeq) return;
        pane.find('.tab-pane-loading').addClass('d-none');
        var msg = (xhr && xhr.responseJSON && xhr.responseJSON.message)
            || '数据加载失败（或无权查看该数据）';
        showEmpty(state.tab, msg);
    });
}

// R-2：转义统一复用公共实现（static/js/common.js，base.html 全局引入），
// 此处仅在页面未加载 common.js 时兜底，避免多份实现漂移。
var escHtml = window.escHtml || function (s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
        return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
    });
};

function buildExamInfo(data){
    // 2026-10-19 用户口径：内容区不再重复「当前考试：…」提示条
    // （考试态页头已写明本场、全局态顶部有考试下拉，此条属冗余）。
    // 分批导入（一次导一科）时，总分只是「已导入科目合计」，必须显式提示避免误读。
    var p = data.meta && data.meta.partial;
    if (!p) return null;
    var wrap = $('<div>');
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
    return wrap;
}

function showEmpty(tab, msg){
    var pane = $('#pane-' + tab);
    pane.find('.tab-pane-loading').addClass('d-none');
    pane.find('.tab-content-area').empty().addClass('d-none');
    pane.find('.empty-state p').text(msg);
    pane.find('.empty-state').removeClass('d-none');
}

/* ============ 指标卡双视图：表格 / 图表 ============ */
// 每张指标卡默认视图（可被页顶总开关批量改写）。
// 2026-10-19 用户口径：默认「图 + 表」同屏（有图的卡片上方画图、下方紧凑数据表），
// 页顶总开关「表格」只用于需要纯表格（打印 / 抄数）时整体切换。
var masterView = 'chart';

function isNumVal(v){
    if (v === null || v === undefined || v === '') return false;
    return !isNaN(Number(v));
}

/* 由表格的 columns+rows 推导图表规格；无法成图返回 null（此时卡片只显示「表格」Tab）。
   优先级：后端 chartHint（完整规格） > chartHint 部分字段 > 自动推断。 */
function autoChartSpec(t, key){
    var hint = t.chartHint || null;
    // 后端显式 noChart（如「班级学生各科成绩表」）→ 该卡只出表格，不画图
    if (t.noChart || hint === false) return null;
    // hint 带 xAxis+series（或 indicators）= 完整规格，直接用
    if (hint && ((hint.xAxis && hint.series) || hint.indicators)){
        return $.extend({title: t.title || key}, hint);
    }
    var cols = t.columns || [], rows = t.rows || [];
    if (!cols.length || !rows.length) return null;
    // xKey / seriesKeys 优先取后端 hint，其次自动推断（所有含数值的列）
    var xKey = hint && hint.xKey ? hint.xKey : cols[0].key;
    var topN = (hint && hint.topN) || 0;
    var seriesKeys = (hint && hint.seriesKeys) || null;
    if (!seriesKeys){
        seriesKeys = [];
        cols.forEach(function(c){
            if (c.key === xKey) return;
            // 分层人数列（l_0…）、单/双上线人数列（lo_0_n…）与均分口径不同，
            // 混在同一张图里量级差太大，不自动入图（这些指标各自另有表/图）
            if (/^l_\d+$/.test(c.key) || /^lo_\d+/.test(c.key)) return;
            if (c.type === 'num' || c.type === 'int' || rows.some(function(r){ return isNumVal(r[c.key]); })){
                seriesKeys.push(c.key);
            }
        });
    }
    if (!seriesKeys.length) return null;
    // 行数过多（如每生一行的明细表）只画前 N 行，避免柱子挤成一片
    var view = rows;
    if (topN && rows.length > topN) view = rows.slice(0, topN);
    var labels = {}, byKey = {};
    cols.forEach(function(c){ labels[c.key] = c.label || c.key; });
    var limit = (hint && hint.maxSeries) || 12;
    var useKeys = seriesKeys.slice(0, limit);
    seriesKeys.forEach(function(k){ byKey[k] = []; });
    view.forEach(function(r){
        seriesKeys.forEach(function(k){ byKey[k].push(isNumVal(r[k]) ? Number(r[k]) : null); });
    });
    var xAxis = view.map(function(r){ return fmt(r[xKey]); });
    var type = (hint && hint.type) || (useKeys.length === 1 ? 'bar' : 'groupbar');
    // 分段表（分数段/名次段：每班一列 c_xxx）自动改堆叠，并把合计/占比小数列排除在堆叠之外
    var segKeys = seriesKeys.filter(function(k){ return /^c\d/.test(k) || /^c_/.test(k); });
    if (!hint || !hint.type){
        if (segKeys.length >= 2){
            useKeys = segKeys.slice(0, limit);
            seriesKeys = segKeys;
            type = 'stack';
        }
    }
    var spec = {
        title: t.title || key,
        type: type,
        xAxis: xAxis,
        series: useKeys.map(function(k){ return {name: labels[k] || k, data: byKey[k]}; })
    };
    if (seriesKeys.length > limit) spec.note = '仅显示前 ' + limit + ' 个数据列';
    return spec;
}

/* 当前生效的列（列显隐组件过滤后；未设置过 = 全部列）。
   注意不要改 t.columns 本身——图表 autoChartSpec 依赖全列口径。 */
function visColsOf(t){ return t.visCols || t.columns || []; }

function buildTbody(t, rows){
    var tbody = $('<tbody>');
    (rows || []).forEach(function(r){
        var tr = $('<tr>');
        visColsOf(t).forEach(function(c){
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
    return tbody;
}

/* 按列排序；空值（—）恒排末尾，文本按中文排序；返回新数组 */
function sortRows(rows, key, type, dir){
    var numeric = (type === 'num' || type === 'int');
    var arr = (rows || []).slice();
    arr.sort(function(a, b){
        if (numeric){
            var va = a[key], vb = b[key];
            var na = (va === null || va === undefined || va === '') ? null : Number(va);
            var nb = (vb === null || vb === undefined || vb === '') ? null : Number(vb);
            if (na === null && nb === null) return 0;
            if (na === null) return 1;
            if (nb === null) return -1;
            return dir === 'asc' ? na - nb : nb - na;
        }
        var sa = fmt(a[key]), sb = fmt(b[key]);
        return dir === 'asc' ? sa.localeCompare(sb, 'zh-Hans-CN')
                             : sb.localeCompare(sa, 'zh-Hans-CN');
    });
    return arr;
}

/* 刷新整张卡：表格行重排 + 排序图标更新 + 图表按同一份行数据重绘（保持表图同源） */
function applyCardRows(card, rows){
    var t = card.data('table');
    var key = card.data('tableKey');
    card.data('rows', rows);
    var spec = autoChartSpec($.extend({}, t, {rows: rows}), key);
    card.data('spec', spec);
    card.find('.tbl-wrap table tbody').replaceWith(buildTbody(t, rows));
    // 图表视图下方的紧凑表与上方图共用同一份行数据 → 排序/列显隐后一起刷新
    card.find('.chart-data table tbody').replaceWith(buildTbody(t, rows));
    updateSortHead(card);
    var tip = spec ? (spec.title + (spec.note ? '（' + spec.note + '）' : '')) : '';
    card.find('.chart-tip').text(tip ? '图表数据见右侧数据表：' + tip : '');
    if (card.data('chartReady')){
        var inst = charts[card.data('chartKey')];
        if (inst && spec){
            try { inst.setOption(chartOption(spec), true); } catch (e) {}
        }
    }
}

function updateSortHead(card){
    var key = card.data('sortKey'), dir = card.data('sortDir');
    card.find('.tbl-wrap thead th.sortable').each(function(){
        var isThis = $(this).data('key') === key;
        $(this).removeClass('sorted-asc sorted-desc');
        if (isThis && dir) $(this).addClass(dir === 'asc' ? 'sorted-asc' : 'sorted-desc');
        $(this).find('.th-sort').attr('class', 'bi th-sort ' + (
            !isThis || !dir ? 'bi-arrow-down-up'
            : dir === 'asc' ? 'bi-sort-up-alt' : 'bi-sort-down'));
    });
}

function buildThead(t){
    var thead = $('<thead><tr></tr></thead>');
    visColsOf(t).forEach(function(c){
        thead.find('tr').append(
            '<th class="sortable' + (c.type !== 'text' ? ' num' : '') + '" data-key="'
            + escHtml(c.key) + '" data-type="' + escHtml(c.type || 'text') + '">'
            + escHtml(c.label) + '<i class="bi bi-arrow-down-up th-sort"></i></th>');
    });
    return thead;
}

/* 表头排序绑定：升序 → 降序 → 还原原始顺序（数据、图表同份，导出仍取后端原序）。
   列显隐重建表头后需重新绑定（旧 th 随 replaceWith 一起移除，事件不残留） */
function bindSortHead(card){
    card.find('.tbl-wrap thead th.sortable').on('click', function(){
        var ckey = $(this).data('key'), ctype = $(this).data('type');
        var curKey = card.data('sortKey'), curDir = card.data('sortDir');
        var dir = (curKey === ckey && curDir === 'asc') ? 'desc' : 'asc';
        if (curKey === ckey && curDir === 'desc'){
            card.data('sortKey', null).data('sortDir', null);
            applyCardRows(card, card.data('baseRows').slice());
            return;
        }
        card.data('sortKey', ckey).data('sortDir', dir);
        applyCardRows(card, sortRows(card.data('baseRows'), ckey, ctype, dir));
    });
}

function buildTableHtml(t){
    var table = $('<table class="table table-hover g-table mb-0">');
    table.append(buildThead(t));
    table.append(buildTbody(t, t.rows || []));
    return table;
}

/* 图表视图下方的「紧凑数据表」（2026-10-19 用户口径：上方图、下方表的数据，
   表格紧凑一点）。与上方图同源（同一份 rows），跟随列显隐/排序刷新；
   本身不绑排序交互（排序仍走「表格」视图），表头图标由 CSS 隐藏。 */
function buildCompactTable(t){
    var table = $('<table class="table g-table g-table-compact mb-0">');
    table.append(buildThead(t));
    table.append(buildTbody(t, t.rows || []));
    return table;
}

/* 切换单卡的视图：'table' | 'chart'。图表首次切到时才初始化，之后仅显隐。 */
function setCardView(card, view){
    var spec = card.data('spec');
    if (view === 'chart' && !spec) view = 'table';   // 无图卡片不能被切成空白
    var tPane = card.find('.view-pane[data-view="table"]');
    var cPane = card.find('.view-pane[data-view="chart"]');
    card.find('.view-switch .btn').each(function(){
        $(this).toggleClass('active', $(this).data('view') === view);
    });
    if (view === 'chart'){
        tPane.attr('hidden', 'hidden');
        cPane.removeAttr('hidden');
        card.data('view', 'chart');
        if (spec && !card.data('chartReady')){
            // 先占位防止重复排队；初始化延迟到容器完成布局（渲染首帧容器宽高可能仍为 0，
            // echarts 会把 canvas 画成 0×0），每帧复查尺寸，就绪后才 init
            card.data('chartReady', true);
            var tries = 0;
            var tryInit = function(){
                var box = cPane.find('.tbl-chart')[0];
                if (!box || !box.isConnected || typeof echarts === 'undefined') return;
                if ((!box.clientWidth || !box.clientHeight) && ++tries < 30){
                    requestAnimationFrame(tryInit);
                    return;
                }
                var ck = card.data('chartKey');
                if (charts[ck]){ try { charts[ck].dispose(); } catch (e) {} }
                renderChart(box, ck, spec);   // 内部完成 echarts.init 并登记到 charts
                var inst = charts[ck];
                if (inst) inst.resize();
            };
            if (typeof requestAnimationFrame === 'function'){
                requestAnimationFrame(tryInit);
            } else {
                setTimeout(tryInit, 0);
            }
        } else if (card.data('chartReady')){
            var inst = charts[card.data('chartKey')];
            if (inst) inst.resize();
        }
    } else {
        cPane.attr('hidden', 'hidden');
        tPane.removeAttr('hidden');
        card.data('view', 'table');
    }
}

function renderTables(area, tables){
    var wrap = $('<div>');
    Object.keys(tables).forEach(function(key){
        var t = tables[key];
        var spec = autoChartSpec(t, key);
        var card = $('<div class="card tbl-card mb-3">');
        var ck = state.tab + '::' + key;
        card.data({view: masterView, spec: spec, chartKey: ck, chartReady: false,
                   table: t, tableKey: key, rows: t.rows || [],
                   baseRows: (t.rows || []).slice(), sortKey: null, sortDir: null});
        // 列显隐（勾选即显示、不勾选隐藏；选择按表持久化，见 table_cols.js）
        var colsAll = t.columns || [];
        if (window.TableCols){
            var visKeys = TableCols.visible(ck, colsAll);
            t.visCols = colsAll.filter(function(c){ return visKeys.indexOf(c.key) >= 0; });
        }

        var titleHtml = '<h6 class="mb-0"><i class="bi bi-table me-1"></i>'
            + escHtml(t.title || key) + '</h6>';

        var controls = $('<div class="d-flex align-items-center gap-2">');
        // 2026-10-19 用户口径：卡片头的「表格 | 图表」切换去掉 ——
        // 有图的卡片固定「上方图 + 下方紧凑数据表」，无图的仍是纯表格；
        // 需要整体只看表格时用页顶总开关。
        if (window.TableCols){
            controls.append(TableCols.menu(ck, colsAll, function(keys){
                t.visCols = colsAll.filter(function(c){ return keys.indexOf(c.key) >= 0; });
                // 重建表头 + 行（当前行序保持；排序状态与图表同源不变）
                // 图表视图下方的紧凑表也要跟着换表头（同一份列显隐口径）
                var $tbl = card.find('.tbl-wrap table').first();
                $tbl.find('thead').replaceWith(buildThead(t));
                card.find('.chart-data table thead').replaceWith(buildThead(t));
                bindSortHead(card);
                applyCardRows(card, card.data('rows') || []);
            }));
        }
        controls.append('<button class="btn btn-sm btn-outline-secondary tbl-fold">折叠</button>');
        card.append($('<div class="card-header d-flex justify-content-between align-items-center">')
            .append(titleHtml).append(controls));

        var body = $('<div class="card-body p-0">');
        body.append($('<div class="tbl-wrap view-pane" data-view="table">').append(buildTableHtml(t)));
        if (spec){
            body.append($('<div class="view-pane chart-pane" data-view="chart" hidden>')
                .append('<div class="chart-tip px-3 pt-2 pb-1">图表数据见右侧数据表：'
                    + escHtml(t.title || key)
                    + (spec.note ? '（' + escHtml(spec.note) + '）' : '') + '</div>')
                .append('<div class="tbl-chart"></div>')
                .append($('<div class="chart-data">').append(buildCompactTable(t))));
        }
        card.append(body);

        card.find('.view-switch .btn').on('click', function(){
            setCardView(card, $(this).data('view'));
        });
        // 表头排序：升序 → 降序 → 还原原始顺序（数据、图表同份，导出仍取后端原序）
        bindSortHead(card);
        // 只有真的能出图的卡片才切图表视图，否则会把表格藏起来、卡片变空白
        if (masterView === 'chart' && spec) setCardView(card, 'chart');
        card.find('.tbl-fold').on('click', function(){
            var visible = body.is(':visible');
            body.toggle();
            if (!visible && card.data('view') === 'chart'){
                var inst = charts[ck];
                if (inst) inst.resize();
            }
            $(this).text(visible ? '展开' : '折叠');
        });
        wrap.append(card);
    });
    area.append(wrap);
}

/* 页顶总开关：批量切换当前模块所有指标卡（图表仍为首次可见时才初始化） */
function applyMasterView(view){
    masterView = view;
    try { sessionStorage.setItem('gradesMasterView', view); } catch (e) {}
    var pane = $('#pane-' + state.tab);
    pane.find('.tbl-card').each(function(){
        var card = $(this);
        var spec = card.data('spec');
        if (view === 'chart' && spec) setCardView(card, 'chart');
        else setCardView(card, 'table');
    });
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

function chartOption(c){
    switch (c.type) {
        case 'bar': return barOption(c, false);
        case 'groupbar': return barOption(c, true);
        case 'stack': return stackOption(c);
        case 'boxplot': return boxOption(c);
        case 'line': return lineOption(c);
        case 'dual': return dualOption(c);
        case 'combo': return comboOption(c);
        case 'radar': return radarOption(c);
        case 'heatmap': return heatOption(c);
        default: return {};
    }
}

function renderChart(dom, key, c){
    var chart = echarts.init(dom);
    charts[key] = chart;
    chartSpecs[key] = c;   // 主题切换时用它按新色板重绘
    var opt = chartOption(c);
    /* 2026-10-20 性能：切页签时一次要画 2~4 张图，带入场动画会明显"顿"一下，
       关掉动画直接出结果（数据没变，老师不需要看生长过程）。 */
    if (opt && opt.animation === undefined) { opt.animation = false; }
    chart.setOption(opt, true);
}

/* 2026-10-14：切换主题后按新调色板重设已渲染的图表（notMerge，颜色整体替换）。
   未切换主题时不会触发，零开销。 */
function repaintCharts(){
    Object.keys(charts).forEach(function(k){
        var c = chartSpecs[k];
        if (!c) return;
        try { charts[k].setOption(chartOption(c), true); } catch (e) {}
    });
}
if (window.stuOnThemeChange) { window.stuOnThemeChange(repaintCharts); }

function baseGrid(){ return {left: 60, right: 40, top: 50, bottom: 40, containLabel: true}; }
function colorOf(i){ var p = palette(); return p[i % p.length]; }
/* 热力图渐变：深色为深→亮逆序（--sl-heat-1..4），浅色保持原浅蓝→深蓝 */
function heatColors(){
    return (window.stulinkHeatColors && window.stulinkHeatColors()) ||
           ['#eff6ff', '#93c5fd', '#3b82f6', '#1d4ed8'];
}
function heatLabel(){
    return (window.stulinkHeatLabel && window.stulinkHeatLabel()) || '#0f172a';
}
function yFmt(){ return {type: 'value', axisLabel: {formatter: '{value}'}}; }

/* 横轴类目多时倾斜标签（班级/科目/日期一多就会糊成一片） */
function axisRotate(c){
    var n = (c.xAxis || []).length;
    if (n <= 8) return 0;
    return n <= 20 ? 30 : 45;
}

function hasZoom(c){ return (c.xAxis || []).length > 8; }

/* 类目轴：倾斜 + 全部显示（配合 hideOverlap 兜底） */
function catAxisOf(c){
    return {type: 'category', data: c.xAxis || [],
        axisLabel: {interval: 0, rotate: axisRotate(c), fontSize: 11, hideOverlap: true},
        axisTick: {alignWithLabel: true}};
}

/* 缩放：滚轮/双指缩放 + 类目多时给底部滑块（可拖动平移）；初始展示约 24 个类目 */
function zoomOf(c){
    var n = (c.xAxis || []).length;
    if (n <= 8) return [];
    var end = Math.round(100 * Math.min(n, 24) / n);
    var dz = [{type: 'inside', xAxisIndex: 0, start: 0, end: end,
               zoomOnMouseWheel: true, moveOnMouseMove: true, moveOnMouseWheel: false}];
    if (n > 15){
        dz.push({type: 'slider', xAxisIndex: 0, height: 14, bottom: 4,
                 start: 0, end: end, brushSelect: false, showDetail: false});
    }
    return dz;
}

/* 旋转 + 滑块都要占用底部空间，避免标签/滑块被裁掉 */
function gridOf(c){
    var rot = axisRotate(c);
    var base = rot >= 45 ? 72 : (rot ? 56 : 40);
    return {left: 60, right: 40, top: 50, containLabel: true,
            bottom: base + ((c.xAxis || []).length > 15 ? 24 : 0)};
}

function barOption(c, grouped){
    var series = (c.series || []).map(function(s, i){
        var item = {name: s.name, type: 'bar', data: s.data, itemStyle: {color: colorOf(i)}};
        if (!grouped && i === 0){
            // 年级均值参考线 + 该科各层划线参考线
            var ml = [];
            if (c.markLine != null){
                ml.push({yAxis: c.markLine, lineStyle: {color: sem().warn, type: 'dashed'},
                    label: {formatter: '年级均值 {c}'}});
            }
            (c.bandLines || []).forEach(function(b){
                ml.push({yAxis: b.value, lineStyle: {color: sem().danger, type: 'dashed'},
                    label: {formatter: b.name + ' {c}'}});
            });
            if (ml.length){ item.markLine = {silent: true, symbol: 'none', data: ml}; }
        }
        return item;
    });
    return {color: palette(), tooltip: {trigger: 'axis'}, legend: grouped ? {top: 0} : undefined,
        grid: gridOf(c), xAxis: catAxisOf(c), dataZoom: zoomOf(c),
        yAxis: yFmt(), series: series};
}

function stackOption(c){
    // 2026-10-19 左图右表后图宽只有卡片的 42%，默认 12px 图例放不下 8 个名次段
    // → 图例字号/间距压小，仍保留 scroll 分页兜底
    return {tooltip: {trigger: 'axis'},
        legend: {top: 0, type: 'scroll', itemGap: 8, itemWidth: 10,
                 itemHeight: 10, textStyle: {fontSize: 10}},
        grid: gridOf(c), xAxis: catAxisOf(c), dataZoom: zoomOf(c),
        yAxis: yFmt(),
        series: (c.series || []).map(function(s, i){
            return {name: s.name, type: 'bar', stack: 'total', data: s.data,
                itemStyle: {color: colorOf(i)}};
        })};
}

function boxOption(c){
    return {tooltip: {trigger: 'item'}, grid: gridOf(c), xAxis: catAxisOf(c),
        dataZoom: zoomOf(c),
        yAxis: yFmt(),
        series: (c.series || []).map(function(s){
            return {name: s.name || '总分分布', type: 'boxplot', data: s.data || [],
                itemStyle: {color: palette()[0]}};
        })};
}

function lineOption(c){
    // selected 是后端可选的「默认勾选哪几条系列」映射（如年级多科折线只默认显示总分+主科）。
    // 未提供该字段时，所有系列必须默认全部显示——否则 !!undefined=false 会把折线全隐藏，
    // 图例变灰、画布空白，需手动点图例才出图（班级走势/教师走势即属此列）。
    var sel = c.selected;
    var hasSel = !!(sel && Object.keys(sel).length);
    var selected = {};
    (c.series || []).forEach(function(s){ selected[s.name] = hasSel ? !!sel[s.name] : true; });
    return {color: palette(), tooltip: {trigger: 'axis'},
        legend: {top: 0, type: 'scroll', selected: selected},
        grid: gridOf(c), xAxis: catAxisOf(c), dataZoom: zoomOf(c),
        yAxis: yFmt(),
        series: (c.series || []).map(function(s){
            return {name: s.name, type: 'line', data: s.data, connectNulls: false,
                smooth: true, symbolSize: 5};
        })};
}

function dualOption(c){
    return {color: palette(), tooltip: {trigger: 'axis'},
        legend: {top: 0},
        grid: gridOf(c), xAxis: catAxisOf(c), dataZoom: zoomOf(c),
        yAxis: [{type: 'value', name: '均分'}, {type: 'value', name: '%', max: 100}],
        series: (c.series || []).map(function(s){
            return {name: s.name, type: 'line', data: s.data, yAxisIndex: s.yAxis || 0,
                smooth: true, symbolSize: 5};
        })};
}

/* 柱+折线组合（2026-10-19 年级汇总表）：人数走左轴柱形，率% 走右轴折线。 */
function comboOption(c){
    var names = c.yAxisNames || ['人数', '上线率%'];
    return {color: palette(), tooltip: {trigger: 'axis'},
        legend: {top: 0, type: 'scroll'},
        grid: gridOf(c), xAxis: catAxisOf(c), dataZoom: zoomOf(c),
        yAxis: [
            {type: 'value', name: names[0], axisLabel: {formatter: '{value}'}},
            {type: 'value', name: names[1], max: 100,
             axisLabel: {formatter: '{value}%'}}
        ],
        series: (c.series || []).map(function(s){
            var yi = s.axis === 1 ? 1 : 0;
            if (s.type === 'line'){
                return {name: s.name, type: 'line', yAxisIndex: yi,
                    data: s.data, smooth: true, symbolSize: 6,
                    connectNulls: false};
            }
            return {name: s.name, type: 'bar', yAxisIndex: yi, data: s.data};
        })};
}

function radarOption(c){
    return {color: palette(), tooltip: {},
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
    // 热力图底部被 visualMap 占用，故只开滚轮/拖拽缩放，不加滑块；标签按列数倾斜
    var rot = xs.length > 20 ? 45 : (xs.length > 8 ? 30 : 0);
    var hz = xs.length > 8 ? [{type: 'inside', xAxisIndex: 0, zoomOnMouseWheel: true,
                               moveOnMouseMove: true}] : [];
    return {tooltip: {position: 'top',
                formatter: function(p){ return xs[p.value[0]] + ' · ' + ys[p.value[1]]
                    + '<br/>平均分：' + p.value[2]; }},
        grid: {left: 60, right: 40, top: 40, bottom: rot >= 45 ? 110 : 88},
        xAxis: {type: 'category', data: xs,
                axisLabel: {interval: 0, rotate: rot, fontSize: 10, hideOverlap: true}},
        yAxis: {type: 'category', data: ys},
        dataZoom: hz,
        visualMap: {min: Math.floor(min), max: Math.ceil(max), calculable: true,
            orient: 'horizontal', left: 'center', bottom: 0,
            inRange: {color: heatColors()}},
        series: [{type: 'heatmap', data: cells,
            label: {show: true, fontSize: 10, color: heatLabel()}}]};
}

function buildQuery(){
    var qs = [];
    if (state.examId) qs.push('exam_id=' + state.examId);
    if (state.tab === 'class' && state.class_name) qs.push('class_name=' + encodeURIComponent(state.class_name));
    if (state.tab === 'compare' && state.classes.length)
        qs.push('classes=' + encodeURIComponent(state.classes.join(',')));
    if ((state.tab === 'subject' || state.tab === 'teacher') && state.subject)
        qs.push('subject=' + encodeURIComponent(state.subject));
    if ((state.tab === 'grade' || state.tab === 'subject' || state.tab === 'class')
            && state.direction)
        qs.push('direction=' + encodeURIComponent(state.direction));
    // 2026-10-16 组合维度：年级/学科/班级分析都支持按选科组合收窄
    if ((state.tab === 'grade' || state.tab === 'subject' || state.tab === 'class')
            && state.selection)
        qs.push('selection=' + encodeURIComponent(state.selection));
    // 2026-10-17 统计口径：所有 tab 都带（enrolled 时人数/率/均分按班级在籍人数算）
    if (state.denom && state.denom !== 'ref') qs.push('denom=' + state.denom);
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
        // 2026-10-20：复用已有实例 —— 每次 new Modal 都会再挂一套事件监听与
        // 一个 backdrop 节点（Bootstrap 不允许同元素多实例，Data.set 会静默失败），
        // AI 面板开 20 次就多 60 个监听、20 个节点。
        var el = document.getElementById(aiModalId);
        var m = bootstrap.Modal.getOrCreateInstance(el);
        if (m._isTransitioning) {
            // 关闭动画还没走完时 show() 会被 Bootstrap 直接 return（点了像没反应），
            // 复用实例后必须等 hidden 再显示 —— 原先是 new 一个新对象绕过去的。
            el.addEventListener('hidden.bs.modal', function once(){
                el.removeEventListener('hidden.bs.modal', once);
                m.show();
            });
        } else {
            m.show();
        }
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

    // v1.19.0 个人 Key 已统一到「用户设置」（只存本机浏览器，不上服务器）；
    // 这里不再提供个人 Key 表单，只展示本机配置状态 + 跳转入口。
    function personalKeyNotice(){
        var api = window.StuLinkAI || null;
        var ok = api && api.has();
        var c = (api && api.get()) || {};
        return '<div class="alert ' + (ok ? 'alert-success' : 'alert-warning')
            + ' small py-2"><i class="bi '
            + (ok ? 'bi-check-circle' : 'bi-exclamation-triangle') + '"></i> '
            + (ok ? ('已配置本机 AI：' + esc(c.model || '（默认模型）'))
                  : '未配置 AI Key，AI 功能不可用')
            + '　<a href="/my/settings#ai" class="btn btn-sm btn-outline-primary ms-1 py-0">'
            + '去用户设置配置</a>'
            + '<div class="text-muted mt-1">为保护隐私，个人 Key 只保存在你自己的浏览器，'
            + '不上传服务器；换电脑或清缓存后需重新填写。</div></div>';
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
                    isGlobal ? keyFormHtml(k, isGlobal) : personalKeyNotice(), foot);
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
        // v1.19.0 所有 AI 链路统一入口：
        // ① 未配置“本机浏览器里的 Key”时先提醒并引导去用户设置（不再用服务器存的私人 Key）
        // ② 已配置则把 Key 随请求带上（服务器只用不存）
        if (url.indexOf('/grades/ai/') === 0){
            if (!window.StuLinkAI || !window.StuLinkAI.ensure()){
                // 2026-10-16 第八轮审计（U-19）：原实现直接 return，而调用方
                // （runAnalyze 等）已把按钮置 disabled、aiBusy=true → 没配 Key 时点一次
                // 「AI 分析」，按钮从此永久变灰、aiBusy 卡死连追问也全被挡。
                // ensure() 自己已弹引导框，这里只补收尾回调（恢复按钮与 busy 态）。
                handlers.onDone && handlers.onDone();
                return;
            }
            payload = $.extend({}, payload, {ai: window.StuLinkAI.payload()});
        }
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
            // 2026-10-16 第八轮审计（U-20）：原先只报 onError、不收尾 → 网络异常时
            // 按钮与 aiBusy 同样卡死（与 U-19 同一后果），这里成对回调。
            handlers.onError && handlers.onError((e && e.message) || '网络异常');
            handlers.onDone && handlers.onDone();
        });
    }

    // ---- 图表渲染（ECharts，数据由后端本地统计，非模型生成）----
    // 2026-10-14：改读主题调色板（AI 生成的图表同样要跟随深色，不能永远浅色字面量）
    var AI_PALETTE_FALLBACK = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#06b6d4'];
    function aiPalette(){
        var p = (window.stulinkPalette && window.stulinkPalette()) || AI_PALETTE_FALLBACK;
        // 语义槽位对齐原顺序：蓝 / 绿 / 橙 / 红 / 紫 / 青 → 主题色板 1/3/4/5/2/6
        return [p[0], p[2], p[3], p[4], p[1], p[5]].filter(function(c){ return !!c; });
    }
    var aiCharts = [];   // {chart, spec} —— 主题切换时按新色板重绘
    var aiResizeBound = false;
    /* resize 只绑一次，遍历当前存活实例 —— 原先是每张图各绑一个匿名函数，
       重绘后旧图的监听还留在 window 上（既泄漏闭包，又对已销毁实例调 resize）。 */
    function bindAiResize(){
        if (aiResizeBound) { return; }
        aiResizeBound = true;
        window.addEventListener('resize', function(){
            aiCharts.forEach(function(x){
                try { if (!x.chart.isDisposed()) { x.chart.resize(); } } catch (e) {}
            });
        });
    }
    function aiChartOption(s){
        var P = aiPalette();
        var opt;
        if (s[2] === 'pie'){
            opt = {
                tooltip: {trigger: 'item'},
                legend: {bottom: 0, itemWidth: 10, itemHeight: 10, textStyle: {fontSize: 11}},
                color: P,
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
                    itemStyle: {color: P[0], borderRadius: [4, 4, 0, 0]},
                    label: {show: true, position: 'top', fontSize: 10}
                }]
            };
        }
        return opt;
    }
    if (window.stuOnThemeChange) {
        window.stuOnThemeChange(function () {
            aiCharts.forEach(function (x) {
                try { x.chart.setOption(aiChartOption(x.spec), true); } catch (e) {}
            });
        });
    }
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
        // 2026-10-20：重绘前销毁上一轮实例 —— 每跑一次分析都会整块替换 #aiChatCharts，
        // 不 dispose 的话旧 ECharts 实例连同 canvas 会留在 echarts 的全局实例表里，
        // 连跑十次就是几十个图表对象（约占几十 MB），属于真泄漏。
        var host = $(container)[0];
        aiCharts = aiCharts.filter(function(x){
            var dom = x.chart.getDom ? x.chart.getDom() : null;
            // 每点一次「AI 分析」都会重建弹窗 body（showAi 里 .html(...)），
            // 旧图的 dom 早已脱离文档 —— 用 contains(document) 而不是 contains(host)，
            // 否则跨轮次的实例永远清不掉。
            var gone = !dom || !document.documentElement.contains(dom);
            if (gone || (host && $.contains(host, dom))) {
                try { x.chart.dispose(); } catch (e) {}
                return false;
            }
            return true;
        });
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
                    color: aiPalette(),
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
                        itemStyle: {color: aiPalette()[0], borderRadius: [4, 4, 0, 0]},
                        label: {show: true, position: 'top', fontSize: 10}
                    }]
                };
            }
            chart.setOption(opt);
            aiCharts.push({chart: chart, spec: s});
            bindAiResize();
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
                        + '也可联系管理员配置公共 Key 后免填。</p>' + personalKeyNotice(),
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
            // 2026-10-13（v1.19.50.63）：原提示「已保存到 AI 报告历史」是假的 ——
            // 这里只把报告插入页面，并不写库；报告只有在「AI 分析」成功时才自动落库，
            // 聊天追问生成的内容不会进历史。文案必须与事实一致，别误导用户。
            showReport(txt, '已插入到当前页签（仅「AI 分析」生成的报告会存入报告历史）');
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
