/* StuLink v1.19.0 2026-10-11
   考务导入前端：上传 → 识别（AI/自动）→ 工作表用途 + 列映射 → 预览 → 导入
   Key 走浏览器本机（window.StuLinkAI），服务器只用不存。
   Copyright (c) 2026 zkxxzf. Apache License 2.0 */
(function () {
    'use strict';
    var AID = window.AFFAIR_ID;
    var FIELDS = window.AFFAIR_FIELDS || {};
    var FILES = null;            // 保留 FileList，apply 时需再次上传
    var SHEETS = [];
    var NOTES = '';

    function fieldOptions() {
        var o = [['', '（忽略此列）']];
        ['no', 'name', 'class_name', 'exam_no', 'room', 'seat', 'room_seat',
         'selection', 'attend', 'room_loc'].forEach(function (k) {
            if (FIELDS[k]) { o.push([k, FIELDS[k]]); }
        });
        return o;
    }

    function msg(sel, ok, text) {
        $(sel).html($('<span>').addClass('text-' + (ok ? 'success' : 'danger')).text(text || ''));
    }

    /** 服务端自动归类（列号）→ {列名: 字段} */
    function autoFlat(sh) {
        var out = {}, heads = sh.headers || [];
        function put(i, v) { if (i != null && heads[i]) { out[heads[i]] = v; } }
        Object.keys(sh.auto || {}).forEach(function (f) { put(sh.auto[f], f); });
        Object.keys(sh.ai || {}).forEach(function (f) { put(sh.ai[f], f); });   // AI 优先覆盖
        return out;
    }

    function renderMaps() {
        var $box = $('#afMapBody').empty();
        SHEETS.forEach(function (sh, idx) {
            var flat = {};
            var heads = sh.headers || [];
            // 反查：字段值 → 列名
            function putFrom(src) {
                Object.keys(src || {}).forEach(function (f) {
                    var ci = src[f];
                    if (typeof ci === 'number' && heads[ci]) { flat[heads[ci]] = f; }
                });
            }
            putFrom(sh.auto); putFrom(sh.ai);
            var rowsInfo = (sh.header_rows && sh.header_rows.length > 1)
                ? ('表头第 ' + sh.header_rows.join('+') + ' 行（已合并）')
                : ('表头在第 ' + sh.header_row + ' 行');
            var card = $('<div>').addClass('border rounded p-2 mb-3');
            var head = $('<div>').addClass('d-flex align-items-center mb-2 flex-wrap gap-2')
                .append($('<b>').addClass('small').text('[工作表] ' + sh.name))
                .append($('<span>').addClass('text-muted small').text(
                    (sh.file ? ('文件：' + sh.file + '｜') : '') + rowsInfo))
                .append($('<select>').addClass('form-select form-select-sm af-kind')
                    .attr('data-idx', idx).css('width', '170px')
                    .append('<option value="students">学生名单</option>')
                    .append('<option value="rooms">考场位置</option>')
                    .append('<option value="skip">不使用此工作表</option>'));
            card.append(head);
            card.find('.af-kind').val(sh.kind || 'skip');

            var tbl = $('<table>').addClass('table table-sm mb-0');
            tbl.append($('<thead>').append('<tr><th style="width:42%">原表列名</th>'
                + '<th style="width:30%">样本值</th><th>对应字段</th></tr>'));
            var tbody = $('<tbody>');
            heads.forEach(function (h, ci) {
                if (!h) { return; }
                var sample = ((sh.sample || [])[0] || [])[ci] || '';
                var $sel = $('<select>').addClass('form-select form-select-sm af-col')
                    .attr('data-idx', idx).attr('data-col', h);
                fieldOptions().forEach(function (v) {
                    $sel.append($('<option>').val(v[0]).text(v[1]));
                });
                $sel.val(flat[h] || '');
                tbody.append($('<tr>')
                    .append($('<td>').addClass('small').attr('title', h).text(h))
                    .append($('<td>').addClass('small text-muted').text(sample))
                    .append($('<td>').append($sel)));
            });
            card.append(tbl.append(tbody));
            $box.append(card);
        });
    }

    function collectSpec() {
        var byIdx = {};
        $('.af-col').each(function () {
            var i = +$(this).data('idx'), col = $(this).data('col'), v = $(this).val();
            if (!v) { return; }
            var b = byIdx[i] = byIdx[i] || {};
            b[v] = col;
        });
        return SHEETS.map(function (sh, i) {
            return {name: sh.name,
                    kind: $('.af-kind[data-idx=' + i + ']').val() || 'skip',
                    mapping: byIdx[i] || {}};
        });
    }

    function postFiles(url, extra, cb) {
        var fd = new FormData();
        for (var i = 0; i < FILES.length; i++) { fd.append('files', FILES[i]); }
        Object.keys(extra || {}).forEach(function (k) { fd.append(k, extra[k]); });
        if (window.StuLinkAI && window.StuLinkAI.has() && url.indexOf('analyze') >= 0) {
            fd.append('ai', JSON.stringify(window.StuLinkAI.payload()));
        }
        return $.ajax({url: url, type: 'POST', data: fd, processData: false,
                       contentType: false, headers: {'X-CSRFToken': window.csrfToken || ''}})
                .done(cb);
    }

    $(function () {
        $('#afAnalyze').on('click', function () {
            var f = $('#afFiles')[0].files;
            if (!f || !f.length) { msg('#afMsg', false, '请先选择文件'); return; }
            FILES = f;
            var $b = $(this).prop('disabled', true).text('识别中…');
            msg('#afMsg', true, '正在读取文件…');
            postFiles('/grades/affairs/' + AID + '/ai-import/analyze', {}, function (res) {
                if (!res.success) { msg('#afMsg', false, res.message || '失败'); return; }
                SHEETS = res.sheets || [];
                NOTES = res.ai_error || '';
                renderMaps();
                $('#afNotes').text(NOTES);
                $('#afNotesBox, #afMapBox, #afRunBox').removeClass('d-none');
                msg('#afMsg', true, '已识别 ' + SHEETS.length + ' 个工作表，请核对用途与列映射');
            }).fail(function (x) {
                msg('#afMsg', false, (x.responseJSON && x.responseJSON.message) || '请求失败');
            }).always(function () {
                $b.prop('disabled', false).html('<i class="bi bi-magic"></i> 识别');
            });
        });

        function run(dry) {
            if (!FILES) { msg('#afRunMsg', false, '请先上传并识别'); return; }
            var extra = {spec: JSON.stringify(collectSpec()),
                         dry_run: dry ? '1' : '0',
                         replace: $('#afReplace').prop('checked') ? '1' : '0'};
            msg('#afRunMsg', true, dry ? '预览中…' : '导入中…');
            postFiles('/grades/affairs/' + AID + '/ai-import/apply', extra, function (res) {
                if (!res.success) {
                    msg('#afRunMsg', false, res.message || '失败');
                    if (res.errors && res.errors.length) {
                        $('#afPreviewBox').removeClass('d-none').text(JSON.stringify(res.errors, null, 1));
                    }
                    return;
                }
                $('#afPreviewBox').removeClass('d-none').text(JSON.stringify(
                    dry ? {preview: res.preview, errors: res.errors}
                        : {preview: res.preview, added: res.added, updated: res.updated,
                           removed: res.removed, errors: res.errors}, null, 1));
                if (dry) {
                    msg('#afRunMsg', true, '预览完成');
                } else {
                    msg('#afRunMsg', true, '导入成功，正在跳转…');
                    setTimeout(function () {
                        location.href = '/grades/affairs/' + AID;
                    }, 900);
                }
            }).fail(function (x) {
                msg('#afRunMsg', false, (x.responseJSON && x.responseJSON.message) || '请求失败');
            });
        }

        $('#afPreview').on('click', function () { run(true); });
        $('#afApply').on('click', function () {
            if (!confirm('确认导入？勾选“整场覆盖”时会替换本场现有考务名单。')) { return; }
            run(false);
        });
    });
})();
