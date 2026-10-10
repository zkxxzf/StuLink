/* StuLink v1.18.9.1 2026-10-09 全局对比
 * 班级 × 学科宽表：固定列（年级/班级/人数/方向/属性/班主任）+ 每科 3 列
 * （去差均分 / 特优线 / 本科线），分科时按方向分组并出「○○全年级」小计。
 * 依赖：jQuery（base.html 全局）；数据源 /grades/api/global-compare/report。
 * 渲染后实测表头高度与冻结列宽度，回写 CSS 变量（吸顶偏移/粘性列 left），
 * 避免写死像素在字体/缩放变化时错位。 */
(function () {
    'use strict';
    var examId = $('#ldApp').data('exam-id');
    var API = '/grades/api/global-compare/report';
    var EXPORT_API = '/grades/api/global-compare/export';
    var $body = $('#ldBody');

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
        return '<span class="ld-online"><span class="n">' + n + '</span>'
            + ' <span class="r">/ ' + (r === null || r === undefined ? 0 : r) + '%</span></span>';
    }
    function dirBadge(d) {
        if (!d) { return '<span class="ld-none">—</span>'; }
        var cls = d === '物理' ? 'ld-dir-phy' : (d === '历史' ? 'ld-dir-his' : 'ld-dir-all');
        return '<span class="ld-dir ' + cls + '">' + esc(d) + '</span>';
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
        if (!sel.val() && sel.find('option').length) { sel.prop('selectedIndex', 0); }
        if (!sel.find('option').length) {
            $body.html(emptyHtml('暂无可查看的考试'));
            return;
        }
        load();
    }).fail(function (x) {
        $body.html(emptyHtml(x.status === 403
            ? '您没有全局对比的查看权限（仅管理员/校级领导/年级长可访问）'
            : '考试列表加载失败（' + x.status + '），请刷新重试'));
    });

    $('#ldExam').on('change', load);
    $('#ldDir').on('change', load);
    $('#ldExport').on('click', function () {
        window.location = EXPORT_API
            + '?exam_id=' + encodeURIComponent($('#ldExam').val() || '')
            + '&direction=' + encodeURIComponent($('#ldDir').val() || '');
    });

    function load() {
        var eid = $('#ldExam').val();
        if (!eid) { return; }
        $body.html('<div class="text-center text-muted py-5"><div class="spinner-border"></div>'
            + '<div class="mt-2">加载中…</div></div>');
        $.getJSON(API + '?exam_id=' + encodeURIComponent(eid)
            + '&direction=' + encodeURIComponent($('#ldDir').val() || ''))
            .done(function (res) {
                if (!res.success) { $body.html(emptyHtml(res.message || '加载失败')); return; }
                render(res.data);
            })
            .fail(function (x) {
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

    /* ---------- 渲染 ---------- */
    function render(d) {
        fillDirSelect(d);
        var ex = d.exam || {};
        var meta = (ex.grade || '') + (ex.grade_label ? '（' + ex.grade_label + '）' : '')
            + ' · ' + (ex.name || '') + (ex.date ? ' · ' + ex.date : '')
            + ' · 共 ' + (d.subject_count != null ? d.subject_count
                : Math.max(0, (d.subjects || []).length - 1)) + ' 科';
        var note = '口径：去差均分＝按班型剔除总分末尾 N 人（卓越班 2 人）后均分；'
            + '特优/本科线＝各方向总分前两层的同名单科线；上线单元格＝人数 / 上线率。';
        if (d.partial) {
            note += ' ⚠ 本场为分批导入（缺 ' + (d.partial.missing || []).join('、')
                + '），总分口径不完整。';
        }
        $('#ldMeta').html('<div>' + esc(meta) + '</div><div>' + esc(note) + '</div>');

        var subjects = d.subjects || [];
        if (!subjects.length) { $body.html(emptyHtml('该考试没有可展示的科目成绩')); return; }
        var l1 = lineLabel(d.l1_name), l2 = lineLabel(d.l2_name);

        var h = ['<div class="ld-wrap"><table class="ld-table" id="ldTable"><thead><tr>'];
        ['年级', '班级', '班级人数', '方向', '属性', '班主任'].forEach(function (b, i) {
            h.push('<th rowspan="2" class="'
                + (i === 0 ? 'c-year' : (i === 1 ? 'c-cls' : '')) + '">' + b + '</th>');
        });
        subjects.forEach(function (s) {
            h.push('<th colspan="3" class="grp">' + esc(s) + '</th>');
        });
        h.push('</tr><tr>');
        subjects.forEach(function () {
            h.push('<th>去差均分</th><th>' + esc(l1) + '</th><th>' + esc(l2) + '</th>');
        });
        h.push('</tr></thead><tbody>');

        function rowHtml(r) {
            var cls = r.is_grand ? 'grand' : (r.is_subtotal ? 'sub' : '');
            var tds = [
                '<td class="c-year">' + esc(r.grade || '') + '</td>',
                '<td class="c-cls">' + esc(r.class_name || '') + '</td>',
                '<td>' + (r.count == null ? '—' : r.count) + '</td>',
                '<td>' + dirBadge(r.direction) + '</td>',
                '<td class="ld-type">' + esc(r.class_type || '—') + '</td>',
                '<td>' + esc(r.headteacher || '—') + '</td>'
            ];
            var cells = r.cells || {};
            subjects.forEach(function (s) {
                var c = cells[s] || {};
                tds.push('<td class="ld-avg">' + fmtAvg(c.trim_avg) + '</td>');
                tds.push('<td>' + fmtOnline(c.l1_n, c.l1_rate) + '</td>');
                tds.push('<td>' + fmtOnline(c.l2_n, c.l2_rate) + '</td>');
            });
            return '<tr class="' + cls + '">' + tds.join('') + '</tr>';
        }

        (d.groups || []).forEach(function (g) {
            (g.rows || []).forEach(function (r) { h.push(rowHtml(r)); });
            if (g.subtotal) { h.push(rowHtml(g.subtotal)); }
        });
        if (d.grand) { h.push(rowHtml(d.grand)); }
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
})();
