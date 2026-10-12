/* StuLink v1.19.0 2026-10-10
   AI 智能导入前端：上传 → AI 识别 → 列映射（可改）→ 预览 → 导入
   Key 走浏览器本机（window.StuLinkAI），服务器只用不存。
   Copyright (c) 2026 zkxxzf. Apache License 2.0 */
(function () {
    'use strict';
    var EID = window.AI_IMPORT_EXAM;
    var SUBJECTS = window.AI_SUBJECTS || [];
    var KIND_LABEL = window.AI_KIND_LABEL || {};
    var AFFAIR_FIELDS = window.AFFAIR_FIELDS || {};   // 考务字段：考生名单 / 考场信息表
    var SCHEDULE_KIND = window.SCHEDULE_KIND || 'exam_schedule';   // 考试时间表/监考表（划分场次）
    var SCHEDULE_KIND_LABEL = window.SCHEDULE_KIND_LABEL || '考试时间表 / 监考表（划分场次）';
    var TOKEN = '';
    var SHEETS = [];          // 服务端返回的 sheet 概要
    var SUGGEST = {};         // AI 建议
    var IMG_SESSIONS = [];    // 图片识图得到的场次（可编辑，导入时回传给服务端）

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

    /** 服务端「自动归类」（按列号）→ {列名: 字段}，用于无 AI 时预填 */
    function autoFlat(sh, headers) {
        var out = {};
        var a = sh.auto || {};
        function put(idx, val) {
            if (idx != null && headers[idx]) { out[headers[idx]] = val; }
        }
        Object.keys(a.cols || {}).forEach(function (f) { put(a.cols[f], f); });
        Object.keys(a.subjects || {}).forEach(function (s) { put(a.subjects[s], s); });
        Object.keys(a.raw_subjects || {}).forEach(function (s) {
            put(a.raw_subjects[s], 'raw:' + s);
        });
        return out;
    }

    /** 考务字段选项（考生名单 / 考场信息表） */
    function affairFieldOptions() {
        var o = [['', '（忽略此列）']];
        Object.keys(AFFAIR_FIELDS).forEach(function (k) {
            if (AFFAIR_FIELDS[k]) { o.push([k, AFFAIR_FIELDS[k]]); }
        });
        return o;
    }

    function isAffairRole(role) {
        return role === 'affair_students' || role === 'affair_rooms'
            || role === 'affair_seats';
    }

    /** 服务端「考务自动归类」（{字段: 列号}）→ {列名: 字段} */
    function affairAutoFlat(sh) {
        var out = {}, a = sh.affair_auto || {}, heads = sh.headers || [];
        Object.keys(a).forEach(function (f) {
            if (a[f] != null && heads[a[f]]) { out[heads[a[f]]] = f; }
        });
        return out;
    }

    /** 列映射表：成绩角色 → 科目/字段；考务角色 → 考务字段 */
    function buildColTable(sh, idx, role, scoreFlat) {
        var affair = isAffairRole(role);
        var flat = affair ? affairAutoFlat(sh) : (scoreFlat || {});
        var opts = affair ? affairFieldOptions() : fieldOptions();
        var tbl = $('<table>').addClass('table table-sm mb-0');
        tbl.append($('<thead>').append($('<tr>').append(
            '<th style="width:42%">原表列名</th>'
            + '<th style="width:30%">样本值</th><th>对应字段</th>')));
        var tbody = $('<tbody>');
        (sh.headers || []).forEach(function (h, ci) {
            if (!h) { return; }
            var sample = ((sh.sample || [])[0] || [])[ci] || '';
            var $sel = $('<select>').addClass('form-select form-select-sm ai-col')
                .attr('data-idx', idx).attr('data-col', h);
            opts.forEach(function (v) {
                $sel.append($('<option>').val(v[0]).text(v[1]));
            });
            $sel.val(flat[h] || (!affair && SUBJECTS.indexOf(h) >= 0 ? h : ''));
            tbody.append($('<tr>')
                .append($('<td>').addClass('small').attr('title', h).text(h))
                .append($('<td>').addClass('small text-muted').text(sample))
                .append($('<td>').append($sel)));
        });
        return tbl.append(tbody);
    }

    function renderMaps() {
        var $box = $('#mapBody').empty();
        if (!SHEETS.length) { return; }
        SHEETS.forEach(function (sh, idx) {
            var flat = flatten(sh.map);
            if (!Object.keys(flat).length) { flat = autoFlat(sh, sh.headers || []); }
            var rowsInfo = (sh.header_rows && sh.header_rows.length > 1)
                ? ('表头第 ' + sh.header_rows.join('+') + ' 行（已合并）')
                : ('表头在第 ' + sh.header_row + ' 行');
            var card = $('<div>').addClass('border rounded p-2 mb-3');
            var head = $('<div>').addClass('d-flex align-items-center mb-2 flex-wrap gap-2')
                .append($('<b>').addClass('small').text('[工作表] ' + sh.name))
                .append($('<span>').addClass('text-muted small').text(
                    (sh.file ? ('文件：' + sh.file + '｜') : '') + rowsInfo))
                .append($('<select>').addClass('form-select form-select-sm ai-role')
                    .attr('data-idx', idx).css('width', '150px')
                    .append('<option value="converted">这份是：赋分成绩</option>')
                    .append('<option value="raw">这份是：原始成绩</option>')
                    .append('<option value="both">同表内两者都有</option>')
                    .append('<option value="bands">这份是：各科分数线</option>')
                    .append($('<option>').val(SCHEDULE_KIND)
                        .text('这份是：' + SCHEDULE_KIND_LABEL))
                    .append('<option value="affair_students">这份是：考生名单／编排结果（学生信息表）</option>')
                    .append('<option value="affair_rooms">这份是：考场设置（考场信息表）</option>')
                    .append('<option value="affair_seats">这份是：座位表／桌签（仅用于考场容量）</option>')
                    .append('<option value="skip">不使用此工作表</option>'));
            // 默认角色：分数线 / 考生安排 / 考场位置 由服务端识别；成绩表按 AI 判断的整表角色
            var auto = sh.auto || {};
            var hasSubj = Object.keys(auto.subjects || {}).length
                       || Object.keys(auto.raw_subjects || {}).length;
            var role = (sh.kind === 'bands') ? 'bands'
                     : (sh.kind === SCHEDULE_KIND) ? SCHEDULE_KIND
                     : (sh.kind === 'affair_students') ? 'affair_students'
                     : (sh.kind === 'affair_rooms') ? 'affair_rooms'
                     : (sh.kind === 'affair_seats') ? 'affair_seats'
                     : (sh.kind === 'skip') ? 'skip'
                     : !hasSubj ? 'skip'
                     : (SUGGEST.score_kind === 'raw') ? 'raw'
                     : (SUGGEST.score_kind === 'both') ? 'both' : 'converted';
            card.append(head.append($('<span>').addClass('ms-auto badge bg-light text-dark border')
                .attr('id', 'roleHint' + idx).text(KIND_LABEL[SUGGEST.score_kind] || '')));
            card.find('.ai-role').val(role);
            var $cb = $('<div>').addClass('ai-colbox').attr('data-idx', idx)
                .append(buildColTable(sh, idx, role, flat));
            card.append($cb);
            card.find('.ai-role').on('change', function () {   // 切换用途 → 换一套字段词表
                var r = $(this).val();
                $cb.empty().append(buildColTable(sh, idx, r, flat));
            });
            $box.append(card);
        });
    }

    /** 渲染「识别出的考试场次」：**所有行都可编辑**（图片识图 + 表格解析），
        导入时整表回传给服务端作为权威场次（便于补全「四选二」这类空缺科目）。 */
    function renderSchedules(res) {
        var $tb = $('#schedTable tbody').empty();
        IMG_SESSIONS = (res.image_schedules || []).map(function (s) {
            return {date: s.date || '', start: s.start || '', end: s.end || '',
                    subject: s.subject || '', selection: s.selection || '', src: '图片识别'};
        });
        // 表格（监考表 / 时间表）解析出的场次：与图片结果一并回传
        var sheetSess = [];
        (res.sheets || []).forEach(function (sh) {
            if (sh.kind === SCHEDULE_KIND && sh.schedule && (sh.schedule.sessions || []).length) {
                sh.schedule.sessions.forEach(function (s) {
                    sheetSess.push({date: s.date || '', start: s.start || '', end: s.end || '',
                                    subject: s.subject || '', selection: s.selection || '',
                                    src: '表格：' + sh.name});
                });
            }
        });
        if (!IMG_SESSIONS.length && !sheetSess.length) { $('#schedBox').addClass('d-none'); return; }
        var n = 0;
        function addRow(s, editable) {
            n++;
            var tr = $('<tr>');
            tr.append($('<td>').addClass('text-muted small').text(n));
            ['date', 'start', 'end', 'subject', 'selection'].forEach(function (k) {
                var $td = $('<td>');
                if (editable) {
                    $td.append($('<input>').addClass('form-control form-control-sm sch-in')
                        .attr('data-k', k).val(s[k] || ''));
                } else {
                    $td.addClass('small').text(s[k] || '—');
                }
                tr.append($td);
            });
            tr.append($('<td>').addClass('small text-muted').text(s.src));
            tr.append($('<td>'));
            $tb.append(tr);
        }
        IMG_SESSIONS.forEach(function (s) { addRow(s, true); });
        sheetSess.forEach(function (s) { addRow(s, true); });
        var warns = [];
        (res.sheets || []).forEach(function (sh) {
            if (sh.kind === SCHEDULE_KIND && sh.schedule) {
                (sh.schedule.errors || []).forEach(function (e) {
                    warns.push(sh.name + '：' + (e.reason || e));
                });
            }
        });
        if (res.image_notes) { warns.push('识图备注：' + res.image_notes); }
        $('#schedWarn').text(warns.join('；'));
        $('#schedBox').removeClass('d-none');
    }

    /** 收集「场次」表里的全部行（图片 + 表格解析），整表回传服务端 */
    function collectSchedules() {
        var out = [];
        $('#schedTable tbody tr').each(function () {
            var $r = $(this);
            if (!$r.find('.sch-in').length) { return; }
            var o = {};
            $r.find('.sch-in').each(function () {
                o[$(this).data('k')] = ($(this).val() || '').trim();
            });
            if (o.date || o.start || o.subject) { out.push(o); }
        });
        return out;
    }

    /** 收集映射 → 后端 sheets 参数 */
    function collectSheets() {
        var byIdx = {}, roles = {};
        $('.ai-role').each(function () { roles[+$(this).data('idx')] = $(this).val() || 'converted'; });
        $('.ai-col').each(function () {
            var i = +$(this).data('idx'), col = $(this).data('col'), v = $(this).val();
            if (!v || isAffairRole(roles[i])) { return; }   // 考务字段单独收集
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
            var role = roles[i] || 'converted';
            if (isAffairRole(role)) {          // 考务：扁平 {字段: 列名}
                var m = {};
                $('.ai-col[data-idx=' + i + ']').each(function () {
                    var v = $(this).val();
                    if (v) { m[v] = $(this).data('col'); }
                });
                return {name: sh.name, mapping: m, score_kind: role};
            }
            // 同一 sheet 内“两者都有” → 原始分列归 raw_subjects；
            // 若整表是原始表，则所有科目列按原始处理（后端会按 role 再处理一次）
            return {name: sh.name,
                    mapping: byIdx[i] || {cols: {}, subjects: {}, raw_subjects: {}},
                    score_kind: role};
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
                    if (!SHEETS.length) {
                        // 只上传了图片：没有工作表可映射，提示看下方场次表
                        $('#aiKind').html('<b>已识别到考试场次</b>'
                            + '<span class="text-muted small ms-2">'
                            + '请核对下方「识别出的考试场次」后导入</span>');
                    } else {
                        $('#aiKind').html('<b>AI 判断：这份数据是 </b><span class="badge bg-primary">'
                            + (KIND_LABEL[kind] || '无法判断') + '</span>'
                            + '<span class="text-muted small ms-2">若不对，请在下方每个工作表右侧手动改“这份是”</span>');
                    }
                    var qs = SUGGEST.questions || [];
                    $('#aiQuestions').html(qs.length
                        ? '<div class="alert alert-warning py-2 small mb-2"><b>AI 需要你确认：</b><br>'
                          + qs.map(esc).join('<br>') + '</div>' : '');
                    $('#aiNotes').text(SUGGEST.notes || res.ai_error || '');
                    renderMaps();
                    renderSchedules(res);
                    $('#mapBox, #runBox').removeClass('d-none');
                    var parts = [];
                    if (SHEETS.length) { parts.push(SHEETS.length + ' 个工作表'); }
                    var nSched = (res.image_schedules || []).length;
                    if (nSched) { parts.push('图片识别出 ' + nSched + ' 个场次'); }
                    msg('#aiMsg', true, '已识别 ' + (parts.join('、') || '文件')
                        + '，请核对后导入' + (res.ai_error ? '（' + res.ai_error + '）' : ''));
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
            // v1.19.47.4：图片识别出的场次（含用户在表格里的修改）＋ 场次覆盖方式
            var sess = collectSchedules();
            if (sess.length) { body.schedules = sess; }
            body.schedule_mode = $('#schedReplace').prop('checked') ? 'replace' : 'append';
            msg('#aiRunMsg', true, dry ? '预览中…' : '导入中…');
            post('/grades/exams/' + EID + '/ai-import/apply', body)
                .done(function (res) {
                    if (!res.success) {
                        // 2026-10-12 数据可信度审计（§3.4）：本场已有成绩 + 模式 B（整场重置）
                        // 时服务端要求账号密码二次确认。确认后带 confirmed/username/password
                        // 重发，不再让用户能一键清空整场成绩。
                        if (res.need_confirm) {
                            msg('#aiRunMsg', false, res.message || '需要二次确认');
                            askResetConfirm(function (u, p) {
                                body.username = u;
                                body.password = p;
                                body.confirmed = true;
                                sendApply(body);
                            });
                            return;
                        }
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
                        // 2026-10-12 审查修复 B：场次（覆盖式）属于破坏性写入，
                        // 结果里明确回显「新建/覆盖了多少场次、清空了多少旧数据」，不再静默。
                        var parts = [];
                        if (res.summary && !res.summary.non_score_only) {
                            parts.push('成绩 ' + (res.summary.students || 0) + ' 人');
                        }
                        if (res.schedule) {
                            parts.push('场次 ' + res.schedule.sessions + ' 个（'
                                + (res.schedule.mode === 'replace' ? '覆盖' : '追加')
                                + (res.schedule.invigs ? '，含监考 ' + res.schedule.invigs + ' 格' : '')
                                + (res.schedule.cleared_sessions
                                    ? '；已清空原 ' + res.schedule.cleared_sessions + ' 场次/'
                                      + res.schedule.cleared_invigs + ' 格监考' : '')
                                + '）');
                        }
                        if (res.bands) { parts.push('分数线 ' + res.bands.bands + ' 条'); }
                        if (res.affair) { parts.push('考务 ' + res.affair.students + ' 人'); }
                        msg('#aiRunMsg', true, '导入成功：' + (parts.join('；') || '已完成')
                            + '，正在跳转…');
                        setTimeout(function () {
                            location.href = '/grades/exams/' + EID;
                        }, 1200);
                        $('#aiPreviewBox').removeClass('d-none').text(JSON.stringify({
                            summary: res.summary, schedule: res.schedule, bands: res.bands,
                            affair: res.affair, raw_cells: res.raw_cells,
                            errors: (res.errors || []).slice(0, 10)}, null, 1));
                    }
                })
                .fail(function (x) {
                    msg('#aiRunMsg', false, (x.responseJSON && x.responseJSON.message) || '请求失败');
                });
        }

        function sendApply(body) {
            msg('#aiRunMsg', true, '导入中…');
            post('/grades/exams/' + EID + '/ai-import/apply', body)
                .done(function (res) {
                    if (!res.success) {
                        msg('#aiRunMsg', false, res.message || '失败');
                        return;
                    }
                    msg('#aiRunMsg', true, '导入成功，正在跳转…');
                    setTimeout(function () {
                        location.href = '/grades/exams/' + EID;
                    }, 800);
                })
                .fail(function (x) {
                    msg('#aiRunMsg', false, (x.responseJSON && x.responseJSON.message) || '请求失败');
                });
        }

        /** 整场重置二次确认：账号 + 密码（与删除考试同口径） */
        function askResetConfirm(cb) {
            var u = window.prompt('本场已有成绩，「整场覆盖」会先清空再重导。\n请输入当前登录账号：', '');
            if (u === null) { msg('#aiRunMsg', false, '已取消导入'); return; }
            u = (u || '').trim();
            if (!u) { msg('#aiRunMsg', false, '已取消导入'); return; }
            var p = window.prompt('请输入该账号的密码：', '');
            if (p === null || !p) { msg('#aiRunMsg', false, '已取消导入'); return; }
            cb(u, p);
        }

        function esc(s) {
            return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
                return {'&': '&amp;', '<': '&lt;', '>': '&gt;',
                        '"': '&quot;', "'": '&#39;'}[c];
            });
        }

        $('#aiPreview').on('click', function () { run(true); });
        $('#aiApply').on('click', function () {
            var hasSched = collectSchedules().length > 0
                || $('#schedTable tbody tr').length > 0;
            var tip = '确认导入？勾选“整场覆盖”时会替换本场现有成绩。';
            if (hasSched) {
                var rep = $('#schedReplace').prop('checked');
                tip += '\n场次按「' + (rep ? '覆盖' : '追加') + '」方式写入本场考务';
                tip += rep ? '：本批次**已有场次与监考安排会被清空重建**。'
                           : '：保留原有场次，只追加新场次。';
            }
            if (!confirm(tip)) { return; }
            run(false);
        });
    });
})();
