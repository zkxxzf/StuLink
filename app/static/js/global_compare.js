/* StuLink v1.19.47.30 2026-10-10 全局对比
 * 班级 × 学科宽表：固定列（年级/班级/人数/方向/属性）+ 每科（去差均分 + 各分数线）。
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
            // v1.19.47.30 本场考试不在可见列表（已删除 / 超出数据范围 / 已毕业）：
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
    $('#ldExport').on('click', function () {
        window.location = EXPORT_API
            + '?exam_id=' + encodeURIComponent($('#ldExam').val() || '')
            + '&direction=' + encodeURIComponent($('#ldDir').val() || '')
            + '&lines=' + encodeURIComponent(activeLines().join(','));
    });

    function load() {
        var eid = $('#ldExam').val();
        if (!eid) { return; }
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
            renderTable(lastData);
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
        var note = '口径：去差均分＝按班型剔除总分末尾 N 人（卓越班 2 人）后均分'
            + '（总分格下方为班主任、各科格下方为该科任课教师，取本场考试当时的快照）；'
            + '上线单元格＝人数 / 上线率（按各方向同名单科线判定）；分数线可勾选显示。';
        if (d.partial) {
            note += ' ⚠ 本场为分批导入（缺 ' + (d.partial.missing || []).join('、')
                + '），总分口径不完整。';
        }
        $('#ldMeta').html('<div>' + esc(meta) + '</div><div>' + esc(note) + '</div>');

        var subjects = d.subjects || [];
        if (!subjects.length) {
            $lines.empty();
            $body.html(emptyHtml('该考试没有可展示的科目成绩'));
            return;
        }
        renderLineToggles(d);
        renderTable(d);
    }

    function renderTable(d) {
        if (!d) { return; }
        var subjects = d.subjects || [];
        var lines = activeLines();
        var per = 1 + lines.length;

        var h = ['<div class="ld-wrap"><table class="ld-table" id="ldTable"><thead><tr>'];
        ['年级', '班级', '班级人数', '方向', '属性'].forEach(function (b, i) {
            h.push('<th rowspan="2" class="'
                + (i === 0 ? 'c-year' : (i === 1 ? 'c-cls' : '')) + '">' + b + '</th>');
        });
        subjects.forEach(function (s) {
            h.push('<th colspan="' + per + '" class="grp">' + esc(s) + '</th>');
        });
        h.push('</tr><tr>');
        subjects.forEach(function () {
            h.push('<th class="c-avg">去差<br>均分</th>');
            lines.forEach(function (n) { h.push('<th>' + esc(lineLabel(n)) + '</th>'); });
        });
        h.push('</tr></thead><tbody>');

        function rowHtml(r) {
            var cls = r.is_grand ? 'grand' : (r.is_subtotal ? 'sub' : '');
            var tds = [
                '<td class="c-year">' + esc(r.grade || '') + '</td>',
                '<td class="c-cls">' + esc(r.class_name || '') + '</td>',
                '<td>' + (r.count == null ? '—' : r.count) + '</td>',
                '<td>' + dirBadge(r.direction) + '</td>',
                '<td class="ld-type">' + esc(r.class_type || '—') + '</td>'
            ];
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
                tds.push('<td class="ld-avg">' + fmtAvg(c.trim_avg) + extra + '</td>');
                var lm = c.lines || {};
                lines.forEach(function (n) {
                    var ln = lm[n] || {};
                    tds.push('<td class="ld-on">' + fmtOnline(ln.n, ln.rate) + '</td>');
                });
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
