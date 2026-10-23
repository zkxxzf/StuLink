/* StuLink v1.19.70.64 2026-10-10 全局对比
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
            // v1.19.70.64 本场考试不在可见列表（已删除 / 超出数据范围 / 已毕业）：
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
    // 横轴排序：默认按班级序号，勾上「先按班型排」＝班型优先（只重画图表，不重新拉数据）
    $('#ldSortType').prop('checked', sortByType).on('change', function () {
        sortByType = !!this.checked;
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
        // 重绘会把「放大」浮层连带销毁 → 同步解锁页面滚动
        $('body').removeClass('ld-chart-max-open');
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
    /* 2026-10-21 用户口径：横轴**默认按班级序号**（01班 → 02班 …），
       需要时用户手动勾「先按班型排」再切成班型优先；不记忆到 localStorage，
       每次进页面都回到默认的班级顺序。 */
    var sortByType = false;

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
                // 2026-10-21 用户口径：同一班型内**先按班号**（01→02→03…）；
                // 原来这里落到「方向 → 组合 → 班号」，同一班型里混组合的班会按
                // 组合名分组（物化地全部排在物化生之前），看起来就成了
                // 03/04/07/08/09/10 → 01/02/…，与班号顺序不符。
                var nt = chartClassNo(a) - chartClassNo(b);
                if (nt) { return nt; }
            } else {
                // 2026-10-21 用户口径：不勾「先按班型排」时，横轴按**班级序号从小到大**
                //（01班 → 02班 → …；同号再按方向 / 组合 / 班名兜底，保证顺序稳定）
                var n0 = chartClassNo(a) - chartClassNo(b);
                if (n0) { return n0; }
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

    /* 一张图 = 百分比堆积柱（每班都 100%）+ 各分层上线率折线（同一 0~100% 轴）。
       柱：自下而上按分数线由高到低堆积，未上线在最上；段内标人数。
       线：每层沿自己那一段的顶边走，把各班占比连起来看趋势。 */
    function comboOption(subject, items, d, colors, big) {
        var layers = d.layers || [];
        // 2026-10-21 用户口径：全屏放大态下柱更粗、字更大（同一个 option 换一套尺寸）
        var BIG = !!big;
        var BAR_W = BIG ? 60 : 26;
        function fs(n) { return BIG ? Math.round(n * 1.4) : n; }
        // 一张图里有 ≥2 个组合时，横轴带上组合名（否则按组合分组后
        // 班号看着是跳的：03、04、10、05…，加上「·物化地」才看得懂）
        var comboSet = {};
        items.forEach(function (x) { comboSet[chartCombo(x.row)] = 1; });
        var showCombo = Object.keys(comboSet).filter(function (k) {
            return k;
        }).length > 1;
        var names = items.map(function (x) {
            var cmb = chartCombo(x.row);
            // 混组合班的「班级合计」行不再挂主组合名（否则看着像只有物化地），
            // 它的组合口径改由柱顶的两个「去差均分」说明
            var mixed = ((x.row.selections || []).length > 1);
            return (showCombo && cmb && !mixed) ? (x.label + '·' + cmb) : x.label;
        });
        var stacks = items.map(function (x) {
            return countStacks((x.row.cells || {})[subject] || {}, layers);
        });
        var series = [];
        var legendData = [];
        var selected = {};
        var rotate = names.length > 9 ? 30 : 0;
        var countOfSeries = {};      // 系列名 → 各班人数（tooltip 用）

        /* 某班某科的某条线：{n（该线及以上人数）, rate（上线率%）} */
        function lineOf(x, name) {
            var c = (x.row.cells || {})[subject] || {};
            return (c.lines || {})[name] || null;
        }

        /* 该行在本科上的任课教师：总分列＝班主任，单科＝该科任课教师（来自本场快照） */
        function teacherOf(x) {
            if (!x || !x.row) { return ''; }
            if (subject === '总分') { return x.row.headteacher || ''; }
            var c = (x.row.cells || {})[subject] || {};
            return c.teacher || '';
        }

        /* 2026-10-21 用户口径：混组合班（如 03 班＝物化地 + 物化政）在柱头要显示**两个**
           去差均分，且必须与表格里按组合明细行显示的数一致 —— 从同班的组合明细行取。
           只收该科有成绩的组合（如 03 班的政治只有物化政有人考）。 */
        function comboAvgsOf(row) {
            var out = [];
            (d.groups || []).forEach(function (g) {
                (g.rows || []).forEach(function (r) {
                    if (!r.is_combo || r.class_name !== row.class_name) { return; }
                    if ((r.direction || '') !== (row.direction || '')) { return; }
                    var c = (r.cells || {})[subject] || {};
                    if (!(c.count > 0)) { return; }
                    out.push({ sel: chartCombo(r) || '组合', avg: c.trim_avg });
                });
            });
            return out;
        }

        /* 2026-10-21 用户口径（第二轮）：改成**百分比堆积柱**——每根柱都是 100%，
           段高＝该段人数占本班参考人数的比例，便于班与班之间横向比较。 */
        function pctOf(s, n) {
            return s.total ? +(n * 100 / s.total).toFixed(2) : 0;
        }

        /* 柱顶标签＝去差均分（2026-10-21 用户口径：分数上方要写清是「去差均分」）。
           纯组合班/单组合：两行「去差均分 / 数值」；
           混组合班（物化地+物化政）：每个组合各一行，与表格组合明细行一致。 */
        function avgLabel() {
            return {
                show: true, position: 'top', distance: 4,
                formatter: function (p) {
                    var it = items[p.dataIndex] || {};
                    var c = (it.row.cells || {})[subject] || {};
                    var combos = comboAvgsOf(it.row);
                    if (combos.length > 1) {
                        var lines = ['{t|去差均分}'];
                        combos.forEach(function (x) {
                            lines.push('{c|' + x.sel + ' ' + (x.avg == null ? '—' : x.avg) + '}');
                        });
                        return lines.join('\n');
                    }
                    var v = c.trim_avg;
                    if (combos.length === 1) { v = combos[0].avg; }   // 该科只有一个组合有成绩
                    return (v === null || v === undefined)
                        ? '' : '{t|去差均分}\n{v|' + v + '}';
                },
                rich: {
                    t: {
                        fontSize: fs(10), lineHeight: fs(12), color: colors.text,
                        align: 'center'
                    },
                    v: {
                        fontSize: fs(13), fontWeight: 600, lineHeight: fs(15),
                        color: colors.text, align: 'center'
                    },
                    c: {
                        fontSize: fs(10), lineHeight: fs(13), color: colors.text,
                        align: 'center'
                    }
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
            /* 该科一层都没划：一根满格柱，只标参考人数，别把全班算成「未上线」 */
            var totals = stacks.map(function (s) { return s.total; });
            countOfSeries['参考人数'] = totals;
            series.push({
                name: '参考人数', type: 'bar', yAxisIndex: 0, barMaxWidth: BAR_W,
                data: stacks.map(function () { return 100; }),
                itemStyle: { color: colors.palette[0], borderRadius: [4, 4, 0, 0] },
                label: {
                    show: true, position: 'inside', color: '#fff',
                    fontSize: fs(12), fontWeight: 600,
                    formatter: function (p) { return totals[p.dataIndex] || ''; }
                },
                emphasis: { disabled: true }
            });
        } else {
            /* 2026-10-21 用户口径：柱子显示**班级内全部学生**的占比，自下而上按
               分数线由高到低堆积 —— 清北（最高线）在最底、依次 985 / 211 / 特控 /
               本科，未上线压在最上面；以后新增分数线同样按高低插进这条顺序。 */
            segNames.forEach(function (sn, j) {
                var counts = stacks.map(function (s) { return segOf(s, sn); });
                var nm = lineLabel(sn) + '人数';
                countOfSeries[nm] = counts;
                series.push({
                    name: nm, type: 'bar', yAxisIndex: 0, stack: 'cnt',
                    barMaxWidth: BAR_W,
                    data: stacks.map(function (s, i) { return pctOf(s, counts[i]); }),
                    itemStyle: {
                        color: colors.palette[j % colors.palette.length]
                    },
                    label: {
                        // 段内标**人数**（柱高是百分比，人数才是教务要看的口径）
                        show: true, position: 'inside', color: '#fff',
                        fontSize: fs(11), fontWeight: 600,
                        formatter: function (p) {
                            var v = counts[p.dataIndex];
                            return (v == null || v === 0) ? '' : v;
                        }
                    },
                    emphasis: { disabled: true }
                });
            });
            // 未上线（灰）＝最后一段，堆在最上面
            var offs = stacks.map(function (s) { return s.off; });
            countOfSeries['未上线人数'] = offs;
            series.push({
                name: '未上线人数', type: 'bar', yAxisIndex: 0, stack: 'cnt',
                barMaxWidth: BAR_W,
                data: stacks.map(function (s, i) { return pctOf(s, offs[i]); }),
                itemStyle: { color: withAlpha(colors.muted, 0.55) },
                label: {
                    show: true, position: 'inside', color: '#fff',
                    fontSize: fs(11), fontWeight: 600,
                    formatter: function (p) {
                        var v = offs[p.dataIndex];
                        return v ? v : '';
                    }
                },
                emphasis: { disabled: true }
            });
            legendData = segNames.map(function (n) {
                return lineLabel(n) + '人数';
            });
            legendData.push('未上线人数');
            // 均分标签锚点：零高段堆在 100% 顶端，柱顶那排数字不随图例显隐跑掉
            series.push({
                name: '__avg', type: 'bar', yAxisIndex: 0, stack: 'cnt',
                data: stacks.map(function () { return 0; }),
                silent: true,
                emphasis: { disabled: true },
                label: avgLabel()
            });

            /* 2026-10-21 用户口径（第二轮）：加上**比例折线**把各班连起来 ——
               每条线走自己那一段的顶边（＝该线及以上占比），趋势一眼可见。 */
            segNames.forEach(function (name, i) {
                var lc = colors.palette[i % colors.palette.length];
                var nm = lineLabel(name) + '上线率';
                series.push({
                    name: nm, type: 'line', yAxisIndex: 0,
                    data: items.map(function (x) {
                        var ln = lineOf(x, name);
                        return (ln && ln.rate != null) ? ln.rate : null;
                    }),
                    smooth: true, symbol: 'circle', symbolSize: BIG ? 8 : 5,
                    connectNulls: true,
                    lineStyle: { width: BIG ? 3 : 2 },
                    itemStyle: { color: lc },
                    label: {
                        show: true, position: 'right', distance: 3,
                        fontSize: fs(10), color: lc,
                        backgroundColor: withAlpha(colors.surface, 0.85),
                        padding: [1, 2], borderRadius: 2,
                        formatter: function (p) {
                            return (p.value == null) ? '' : p.value + '%';
                        }
                    },
                    labelLayout: { hideOverlap: true },
                    z: 6,
                    emphasis: { disabled: true }
                });
                legendData.push(nm);
            });
            legendData.forEach(function (n) { selected[n] = true; });
        }
        return {
            animationDuration: 260,
            // 图例行数比原来多（堆积段 + 比例折线），顶部留两到三行；
            // 再加柱顶「去差均分」标签（混组合班是 3 行）的高度
            grid: {
                left: 8, right: BIG ? 84 : 52,
                top: BIG ? 152 : 104, bottom: 4, containLabel: true
            },
            legend: {
                top: 0, type: 'plain',
                itemWidth: BIG ? 18 : 12, itemHeight: BIG ? 13 : 9,
                itemGap: BIG ? 16 : 10,
                textStyle: { color: colors.text, fontSize: fs(12) },
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
                    var _combos = comboAvgsOf(it.row);
                    var av;
                    if (_combos.length > 1) {
                        av = _combos.map(function (x) {
                            return x.sel + ' ' + (x.avg == null ? '—' : x.avg);
                        }).join(' / ');
                    } else {
                        var _v = _combos.length === 1 ? _combos[0].avg : cc.trim_avg;
                        av = (_v === null || _v === undefined) ? '—' : _v;
                    }
                    var out = esc(ps[0].axisValue) + '　参考 ' + st.total
                        + ' 人 · 去差均分 ' + av + '<br>';
                    ps.forEach(function (p) {
                        if (('' + p.seriesName).indexOf('__') === 0) { return; }  // 均分锚点
                        var cnt = countOfSeries[p.seriesName];
                        if (cnt) {       // 堆积段：人数 + 占比
                            out += p.marker + esc(p.seriesName) + '：'
                                + (cnt[p.dataIndex] == null ? '—' : cnt[p.dataIndex] + ' 人')
                                + '（' + p.value + '%）<br>';
                        } else {         // 比例折线：只给百分比
                            out += p.marker + esc(p.seriesName) + '：'
                                + (p.value == null ? '—' : p.value + '%') + '<br>';
                        }
                    });
                    return out;
                }
            },
            xAxis: {
                type: 'category', data: names,
                axisLabel: {
                    interval: 0, rotate: rotate, color: colors.text,
                    // 2026-10-21 用户口径：班级下面再挂一行教师名（总分＝班主任，单科＝任课教师）
                    formatter: function (v, i) {
                        var t = teacherOf(items[i]);
                        return t ? '{n|' + v + '}\n{t|' + t + '}' : '{n|' + v + '}';
                    },
                    rich: {
                        n: {
                            fontSize: fs(12), lineHeight: fs(15),
                            color: colors.text, align: 'center'
                        },
                        t: {
                            fontSize: fs(10), lineHeight: fs(13),
                            color: colors.muted, align: 'center'
                        }
                    }
                },
                axisTick: { show: false },
                axisLine: { lineStyle: { color: colors.line } }
            },
            yAxis: [{
                type: 'value', min: 0, max: 100, interval: 20,
                axisLabel: {
                    color: colors.text, fontSize: fs(12),
                    formatter: '{value}%'
                },
                splitLine: { lineStyle: { color: colors.line } }
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
        // 2026-10-21 用户口径：每张图右上角加一个「放大」按钮，点一下铺满屏幕宽度（Esc 退出）；
        // 放大态标题要带上本场考试名（「2024级…月考（高三上）-总分」）。
        var examName = (d.exam && d.exam.name) || '';
        var html = cards.map(function (c, i) {
            return '<div class="ld-chart-card" data-idx="' + i + '"'
                + ' data-subject="' + esc(c.subject) + '" data-exam="' + esc(examName) + '">'
                + '<div class="ld-chart-title"><span class="ld-chart-name">' + esc(c.subject) + '</span>'
                + '<button type="button" class="ld-chart-max" title="放大到全屏宽度（再点一次或 Esc 退出）"'
                + ' aria-label="放大 ' + esc(c.subject) + ' 图表">'
                + '<i class="bi bi-arrows-fullscreen"></i><i class="bi bi-fullscreen-exit"></i>'
                + '</button></div>'
                + '<div class="ld-chart" id="ldAvgChart' + i + '"></div></div>';
        }).join('');
        $('#ldAvgCharts').html(html);
        cards.forEach(function (c, i) {
            var el = document.getElementById('ldAvgChart' + i);
            if (!el) { return; }
            var ch = window.echarts.init(el);
            // 放大态要重画一套「加粗柱 + 大字号」，所以把渲染入参挂在元素上备用
            el.__slCfg = { subject: c.subject, rows: c.rows, d: d, colors: colors };
            el.__slChart = ch;
            ch.setOption(comboOption(c.subject, c.rows, d, colors, false), true);
            charts.push(ch);
        });

        if (!resizeBound) {
            resizeBound = true;
            window.addEventListener('resize', function () {
                charts.forEach(function (c) { c.resize(); });
            });
        }
    }

    /* 2026-10-21 用户口径：每张图右上角的「放大」按钮 —— 点一下把该图铺满整个屏幕宽度
       （卡片改为固定在视口内的浮层，再点一次或按 Esc 退出）。
       放大态还要**柱更粗、字更大**：按 big 参数重画一遍 option，再 resize。 */
    /* 标题：常规只写科目名；放大态写成「考试名-科目」（用户口径 2026-10-21） */
    function setCardTitle($card, big) {
        var $nm = $card.find('.ld-chart-name');
        var sub = $card.attr('data-subject') || '';
        var ex = $card.attr('data-exam') || '';
        $nm.text((big && ex) ? (ex + '-' + sub) : sub);
    }
    function applyChartMode($card, big) {
        var el = $card.find('.ld-chart')[0];
        if (!el || !el.__slCfg || !el.__slChart) { return; }
        var cfg = el.__slCfg;
        try {
            el.__slChart.setOption(
                comboOption(cfg.subject, cfg.rows, cfg.d, cfg.colors, big), true);
            el.__slChart.resize();
        } catch (e) { /* 图表已销毁 */ }
    }
    function resizeChartCard($card) {
        var i = parseInt($card.attr('data-idx'), 10);
        var ch = charts[i];
        if (ch) { try { ch.resize(); } catch (e) { /* 已销毁 */ } }
    }
    function closeMaxChart() {
        var $c = $('.ld-chart-card.is-max').removeClass('is-max');
        $('body').removeClass('ld-chart-max-open');
        if ($c.length) {
            setTimeout(function () {
                setCardTitle($c, false);
                applyChartMode($c, false);
                resizeChartCard($c);
            }, 60);
        }
    }
    $(document).on('click', '.ld-chart-max', function () {
        var $card = $(this).closest('.ld-chart-card');
        var toMax = !$card.hasClass('is-max');
        $('.ld-chart-card.is-max').removeClass('is-max');
        $card.toggleClass('is-max', toMax);
        $('body').toggleClass('ld-chart-max-open', toMax);
        setTimeout(function () {
            setCardTitle($card, toMax);
            applyChartMode($card, toMax);
            resizeChartCard($card);
        }, 60);
    });
    $(document).on('keydown', function (e) {
        if (e.key === 'Escape' || e.keyCode === 27) { closeMaxChart(); }
    });

    // 深浅色切换：图表颜色来自 CSS 变量，主题变了按新色重绘
    if (window.stuOnThemeChange) {
        window.stuOnThemeChange(function () {
            if (lastChartData) { renderCharts(lastChartData); }
        });
    }
})();
