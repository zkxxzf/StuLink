/* StuLink v1.19.51.64 2026-10-20
   限时练 AI 智能导入：上传 → 识别列 → 列映射（可改）→ 预览 → 导入
   Key 走浏览器本机（window.StuLinkAI），服务器只用不存。
   Copyright (c) 2026 zkxxzf. Apache License 2.0 */
(function () {
    'use strict';
    var $ = window.jQuery;
    if (!$) { return; }
    var IMP = window.TIMED_IMPORT || null;
    if (!IMP) { return; }

    function aiPayload() {
        if (window.StuLinkAI && typeof window.StuLinkAI.payload === 'function') {
            return window.StuLinkAI.payload() || {};
        }
        return {};
    }

    function esc(text) {
        return String(text == null ? '' : text)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function toast(msg, tone) {
        if (typeof window.stuToast === 'function') { window.stuToast(msg, tone); }
        else if (tone === 'err') { window.alert(msg); }
    }

    function msg(text, tone) {
        $('#aiMsg').html(text
            ? '<span class="text-' + (tone || 'muted') + '">' + text + '</span>'
            : '');
    }

    function mappingHtml(fields, headers, mapping) {
        var html = '';
        fields.forEach(function (f) {
            var opts = '<option value="">（不指定）</option>';
            headers.forEach(function (h, i) {
                var sel = (mapping && mapping[f.key] === i) ? ' selected' : '';
                opts += '<option value="' + i + '"' + sel + '>' +
                    (i + 1) + '. ' + esc(h || '（空）') + '</option>';
            });
            html += '<div class="col-6"><label class="form-label small mb-1">' +
                esc(f.name) + '</label>' +
                '<select class="form-select form-select-sm" data-field="' +
                f.key + '">' + opts + '</select></div>';
        });
        return html;
    }

    function readMapping() {
        var out = {};
        $('#aiMappingBody select[data-field]').each(function () {
            var v = $(this).val();
            out[$(this).data('field')] = (v === '' ? null : parseInt(v, 10));
        });
        return out;
    }

    function buildForm(dryRun) {
        var file = $('#aiFile')[0] && $('#aiFile')[0].files[0];
        if (!file) { toast('请先选择成绩文件', 'err'); return null; }
        var fd = new FormData();
        fd.append('file', file);
        fd.append('subject', $('#aiSubject').val());
        fd.append('mapping', JSON.stringify(readMapping()));
        fd.append('dry_run', dryRun ? '1' : '0');
        return fd;
    }

    function renderPreview(pv) {
        var html = '<div class="border rounded p-2">';
        html += '<div class="small mb-1">将写入 <b>' + pv.total_rows +
            '</b> 人：新增 ' + pv.diff.new_students +
            ' · 改分 ' + pv.diff.updated_rows +
            ' · 移除 ' + pv.diff.deleted_rows +
            ' · 错误 ' + pv.errors.length + '</div>';
        html += '<div class="table-responsive" style="max-height:220px">' +
            '<table class="table table-sm mb-1"><thead><tr>' +
            '<th>考号</th><th>姓名</th><th>班级</th><th class="text-end">分数</th>' +
            '</tr></thead><tbody>';
        pv.rows.forEach(function (r) {
            html += '<tr><td class="small">' + esc(r.zhixue_no || r.student_no) +
                '</td><td>' + esc(r.student_name) + '</td><td>' +
                esc(r.class_name) + '</td><td class="text-end">' + r.score +
                '</td></tr>';
        });
        if (!pv.rows.length) {
            html += '<tr><td colspan="4" class="text-muted text-center">无可导入行</td></tr>';
        }
        html += '</tbody></table></div>';
        if (pv.total_rows > pv.rows.length) {
            html += '<div class="small text-muted">仅显示前 ' + pv.rows.length +
                ' 行，实际 ' + pv.total_rows + ' 行</div>';
        }
        html += '<button class="btn btn-sm btn-success mt-2" id="aiConfirm" type="button">' +
            '<i class="bi bi-check-lg"></i> 确认导入</button></div>';
        $('#aiPreviewBox').html(html);
    }

    $(document).on('click', '#aiAnalyze', function () {
        var file = $('#aiFile')[0] && $('#aiFile')[0].files[0];
        if (!file) { toast('请先选择成绩文件', 'err'); return; }
        if (window.StuLinkAI && !window.StuLinkAI.has()) {
            msg('未配置 AI Key，使用内置列名规则识别', 'warning');
        }
        var $btn = $(this).prop('disabled', true);
        msg('正在识别列…', 'muted');
        var fd = new FormData();
        fd.append('file', file);
        fd.append('subject', $('#aiSubject').val());
        var cfg = aiPayload();
        if (cfg.api_key) { fd.append('ai', JSON.stringify(cfg)); }
        $.ajax({
            url: IMP.analyze, method: 'POST', data: fd,
            processData: false, contentType: false
        }).done(function (res) {
            if (!res || !res.success) {
                msg((res && res.message) || '识别失败', 'danger');
                return;
            }
            $('#aiMapping').removeClass('d-none');
            $('#aiMappingBody').html(mappingHtml(res.fields, res.headers,
                                                res.mapping));
            $('#aiPreviewBox').empty();
            msg(res.ai_error ? ('AI 未生效，已用内置规则：' + esc(res.ai_error))
                : '列已识别，可手工调整后预览',
                res.ai_error ? 'warning' : 'success');
        }).fail(function () {
            msg('请求失败：请检查文件格式或登录状态', 'danger');
        }).always(function () {
            $btn.prop('disabled', false);
        });
    });

    $(document).on('click', '#aiPreview', function () {
        var fd = buildForm(true);
        if (!fd) { return; }
        var $btn = $(this).prop('disabled', true);
        msg('正在解析…', 'muted');
        $.ajax({
            url: IMP.apply, method: 'POST', data: fd,
            processData: false, contentType: false
        }).done(function (res) {
            if (!res || !res.success) {
                msg((res && res.message) || '解析失败', 'danger');
                return;
            }
            msg('', 'muted');
            renderPreview(res.preview);
        }).fail(function () {
            msg('解析失败：文件可能过大或格式不支持', 'danger');
        }).always(function () {
            $btn.prop('disabled', false);
        });
    });

    $(document).on('click', '#aiConfirm', function () {
        if (!window.confirm('确认导入？本场该科目将按本次文件整科覆盖。')) { return; }
        var fd = buildForm(false);
        if (!fd) { return; }
        var $btn = $(this).prop('disabled', true).text('导入中…');
        $.ajax({
            url: IMP.apply, method: 'POST', data: fd,
            processData: false, contentType: false
        }).done(function (res) {
            if (!res || !res.success) {
                toast((res && res.message) || '导入失败', 'err');
                $btn.prop('disabled', false).text('确认导入');
                return;
            }
            var rep = (res.diff && res.diff.deleted_rows) || 0;
            toast('已导入 ' + res.applied + ' 人' +
                  (rep ? '，替换原有 ' + rep + ' 条' : ''), 'ok');
            location.href = IMP.detail + '&subject=' +
                encodeURIComponent($('#aiSubject').val());
        }).fail(function () {
            toast('导入失败：请重试', 'err');
            $btn.prop('disabled', false).text('确认导入');
        });
    });
})();
