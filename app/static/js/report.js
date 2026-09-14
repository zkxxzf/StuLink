/* StuLink v1.13.1 成绩汇报区
 * 表格全部以内联样式输出，保证框选复制 → PPT/WPS 粘贴时保留蓝框、底色、对齐。
 * v1.13.1 变更：
 *  - 排名勾选重构：每个指标列独立 checkbox，勾选后该列右侧即时插入「该列名次」列，
 *    多列可同时勾选；名次=当前表内各行在该指标上的排名（并列同名次，竞赛排名）。
 *  - 任课教师列改用后端 subject-layer 直接下发的 teachers 映射（不再依赖划线，修复「—」）。
 *  - 对比考试下拉（默认上一场，可任选同年级考试）。
 *  - 全部选择（考试/方向/层/对比/各表勾选列/班/科目）持久化到 localStorage，下次自动恢复。
 *  - 顶部新增「导出 HTML / 导出 PPT」两个交付物按钮。 */
(function () {
    'use strict';
    var examId = $('#reportApp').data('exam-id');
    var LS_KEY = 'rp_cfg_v1';
    var state = {
        exams: [], layers: [],
        teacherRanks: null,      // 教师×学科 排名（教师名后缀名次标注用）
        // 各表勾选的排名指标列 key 集合（数组存储以便 JSON 序列化）
        checked: {sl: [], co: [], cs: [], sc: []},
        lastSl: null, lastCo: null,   // 最近渲染数据，勾选变化免请求重绘
        cmp: ''                       // 对比考试 id（''=默认上一场）
    };

    /* ---------- 内联样式常量（PPT 风格） ---------- */
    var S = {
        table: 'border-collapse:collapse;width:100%;font-family:"Microsoft YaHei",微软雅黑;font-size:15px;background:#fff;',
        th: 'border:1.5px solid #2b5f9e;padding:7px 8px;text-align:center;font-weight:700;color:#1a1a1a;background:#fff;',
        thDark: 'border:1.5px solid #2b5f9e;padding:7px 8px;text-align:center;font-weight:700;color:#fff;background:#1f4e79;font-size:14px;',
        td: 'border:1.5px solid #2b5f9e;padding:6px 8px;text-align:center;font-weight:700;color:#222;',
        tdAlt: 'background:#f3f5f9;',
        green: 'background:#70ad47;color:#fff;font-size:16px;',
        label: 'font-weight:400;color:#666;font-size:13px;',
        rank: 'color:#1f4e79;background:#eef3fa;'
    };
    function el(tag, style, text) {
        var e = document.createElement(tag);
        e.setAttribute('style', style || '');
        if (text !== undefined && text !== null) e.textContent = text;
        return e;
    }
    function pct(v) { return (v === null || v === undefined) ? '—' : v + '%'; }
    function num(v) { return (v === null || v === undefined) ? '—' : v; }

    /* ---------- 配置持久化（#6） ---------- */
    function saveCfg() {
        try {
            localStorage.setItem(LS_KEY, JSON.stringify({
                exam: curExam(), dir: curDir(), layer: curLayer(), cmp: state.cmp,
                checked: state.checked, cls: state.cfgCls, subj: state.cfgSubj
            }));
        } catch (e) { /* 隐私模式等场景静默失败 */ }
    }
    function loadCfg() {
        try { return JSON.parse(localStorage.getItem(LS_KEY) || 'null'); }
        catch (e) { return null; }
    }

    /* ---------- 初始化 ---------- */
    // 兜底看门狗：选项接口 15 秒未返回则明确报错，避免页面无限停留在「加载中」
    var booted = false;
    setTimeout(function () {
        if (!booted) $('#rpBody').html(errHtml('加载超时（15 秒未响应），请检查后端服务是否正常运行后刷新重试'));
    }, 15000);
    $.getJSON('/grades/api/options', function (res) {
        booted = true;
        var data = res.data || {};
        var sel = $('#rpExam').empty();
        Object.keys(data.exams || {}).sort().reverse().forEach(function (g) {
            (data.exams[g] || []).forEach(function (e) {
                state.exams[e.id] = e;
                // #4：下拉含全部考试；未划总分线的标注出来，选到会有明确提示
                sel.append('<option value="' + esc(e.id) + '">' + esc(g) + ' · '
                    + esc(e.name) + (e.banded ? '' : '（未划线）') + '</option>');
            });
        });
        var cfg = loadCfg();
        if (examId) sel.val(String(examId));
        else if (cfg && cfg.exam) sel.val(String(cfg.exam));
        if (!sel.val() && sel.find('option').length) sel.prop('selectedIndex', 0);
        if (cfg) {
            if (cfg.dir !== undefined) $('#rpDir').val(cfg.dir);
            if (cfg.checked) state.checked = {
                sl: cfg.checked.sl || [], co: cfg.checked.co || [],
                cs: cfg.checked.cs || [], sc: cfg.checked.sc || []};
            state.cmp = cfg.cmp || '';
            state.cfgCls = cfg.cls || '';
            state.cfgSubj = cfg.subj || '';
        }
        bindEvents();
        loadAll();
    }).fail(function (x) {
        booted = true;
        var msg = x.status === 403
            ? '您没有年级汇报区的查看权限（仅管理员/校级领导/年级长可访问）'
            : '考试选项加载失败（' + x.status + '），请刷新重试';
        $('#rpBody').html(errHtml(msg));
    });

    function bindEvents() {
        $('#rpExam').on('change', function () { state.cmp = ''; fillCmpSelect(); loadAll(); });
        $('#rpDir').on('change', function () { state.cmp = ''; fillCmpSelect(); loadAll(); });
        $('#rpLayer').on('change', loadSubjectLayer);
        $('#rpCmp').on('change', function () { state.cmp = this.value; loadSubjectLayer(); saveCfg(); });
        // 每列独立 checkbox：勾选/取消即时重绘该表（缓存数据，不重新请求）
        $(document).on('change', 'input[data-rank-group]', function () {
            var g = this.getAttribute('data-rank-group');
            var arr = state.checked[g] || (state.checked[g] = []);
            var i = arr.indexOf(this.value);
            if (this.checked && i < 0) arr.push(this.value);
            if (!this.checked && i >= 0) arr.splice(i, 1);
            saveCfg();
            if (g === 'sl' && state.lastSl) rebuildTable('rpSlTbl', buildSubjectTable, state.lastSl);
            if (g === 'co' && state.lastCo) rebuildTable('rpCoTbl', buildClassTable, state.lastCo);
            $(document).trigger('rp:rankchange', [g]);
        });
    }

    function curExam() { return $('#rpExam').val(); }
    function curDir() { return $('#rpDir').val() || ''; }
    function curLayer() { return $('#rpLayer').val() || ''; }

    /* 对比考试下拉：同年级全部考试，默认「上一场」 */
    function fillCmpSelect() {
        var sel = $('#rpCmp').empty();
        var cur = state.exams[curExam()];
        var grade = cur && cur.grade;
        sel.append('<option value="">上一场（默认）</option>');
        Object.keys(state.exams).forEach(function (id) {
            var e = state.exams[id];
            if (String(id) === String(curExam())) return;
            if (grade && e.grade && e.grade !== grade) return;
            sel.append('<option value="' + esc(id) + '">' + esc(e.name)
                + (e.banded ? '' : '（未划线）') + '</option>');
        });
        sel.val(state.cmp || '');
    }

    function loadAll() {
        examId = curExam();
        if (!examId) return;
        saveCfg();
        // 预挂五个板块容器并固定顺序，避免各板块异步返回顺序不同导致排列错乱
        $('#rpBody').empty();
        ['rpSl', 'rpCo', 'rpCS', 'rpSC', 'rpNL'].forEach(function (id) {
            $('<div class="rp-section" id="' + id + '">').appendTo('#rpBody');
        });
        loadSubjectLayer();
        loadClassOverview();
        loadTeacherRanks();
        // 通知板块三/四/五（report_pivot.js / report_warning.js）：考试或方向已切换
        $(document).trigger('rp:context');
    }

    /* ---------- 教师排名（教师名后缀名次标注用） ---------- */
    function loadTeacherRanks() {
        state.teacherRanks = null;
        var qs = '?exam_id=' + examId + '&direction=' + encodeURIComponent(curDir());
        $.getJSON('/grades/api/report/teacher-ranks' + qs, function (res) {
            if (!res.success) return;
            state.teacherRanks = res.data;
            if (state.lastSl) rebuildTable('rpSlTbl', buildSubjectTable, state.lastSl);
            $(document).trigger('rp:ranks');
        }).fail(function () { /* 排名加载失败不阻塞主表 */ });
    }

    /* 勾选指标后/排名数据到达后，用缓存数据原地替换表格，不重新请求 */
    function rebuildTable(tableId, builder, data) {
        var old = document.getElementById(tableId);
        if (!old || !data) return;
        var host = old.closest('.rp-table-wrap');
        if (host) host.replaceWith(builder(data));
    }

    /* ---------- 通用列驱动表渲染（五表共用，report_pivot.js 亦调用） ----------
     * cfg = {
     *   id, group,                       // group: 勾选组名（sl/co/cs/sc）
     *   cols: [ {key,label,get,fmt, fixed, rowspan2, thStyle, tdStyle, rankable} ],
     *   groups: [ {label, cols:[...]} ], // 两级表头分组（组内列全部可勾选）
     *   rows: [...]
     * }
     * 勾选 cfg.group 下任意列 key → 该列右侧即时插入「名次」列（竞赛排名，同分同名次）。 */
    function makeRankTable(cfg) {
        var checked = state.checked[cfg.group] || [];
        var hasGroups = !!(cfg.groups && cfg.groups.length);
        var t = el('table', S.table); t.id = cfg.id;
        var thead = el('thead');
        var tr1 = el('tr'), tr2 = el('tr');
        var flat = [];   // 渲染顺序：{col, rankable}

        function headOf(col, rankable) {
            var th = el('th', col.thStyle || S.th, col.label);
            if (rankable) {
                var lab = document.createElement('label');
                lab.className = 'rp-rank-opt';
                lab.innerHTML = '<input type="checkbox" data-rank-group="' + cfg.group
                    + '" value="' + esc(col.key) + '"'
                    + (checked.indexOf(col.key) >= 0 ? ' checked' : '') + '>名次';
                th.appendChild(lab);
            }
            return th;
        }
        (cfg.cols || []).forEach(function (col) {
            var rankable = !col.fixed && col.rankable !== false;
            if (hasGroups) {
                var th = headOf(col, false);
                th.rowSpan = 2;
                tr1.appendChild(th);
            } else {
                tr1.appendChild(headOf(col, rankable));
            }
            flat.push({col: col, rankable: hasGroups ? false : rankable});
        });
        if (hasGroups) {
            cfg.groups.forEach(function (g) {
                // 组头跨列 = 组内列数 + 已勾选列数（每勾选一列右侧多一个名次列）
                var gh = el('th', S.thDark, g.label);
                gh.colSpan = g.cols.length +
                    g.cols.filter(function (c) { return checked.indexOf(c.key) >= 0; }).length;
                tr1.appendChild(gh);
                g.cols.forEach(function (col) {
                    tr2.appendChild(headOf(col, true));
                    flat.push({col: col, rankable: true});
                });
            });
            thead.appendChild(tr1); thead.appendChild(tr2);
        } else {
            thead.appendChild(tr1);
        }
        t.appendChild(thead);

        // 每个勾选列的「行名次」映射：竞赛排名（1,2,2,4），null 值不参与
        var rankMaps = {};
        flat.forEach(function (f) {
            if (!f.rankable || checked.indexOf(f.col.key) < 0) return;
            var vals = [];
            cfg.rows.forEach(function (r) {
                var v = f.col.get(r);
                if (v !== null && v !== undefined && vals.indexOf(v) < 0) vals.push(v);
            });
            vals.sort(function (a, b) { return b - a; });
            var m = {};
            vals.forEach(function (v, i) { m[v] = i + 1; });
            rankMaps[f.col.key] = m;
        });

        var tb = el('tbody');
        cfg.rows.forEach(function (r, i) {
            var tr = el('tr');
            var stl = S.td + (i % 2 ? S.tdAlt : '');
            flat.forEach(function (f) {
                var v = f.col.get(r);
                var style = stl + (f.col.tdStyle ? f.col.tdStyle(r) : '');
                tr.appendChild(el('td', style, (f.col.fmt || num)(v)));
                if (f.rankable && rankMaps[f.col.key]) {
                    var rv = f.col.get(r);
                    var rk = (rv === null || rv === undefined) ? null : rankMaps[f.col.key][rv];
                    tr.appendChild(el('td', stl + S.rank, rk == null ? '—' : rk));
                }
            });
            tb.appendChild(tr);
        });
        t.appendChild(tb);
        var wrap = el('div', '');
        wrap.className = 'rp-table-wrap';
        wrap.appendChild(t);
        return wrap;
    }

    /* ---------- 板块一：年级各科层上线 ---------- */
    function loadSubjectLayer() {
        var qs = '?exam_id=' + examId + '&direction=' + encodeURIComponent(curDir())
            + '&layer=' + encodeURIComponent(curLayer())
            + (state.cmp ? '&compare_exam_id=' + encodeURIComponent(state.cmp) : '');
        $.getJSON('/grades/api/report/subject-layer' + qs, function (res) {
            var box = $('#rpSl');
            if (!box.length) box = $('<div class="rp-section" id="rpSl">').appendTo('#rpBody');
            if (!res.success) {
                box.html(sectionHead('各科成绩 上线对比', '') + errHtml(res.message));
                return;
            }
            var d = res.data;
            // 后端在「全部方向」时自动落到第一个有划线的方向，同步下拉显示（不触发 change）
            if (d.direction && d.direction !== '全部') $('#rpDir').val(d.direction);
            // 层选项（首次或考试变化时刷新，默认选「特控」）
            if (d.layers.join('|') !== state.layers.join('|')) {
                state.layers = d.layers || [];
                var ls = $('#rpLayer').empty();
                d.layers.forEach(function (n) { ls.append('<option>' + esc(n) + '</option>'); });
                ls.val(d.layer);
                // 层列表重建完成再通知依赖层的板块（四/五），避免它们用旧考试的层名发起请求
                $(document).trigger('rp:layers');
            }
            fillCmpSelect();
            box.html('').append(sectionHead(d.direction + '各科成绩 · ' + d.layer + ' 上线',
                d.exam.name + '（' + d.exam.date + '）' + (d.prev_exam ? '　对比：' + d.prev_exam : '')));
            state.lastSl = d;
            box.append(buildSubjectTable(d));
            box.append(copyBar('rpSlTbl'));
            box.append('<div class="rp-hint">口径：单上线＝单科达该科' + esc(d.layer) + '线；双上线＝单科且总分双达'
                + esc(d.layer) + '线；去差均分＝卓越班各班剔除总分末2名后均分。点各列表头「名次」即在该列右侧插入排名列。</div>');
        }).fail(function (x) {
            var box = $('#rpSl');
            if (!box.length) box = $('<div class="rp-section" id="rpSl">').appendTo('#rpBody');
            box.html(sectionHead('各科成绩 上线对比', '')
                + errHtml(x.status === 403 ? '无查看权限' : ('加载失败（' + x.status + '）')));
        });
    }

    function buildSubjectTable(d) {
        var prevMap = {};
        (d.prev || []).forEach(function (p) { prevMap[p.subject] = p; });
        var hasPrev = !!d.prev_exam;
        var t = makeRankTable({
            id: 'rpSlTbl', group: 'sl', rows: d.rows,
            cols: [
                {key: 'subject', label: d.layer, fixed: true, thStyle: S.th + S.green,
                 get: function (r) { return r.subject; }, fmt: String},
                {key: 'line', label: '线分', fixed: true, thStyle: S.th + S.label,
                 get: function (r) { return r.line; }},
                {key: 'teacher', label: '任课教师', fixed: true, thStyle: S.th + S.label,
                 get: function (r) { return subjectTeacherText(d, r.subject); }}
            ],
            groups: [
                {label: '本次', cols: [
                    {key: 'count', label: '参考数', get: function (r) { return r.count; }},
                    {key: 'online_n', label: '上线数', get: function (r) { return r.online_n; }},
                    {key: 'online_rate', label: '上线率', fmt: pct, get: function (r) { return r.online_rate; }},
                    {key: 'dual_n', label: '双上线数', get: function (r) { return r.dual_n; }},
                    {key: 'dual_rate', label: '双上线率', fmt: pct, get: function (r) { return r.dual_rate; }}
                ]},
                hasPrev ? {label: d.prev_exam, cols: [
                    {key: 'p_online_n', label: '上线数', get: function (r) { return (prevMap[r.subject] || {}).online_n; }},
                    {key: 'p_online_rate', label: '上线率', fmt: pct, get: function (r) { return (prevMap[r.subject] || {}).online_rate; }}
                ]} : null,
                {label: '去差均分', cols: hasPrev ? [
                    {key: 'p_trim_avg', label: '上次', get: function (r) { return (prevMap[r.subject] || {}).trim_avg; }},
                    {key: 'trim_avg', label: '本次', get: function (r) { return r.trim_avg; }}
                ] : [
                    {key: 'trim_avg', label: '本次', get: function (r) { return r.trim_avg; }}
                ]}
            ].filter(Boolean)
        });
        return t;
    }

    /* 图1 任课教师单元格：姓名来自后端 teachers 映射（不依赖划线）；
       勾选了可排名指标时，教师名后缀标注其在该指标上的教师名次 */
    function subjectTeacherText(d, subj) {
        var names = (d.teachers && d.teachers[subj]) || '';
        var rk = state.teacherRanks;
        var key = firstTeacherRankKey('sl');
        if (!names || !rk || !key) return names || '—';
        var group = rk.l1_name === d.layer ? rk.l1
            : (rk.l2_name === d.layer ? rk.l2 : null);
        var ts = (group && group[subj]) || [];
        if (!ts.length) return names;
        var rankOf = {};
        ts.forEach(function (x) { rankOf[x.name] = x.rank[key]; });
        return names.split('、').map(function (nm) {
            var v = rankOf[nm];
            return nm + '(' + (v == null ? '—' : v) + ')';
        }).join('、');
    }

    /* 该勾选组里第一个「教师可参与排名」的指标（跳过 p_ 上次指标；表三/四的 l1_/l2_ 前缀在 pivot 内解析） */
    function firstTeacherRankKey(group) {
        var ok = ['count', 'online_n', 'online_rate', 'dual_n', 'dual_rate', 'trim_avg',
                  'l1_n', 'l1_rate', 'l2_n', 'l2_rate'];
        var arr = state.checked[group] || [];
        for (var i = 0; i < arr.length; i++) {
            if (ok.indexOf(arr[i]) >= 0) return arr[i];
        }
        return null;
    }

    /* ---------- 板块二：班级概况 ---------- */
    function loadClassOverview() {
        var qs = '?exam_id=' + examId + '&direction=' + encodeURIComponent(curDir());
        $.getJSON('/grades/api/report/class-overview' + qs, function (res) {
            var box = $('#rpCo');
            if (!box.length) box = $('<div class="rp-section" id="rpCo">').appendTo('#rpBody');
            if (!res.success) { box.html(sectionHead('班级概况', '') + errHtml(res.message)); return; }
            var d = res.data;
            box.html('').append(sectionHead('班级概况（' + d.direction + '）',
                '上线按总分线、各方向分别判定合并'));
            state.lastCo = d;
            box.append(buildClassTable(d));
            box.append(copyBar('rpCoTbl'));
            box.append('<div class="rp-hint">去差均分＝按班型剔除各班总分末 N 人（卓越班 2 人，其余 0 人）后的总分均分。</div>');
        }).fail(function (x) {
            var box = $('#rpCo');
            if (!box.length) box = $('<div class="rp-section" id="rpCo">').appendTo('#rpBody');
            box.html(sectionHead('班级概况', '')
                + errHtml(x.status === 403 ? '无查看权限' : ('加载失败（' + x.status + '）')));
        });
    }

    function buildClassTable(d) {
        var cols = [
            {key: 'class_name', label: '班级', fixed: true, get: function (r) { return r.class_name; }, fmt: String},
            {key: 'headteacher', label: '班主任', fixed: true, thStyle: S.th + S.label,
             get: function (r) { return r.headteacher || '—'; }}
        ];
        d.layers.forEach(function (n) {
            cols.push({key: n + '@n', label: n + '上线',
                get: function (r) { return r[n + '_n']; }});
            cols.push({key: n + '@rate', label: n + '上线率', fmt: pct,
                get: function (r) { return r[n + '_rate']; }});
        });
        cols.push({key: 'trim_avg', label: '去差均分', get: function (r) { return r.trim_avg; }});
        return makeRankTable({id: 'rpCoTbl', group: 'co', rows: d.rows, cols: cols});
    }

    /* ---------- 通用片段 ---------- */
    function sectionHead(title, sub) {
        return '<div class="rp-title"><h6>' + esc(title) + '</h6>'
            + (sub ? '<span class="rp-sub">' + esc(sub) + '</span>' : '') + '</div>';
    }
    function errHtml(msg) { return '<div class="alert alert-warning py-2 small">' + esc(msg || '暂无数据') + '</div>'; }

    function copyBar(tableId) {
        return '<div class="rp-toolbar no-copy text-end">'
            + '<button class="btn btn-sm btn-outline-primary" onclick="window._rpCopy(\'' + tableId + '\')">'
            + '<i class="bi bi-clipboard"></i> 复制本表（粘贴到 PPT）</button></div>';
    }
    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
        });
    }

    /* 选中整张表并复制；失败时提示手动框选 */
    window._rpCopy = function (tableId) {
        var node = document.getElementById(tableId);
        if (!node) return;
        // 复制瞬间隐藏表头的排名勾选控件，避免小圆圈被一起粘进 PPT（复制完立即恢复）
        var section = node.closest('.rp-section');
        if (section) section.classList.add('rp-copying');
        var range = document.createRange();
        range.selectNode(node);
        var sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        var ok = false;
        try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
        sel.removeAllRanges();
        if (section) section.classList.remove('rp-copying');
        var tip = document.createElement('div');
        tip.className = 'rp-copied';
        tip.textContent = ok ? '已复制，到 PPT 里 Ctrl+V 即可' : '复制失败，请直接用鼠标框选表格后 Ctrl+C';
        document.body.appendChild(tip);
        setTimeout(function () { tip.remove(); }, 1800);
    };

    /* ---------- 交付物导出（#3） ---------- */
    // 把汇报区五个板块序列化为独立 HTML 文件下载（保留内联样式，可直接浏览器打开/转发）
    window._rpExportHtml = function () {
        var body = document.getElementById('rpBody');
        if (!body) return;
        var clone = body.cloneNode(true);
        // 剔除交互控件（勾选框/按钮/工具条），只留表格本体与标题
        clone.querySelectorAll('.no-copy, .rp-toolbar, .rp-rank-opt, .rp-hint').forEach(function (n) { n.remove(); });
        var examName = $('#rpExam option:selected').text();
        var html = '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
            + '<title>成绩汇报 · ' + esc(examName) + '</title></head>'
            + '<body style="background:#fff;padding:16px;font-family:\'Microsoft YaHei\',微软雅黑;">'
            + clone.innerHTML + '</body></html>';
        var blob = new Blob([html], {type: 'text/html;charset=utf-8'});
        var a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = '成绩汇报_' + examName.replace(/[\\/:*?"<>|]/g, '_') + '.html';
        a.click();
        setTimeout(function () { URL.revokeObjectURL(a.href); }, 4000);
    };

    // 收集各表结构化数据 → 后端 python-pptx 生成 .pptx 下载
    window._rpExportPptx = function () {
        var tables = [];
        document.querySelectorAll('#rpBody .rp-section').forEach(function (sec) {
            var t = sec.querySelector('table');
            if (!t) return;
            var title = (sec.querySelector('.rp-title h6') || {}).textContent || '';
            var head = sec.querySelector('.rp-title .rp-sub');
            var rows = [];
            t.querySelectorAll('tr').forEach(function (tr) {
                var cells = [];
                tr.querySelectorAll('th,td').forEach(function (c) {
                    // 勾选框标签不进导出内容
                    var lab = c.querySelector('.rp-rank-opt');
                    var txt = c.textContent;
                    if (lab) txt = txt.replace(lab.textContent, '');
                    cells.push({
                        text: txt.trim(),
                        color: (getComputedStyle(c).color || '').replace(/\s/g, ''),
                        bg: (getComputedStyle(c).backgroundColor || '').replace(/\s/g, ''),
                        bold: /700|bold/.test(getComputedStyle(c).fontWeight)
                    });
                });
                if (cells.length) rows.push(cells);
            });
            if (rows.length >= 2) tables.push({title: title, sub: head ? head.textContent : '', rows: rows});
        });
        if (!tables.length) { alert('暂无可导出的表格'); return; }
        var btn = document.getElementById('rpExportPptx');
        if (btn) btn.disabled = true;
        $.ajax({
            url: '/grades/api/report/export-pptx', type: 'POST',
            contentType: 'application/json', data: JSON.stringify({tables: tables}),
            xhrFields: {responseType: 'blob'},
            success: function (blob, status, xhr) {
                var cd = xhr.getResponseHeader('Content-Disposition') || '';
                var m = cd.match(/filename\*=UTF-8''([^;]+)/i);
                var name = m ? decodeURIComponent(m[1]) : '成绩汇报.pptx';
                var a = document.createElement('a');
                a.href = URL.createObjectURL(blob);
                a.download = name;
                a.click();
                setTimeout(function () { URL.revokeObjectURL(a.href); }, 4000);
            },
            error: function () { alert('导出 PPT 失败，请重试'); },
            complete: function () { if (btn) btn.disabled = false; }
        });
    };

    // 向板块三/四脚本（report_pivot.js）导出公共片段，保证表格风格/复制行为一致
    window._rpUI = { el: el, esc: esc, S: S, pct: pct, num: num,
        sectionHead: sectionHead, copyBar: copyBar, errHtml: errHtml,
        makeRankTable: makeRankTable, state: state, saveCfg: saveCfg,
        getRanks: function () { return state.teacherRanks; },
        firstTeacherRankKey: firstTeacherRankKey };
})();
