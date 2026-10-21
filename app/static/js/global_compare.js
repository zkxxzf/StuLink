/* StuLink v1.19.56.64 2026-10-10 全局对比
 * 班级 × 学科宽表：固定列（年级/班级/人数/方向/组合/属性）+ 每科（去差均分 + 各分数线）。
 * 分数线可选：默认勾选 211线/特控线/本科线，其余层（清北/985…）勾选才显示；
 * 班主任并入「总分」格下方（不再单列），各科成绩下方为本场任课教师快照。
 * 依赖：jQuery（base.html 全局）；数据源 /grades/api/global-compare/report。
 * 渲染后实测表头高度与冻结列宽度，回写 CSS 变量（吸顶偏移/粘性列 left）。 */
(function () {
    'use strict';
    var examId = $('#ldApp').data('exam-id');
    var API = '/grades/api/global-compare/report';
    var EXPORT_API = '/grades/api/global-compare/export';
    var $body = $('#ldBody');
    var $lines = $('#ldLines');
    var lastData = null;      // 最近一次渲染的数据
    var checkedLines = null;  // 当前勾选的层名（null=未初始化，用后端默认）

    function esc(v) {
        return String(v == null ? '' : v)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function lineLabel(name) {
        if (!name) { return ''; }
        return /线$/.test(name) ? name : name + '线';
    }
    function emptyHtml(msg) {
        return '<div class="alert alert-warning mb-0"><i class="bi bi-info-circle"></i> '
            + esc(msg) + '</div>';
    }
    function fmtAvg(v) {
        return (v === null || v === undefined) ? '<span class="ld-none">—</span>' : v;
    }
    function fmtOnline(n, r) {
        if (n === null || n === undefined) { return '<span class="ld-none">—</span>'; }
        // 人数在上、上线率在下（竖排压缩列宽，避免整表横向滚动）
        return '<span class="ld-online"><span class="n">' + n + '</span>'
            + '<span class="r">' + (r === null || r === undefined ? 0 : r) + '%</span></span>';
    }
    function dirBadge(d) {
        if (!d) { return '<span class="ld-none">—</span>'; }
        var cls = d === '物理' ? 'ld-dir-phy' : (d === '历史' ? 'ld-dir-his' : 'ld-dir-all');
        return '<span class="ld-dir ' + cls + '">' + esc(d) + '</span>';
    }
    /* 2026-10-15 组合列：本行（班级/小计/总计）的选科组合分布。
       多组合竖排（人数降序，后端已排好），title 给完整明细；未分科无组合 → 「—」。 */
    function selHtml(list) {
        if (!list || !list.length) { return '<span class="ld-none">—</span>'; }
        var tip = list.map(function (s) { return s.name + ' ' + s.count + '人'; }).join('、');
        var html = '<span class="ld-sels" title="选科组合分布：' + esc(tip) + '">';
        list.forEach(function (s) {
            html += '<span class="ld-sel-i">' + esc(s.name)
                + '<span class="n">' + s.count + '</span></span>';
        });
        return html + '</span>';
    }

    /* ---------- 初始化：考试下拉复用成绩模块 /grades/api/options ---------- */
    $.getJSON('/grades/api/options', function (res) {
        var sel = $('#ldExam').empty();
        var data = res.data || {};
        Object.keys(data.exams || {}).sort().reverse().forEach(function (g) {
            (data.exams[g] || []).forEach(function (e) {
                sel.append('<option value="' + esc(e.id) + '">' + esc(g) + ' · '
                    + esc(e.name) + (e.banded ? '' : '（未划线）') + '</option>');
            });
        });
        if (examId) { sel.val(String(examId)); }
        var missed = !!(examId && sel.val() !== String(examId));
        if (!sel.val() && sel.find('option').length) { sel.prop('selectedIndex', 0); }
        if (!sel.find('option').length) {
            $body.html(emptyHtml('暂无可查看的考试'));
            return;
        }
        if (missed) {
            // v1.19.56.64 本场考试不在可见列表（已删除 / 超出数据范围 / 已毕业）：
            // 不能静默显示最近一场让用户误当本场（考试态下下拉原本隐藏）——
            // 恢复下拉并明确提示。
            $('#ldExam').removeClass('d-none');
            $('#ldMeta').before('<div id="ldNotice" class="alert alert-warning py-2 small mb-2">'
                + '<i class="bi bi-exclamation-triangle"></i> 本场考试不可见（可能已删除或不在您的数据范围），'
                + '已改为显示最近一场，请重新选择。</div>');
        }
        load();
    }).fail(function (x) {
        $body.html(emptyHtml(x.status === 403
            ? '您没有全局对比的查看权限（仅管理员/校级领导/年级长可访问）'
            : '考试列表加载失败（' + x.status + '），请刷新重试'));
    });

    $('#ldExam').on('change', function () { checkedLines = null; load(); });
    $('#ldDir').on('change', load);
    // 横轴排序：勾上=班型优先，取消=方向优先（只重画图表，不重新拉数据）
    $('#ldSortType').prop('checked', sortByType).on('change', function () {
        sortByType = !!this.checked;
        try {
            window.localStorage.setItem(SORT_STORE, sortByType ? 'type' : 'dir');
        } catch (e) { /* 隐私模式忽略 */ }
        if (lastChartData) { renderCharts(lastChartData); }
    });
    $('#ldExport').on('click', function () {
        window.location = EXPORT_API
            + '?exam_id=' + encodeURIComponent($('#ldExam').val() || '')
            + '&direction=' + encodeURIComponent($('#ldDir').val() || '')
            + '&lines=' + encodeURIComponent(activeLines().join(','));
    });

    function load() {
        var eid = $('#ldExam').val();
        if (!eid) { return; }
        // 换考试/方向时先把旧图表藏起来，避免看到上一场的数据
        $('#ldCharts').addClass('d-none');
        $body.html('<div class="text-center text-muted py-5"><div class="spinner-border"></div>'
            + '<div class="mt-2">加载中…</div></div>');
        $.getJSON(API + '?exam_id=' + encodeURIComponent(eid)
            + '&direction=' + encodeURIComponent($('#ldDir').val() || ''))
            .done(function (res) {
                if (!res.success) { $lines.empty(); $body.html(emptyHtml(res.message || '加载失败')); return; }
                render(res.data);
            })
            .fail(function (x) {
                $lines.empty();
                $body.html(emptyHtml(x.status === 403
                    ? '您没有全局对比的查看权限（仅管理员/校级领导/年级长可访问）'
                    : '数据加载失败（' + x.status + '），请刷新重试'));
            });
    }

    function fillDirSelect(d) {
        var dirs = d.directions || [];
        var sel = $('#ldDir');
        var cur = sel.val() || '';
        sel.empty();
        if (!dirs.length) { sel.hide(); sel.append('<option value=""></option>'); return; }
        sel.show().append('<option value="">全部方向</option>');
        dirs.forEach(function (x) {
            sel.append('<option value="' + esc(x) + '">' + esc(x) + '方向</option>');
        });
        if (dirs.indexOf(cur) >= 0) { sel.val(cur); }
    }

    /* 当前勾选且可用的层名（按 layers 高→低顺序） */
    function activeLines() {
        var all = (lastData && lastData.layers) || [];
        var ck = checkedLines || [];
        return all.filter(function (n) { return ck.indexOf(n) >= 0; });
    }

    /* ---------- 分数线勾选（默认 211/特控/本科，其余可选） ---------- */
    function renderLineToggles(d) {
        var all = d.layers || [];
        var def = d.default_lines || [];
        if (checkedLines === null) {
            checkedLines = def.slice();
        } else {
            // 换考试/方向后层名可能变：保留仍有效的，全部失效则回到默认
            var kept = checkedLines.filter(function (n) { return all.indexOf(n) >= 0; });
            checkedLines = kept.length ? kept : def.slice();
        }
        if (!all.length) { $lines.empty(); return; }
        var html = ['<span class="ld-lines-label">分数线：</span>'];
        all.forEach(function (n) {
            var on = checkedLines.indexOf(n) >= 0;
            html.push('<label class="ld-line-chk"><input type="checkbox" value="'
                + esc(n) + '"' + (on ? ' checked' : '') + '> ' + esc(lineLabel(n)) + '</label>');
        });
        $lines.html(html.join(''));
        $lines.find('input[type=checkbox]').on('change', function () {
            var name = $(this).val();
            if (this.checked) {
                if (checkedLines.indexOf(name) < 0) { checkedLines.push(name); }
            } else {
                checkedLines = checkedLines.filter(function (x) { return x !== name; });
            }
            // 2026-10-16 第九轮审计（PERF-P11）：原先整表重建 innerHTML（22+ 行 × 36 列
            // ≈2600 节点 + 自动表格布局重排，单次勾选 30-120ms，连点就卡）。
            // 现在列头与数据格都带 data-line，只切该层显隐；分组表头的 colspan 要跟着
            // 可见层数缩，否则剩下的列会错位。
            var key = (window.CSS && window.CSS.escape) ? window.CSS.escape(name) : name;
            $('#ldTable [data-line="' + key + '"]').toggle(this.checked);
            $('#ldTable thead tr:first-child th.grp')
                .attr('colspan', 1 + activeLines().length);
        });
    }

    /* ---------- 渲染 ---------- */
    function render(d) {
        lastData = d;
        fillDirSelect(d);
        var ex = d.exam || {};
        var meta = (ex.grade || '') + (ex.grade_label ? '（' + ex.grade_label + '）' : '')
            + ' · ' + (ex.name || '') + (ex.date ? ' · ' + ex.date : '')
            + ' · 共 ' + (d.subject_count != null ? d.subject_count
                : Math.max(0, (d.subjects || []).length - 1)) + ' 科';
        var note = '口径：去差均分＝按班型剔除总分末尾 N 人（卓越班 2 人）后均分，'
            + '其下方小字＝全体均分（不剔人；与去差一致时不显示）；'
            + '混组合班的班级合计行与其下方各组合明细行：合计行给人数与上线率、'
            + '不给均分（组合线不同、混算无意义，显示「—」），均分看各组合明细行；'
            + '方向小计/全年级同款（总计的组合明细与方向小计重复，不再重复列）；'
            + '上线率按每生各自选科组合的同名单科线逐生判定；'
            + '总分格下方为班主任、各科格下方为该科任课教师（取本场考试当时的快照）；'
            + '上线单元格＝人数 / 上线率；分数线可勾选显示。';
        if (d.partial) {
            note += ' ⚠ 本场为分批导入（缺 ' + (d.partial.missing || []).join('、')
                + '），总分口径不完整。';
        }
        $('#ldMeta').html('<div>' + esc(meta) + '</div><div>' + esc(note) + '</div>');

        var subjects = d.subjects || [];
        if (!subjects.length) {
            $lines.empty();
            $('#ldCharts').addClass('d-none');
            $body.html(emptyHtml('该考试没有可展示的科目成绩'));
            return;
        }
        renderLineToggles(d);
        renderTable(d);
        renderCharts(d);
    }

    function renderTable(d) {
        if (!d) { return; }
        var subjects = d.subjects || [];
        // 2026-10-16 第九轮审计（PERF-P11）：全部层都建列、未勾选的先藏（.ld-line-off），
        // 勾选时只切显隐，不再整表重建；分组表头 colspan 按**可见**层数算
        var allLines = d.layers || [];
        var lines = activeLines();
        var per = 1 + lines.length;

        /* 2026-10-17 口径：高一未选科时本场没有方向/组合（后端 has_direction /
           has_selection），这两列整列都是「—」→ 直接不显示（undefined 视为显示，兼容旧缓存） */
        var hasDir = d.has_direction !== false;
        var hasSel = d.has_selection !== false;
        /* 2026-10-20 用户口径：「班级人数 / 参考人数 / 去差后人数」三列并排，
           不同颜色区分；去差后 = min(参考人数, 班级人数 - 班型去差名额)。 */
        var fixHeads = ['年级', '班级', '班级人数', '参考人数', '去差后'];
        if (hasDir) { fixHeads.push('方向'); }
        if (hasSel) { fixHeads.push('组合'); }
        fixHeads.push('属性');

        var h = ['<div class="ld-wrap"><table class="ld-table" id="ldTable"><thead><tr>'];
        var headTips = {
            '班级人数': '名册在册人数（不含已转出/离校/退学）',
            '参考人数': '参加本场考试的人数（有总分的）',
            '去差后': '去差后人数 = min(参考人数, 班级人数 - 班型去差名额)；缺考先顶替去差名额'
        };
        fixHeads.forEach(function (b, i) {
            h.push('<th rowspan="2" class="'
                + (i === 0 ? 'c-year' : (i === 1 ? 'c-cls'
                    : (b === '班级人数' ? 'c-enr' : (b === '参考人数' ? 'c-ref'
                        : (b === '去差后' ? 'c-trim' : '')))))
                + '" title="' + esc(headTips[b] || '') + '">' + b + '</th>');
        });
        subjects.forEach(function (s) {
            h.push('<th colspan="' + per + '" class="grp">' + esc(s) + '</th>');
        });
        h.push('</tr><tr>');
        subjects.forEach(function () {
            h.push('<th class="c-avg">去差<br>均分</th>');
            allLines.forEach(function (n) {
                h.push('<th data-line="' + esc(n) + '"'
                    + (lines.indexOf(n) >= 0 ? '' : ' class="ld-line-off"') + '>'
                    + esc(lineLabel(n)) + '</th>');
            });
        });
        h.push('</tr></thead><tbody>');

        function rowHtml(r, zebra) {
            var cls = r.is_grand ? 'grand' : (r.is_subtotal ? 'sub' : '');
            /* 2026-10-16 组合明细行（小计/总计按选科组合拆出）：底色随父行、去掉加粗 */
            if (r.is_combo) { cls += (cls ? ' ' : '') + 'combo'; }
            /* 2026-10-20 用户口径「表格有点太大，需要加底纹，防止看串行」：
               同一班级（含其组合明细行）共用一个底色，相邻班级交替白/灰。 */
            if (zebra) { cls += (cls ? ' ' : '') + zebra; }
            var ref = (r.exam_count == null
                ? (r.count == null ? null : r.count) : r.exam_count);
            var tc = (r.trim_count == null ? null : r.trim_count);
            var trimTip = (r.trim_n ? ('实际剔除 ' + r.trim_n + ' 人：' + r.class_name) : '未去差');
            var tds = [
                '<td class="c-year">' + esc(r.grade || '') + '</td>',
                '<td class="c-cls">' + esc(r.class_name || '') + '</td>',
                '<td class="c-enr" title="名册在册人数">'
                    + (r.enrolled == null ? '—' : r.enrolled) + '</td>',
                '<td class="c-ref" title="参加考试人数（有总分）">'
                    + (ref == null ? '—' : ref) + '</td>',
                '<td class="c-trim" title="' + esc(trimTip) + '">'
                    + (tc == null ? '—' : tc) + '</td>'
            ];
            // 与表头同口径：无方向不出方向列、无组合不出组合列
            if (hasDir) { tds.push('<td>' + dirBadge(r.direction) + '</td>'); }
            if (hasSel) { tds.push('<td class="ld-sel">' + selHtml(r.selections) + '</td>'); }
            tds.push('<td class="ld-type">' + esc(r.class_type || '—') + '</td>');
            var cells = r.cells || {};
            subjects.forEach(function (s, si) {
                var c = cells[s] || {};
                // 总分格（第一列）下方＝班主任；其余科目下方＝该科任课教师（本场快照）
                var extra = '';
                if (si === 0) {
                    if (r.headteacher) {
                        extra = '<div class="ld-teacher" title="本场考试的班主任">'
                            + esc(r.headteacher) + '</div>';
                    }
                } else if (c.teacher) {
                    extra = '<div class="ld-teacher" title="本场考试的任课教师">'
                        + esc(c.teacher) + '</div>';
                }
                /* 2026-10-16 用户要「不'去差'的那种均分」：人数是全体人数，均分也补一个
                   全体口径（含被剔学生）的小字；两者一致（没剔过人）时不重复显示 */
                var allAvg = '';
                if (c.avg !== null && c.avg !== undefined && c.avg !== c.trim_avg) {
                    allAvg = '<div class="ld-avg-all" title="全体均分（不去差、不剔人）">('
                        + c.avg + ')</div>';
                }
                /* 跨组合的合计行不给均分（物化生/物化地/物化政线各不相同，混算没有可比性）——
                   2026-10-16 用户口径：班级合计 / 方向小计 / 全年级都是这一档，
                   均分位显示「—」（班主任/任课教师仍照常显示在下方）。 */
                if (r.hide_avg) {
                    tds.push('<td class="ld-avg"><span class="ld-none" title="跨组合合计：'
                        + '组合不同、线不同，混算均分没有可比性；请看按组合明细行、'
                        + '上线率见右侧各线列">—</span>' + extra + '</td>');
                } else {
                    tds.push('<td class="ld-avg">' + fmtAvg(c.trim_avg) + allAvg + extra + '</td>');
                }
                var lm = c.lines || {};
                allLines.forEach(function (n) {
                    var ln = lm[n] || {};
                    tds.push('<td class="ld-on'
                        + (lines.indexOf(n) >= 0 ? '' : ' ld-line-off')
                        + '" data-line="' + esc(n) + '">'
                        + fmtOnline(ln.n, ln.rate) + '</td>');
                });
            });
            return '<tr class="' + cls + '">' + tds.join('') + '</tr>';
        }

        /* 小计/总计行 + 其后按组合拆出的明细行（2026-10-16 用户要求：组合列既有
           分组合人数，去差均分/上线也要按组合给）——同源数据，三个消费方（页面/
           导出/打印版）都渲染 combo_rows。 */
        function pushSummary(r) {
            h.push(rowHtml(r));
            (r.combo_rows || []).forEach(function (x) { h.push(rowHtml(x)); });
        }
        /* 2026-10-20 斑马纹：以「班级（+方向）」为分组单位切换底色，
           混组合班的合计行与组合明细行同色，看行时不会串到隔壁班。 */
        var zebraIdx = 0;
        var lastKey = null;
        function pushClassRow(r) {
            var key = (r.class_name || '') + '|' + (r.direction || '');
            if (key !== lastKey) { lastKey = key; zebraIdx += 1; }
            h.push(rowHtml(r, zebraIdx % 2 === 1 ? 'zb-a' : 'zb-b'));
        }
        (d.groups || []).forEach(function (g) {
            (g.rows || []).forEach(function (r) { pushClassRow(r); });
            if (g.subtotal) { pushSummary(g.subtotal); }
        });
        if (d.grand) { pushSummary(d.grand); }
        h.push('</tbody></table></div>');
        $body.html(h.join(''));

        // 实测表头第一行高度与前两列宽度 → CSS 变量（吸顶 top / 冻结列 left）
        var table = document.getElementById('ldTable');
        if (table && table.tHead && table.tHead.rows[0]) {
            table.style.setProperty('--ld-h1', table.tHead.rows[0].offsetHeight + 'px');
        }
        var yearTd = table ? table.querySelector('tbody td.c-year') : null;
        if (yearTd) { table.style.setProperty('--ld-f0w', yearTd.offsetWidth + 'px'); }
    }

    /* ==================== 2026-10-19 总览页下方图表 ====================
       用户口径（本日第五次调整后定稿）：
       · 「总分以及各学科的去差均分的各班的对比…分成 10 个图」→ 改人数口径；
       · 「改成依据分层的上线人数堆积图，然后上线率用折线表示，维持现状」；
       · 「去差均分（去掉），只在堆积图的条条上边加个数字即可」；
       · 「柱形图的上方不要显示人数了，给我显示去差均分」——
         柱顶数字改回均分（柱高仍是人数），两样都要看；
       · 「把人数标注在柱子上线的人数，然后上方应该有标签可以点击呀」——
         每段柱内标该段人数，图例把堆积段和折线都列出来、都能点；
       · 「上线率不能丢，最好也能标注一下；未上线的默认不标注，但是要有」——
         折线点上标百分比，未上线段保留但段内不标数字；
       · 「现在是已经点击过展示未上线人数的状态了，我希望他不展示」——
         「未上线人数」这一段默认收起（图例里置灰可点），其余层默认全开；
       · 「横轴按照方向、组合、班号顺序展示，该学科只有几个班考就只展示这几个班」——
         `chartSort` 排序（物理→历史→无方向，同方向内按组合、再按班号），
         每张图只保留该科 `count > 0` 的班（`renderCharts` 里筛）；
       · 「两种方案能让我自己点击勾选吗」——图例上方加勾选框
         「先按班型排」：勾上＝班型→方向→组合→班号，取消＝方向→组合→班号，
         选择存 localStorage。
       所以每张图 = 堆积柱（各分层上线人数 + 底部「未上线」，左轴人数）
                 + 折线（各分层上线率，右轴 %），共 10 张（总分 + 9 科）；
       柱顶数字＝该班该科的去差均分（不可见锚点系列，隐藏某层也不跑位）。
       数据与上表同源（同一份 API 返回），不另算口径：
       后端 lines[层].n 是「该层及以上人数」，独占段 = 本层 n − 更高一层 n。 */
    var charts = [];            // 当前页面的 ECharts 实例
    var lastChartData = null;   // 最近一次数据（主题切换时重绘）
    var resizeBound = false;

    /* 把 #rrggbb 加透明度（深色主题下「未上线」也能保持低对比） */
    function withAlpha(c, a) {
        var m = /^#([0-9a-f]{6})$/i.exec(String(c || ''));
        if (!m) { return c || ('rgba(148,163,184,' + a + ')'); }
        var v = parseInt(m[1], 16);
        return 'rgba(' + ((v >> 16) & 255) + ',' + ((v >> 8) & 255) + ','
            + (v & 255) + ',' + a + ')';
    }

    function chartColors() {
        var v = window.stuCssVar || function (n, f) { return f; };
        var pal = window.stulinkPalette ? window.stulinkPalette() : [
            '#3b82f6', '#8b5cf6', '#10b981', '#f59e0b', '#ef4444',
            '#06b6d4', '#ec4899', '#84cc16', '#f97316', '#6366f1'];
        return {
            text: v('--sl-text-muted', '#64748b'),
            line: v('--sl-border', '#e2e8f0'),
            muted: v('--sl-text-muted', '#94a3b8'),
            surface: v('--sl-surface', '#ffffff'),
            palette: pal
        };
    }

    function disposeCharts() {
        charts.forEach(function (c) {
            try { c.dispose(); } catch (e) { /* 已销毁 */ }
        });
        charts = [];
        $('#ldAvgCharts').empty();
    }

    /* 显示名：混组合明细行带组合；同班跨方向时带方向（否则两根同名柱） */
    function labeled(rows) {
        var cnt = {};
        rows.forEach(function (r) {
            cnt[r.class_name] = (cnt[r.class_name] || 0) + 1;
        });
        return rows.map(function (r) {
            var label = r.class_name || '';
            if (r.is_combo) {
                var s = (r.selections && r.selections[0] && r.selections[0].name) || '';
                if (s) { label += '·' + s; }
            } else if (cnt[r.class_name] > 1 && r.direction) {
                label += '·' + r.direction;
            }
            return { label: label, row: r };
        });
    }

    /* 横轴顺序（2026-10-19 用户口径）两种方案，图表标题右边可勾选切换：
         · 勾上「先按班型」：班型 → 方向 → 组合 → 班号
         · 取消勾选：     方向 → 组合 → 班号
       原来直接吃后端 groups 的顺序，同一方向里组合来回跳，看着乱。 */
    var SORT_STORE = 'stulink.gc.chartSort';
    var sortByType = true;      // 默认先按班型（用户口径：先班型再方向）
    try {
        var _savedSort = window.localStorage.getItem(SORT_STORE);
        if (_savedSort === 'dir') { sortByType = false; }
        if (_savedSort === 'type') { sortByType = true; }
    } catch (e) { /* 隐私模式读不到就按默认 */ }

    var CHART_TYPE_ORDER = { '卓越班': 0, '强基班': 1 };   // 未知班型排最后
    var CHART_DIR_ORDER = { '物理': 0, '历史': 1 };
    function chartDirRank(r) {
        var d = r.direction || '';
        return Object.prototype.hasOwnProperty.call(CHART_DIR_ORDER, d)
            ? CHART_DIR_ORDER[d] : 2;      // 无方向（全科）排最后
    }
    function chartTypeRank(r) {
        var t = r.class_type || '';
        if (!t) { return 8; }
        return Object.prototype.hasOwnProperty.call(CHART_TYPE_ORDER, t)
            ? CHART_TYPE_ORDER[t] : 5;
    }
    function chartCombo(r) {
        var s = (r.selections || [])[0];   // 后端已按人数降序，取主组合
        return (s && s.name) || '';
    }
    function chartClassNo(r) {
        var m = /(\d+)/.exec(String(r.class_name || ''));
        return m ? parseInt(m[1], 10) : 9999;
    }
    function chartSort(rows) {
        return rows.slice().sort(function (a, b) {
            if (sortByType) {                       // 方案 A：班型最优先
                var ta = chartTypeRank(a), tb = chartTypeRank(b);
                if (ta !== tb) { return ta - tb; }
                var na0 = a.class_type || '', nb0 = b.class_type || '';
                if (na0 !== nb0) { return na0 < nb0 ? -1 : 1; }
            }
            var x = chartDirRank(a) - chartDirRank(b);
            if (x) { return x; }
            var ca = chartCombo(a), cb = chartCombo(b);
            if (ca !== cb) { return ca < cb ? -1 : 1; }
            var na = chartClassNo(a) - chartClassNo(b);
            if (na) { return na; }
            var la = String(a.class_name || ''), lb = String(b.class_name || '');
            return la < lb ? -1 : (la > lb ? 1 : 0);
        });
    }

    /* 图表行：一班一根柱 —— 混组合班只取「班级合计」行（全班人数），
       不再画组合明细行（合计 48 + 明细 35 + 13 会重复算人，看着像 96 人）；
       均分时代因合计行 hide_avg 才改用明细行，改人数口径后不需要了。 */
    function chartRows(d) {
        var rows = [];
        (d.groups || []).forEach(function (g) {
            (g.rows || []).forEach(function (r) {
                if (!r.is_combo) { rows.push(r); }
            });
        });
        return labeled(chartSort(rows));
    }

    /* 堆积段：lines[层].n 是「该层及以上」人数（层名高→低），
       本层独占 = 本层 n − 更高一层 n；最低层之下＝未上线。
       返回 {segs:[{name,n}](高→低), off, total}。 */
    function countStacks(cell, layers) {
        var got = [];
        layers.forEach(function (name) {
            var ln = (cell.lines || {})[name];
            if (ln && typeof ln.n === 'number') {
                got.push({ name: name, n: ln.n });
            }
        });
        var segs = got.map(function (x, i) {
            var hi = i ? got[i - 1].n : 0;
            return { name: x.name, n: Math.max(0, x.n - hi) };
        });
        var total = cell.count || 0;
        var online = got.length ? got[got.length - 1].n : 0;
        return {
            segs: segs,
            off: got.length ? Math.max(0, total - online) : 0,
            total: total
        };
    }

    /* 一张图 = 分层上线人数堆积柱（左轴）+ 各分层上线率折线（右轴 %）。
       图例只列上线率折线，点一下显隐；默认只开特控线、本科线。
       柱顶数字＝去差均分（柱高才是人数）。 */
    function comboOption(subject, items, d, colors) {
        var layers = d.layers || [];
        // 一张图里有 ≥2 个组合时，横轴带上组合名（否则按组合分组后
        // 班号看着是跳的：03、04、10、05…，加上「·物化地」才看得懂）
        var comboSet = {};
        items.forEach(function (x) { comboSet[chartCombo(x.row)] = 1; });
        var showCombo = Object.keys(comboSet).filter(function (k) {
            return k;
        }).length > 1;
        var names = items.map(function (x) {
            var cmb = chartCombo(x.row);
            return (showCombo && cmb) ? (x.label + '·' + cmb) : x.label;
        });
        var stacks = items.map(function (x) {
            return countStacks((x.row.cells || {})[subject] || {}, layers);
        });
        var series = [];
        var legendData = [];
        var selected = {};
        var rotate = names.length > 9 ? 32 : 0;

        // 左轴留 35% 余量：柱顶落在 3/4 高处，柱顶均分那一排与
        // 上线率（右轴 0~100%）的数值标签错开，不会叠字；
        // 「柱高比」也能精确换算，供 labelLayout 再兜一层避让
        var maxTotal = 1;
        stacks.forEach(function (s) {
            maxTotal = Math.max(maxTotal, s.total || 0);
        });
        var yMax = Math.ceil(maxTotal * 1.35 / 10) * 10 || 10;

        function rateOf(x, name) {
            var c = (x.row.cells || {})[subject] || {};
            var ln = (c.lines || {})[name];
            return (ln && ln.rate !== null && ln.rate !== undefined)
                ? ln.rate : null;
        }

        /* 柱顶标签＝去差均分（挂在最顶那段，零高段也落在堆顶）；
           该科没成绩/没均分时留空，不显示占位数字。 */
        function avgLabel() {
            return {
                show: true, position: 'top', color: colors.text, fontSize: 9,
                formatter: function (p) {
                    var it = items[p.dataIndex] || {};
                    var c = (it.row.cells || {})[subject] || {};
                    var v = c.trim_avg;
                    return (v === null || v === undefined) ? '' : v;
                }
            };
        }

        /* 段内标签＝该段人数；太窄的段（不足 5%）不标，免得压到相邻段，
           精确数字看悬浮提示。 */
        function segLabel(color) {
            return {
                show: true, position: 'inside', color: color, fontSize: 9,
                formatter: function (p) {
                    var st = stacks[p.dataIndex] || { total: 0 };
                    var v = p.value;
                    if (!v || !st.total) { return ''; }
                    return (v * 20 >= st.total) ? v : '';
                }
            };
        }

        /* 各行共同存在的层（高→低）：某行缺该层线时补 0 */
        var segNames = layers.filter(function (name) {
            return stacks.some(function (s) {
                return s.segs.some(function (x) { return x.name === name; });
            });
        });
        function segOf(st, name) {
            for (var k = 0; k < st.segs.length; k++) {
                if (st.segs[k].name === name) { return st.segs[k].n; }
            }
            return 0;
        }

        if (!segNames.length) {
            /* 该科一层都没划：只画一根参考人数，别把全班算成「未上线」 */
            var only = {
                name: '参考人数', type: 'bar', yAxisIndex: 0, barMaxWidth: 24,
                data: stacks.map(function (s) { return s.total; }),
                itemStyle: { color: colors.palette[0], borderRadius: [3, 3, 0, 0] },
                label: avgLabel()
            };
            series.push(only);
        } else {
            // 自下而上：未上线（灰）→ 最低层 → … → 最高层
            series.push({
                // 「未上线」段保留，但默认不在段内标人数（用户口径）；
                // 名字带「人数」、灰色不透明到 0.55 —— 太淡会被看成
                // 图例里「未选中」的置灰项（用户反馈过）
                name: '未上线人数', type: 'bar', yAxisIndex: 0, stack: 'cnt',
                barMaxWidth: 24,
                data: stacks.map(function (s) { return s.off; }),
                itemStyle: { color: withAlpha(colors.muted, 0.55) },
                emphasis: { disabled: true }
            });
            for (var j = segNames.length - 1; j >= 0; j--) {
                var sn = segNames[j];
                var sname = lineLabel(sn) + '人数';
                series.push({
                    name: sname, type: 'bar', yAxisIndex: 0, stack: 'cnt',
                    barMaxWidth: 24,
                    data: stacks.map(function (s) { return segOf(s, sn); }),
                    itemStyle: {
                        color: colors.palette[j % colors.palette.length]
                    },
                    label: segLabel('#fff'),
                    emphasis: { disabled: true }
                });
            }
            // 图例：堆积段（高→低）+ 未上线，全部默认开启、可点隐藏
            legendData = segNames.map(function (n) {
                return lineLabel(n) + '人数';
            });
            legendData.push('未上线人数');
            // 用户口径：「未上线人数」默认不展示（图例里仍留着，点一下才出来）；
            // 其余堆积段默认全开
            legendData.forEach(function (n) {
                selected[n] = (n !== '未上线人数');
            });
            // 均分标签锚点：不可见的零高段堆在最上面，图例里隐藏某一层后
            // 数字仍停在新的堆顶（挂在最高层系列上会跟着被一起隐藏）
            series.push({
                name: '__avg', type: 'bar', yAxisIndex: 0, stack: 'cnt',
                data: stacks.map(function () { return 0; }),
                silent: true,
                emphasis: { disabled: true },
                label: avgLabel()
            });
        }

        var prefer = layers.filter(function (n) { return /特控|本科/.test(n); });
        if (!prefer.length) { prefer = (d.default_lines || []).slice(0, 2); }
        var lineBase = segNames.length || 1;   // 折线配色避开堆积段用过的颜色
        layers.forEach(function (name, i) {
            var nm = lineLabel(name) + '上线率';
            var lc = colors.palette[(i + lineBase) % colors.palette.length];
            series.push({
                name: nm, type: 'line', yAxisIndex: 1,
                data: items.map(function (x) { return rateOf(x, name); }),
                smooth: true, symbol: 'circle', symbolSize: 4, connectNulls: true,
                lineStyle: { width: 2 },
                itemStyle: { color: lc },
                // 折线数值也标出来：默认在点右侧；与柱顶均分标签同高时
                // 用 labelLayout 把数值上下挪开，别和均分叠字
                label: {
                    show: true, position: 'right', distance: 4,
                    fontSize: 8, color: lc,
                    backgroundColor: withAlpha(colors.surface, 0.82),
                    padding: [1, 2], borderRadius: 2,
                    formatter: function (p) {
                        return (p.value === null || p.value === undefined)
                            ? '' : p.value + '%';
                    }
                },
                labelLayout: function (p) {
                    var x = items[p.dataIndex];
                    var r = x ? rateOf(x, name) : null;
                    if (r === null || r === undefined) { return; }
                    var st = stacks[p.dataIndex] || { total: 0 };
                    var dy = 0;
                    // ① 撞上柱顶那一排均分标签：往上/下挪开
                    var diff = r / 100 - st.total / yMax;
                    if (Math.abs(diff) < 0.08) {
                        dy += (diff > 0 ? -16 : 16);
                    }
                    // ② 同一横坐标上有别的上线率数值贴得很近：低的那条让开
                    var near = null;
                    layers.forEach(function (o) {
                        if (o === name) { return; }
                        var rv = rateOf(x, o);
                        if (rv === null || rv === undefined) { return; }
                        if (Math.abs(rv - r) < 6 && (near === null
                                || Math.abs(rv - r) < Math.abs(near - r))) {
                            near = rv;
                        }
                    });
                    if (near !== null) {
                        if (r > near) {
                            // 高的那条在上、后画的会压住它 → 只要不贴顶就上移
                            if (r <= 88) { dy -= 14; }
                        } else {
                            dy += 14;
                        }
                    }
                    return dy ? { dy: dy } : undefined;
                }
            });
            legendData.push(nm);
            selected[nm] = prefer.indexOf(name) >= 0;
        });
        return {
            animationDuration: 260,
            // 图例改为换行排（原来 scroll 会分页 1/2，多层的场的标签点不到），
            // 顶部多留两行的高度；右侧留出折线数值标签的位置
            grid: { left: 6, right: 36, top: 54, bottom: 4, containLabel: true },
            legend: {
                top: 0, type: 'plain', itemWidth: 10, itemHeight: 8,
                itemGap: 8,
                textStyle: { color: colors.text, fontSize: 10 },
                data: legendData, selected: selected
            },
            tooltip: {
                trigger: 'axis', axisPointer: { type: 'shadow' },
                formatter: function (ps) {
                    if (!ps || !ps.length) { return ''; }
                    var st = stacks[ps[0].dataIndex] || { total: 0 };
                    var it = items[ps[0].dataIndex] || {};
                    var cc = (it.row && it.row.cells
                        ? it.row.cells[subject] : {}) || {};
                    var av = (cc.trim_avg === null || cc.trim_avg === undefined)
                        ? '—' : cc.trim_avg;
                    var out = esc(ps[0].axisValue) + '　参考 ' + st.total
                        + ' 人 · 去差均分 ' + av + '<br>';
                    ps.forEach(function (p) {
                        if (p.seriesName === '__avg') { return; }   // 均分锚点
                        var unit = p.seriesType === 'bar' ? ' 人' : '%';
                        out += p.marker + esc(p.seriesName) + '：'
                            + (p.value == null ? '—' : p.value + unit) + '<br>';
                    });
                    return out;
                }
            },
            xAxis: {
                type: 'category', data: names,
                axisLabel: { color: colors.text, fontSize: 10, interval: 0, rotate: rotate },
                axisTick: { show: false },
                axisLine: { lineStyle: { color: colors.line } }
            },
            yAxis: [{
                type: 'value', min: 0, max: yMax,
                axisLabel: { color: colors.text, fontSize: 10 },
                splitLine: { lineStyle: { color: colors.line } }
            }, {
                type: 'value', min: 0, max: 100,
                axisLabel: { color: colors.text, fontSize: 10, formatter: '{value}%' },
                splitLine: { show: false }
            }],
            series: series
        };
    }

    function renderCharts(d) {
        if (!window.echarts) { return; }
        disposeCharts();
        lastChartData = d;
        var items = chartRows(d);
        var subjects = d.subjects || [];
        if (!items.length || !subjects.length) {
            $('#ldCharts').addClass('d-none');
            return;
        }
        $('#ldCharts').removeClass('d-none');
        var colors = chartColors();

        // 各科只有选考该科的班参考 —— 该科没考的班不进这张图（2026-10-19 口径），
        // 顺带把只有一两个班考的科也画成一两根柱，不再拉一排空柱
        var cards = [];
        subjects.forEach(function (s) {
            var rows = items.filter(function (x) {
                var c = (x.row.cells || {})[s] || {};
                return (c.count || 0) > 0;
            });
            if (rows.length) { cards.push({ subject: s, rows: rows }); }
        });
        if (!cards.length) {
            $('#ldCharts').addClass('d-none');
            return;
        }

        // 总分 + 各科各一张图：堆积柱＝分层上线人数、折线＝各分层上线率
        var html = cards.map(function (c, i) {
            return '<div class="ld-chart-card"><div class="ld-chart-title">'
                + esc(c.subject)
                + '</div><div class="ld-chart" id="ldAvgChart' + i + '"></div></div>';
        }).join('');
        $('#ldAvgCharts').html(html);
        cards.forEach(function (c, i) {
            var el = document.getElementById('ldAvgChart' + i);
            if (!el) { return; }
            var ch = window.echarts.init(el);
            ch.setOption(comboOption(c.subject, c.rows, d, colors), true);
            charts.push(ch);
        });

        if (!resizeBound) {
            resizeBound = true;
            window.addEventListener('resize', function () {
                charts.forEach(function (c) { c.resize(); });
            });
        }
    }

    // 深浅色切换：图表颜色来自 CSS 变量，主题变了按新色重绘
    if (window.stuOnThemeChange) {
        window.stuOnThemeChange(function () {
            if (lastChartData) { renderCharts(lastChartData); }
        });
    }
})();
