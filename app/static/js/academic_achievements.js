/* StuLink 教师业绩库（/academic/achievements）
 *
 * 2026-10-10 性能优化（目标：给 181KB 的页面瘦身，不减信息量、不改交互结果）：
 *   1) 原来内联在 achievements.html 里三段 <script>（审核弹窗 / 详情抽屉 / 动态字段，合计约 19.4KB）
 *      抽成独立静态文件，可被浏览器缓存，不再随每次 HTML 下发；
 *   2) Jinja 注入的运行时数据（模板 URL、动态字段映射 fields_map、材料分类 doc_types）改由页面
 *      底部 <script id="achievementData" type="application/json"> + JSON.parse 读取；
 *   3) 列表行「删除」由「每行一个 <form> + csrf_token」改为 data-url 按钮 + 底部单一隐藏表单统一提交，
 *      减少 30 份重复的表单标签与 csrf_token（confirm 文案、POST 语义、CSRF 校验、权限判断全部不变）。
 * 约束：DOM id、事件委托、请求端点（url_for 生成值）与原实现逐条一致。
 */
(function () {
    var CFG = {};
    try {
        var _el = document.getElementById('achievementData');
        CFG = _el ? (JSON.parse(_el.textContent) || {}) : {};
    } catch (e) { CFG = {}; }

    // ===================== 1) 审核弹窗（通过 / 驳回）=====================
    // 原 achievements.html:312-332（仅 can_edit 时存在对应 DOM）
    document.addEventListener('DOMContentLoaded', function () {
        var tpl = CFG.reviewTpl || '';
        document.querySelectorAll('[data-bs-target="#reviewModal"]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var d = this.dataset;
                var isReject = d.action === 'reject';
                document.getElementById('reviewForm').action = tpl.replace(/\/\d+\/review$/, '/' + d.id + '/review');
                document.getElementById('rvAction').value = d.action || 'approve';
                document.getElementById('rvTitle').textContent = isReject ? '驳回业绩' : '通过业绩';
                document.getElementById('rvTeacher').textContent = d.teacher || '';
                document.getElementById('rvAch').textContent = d.title || '';
                document.getElementById('rvNote').value = '';
                document.getElementById('rvNote').required = isReject;
                document.getElementById('rvReq').style.visibility = isReject ? 'visible' : 'hidden';
                document.getElementById('rvSubmit').className =
                    'btn btn-sm ' + (isReject ? 'btn-warning' : 'btn-success');
            });
        });
    });

    // ===================== 2) 详情抽屉 + 附件管理 =====================
    // 原 achievements.html:566-753
    (function () {
        var CAN_EDIT = !!CFG.canEdit;
        var DETAIL_TPL = CFG.detailTpl || '';
        var TAGS_TPL = CFG.tagsTpl || '';
        var UP_TPL = CFG.upTpl || '';
        var DEL_TPL = CFG.delTpl || '';
        var VIEW_TPL = CFG.viewTpl || '';
        var DL_TPL = CFG.dlTpl || '';
        var modalEl = document.getElementById('achDetailModal');
        if (!modalEl) { return; }
        var modal = new bootstrap.Modal(modalEl);
        var csrf = (typeof csrfToken !== 'undefined') ? csrfToken : '';

        function withId(tpl, id) { return tpl.replace(/\/0(?=\/|$)/, '/' + id); }
        function esc(s) {
            return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
                .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
        }
        function currentId() { return modalEl.dataset.aid; }

        function attHtml(a) {
            var v = withId(VIEW_TPL, a.id), d = withId(DL_TPL, a.id);
            var head = a.is_image
                ? '<img src="' + v + '" class="img-fluid rounded w-100" style="height:96px;object-fit:cover" alt="">'
                : '<div class="text-center" style="font-size:30px;padding:22px 0">'
                  + (a.ext === 'pdf' ? '<i class="bi bi-file-earmark-pdf text-danger"></i>'
                                     : '<i class="bi bi-file-earmark-text text-secondary"></i>') + '</div>';
            return '<div class="col-6 col-md-3"><div class="border rounded p-1 h-100 d-flex flex-column">'
                + head
                + '<div class="small text-truncate mt-1" title="' + esc(a.file_name) + '">' + esc(a.file_name) + '</div>'
                + (a.doc_type_text ? '<div><span class="badge bg-light text-dark border" style="font-size:10px">'
                   + esc(a.doc_type_text) + '</span></div>' : '')
                + '<div class="text-muted" style="font-size:10px">' + esc(a.size_text)
                + (a.uploaded_name ? ' · ' + esc(a.uploaded_name) : '') + '</div>'
                + '<div class="d-flex gap-1 mt-auto pt-1">'
                + (a.previewable ? '<a class="btn btn-sm btn-outline-primary py-0 flex-fill" target="_blank" href="' + v + '">预览</a>' : '')
                + '<a class="btn btn-sm btn-outline-secondary py-0 flex-fill" href="' + d + '">下载</a>'
                + (CAN_EDIT ? '<button type="button" class="btn btn-sm btn-outline-danger py-0 ad-del" data-id="' + a.id + '">×</button>' : '')
                + '</div></div></div>';
        }

        function renderAtts(list) {
            document.getElementById('adAttCount').textContent = list.length ? '（' + list.length + '）' : '（暂无）';
            var box = document.getElementById('adAttach');
            box.innerHTML = list.length ? list.map(attHtml).join('')
                : '<div class="col-12 text-muted small py-2">还没有附件'
                  + (CAN_EDIT ? '，可上传证书扫描件、获奖照片或 PDF 材料' : '') + '</div>';
        }

        // 2026-10-10：来源=表单收集 → 回看该次提交的答案与附件（文件走鉴权路由）
        function renderSubmission(sub) {
            var box = document.getElementById('adSubmission');
            if (!box) { return; }
            if (!sub) { box.innerHTML = ''; return; }
            var html = '<div class="card border-0 bg-light"><div class="card-body py-2">'
                + '<div class="d-flex justify-content-between align-items-center flex-wrap gap-1 mb-2">'
                + '<span class="fw-bold small"><i class="bi bi-ui-checks-grid"></i> 来自表单收集</span>'
                + '<a class="btn btn-sm btn-outline-secondary py-0" target="_blank" href="'
                + sub.list_url + '">打开该次收集</a></div>'
                + '<div class="text-muted small mb-2">' + esc(sub.template_title || '')
                + (sub.round_label ? ' · ' + esc(sub.round_label) : '')
                + (sub.submitted_at ? ' · 提交于 ' + esc(sub.submitted_at) : '')
                + '</div>';
            (sub.items || []).forEach(function (it) {
                html += '<div class="mb-2"><div class="small text-muted">' + esc(it.title) + '</div>';
                if (it.text) { html += '<div class="small">' + esc(it.text) + '</div>'; }
                (it.files || []).forEach(function (f) {
                    html += '<div class="small"><a target="_blank" href="' + f.url + '">'
                        + '<i class="bi bi-paperclip"></i> ' + esc(f.name) + '</a>'
                        + (f.size_text ? ' <span class="text-muted">(' + esc(f.size_text) + ')</span>' : '')
                        + '</div>';
                });
                html += '</div>';
            });
            html += '</div></div>';
            box.innerHTML = html;
        }

        function renderDetail(d) {
            modalEl.dataset.aid = d.id;
            var editBtn = document.getElementById('adEditBtn');
            if (editBtn) { editBtn.dataset.id = d.id; }   // 抽屉底部「编辑」按钮复用同一委托
            document.getElementById('adSub').textContent = d.teacher_name + ' · ' + d.title;
            var rows = [
                ['教师', esc(d.teacher_name) + (d.teacher_uid ? '（' + esc(d.teacher_uid) + '）' : '')],
                ['类别 / 级别', esc(d.category_text) + (d.level ? ' · ' + esc(d.level) : '')],
                ['取得时间', esc(d.obtain_date) || '-'],
                ['颁发单位', esc(d.issuer) || '-'],
                ['状态', esc(d.status_text) + (d.review_note ? '（' + esc(d.review_note) + '）' : '')],
                ['来源', esc(d.source_type_text || '-')
                    + (d.source_label ? ' · ' + esc(d.source_label) : '')],
                ['备注', esc(d.note) || '-'],
                ];
                // 2026-10-09：按类别的动态字段（课题编号/立项结题/期刊刊号/学时…）
                (d.extra_pairs || []).forEach(function (p) {
                rows.push([esc(p.label), esc(p.value)]);
                });
            document.getElementById('adInfo').innerHTML = rows.map(function (r) {
                return '<dt class="col-3 text-muted">' + r[0] + '</dt><dd class="col-9">' + r[1] + '</dd>';
            }).join('');
            document.getElementById('adTags').value = (d.tags || []).join(',');
            var preset = document.getElementById('adTagPresets');
            if (preset && !preset.dataset.filled) {
                (d.tag_presets || []).forEach(function (t) {
                    var o = document.createElement('option'); o.value = t; preset.appendChild(o);
                });
                preset.dataset.filled = '1';
            }
            renderAtts(d.attachments || []);
            renderSubmission(d.submission);
        }

        function loadDetail(id) {
            document.getElementById('adLoading').classList.remove('d-none');
            document.getElementById('adBody').classList.add('d-none');
            document.getElementById('adHint').textContent = '';
            fetch(withId(DETAIL_TPL, id)).then(function (r) { return r.json(); }).then(function (res) {
                document.getElementById('adLoading').classList.add('d-none');
                if (!res || !res.success) { alert((res && res.message) || '加载失败'); return; }
                renderDetail(res.data);
                document.getElementById('adBody').classList.remove('d-none');
            }).catch(function () {
                document.getElementById('adLoading').classList.add('d-none');
                alert('网络错误，加载失败');
            });
        }

        document.querySelectorAll('.btn-ach-detail').forEach(function (btn) {
            btn.addEventListener('click', function () {
                loadDetail(this.dataset.id);
                modal.show();
            });
        });

        var saveTagsBtn = document.getElementById('adSaveTags');
        if (saveTagsBtn) {
            saveTagsBtn.addEventListener('click', function () {
                var id = currentId();
                var fd = new FormData();
                fd.append('tags', document.getElementById('adTags').value);
                fetch(withId(TAGS_TPL, id), {method: 'POST', headers: {'X-CSRFToken': csrf}, body: fd})
                    .then(function (r) { return r.json(); }).then(function (res) {
                        document.getElementById('adHint').textContent = (res && res.message) || '';
                        if (res && res.success) { setTimeout(function () { window.location.reload(); }, 600); }
                    });
            });
        }

        var upBtn = document.getElementById('adUpload');
        if (upBtn) {
            upBtn.addEventListener('click', function () {
                var files = document.getElementById('adFiles').files;
                if (!files || !files.length) { alert('请先选择文件'); return; }
                var fd = new FormData();
                for (var i = 0; i < files.length; i++) { fd.append('files', files[i]); }
                var dt = document.getElementById('adDocType');
                if (dt && dt.value) { fd.append('doc_type', dt.value); }
                upBtn.disabled = true;
                fetch(withId(UP_TPL, currentId()), {method: 'POST', headers: {'X-CSRFToken': csrf}, body: fd})
                    .then(function (r) { return r.json(); }).then(function (res) {
                        upBtn.disabled = false;
                        document.getElementById('adFiles').value = '';
                        if (!res) { alert('上传失败'); return; }
                        document.getElementById('adHint').textContent = res.message || '';
                        if (res.errors && res.errors.length) { alert(res.errors.join('\n')); }
                        if (res.attachments) { renderAtts(res.attachments); }
                        if (res.uploaded) { window.__achChanged = true; }
                    }).catch(function () { upBtn.disabled = false; alert('网络错误，上传失败'); });
            });
        }

        document.getElementById('adAttach').addEventListener('click', function (ev) {
            var btn = ev.target.closest('.ad-del');
            if (!btn) { return; }
            if (!confirm('删除这个附件？文件会一并删除。')) { return; }
            fetch(withId(DEL_TPL, btn.dataset.id), {method: 'POST', headers: {'X-CSRFToken': csrf}})
                .then(function (r) { return r.json(); }).then(function (res) {
                    if (res && res.success) {
                        window.__achChanged = true;
                        loadDetail(currentId());
                    } else { alert((res && res.message) || '删除失败'); }
                });
        });

        modalEl.addEventListener('hidden.bs.modal', function () {
            if (window.__achChanged) { window.location.reload(); }
        });
    })();

    // 录入表单：点常用标签快速追加（原 achievements.html:755-767）
    document.addEventListener('DOMContentLoaded', function () {
        var quick = document.getElementById('quickTags');
        if (!quick) { return; }
        quick.addEventListener('click', function (ev) {
            var tag = ev.target.dataset && ev.target.dataset.tag;
            if (!tag) { return; }
            var input = quick.closest('form').querySelector('input[name="tags"]');
            var cur = (input.value || '').split(/[,，\s]+/).filter(Boolean);
            if (cur.indexOf(tag) < 0) { cur.push(tag); }
            input.value = cur.join(',');
        });
    });

    // ===================== 3) 动态字段 + 编辑弹窗 =====================
    // 原 achievements.html:772-879（依赖 jQuery，base.html 已先加载）
    (function () {
        'use strict';
        var FMAP = CFG.fieldsMap || {};
        var DTYPES = CFG.docTypes || [];
        var DETAIL_URL = CFG.detailUrl || '';
        var EDIT_URL = CFG.editUrl || '';

        function esc(v) {
            return String(v == null ? '' : v).replace(/&/g, '&amp;').replace(/</g, '&lt;')
                .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
        }

        // cat=类别，$box=字段容器，values=已存的值（编辑时回填）
        function renderFields(cat, $box, values) {
            var list = FMAP[cat] || [], html = '';
            values = values || {};
            list.forEach(function (f) {
                var key = f[0], label = f[1], type = f[2] || 'text';
                var v = String(values[key] || ''), input;
                if (type.indexOf('select:') === 0) {
                    input = '<select class="form-select form-select-sm" name="x_' + key + '">'
                        + '<option value="">--</option>'
                        + type.slice(7).split(',').map(function (o) {
                            return '<option value="' + esc(o) + '"'
                                + (o === v ? ' selected' : '') + '>' + esc(o) + '</option>';
                        }).join('') + '</select>';
                } else if (type === 'date') {
                    input = '<input type="date" class="form-control form-control-sm" name="x_'
                        + key + '" value="' + esc(v) + '">';
                } else {
                    input = '<input class="form-control form-control-sm" name="x_' + key + '"'
                        + ' maxlength="100" value="' + esc(v) + '">';
                }
                html += '<div class="col-6 mb-2"><label class="form-label small mb-1">'
                    + esc(label) + '</label>' + input + '</div>';
            });
            $box.html(html);
        }

        function fillDocTypes($sel, withBlank) {
            var html = withBlank ? '<option value="">-- 材料分类 --</option>' : '';
            DTYPES.forEach(function (d) {
                html += '<option value="' + esc(d[0]) + '">' + esc(d[1]) + '</option>';
            });
            $sel.html(html);
        }

        $(function () {
            if ($('#addCategory').length) {
                $('#addCategory').on('change', function () {
                    renderFields($(this).val(), $('#dynFields'), {});
                });
                renderFields($('#addCategory').val(), $('#dynFields'), {});
            }
            // 编辑弹窗：选项复用新增表单，避免两处维护
            if ($('#editCategory').length && $('#addCategory').length) {
                $('#editCategory').html($('#addCategory').html());
                $('#editLevel').html($('#addLevel').html());
                $('#editCategory').on('change', function () {
                    renderFields($(this).val(), $('#editFields'), {});
                });
            }
            // 点「编辑」→ 拉详情填弹窗
            $(document).on('click', '.btn-ach-edit', function () {
                var id = $(this).data('id');
                // 从详情抽屉里点「编辑」时先收起抽屉，避免两个 modal 叠在一起
                var dmEl = document.getElementById('achDetailModal');
                if (dmEl && window.bootstrap && window.bootstrap.Modal.getInstance) {
                    var dm = window.bootstrap.Modal.getInstance(dmEl);
                    if (dm) { dm.hide(); }
                }
                $.getJSON(String(DETAIL_URL).replace('/0/', '/' + id + '/')).done(function (res) {
                    var d = (res && res.data) || {};
                    $('#editForm').attr('action', String(EDIT_URL).replace('/0/', '/' + id + '/'));
                    $('#editTeacher').text('· ' + (d.teacher_name || ''));
                    $('#editCategory').val(d.category || '');
                    $('#editLevel').val(d.level || '');
                    $('#editTitle').val(d.title || '');
                    $('#editObtain').val(d.obtain_date || '');
                    $('#editIssuer').val(d.issuer || '');
                    $('#editNote').val(d.note || '');
                    $('#editTags').val((d.tags || []).join(','));
                    renderFields(d.category || '', $('#editFields'), d.extra || {});
                    var el = document.getElementById('editModal');
                    if (el && window.bootstrap && window.bootstrap.Modal) {
                        window.bootstrap.Modal.getOrCreateInstance(el).show();
                    }
                }).fail(function () {
                    if (window.toast) { window.toast('读取业绩详情失败', 'danger'); }
                    else { window.alert('读取业绩详情失败'); }
                });
            });
            fillDocTypes($('#addDocType'), true);
            fillDocTypes($('#adDocType'), true);
            // 提交前提醒：选了文件但不是 PDF（后端仍会再次校验）
            $('#addAchForm').on('submit', function () {
                var bad = [];
                ($('#addFiles').length && $('#addFiles')[0].files
                    ? Array.prototype.slice.call($('#addFiles')[0].files) : [])
                    .forEach(function (f) { if (!/\.pdf$/i.test(f.name)) { bad.push(f.name); } });
                if (bad.length && !window.confirm('附件统一要求 PDF，下面这些不是 PDF 会被拒绝：\n'
                    + bad.join('\n') + '\n\n仍要提交（业绩会录入，附件跳过）？')) {
                    return false;
                }
                return true;
            });
        });
    })();

    // ===================== 4) 列表行「删除」（2026-10-10 合并表单）=====================
    (function () {
        var delForm = document.getElementById('achDeleteForm');
        if (!delForm) { return; }
        document.addEventListener('click', function (ev) {
            var btn = ev.target && ev.target.closest ? ev.target.closest('.js-ach-del') : null;
            if (!btn) { return; }
            if (!confirm('确定删除这条业绩记录？')) { return; }
            delForm.action = btn.dataset.url;
            delForm.submit();
        });
    })();
})();
