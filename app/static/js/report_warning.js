/* StuLink v1.9.2 成绩汇报区 · 板块五：压线提醒
 * 总分层线上下浮动范围内的临界生名单（各科成绩 + 方向排名），可框选粘贴 PPT。 */
(function () {
    'use strict';
    var U = null;
    var range = { above: '', below: '' };  // 空串表示首次用后端默认配置
    var nlSeq = 0;                         // 竞态序号：只渲染最后一次查询
    var waitTicks = 0, waiting = false;

    function examId() { return $('#rpExam').val(); }
    function dirVal() { return $('#rpDir').val() || ''; }
    function layerVal() { return $('#rpLayer').val() || ''; }
    function ready() { return !!(window._rpUI && examId()); }

    /* 考试/方向切换、层列表重建都要重查；首屏下拉开就绪前限时等待（≤12 秒），不重复请求 */
    function tryBoot() {
        if (ready()) { waiting = false; U = window._rpUI; loadNearLine(); }
        else if (++waitTicks < 60) { waiting = true; setTimeout(tryBoot, 200); }
    }
    $(document).on('rp:context rp:layers', function () {
        if (ready()) { U = window._rpUI; loadNearLine(); }
        else if (!waiting) tryBoot();
    });
    $(tryBoot);

    function toolbarHtml(d) {
        return '<div class="rp-toolbar no-copy text-start">'
            + '线上 <input id="rpAbove" type="number" min="0" max="100" value="' + U.esc(d.above)
            + '" class="form-control form-control-sm d-inline-block w-auto"> 分以内　'
            + '线下 <input id="rpBelow" type="number" min="0" max="100" value="' + U.esc(d.below)
            + '" class="form-control form-control-sm d-inline-block w-auto"> 分以内　'
            + '<button id="rpNearGo" class="btn btn-sm btn-danger">查询压线名单</button>'
            + '<span class="text-muted small ms-2">层线：' + U.esc(d.layer) + ' ' + U.num(d.line)
            + ' 分　线上 <b class="text-success">' + d.above_n + '</b> 人，'
            + '线下 <b class="text-danger">' + d.below_n + '</b> 人（线下在前）</span></div>';
    }

    function ensureBox(id) {
        var box = $('#' + id);
        if (!box.length) box = $('<div class="rp-section" id="' + id + '">').appendTo('#rpBody');
        return box;
    }

    function loadNearLine() {
        var box = ensureBox('rpNL');
        var my = ++nlSeq;
        var qs = '?exam_id=' + examId() + '&direction=' + encodeURIComponent(dirVal())
            + '&layer=' + encodeURIComponent(layerVal());
        if (range.above !== '') qs += '&above=' + range.above + '&below=' + range.below;
        $.getJSON('/grades/api/report/near-line' + qs, function (res) {
            if (my !== nlSeq) return;  // 已被更新的查询取代
            if (!res.success) { box.html(U.sectionHead('压线提醒', '') + U.errHtml(res.message)); return; }
            var d = res.data;
            range.above = d.above; range.below = d.below;
            box.html('').append(U.sectionHead('压线提醒（' + d.direction + ' · ' + d.layer + '线 '
                + d.line + ' 分）', d.exam.name));
            box.append(toolbarHtml(d));
            if (!d.rows.length) {
                box.append(U.errHtml('该范围内没有学生'));
            } else {
                box.append(buildNearTable(d));
                box.append(U.copyBar('rpNLTbl'));
            }
            $('#rpNearGo').on('click', function () {
                range.above = $('#rpAbove').val() || 0;
                range.below = $('#rpBelow').val() || 0;
                loadNearLine();
            });
        }).fail(function (x) {
            box.html(U.sectionHead('压线提醒', '')
                + U.errHtml(x.status === 403 ? '无查看权限' : ('加载失败（' + x.status + '）')));
        });
    }

    function buildNearTable(d) {
        var S = U.S, t = U.el('table', S.table); t.id = 'rpNLTbl';
        var thead = U.el('thead'), tr = U.el('tr');
        ['班级', '班主任', '姓名', '状态', '总分', '差线', '方向排名'].forEach(function (h) {
            tr.appendChild(U.el('th', S.th, h));
        });
        d.subjects.forEach(function (s) { tr.appendChild(U.el('th', S.th, s.label)); });
        thead.appendChild(tr); t.appendChild(thead);
        var tb = U.el('tbody');
        d.rows.forEach(function (r, i) {
            var btr = U.el('tr'), stl = S.td + (i % 2 ? S.tdAlt : '');
            // 状态用红绿文字（内联样式保证粘贴 PPT 后仍醒目）；差线带正负号
            var online = r.status === '线上';
            var stStyle = stl + (online ? 'color:#548235;' : 'color:#c00000;');
            btr.appendChild(U.el('td', stl, r.class_name));
            btr.appendChild(U.el('td', stl + S.label, r.headteacher || '—'));
            btr.appendChild(U.el('td', stl, r.name));
            btr.appendChild(U.el('td', stStyle, r.status));
            btr.appendChild(U.el('td', stl, r.score));
            btr.appendChild(U.el('td', stStyle, (r.gap > 0 ? '+' : '') + r.gap));
            btr.appendChild(U.el('td', stl, U.num(r.rank_dir)));
            d.subjects.forEach(function (s) { btr.appendChild(U.el('td', stl + S.label, U.num(r[s.key]))); });
            tb.appendChild(btr);
        });
        t.appendChild(tb);
        var wrap = U.el('div', '');
        wrap.className = 'rp-table-wrap';
        wrap.appendChild(t);
        return wrap;
    }
})();
