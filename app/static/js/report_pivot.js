/* StuLink v1.13.1 成绩汇报区 · 板块三/四
 * 板块三：单班各科分析（含任课教师）；板块四：单科各班分析。
 * 复用 report.js 导出的 _rpUI（内联样式/复制/列驱动表 makeRankTable），风格与勾选排名一致。
 * v1.13.1：排名改为每列独立 checkbox，勾选后该列右侧即时插入名次列；教师名后缀标注教师名次。 */
(function () {
    'use strict';
    var U = null;
    var st = {classes: [], subjects: [], cls: '', subj: '', lastCS: null, lastSC: null};
    // 竞态序号：快速切换考试/方向时，只渲染最后一次请求；两条链式流程各自独立
    var seq = {pickCls: 0, cs: 0, pickSubj: 0, sc: 0};

    function examId() { return $('#rpExam').val(); }
    function dirVal() { return $('#rpDir').val() || ''; }
    function layerVal() { return $('#rpLayer').val() || ''; }
    function api(url) { return '/grades/api/report/' + url; }

    /* 考试/方向切换（report.js 广播 rp:context）；首屏下拉未就绪时限时等待（≤12 秒），
       pending 保证上下文触发与首屏轮询只会产生一次加载，避免重复请求 */
    var waitTicks = 0, waiting = false;
    function ready() { return !!(window._rpUI && examId()); }
    function reloadAll() {
        U = window._rpUI;
        loadClassPicker();
        loadSubjectPicker();
    }
    function tryBoot() {
        if (ready()) { waiting = false; reloadAll(); }
        else if (++waitTicks < 60) { waiting = true; setTimeout(tryBoot, 200); }
    }
    $(document).on('rp:context', function () {
        if (ready()) reloadAll();
        else if (!waiting) tryBoot();
    });
    $(document).on('rp:layers', function () {
        if (ready()) { U = window._rpUI; loadSubjectPicker(); }
    });
    // 教师排名到达 / 勾选变化：用缓存数据原地补渲染，不重新请求
    $(document).on('rp:ranks', function () {
        if (st.lastCS) rebuildPivot('rpCSTbl', st.lastCS, 'subject');
        if (st.lastSC) rebuildPivot('rpSCTbl', st.lastSC, 'class_name');
    });
    $(document).on('rp:rankchange', function (e, g) {
        if (g === 'cs' && st.lastCS) rebuildPivot('rpCSTbl', st.lastCS, 'subject');
        if (g === 'sc' && st.lastSC) rebuildPivot('rpSCTbl', st.lastSC, 'class_name');
    });
    $(tryBoot);

    /* ---------- 板块三：班级选择 + 单班各科表 ---------- */
    function loadClassPicker() {
        var my = ++seq.pickCls;
        $.getJSON(api('class-overview?exam_id=') + examId()
            + '&direction=' + encodeURIComponent(dirVal()), function (res) {
            if (my !== seq.pickCls) return;
            var list = res.success ? (res.data.rows || []).map(function (r) { return r.class_name; }) : [];
            st.classes = list;
            var saved = U && U.state.cfgCls;
            if (saved && list.indexOf(saved) >= 0) st.cls = saved;
            else if (list.indexOf(st.cls) < 0) st.cls = list[0] || '';
            loadClassSubject();
        });
    }

    function classPickerHtml() {
        var opts = st.classes.map(function (c) {
            return '<option value="' + U.esc(c) + '"' + (c === st.cls ? ' selected' : '') + '>'
                + U.esc(c) + '</option>';
        }).join('');
        return '<div class="rp-toolbar no-copy text-start">班级：'
            + '<select id="rpClsPick" class="form-select form-select-sm d-inline-block w-auto">'
            + opts + '</select></div>';
    }

    function loadClassSubject() {
        var box = ensureBox('rpCS');
        if (!st.cls) { box.html(U.sectionHead('班级成绩分析', '') + U.errHtml('该方向暂无班级')); return; }
        var my = ++seq.cs;
        $.getJSON(api('class-subject?exam_id=') + examId()
            + '&class_name=' + encodeURIComponent(st.cls), function (res) {
            if (my !== seq.cs) return;
            if (!res.success) { box.html(U.sectionHead('班级成绩分析', '') + U.errHtml(res.message)); return; }
            var d = res.data;
            box.html('').append(U.sectionHead(d.class_name + ' 成绩分析',
                d.exam.name + ' · ' + d.direction + ' · ' + d.student_n + '人'));
            st.lastCS = d;
            box.append(classPickerHtml());
            box.append(buildPivotTable('rpCSTbl', d, 'subject', 'cs'));
            box.append(U.copyBar('rpCSTbl'));
            $('#rpClsPick').on('change', function () {
                st.cls = $(this).val();
                if (U.state) U.state.cfgCls = st.cls;
                U.saveCfg();
                loadClassSubject();
            });
        }).fail(function (x) {
            box.html(U.sectionHead('班级成绩分析', '')
                + U.errHtml(x.status === 403 ? '无查看权限' : ('加载失败（' + x.status + '）')));
        });
    }

    /* ---------- 板块四：科目选择 + 单科各班表 ---------- */
    function loadSubjectPicker() {
        var my = ++seq.pickSubj;
        $.getJSON(api('subject-layer?exam_id=') + examId()
            + '&direction=' + encodeURIComponent(dirVal()) + '&layer=' + encodeURIComponent(layerVal()),
            function (res) {
            if (my !== seq.pickSubj) return;
            var list = res.success ? (res.data.rows || []).map(function (r) { return r.subject; }) : [];
            st.subjects = list;
            var saved = U && U.state.cfgSubj;
            if (saved && list.indexOf(saved) >= 0) st.subj = saved;
            else if (list.indexOf(st.subj) < 0) st.subj = list[0] || '';
            loadSubjectClasses();
        });
    }

    function subjectPickerHtml() {
        var opts = st.subjects.map(function (s) {
            return '<option value="' + U.esc(s) + '"' + (s === st.subj ? ' selected' : '') + '>'
                + U.esc(s) + '</option>';
        }).join('');
        return '<div class="rp-toolbar no-copy text-start">科目：'
            + '<select id="rpSubjPick" class="form-select form-select-sm d-inline-block w-auto">'
            + opts + '</select></div>';
    }

    function loadSubjectClasses() {
        var box = ensureBox('rpSC');
        if (!st.subj) { box.html(U.sectionHead('学科成绩分析', '') + U.errHtml('该方向暂无可选科目')); return; }
        var my = ++seq.sc;
        $.getJSON(api('subject-classes?exam_id=') + examId()
            + '&direction=' + encodeURIComponent(dirVal()) + '&subject=' + encodeURIComponent(st.subj),
            function (res) {
            if (my !== seq.sc) return;
            if (!res.success) { box.html(U.sectionHead('学科成绩分析', '') + U.errHtml(res.message)); return; }
            var d = res.data;
            box.html('').append(U.sectionHead(d.subject + '成绩分析（' + d.direction + '）', d.exam.name));
            st.lastSC = d;
            box.append(subjectPickerHtml());
            box.append(buildPivotTable('rpSCTbl', d, 'class_name', 'sc'));
            box.append(U.copyBar('rpSCTbl'));
            if (d.grade) {
                box.append('<div class="rp-hint">学科强弱：绿字＝' + U.esc(d.l1_name)
                    + '上线率高于年级≥' + d.grade.rate_pp + '个百分点或去差均分高≥' + d.grade.avg_diff
                    + '分；红字＝反之。年级基准：' + U.pct(d.grade.l1_rate) + '、'
                    + U.num(d.grade.trim_avg) + ' 分。</div>');
            }
            $('#rpSubjPick').on('change', function () {
                st.subj = $(this).val();
                if (U.state) U.state.cfgSubj = st.subj;
                U.saveCfg();
                loadSubjectClasses();
            });
        }).fail(function (x) {
            box.html(U.sectionHead('学科成绩分析', '')
                + U.errHtml(x.status === 403 ? '无查看权限' : ('加载失败（' + x.status + '）')));
        });
    }

    function rebuildPivot(tblId, d, firstKey) {
        var old = document.getElementById(tblId);
        if (!old || !d) return;
        var host = old.closest('.rp-table-wrap');
        if (host) host.replaceWith(buildPivotTable(tblId, d, firstKey,
            firstKey === 'subject' ? 'cs' : 'sc'));
    }

    /* 教师名后缀标注：勾选了教师可排名指标时，在该行教师名后加其教师名次。
       表三/四列 key 带 l1_/l2_ 前缀，教师排名数据里对应 online_n/online_rate/trim_avg 等。 */
    function teacherCell(d, firstKey, r, group) {
        var names = r.teacher || '';
        var rk = U.getRanks();
        var key = U.firstTeacherRankKey(group);
        if (!names || !rk || !key) return names || '—';
        // 解析列 key → (教师排名层, 教师排名字段)
        var layerGroup = rk.l1, field = key;
        if (key.indexOf('l1_') === 0) { field = 'online_' + key.slice(3); }
        else if (key.indexOf('l2_') === 0) { layerGroup = rk.l2; field = 'online_' + key.slice(3); }
        var subj = firstKey === 'subject' ? r.subject : d.subject;
        var ts = (layerGroup && layerGroup[subj]) || [];
        if (!ts.length) return names;
        var rankOf = {};
        ts.forEach(function (x) { rankOf[x.name] = x.rank[field]; });
        return names.split('、').map(function (nm) {
            var v = rankOf[nm];
            return nm + '(' + (v == null ? '—' : v) + ')';
        }).join('、');
    }

    /* 两级表头透视表：首列（班级/学科）+ 教师 + 特控组 + 本科组 + 去差均分。
       勾选组内任意列 → 该列右侧即时插入名次列（makeRankTable 统一实现）。 */
    function buildPivotTable(tblId, d, firstKey, group) {
        var S = U.S;
        var cols = [
            {key: firstKey, label: firstKey === 'subject' ? '学科' : '班级', fixed: true,
             thStyle: S.th + S.green, get: function (r) { return r[firstKey]; }, fmt: String},
            {key: 'teacher', label: '教师', fixed: true, thStyle: S.th + S.label,
             get: function (r) { return teacherCell(d, firstKey, r, group); }}
        ];
        var groups = [{
            label: d.l1_name + '上线分析',
            cols: [
                {key: 'l1_n', label: '上线数', get: function (r) { return r.l1_n; }},
                {key: 'l1_rate', label: '上线率', fmt: U.pct,
                 // 学科强弱：上线率相对年级基准红绿标注（仅板块四带 grade 基准时生效）
                 tdStyle: function (r) {
                     if (!d.grade) return '';
                     var base = d.grade.l1_rate, v = r.l1_rate;
                     if (v == null || base == null) return '';
                     var diff = v - base;
                     if (diff >= d.grade.rate_pp) return 'color:#548235;';
                     if (diff <= -d.grade.rate_pp) return 'color:#c00000;';
                     return '';
                 }, get: function (r) { return r.l1_rate; }},
                {key: 'dual_n', label: '双上线数', get: function (r) { return r.dual_n; }},
                {key: 'dual_rate', label: '双上线率', fmt: U.pct, get: function (r) { return r.dual_rate; }}
            ]
        }];
        if (d.l2_name) {
            groups.push({label: d.l2_name + '上线分析', cols: [
                {key: 'l2_n', label: '单上线', get: function (r) { return r.l2_n; }},
                {key: 'l2_rate', label: '上线率', fmt: U.pct, get: function (r) { return r.l2_rate; }}
            ]});
        }
        groups.push({label: '去差均分', cols: [
            {key: 'trim_avg', label: '本次',
             tdStyle: function (r) {
                 if (!d.grade) return '';
                 var base = d.grade.trim_avg, v = r.trim_avg;
                 if (v == null || base == null) return '';
                 var diff = v - base;
                 if (diff >= d.grade.avg_diff) return 'color:#548235;';
                 if (diff <= -d.grade.avg_diff) return 'color:#c00000;';
                 return '';
             }, get: function (r) { return r.trim_avg; }}
        ]});
        return U.makeRankTable({id: tblId, group: group, rows: d.rows, cols: cols, groups: groups});
    }

    function ensureBox(id) {
        var box = $('#' + id);
        if (!box.length) box = $('<div class="rp-section" id="' + id + '">').appendTo('#rpBody');
        return box;
    }
})();
