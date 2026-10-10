/* StuLink v1.19.0 2026-10-10
   AI 智能导入前端：上传 → AI 识别 → 列映射（可改）→ 预览 → 导入
   Key 走浏览器本机（window.StuLinkAI），服务器只用不存。
   Copyright (c) 2026 zkxxzf. Apache License 2.0 */
(function () {
    'use strict';
    var EID = window.AI_IMPORT_EXAM;
    var SUBJECTS = window.AI_SUBJECTS || [];
    var KIND_LABEL = window.AI_KIND_LABEL || {};
    var TOKEN = '';
    var SHEETS = [];          // 服务端返回的 sheet 概要
    var SUGGEST = {};         // AI 建议

    // 每列可选的用途
    function fieldOptions() {
        var o = [['', '（忽略此列）'], ['no', '学号'], ['name', '姓名'],
                 ['class_name', '班级'], ['exam_no', '考号/准考证号'],
                 ['total', '总分（主分）'], ['raw_total', '总分（原始分）']];
        SUBJECTS.forEach(function (s) { o.push([s, '科目：' + s]); });
        SUBJECTS.forEach(function (s) { o.push(['raw:' + s, '原始分：' + s]); });
        return o;
    }

    function msg(sel, ok, text) {
        $(sel).html($('<span>').addClass('text-' + (ok ? 'success' : 'danger')).text(text || ''));
    }

    /** 把 AI 的 cols/subjects/raw_subjects 摊平成 {列名: 字段} */
    function flatten(map) {
        var out = {};
        var m = map || {};
        Object.keys(m.cols || {}).forEach(function (k) {
            if (m.cols[k]) out[m.cols[k]] = k;
        });
        Object.keys(m.subjects || {}).forEach(function (sub) {
            if (m.subjects[sub]) out[m.subjects[sub]] = sub;
        });
        Object.keys(m.raw_subjects || {}).forEach(function (sub) {
            if (m.raw_subjects[sub]) out[m.raw_subjects[sub]] = 'raw:' + sub;
        });
        return out;
    }

    function renderMaps() {
        var $box = $('#mapBody').empty();
        if (!SHEETS.length) { return; }
        SHEETS.forEach(function (sh, idx) {
            var flat = flatten(sh.map);
            var card = $('<div>').addClass('border rounded p-2 mb-3');
            var head = $('<div>').addClass('d-flex align-items-center mb-2 flex-wrap gap-2')
                .append($('<b>').addClass('small').text('[工作表] ' + sh.name))
                .append($('<span>').addClass('text-muted small').text(
                    sh.file ? ('文件：' + sh.file + '｜') : '' + '表头在第 ' + sh.header_row + ' 行'))
                .append($('<select>').addClass('form-select form-select-sm ai-role')
                    .attr('data-idx', idx).css('width', '150px')
                    .append('<option value="converted">这份是：赋分成绩</option>')
                    .append('<option value="raw">这份是：原始成绩</option>')
                    .append('<option value="both">同表内两者都有</option>'));
            // AI 判断的整表角色作为默认值
            var role = (SUGGEST.score_kind === 'raw') ? 'raw'
                     : (SUGGEST.score_kind === 'both') ? 'both' : 'converted';
            card.append(head.append($('<span>').addClass('ms-auto badge bg-light text-dark border')
                .attr('id', 'roleHint' + idx).text(KIND_LABEL[SUGGEST.score_kind] || '')));
            card.find('.ai-role').val(role);
            var tbl = $('<table>').addClass('table table-sm mb-0');
            var trh = $('<tr>').append('<th style="width:42%">原表列名</th>'
                + '<th style="width:30%">样本值</th><th>对应字段</th>');
            tbl.append($('<thead>').append(trh));
            var tbody = $('<tbody>');
            (sh.headers || []).forEach(function (h, ci) {
                if (!h) { return; }
                var sample = ((sh.sample || [])[0] || [])[ci] || '';
                var $sel = $('<select>').addClass('form-select form-select-sm ai-col')
                    .attr('data-idx', idx).attr('data-col', h);
                fieldOptions().forEach(function (v) {
                    $sel.append($('<option>').val(v[0]).text(v[1]));
                });
                $sel.val(flat[h] || (SUBJECTS.indexOf(h) >= 0 ? h : ''));
                tbody.append($('<tr>')
                    .append($('<td>').addClass('small').attr('title', h).text(h))
                    .append($('<td>').addClass('small text-muted').text(sample))
                    .append($('<td>').append($sel)));
            });
            card.append(tbl.append(tbody));
            $box.append(card);
        });
    }

    /** 收集映射 → 后端 sheets 参数 */
    function collectSheets() {
        var byIdx = {};
        $('.ai-col').each(function () {
            var i = +$(this).data('idx'), col = $(this).data('col'), v = $(this).val();
            if (!v) { return; }
            var b = byIdx[i] = byIdx[i] || {cols: {}, subjects: {}, raw_subjects: {}};
            if (v.indexOf('raw:') === 0) {
                b.raw_subjects[v.slice(4)] = col;
            } else if (SUBJECTS.indexOf(v) >= 0) {
                b.subjects[v] = col;
            } else {
                b.cols[v] = col;
            }
        });
        return SHEETS.map(function (sh, i) {
            var role = $('.ai-role[data-idx=' + i + ']').val() || 'converted';
            var m = byIdx[i] || {cols: {}, subjects: {}, raw_subjects: {}};
            // 同一 sheet 内“两者都有” → 原始分列归 raw_subjects；
            // 若整表是原始表，则所有科目列按原始处理（后端会按 role 再处理一次）
            return {name: sh.name, mapping: m, score_kind: role};
        });
    }

    function post(url, body) {
        return $.ajax({url: url, type: 'POST', contentType: 'application/json',
                       headers: {'X-CSRFToken': window.csrfToken || ''},
                       data: JSON.stringify(body || {})});
    }

    $(function () {
        $('#aiAnalyze').on('click', function () {
            var f = $('#aiFiles')[0].files;
            if (!f || !f.length) { msg('#aiMsg', false, '请先选择文件'); return; }
            if (window.StuLinkAI && !window.StuLinkAI.has()
                && !confirm('还没配置本机 AI Key，将跳过 AI 识别、改为手工指定列映射。\n\n继续吗？')) {
                return;
            }
            var fd = new FormData();
            for (var i = 0; i < f.length; i++) { fd.append('files', f[i]); }
            if (window.StuLinkAI && window.StuLinkAI.has()) {
                fd.append('ai', JSON.stringify(window.StuLinkAI.payload()));
            }
            var $b = $(this).prop('disabled', true).text('识别中…');
            msg('#aiMsg', true, '正在读取文件' + (window.StuLinkAI && window.StuLinkAI.has()
                ? '并请 AI 识别…' : '…'));
            $.ajax({url: '/grades/exams/' + EID + '/ai-import/analyze', type: 'POST',
                    data: fd, processData: false, contentType: false,
                    headers: {'X-CSRFToken': window.csrfToken || ''}})
                .done(function (res) {
                    if (!res.success) { msg('#aiMsg', false, res.message || '失败'); return; }
                    TOKEN = res.token;
                    SHEETS = (res.sheets || []).map(function (s, i) {
                        var k = (res.suggest && res.suggest.sheets) || [];
                        var m = null;
                        for (var j = 0; j < k.length; j++) {
                            if (k[j].name === s.name) { m = k[j]; break; }
                        }
                        s.map = m || null;
                        return s;
                    });
                    SUGGEST = (res.suggest && typeof res.suggest === 'object') ? res.suggest : {};
                    var kind = SUGGEST.score_kind || 'unknown';
                    $('#aiBox').removeClass('d-none');
                    $('#aiKind').html('<b>AI 判断：这份数据是 </b><span class="badge bg-primary">'
                        + (KIND_LABEL[kind] || '无法判断') + '</span>'
                        + '<span class="text-muted small ms-2">若不对，请在下方每个工作表右侧手动改“这份是”</span>');
                    var qs = SUGGEST.questions || [];
                    $('#aiQuestions').html(qs.length
                        ? '<div class="alert alert-warning py-2 small mb-2"><b>AI 需要你确认：</b><br>'
                          + qs.map(esc).join('<br>') + '</div>' : '');
                    $('#aiNotes').text(SUGGEST.notes || res.ai_error || '');
                    renderMaps();
                    $('#mapBox, #runBox').removeClass('d-none');
                    msg('#aiMsg', true, '已识别 ' + SHEETS.length + ' 个工作表，请核对列映射'
                        + (res.ai_error ? '（' + res.ai_error + '）' : ''));
                })
                .fail(function (x) {
                    msg('#aiMsg', false, (x.responseJSON && x.responseJSON.message) || '请求失败');
                })
                .always(function () {
                    $b.prop('disabled', false).html('<i class="bi bi-magic"></i> 让 AI 识别');
                });
        });

        function run(dry) {
            if (!TOKEN) { msg('#aiRunMsg', false, '请先上传并识别'); return; }
            var body = {token: TOKEN, sheets: collectSheets(), dry_run: !!dry,
                        mode: $('#aiModeB').prop('checked') ? 'B' : 'A'};
            msg('#aiRunMsg', true, dry ? '预览中…' : '导入中…');
            post('/grades/exams/' + EID + '/ai-import/apply', body)
                .done(function (res) {
                    if (!res.success) {
                        msg('#aiRunMsg', false, res.message || '失败');
                        if (res.errors && res.errors.length) {
                            $('#aiPreviewBox').removeClass('d-none')
                                .text(JSON.stringify(res.errors, null, 1));
                        }
                        return;
                    }
                    if (dry) {
                        msg('#aiRunMsg', true, '预览完成');
                        $('#aiPreviewBox').removeClass('d-none')
                            .text(JSON.stringify(res.preview, null, 1));
                    } else {
                        msg('#aiRunMsg', true, '导入成功，正在跳转…');
                        var s = res.summary || {};
                        setTimeout(function () {
                            location.href = '/grades/exams/' + EID;
                        }, 800);
                        $('#aiPreviewBox').removeClass('d-none').text(JSON.stringify({
                            summary: s, raw_cells: res.raw_cells,
                            errors: (res.errors || []).slice(0, 10)}, null, 1));
                    }
                })
                .fail(function (x) {
                    msg('#aiRunMsg', false, (x.responseJSON && x.responseJSON.message) || '请求失败');
                });
        }

        function esc(s) {
            return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
                return {'&': '&amp;', '<': '&lt;', '>': '&gt;',
                        '"': '&quot;', "'": '&#39;'}[c];
            });
        }

        $('#aiPreview').on('click', function () { run(true); });
        $('#aiApply').on('click', function () {
            if (!confirm('确认导入？勾选“整场覆盖”时会替换本场现有成绩。')) { return; }
            run(false);
        });
    });
})();
