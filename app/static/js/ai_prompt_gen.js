/* StuLink · AI 提示词生成器（2026-10-14）
 * 老师点选「范围（年级/班级/学科）→ 分析侧重 → 输出格式 → 附加要求」，
 * 服务端校验（真实性 + 权限）并拼装成完整提示词 → 填入提示词文本框（仍可手动编辑）
 * → 复制 / 存模板 / 直接开始分析；任务中断或停止后可回填「重新编辑提示词」。
 *
 * 本文件是**可挂载工厂**：AI 分析工作台（/grades/ai）与成绩汇报区（/grades/report）
 * 各挂一份，DOM 选择器与考试来源由调用方传入（见文件尾部的默认实例）。
 *
 * 对接（默认路径 /grades/ai/prompt/*）：
 *   GET  options?exam_id=      选项（按账号权限收窄；班级=本场考试总分行口径）
 *   POST build                 拼装（越权/非法值 → 400 并给出原因）
 *   GET  templates             我的模板
 *   POST templates             保存（新建 / 同名或按 id 覆盖）
 *   POST templates/<id>/delete 删除
 * 安全：本模块所有 UI 文本用 .text() / .val() 构造，不拼接 HTML 字符串。
 * Copyright (c) 2026 zkxxzf. Apache License 2.0 */
(function (window) {
    'use strict';
    if (!window.jQuery) { return; }
    var $ = window.jQuery;

    var BASE = '/grades/ai/prompt';

    function toast(msg, type) {
        if (typeof window.stuToast === 'function') { window.stuToast(msg, type || 'ok'); }
        else if (type === 'err') { window.alert(msg); }
    }
    function csrf() { return (typeof window.csrfToken === 'string') ? window.csrfToken : ''; }

    function create(opts) {
        opts = opts || {};
        var S = {
            panel: opts.panel || '#aiGenPanel',
            toggle: opts.toggle || '#aiGenToggle',
            classes: opts.classes || '#aiGenClasses',
            subjects: opts.subjects || '#aiGenSubjects',
            focus: opts.focus || '#aiGenFocus',
            output: opts.output || '#aiGenOutput',
            extra: opts.extra || '#aiGenExtra',
            presets: opts.presets || '#aiGenExtraPresets',
            tplName: opts.tplName || '#aiGenTplName',
            tplSave: opts.tplSave || '#aiGenTplSave',
            tpl: opts.tpl || '#aiGenTpl',
            tplDel: opts.tplDel || '#aiGenTplDel',
            build: opts.build || '#aiGenBuild',
            prompt: opts.prompt || '#aiQ'
        };
        if (!$(S.panel).length) { return null; }        // 页面没有生成器 DOM：不启用

        var urls = opts.urls || {};
        var OPTS = urls.options || (BASE + '/options');
        var BUILD = urls.build || (BASE + '/build');
        var TPL = urls.templates || (BASE + '/templates');
        var getExamId = (typeof opts.examId === 'function')
            ? opts.examId
            : function () { return $(opts.examSel || '#aiExamSel').val() || ''; };

        var meta = { grades: [], classes: [], subjects: [], focus: [], output: [],
                     presets: [], default_focus: '', default_output: '' };
        var gen = { classes: [], subjects: [] };        // 多选项（班级/学科）；年级/侧重/输出读表单值
        var tplCache = [];
        var used = false;                               // 用过生成器才在提交任务时附 gen
        var loadedFor = null;

        function getPrompt() { return $(S.prompt).val() || ''; }
        function setPrompt(text) { $(S.prompt).val(text || ''); }

        function panelOpen(on) {
            $(S.panel).toggleClass('d-none', !on);
            $(S.toggle).attr('aria-expanded', on ? 'true' : 'false');
        }

        /* ============ 胶囊勾选（班级 / 学科 / 附加要求预设） ============ */
        function chip(text, on, onClick, extraCls) {
            var $c = $('<button type="button" class="chip"></button>').text(text);
            if (extraCls) { $c.addClass(extraCls); }
            if (on) { $c.addClass('on'); }
            $c.on('click', onClick);
            return $c;
        }

        /** 选中但不在候选里的旧值也一并渲染（任务回填/换考试时无损显示） */
        function merged(list, sel) {
            var out = (list || []).slice();
            (sel || []).forEach(function (v) { if (out.indexOf(v) < 0) { out.push(v); } });
            return out;
        }

        function renderMulti($box, all, sel, emptyText) {
            $box.empty();
            if (!all.length) {
                $box.append($('<span class="text-muted small"></span>')
                    .text(emptyText || '（本场考试没有可选项）'));
                return;
            }
            all.forEach(function (v) {
                var on = sel.indexOf(v) >= 0;
                $box.append(chip(v, on, function () {
                    var i = sel.indexOf(v);
                    if (i >= 0) { sel.splice(i, 1); } else { sel.push(v); }
                    used = true;
                    renderMulti($box, merged(all, sel), sel);
                }));
            });
        }

        function fillSelect($sel, options, value, placeholder) {
            $sel.empty();
            if (placeholder !== undefined) {
                $sel.append($('<option value=""></option>').text(placeholder));
            }
            var vals = [];
            (options || []).forEach(function (o) {
                $sel.append($('<option></option>').attr('value', o.value).text(o.label));
                vals.push(o.value);
            });
            if (value && vals.indexOf(value) < 0) {
                $sel.append($('<option></option>').attr('value', value).text(value + '（自定义）'));
            }
            $sel.val(value || '');
        }

        function renderAll() {
            renderMulti($(S.classes), merged(meta.classes, gen.classes), gen.classes,
                '（本场考试还没有成绩或考务数据，先导入成绩再分析）');
            renderMulti($(S.subjects), merged(meta.subjects, gen.subjects), gen.subjects,
                '（本场考试还没有成绩数据）');
            fillSelect($(S.focus), meta.focus, $(S.focus).val() || meta.default_focus);
            fillSelect($(S.output), meta.output, $(S.output).val() || meta.default_output);

            var $box = $(S.presets).empty();
            (meta.presets || []).forEach(function (p) {
                $box.append(chip(p, false, function () {
                    var cur = ($(S.extra).val() || '').trim();
                    if (cur.indexOf(p) >= 0) { toast('该要求已在附加要求里', 'warn'); return; }
                    $(S.extra).val(cur ? (cur + '；' + p) : p);
                    used = true;
                }, 'chip-extra'));
            });
        }

        /* ============ 选项加载 ============ */
        function loadOptions(eid, done) {
            if (!eid) { return; }
            $.getJSON(OPTS + '?exam_id=' + encodeURIComponent(eid), function (res) {
                var d = (res && res.data) || {};
                meta = {
                    grades: d.grades || [], classes: d.classes || [], subjects: d.subjects || [],
                    focus: d.focus || [], output: d.output || [],
                    presets: d.extra_presets || [],
                    default_focus: d.default_focus || '', default_output: d.default_output || ''
                };
                loadedFor = String(eid);
                renderAll();
                if (typeof done === 'function') { done(); }
            }).fail(function (x) {
                toast('生成器选项加载失败'
                    + (x && x.status ? '（HTTP ' + x.status + '）' : ''), 'err');
                if (typeof done === 'function') { done(); }
            });
        }

        /** 考试切换：班级/学科选择作废（它们绑定本场考试） */
        function onExamChanged(eid) {
            gen.classes = [];
            gen.subjects = [];
            $(S.focus).val('');
            $(S.output).val('');
            loadedFor = null;
            if (eid) { loadOptions(eid); }
        }

        /* ============ 读表单 / 拼装 ============
         * 年级恒为空数组＝「本场考试所属年级」：考试自带年级（用户口径 2026-10-14），
         * 生成器只在场次内收窄范围。字段保留是为了模板 payload 的向后兼容。 */
        function readForm() {
            return {
                grades: [],
                classes: gen.classes.slice(),
                subjects: gen.subjects.slice(),
                focus: $(S.focus).val() || '',
                output: $(S.output).val() || '',
                extra: ($(S.extra).val() || '').trim()
            };
        }

        function build(done) {
            var eid = getExamId();
            if (!eid) { toast('请先选择一场考试', 'warn'); return; }
            $.ajax({
                url: BUILD,
                method: 'POST',
                contentType: 'application/json',
                headers: { 'X-CSRFToken': csrf() },
                data: JSON.stringify({ exam_id: eid, gen: readForm() })
            }).done(function (res) {
                if (!res || !res.success) {
                    toast((res && res.message) || '生成失败', 'err');
                    return;
                }
                setPrompt(((res.data || {}).prompt) || '');
                used = true;
                toast('提示词已生成，可直接开始分析或继续手动编辑', 'ok');
                if (typeof done === 'function') { done(res.data || {}); }
            }).fail(function (x) {
                toast((x && x.responseJSON && x.responseJSON.message)
                    || ('生成失败' + (x && x.status ? '（HTTP ' + x.status + '）' : '')), 'err');
            });
        }

        /* ============ 模板 ============ */
        function loadTemplates(done) {
            $.getJSON(TPL, function (res) {
                tplCache = (res && res.data) || [];
                var $sel = $(S.tpl).empty()
                    .append($('<option value=""></option>').text('— 我的模板 —'));
                tplCache.forEach(function (t) {
                    $sel.append($('<option></option>').attr('value', t.id).text(t.name));
                });
                $(S.tplDel).prop('disabled', true);
                if (typeof done === 'function') { done(); }
            }).fail(function () { /* 列表失败不打扰主流程 */ });
        }

        function applyTemplate(t) {
            if (!t) { return; }
            var p = t.payload || {};
            gen.classes = (p.classes || []).slice();
            gen.subjects = (p.subjects || []).slice();
            if (p.extra !== undefined) { $(S.extra).val(p.extra || ''); }
            $(S.focus).val(p.focus || '');
            $(S.output).val(p.output || '');
            used = true;
            renderAll();
            if (p.prompt) { setPrompt(p.prompt); }
            panelOpen(true);
            if (getExamId()) { loadOptions(getExamId()); }
            toast('已套用模板「' + t.name + '」', 'ok');
        }

        function saveTemplate() {
            var name = ($(S.tplName).val() || '').trim();
            if (!name) {
                toast('请先填模板名（便于下次套用）', 'warn');
                $(S.tplName).trigger('focus');
                return;
            }
            $.ajax({
                url: TPL,
                method: 'POST',
                contentType: 'application/json',
                headers: { 'X-CSRFToken': csrf() },
                data: JSON.stringify({ name: name, gen: readForm(), prompt: getPrompt() })
            }).done(function (res) {
                if (!res || !res.success) {
                    toast((res && res.message) || '保存失败', 'err');
                    return;
                }
                toast('模板已保存', 'ok');
                var newId = (res.data || {}).id;
                loadTemplates(function () {
                    if (newId) { $(S.tpl).val(String(newId)); }
                    $(S.tplDel).prop('disabled', !newId);
                });
            }).fail(function (x) {
                toast((x && x.responseJSON && x.responseJSON.message) || '保存失败', 'err');
            });
        }

        function deleteTemplate() {
            var id = $(S.tpl).val();
            if (!id) { return; }
            var name = $(S.tpl + ' option:selected').text();
            if (!window.confirm('确认删除模板「' + name + '」？')) { return; }
            $.ajax({
                url: TPL + '/' + encodeURIComponent(id) + '/delete',
                method: 'POST',
                headers: { 'X-CSRFToken': csrf() }
            }).done(function (res) {
                if (res && res.success === false) { toast(res.message || '删除失败', 'err'); return; }
                toast('模板已删除', 'ok');
                loadTemplates();
            }).fail(function (x) {
                toast('删除失败' + (x && x.status ? '（HTTP ' + x.status + '）' : ''), 'err');
            });
        }

        /* ============ 任务回填（「重新编辑提示词」） ============ */
        function applyTask(task) {
            if (!task) { return; }
            panelOpen(true);
            var g = task.gen || {};
            gen.classes = (g.classes || []).slice();
            gen.subjects = (g.subjects || []).slice();
            if (g.extra !== undefined) { $(S.extra).val(g.extra || ''); }
            if (g.focus) { $(S.focus).val(g.focus); }
            if (g.output) { $(S.output).val(g.output); }
            used = true;
            setPrompt(task.question || '');
            renderAll();
            var eid = task.exam_id;
            if (eid) { loadOptions(eid, renderAll); }   // 选项回来后再次合并渲染（保留回填值）
            try { $(S.prompt).trigger('focus'); } catch (e) { /* 忽略 */ }
        }

        /* ============ 事件绑定 ============ */
        function init() {
            $(S.toggle).on('click', function () {
                var open = $(S.panel).hasClass('d-none');
                panelOpen(open);
                if (open) {
                    used = true;
                    if (getExamId() && loadedFor !== String(getExamId())) {
                        loadOptions(getExamId(), renderAll);
                    }
                    if (!tplCache.length) { loadTemplates(); }
                }
            });
            $(S.focus + ', ' + S.output).on('change', function () { used = true; });
            $(S.extra).on('input', function () { used = true; });
            $(S.build).on('click', function () { build(); });
            $(S.tplSave).on('click', saveTemplate);
            $(S.tplDel).on('click', deleteTemplate);
            $(S.tpl).on('change', function () {
                var id = $(this).val();
                $(S.tplDel).prop('disabled', !id);
                if (!id) { return; }
                var t = tplCache.filter(function (x) {
                    return String(x.id) === String(id);
                })[0];
                if (t) { applyTemplate(t); }
            });
        }

        return {
            init: init,
            onExamChanged: onExamChanged,
            isUsed: function () { return used; },
            getGen: readForm,
            getPrompt: getPrompt,
            setPrompt: setPrompt,
            applyTask: applyTask,
            open: function () { panelOpen(true); }
        };
    }

    /* ---------- 默认实例：AI 分析工作台（/grades/ai） ---------- */
    var cfg = {};
    try {
        cfg = JSON.parse((document.getElementById('aiStudioCfg') || {}).textContent || '{}');
    } catch (e) { cfg = {}; }

    window.AiPromptGen = create({
        urls: {
            options: cfg.prompt_options_url,
            build: cfg.prompt_build_url,
            templates: cfg.prompt_tpl_url
        }
    });
    window.AiPromptGenFactory = create;      // 成绩汇报区等其它页面自建实例
})(window);
