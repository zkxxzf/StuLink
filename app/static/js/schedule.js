/* StuLink v1.18.8.0 学期课表前端
 * 统一入口：各课表页底部注入 <script id="scheduleData" type="application/json"> 配置块。
 * 依赖：jQuery 3.7 + Bootstrap 5 bundle（base.html 已全局加载）；图表页额外加载 echarts.min.js。
 * CSRF：base.html 已 $.ajaxSetup 注入 X-CSRFToken，本文件的 $.ajax/$.getJSON 自动携带。
 * 设计约定见 app/templates/academic/_schedule_grid.html 与 _schedule_parts.html。
 *
 * 2026-09-25 批次 A/B：
 *  - 学科配色（TONES/PRESET 与 app/utils/subject_color.py 必须同步，否则服务端渲染
 *    与 AJAX 重渲染的颜色会漂移）；
 *  - 条目支持拖拽换格调课（drop 后调用条目编辑接口，失败给 toast 提示）；
 *  - 增删改成功后局部刷新网格（不再整页 reload），并同步课时统计。
 */
(function () {
    'use strict';

    var CFG = {};                 // scheduleData 配置
    var STATE = {                 // 运行期状态
        grade: '', className: '',
        room: '',                 // 教室视图当前教室
        teachingClass: '',        // 教学班视图当前教学班（走班）
        periods: [],              // 当前上下文节次定义（供弹窗节次下拉/网格渲染）
        teachers: null,           // 教师列表缓存 [{uid,name,subject}]
        classes: null,            // 年级班级缓存 {grade:[class_name]}
        editingId: null           // 正在编辑的条目 id（null=新增）
    };
    var WD_NAMES = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];

    function esc(v) {
        return String(v == null ? '' : v)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    /* ── 学科配色：与 app/utils/subject_color.py 保持同一调色板与散列算法 ── */
    var TONES = [
        { c: '#dc2626', bg: '#fee2e2', fg: '#991b1b' },
        { c: '#2563eb', bg: '#dbeafe', fg: '#1e40af' },
        { c: '#059669', bg: '#d1fae5', fg: '#065f46' },
        { c: '#d97706', bg: '#fef3c7', fg: '#92400e' },
        { c: '#7c3aed', bg: '#ede9fe', fg: '#5b21b6' },
        { c: '#0891b2', bg: '#cffafe', fg: '#155e75' },
        { c: '#db2777', bg: '#fce7f3', fg: '#9d174d' },
        { c: '#65a30d', bg: '#ecfccb', fg: '#3f6212' },
        { c: '#475569', bg: '#e2e8f0', fg: '#1e293b' },
        { c: '#ca8a04', bg: '#fef9c3', fg: '#854d0e' },
        { c: '#0d9488', bg: '#ccfbf1', fg: '#115e59' },
        { c: '#9333ea', bg: '#f3e8ff', fg: '#6b21a8' }
    ];
    var PRESET_TONE = {
        '语文': 0, '数学': 1, '英语': 2,
        '物理': 4, '化学': 5, '生物': 3,
        '政治': 6, '历史': 7, '地理': 10,
        '体育': 8, '音乐': 11, '美术': 11,
        '信息技术': 5, '通用技术': 5, '班会': 8, '自习': 8, '晚自习': 8
    };

    function subjectTone(name) {
        var s = $.trim(name || '');
        if (!s) { return TONES[8]; }
        if (Object.prototype.hasOwnProperty.call(PRESET_TONE, s)) { return TONES[PRESET_TONE[s]]; }
        var h = 0;
        for (var i = 0; i < s.length; i++) { h = (h * 31 + s.charCodeAt(i)) % TONES.length; }
        return TONES[h];
    }

    function toneStyle(name) {
        var t = subjectTone(name);
        return '--sch:' + t.c + ';--sch-bg:' + t.bg + ';--sch-fg:' + t.fg;
    }

    /* ── 轻量 toast（不引第三方库；CSP 允许内联 style 属性） ── */
    function toast(msg, type) {
        var color = type === 'danger' ? '#dc2626' : (type === 'success' ? '#059669' : '#334155');
        var $t = $('<div></div>').text(msg).css({
            position: 'fixed', top: '72px', left: '50%', transform: 'translateX(-50%)',
            zIndex: 2000, background: '#fff', color: color, border: '1px solid ' + color,
            borderLeft: '4px solid ' + color, borderRadius: '8px', padding: '8px 14px',
            boxShadow: '0 6px 20px rgba(15,23,42,.12)', fontSize: '13px', maxWidth: '80vw'
        });
        $('body').append($t);
        setTimeout(function () { $t.fadeOut(220, function () { $(this).remove(); }); }, 2400);
    }

    function readConfig() {
        var raw = $('#scheduleData').text();
        try { CFG = JSON.parse(raw); } catch (e) { CFG = {}; }
        CFG.urls = CFG.urls || {};
    }

    /* 给查询参数对象按需附加 week（null/undefined 不附加） */
    function withWeek(params) {
        params = params || {};
        if (CFG.week) { params.week = CFG.week; }
        return params;
    }

    /* ══════════════════════════════════════════════════════════════
     * 学期切换器（通用）：path 模式改路径中的 sid；query 模式改 ?sid=
     * ══════════════════════════════════════════════════════════════ */
    function initTermSwitcher() {
        var $sel = $('#termSelect');
        if (!$sel.length) { return; }
        $sel.on('change', function () {
            var sid = $(this).val();
            if (!sid) { return; }
            var mode = $('#termSwitcher').data('mode');
            if (mode === 'query') {
                var u = new URL(window.location.href);
                u.searchParams.set('sid', sid);
                window.location.href = u.toString();
            } else {
                // path 模式：/academic/schedule/<sid>/... → 替换 sid 段，保留查询串
                var path = window.location.pathname.replace(/\/schedule\/\d+/, '/schedule/' + sid);
                window.location.href = path + window.location.search;
            }
        });
    }

    /* ══════════════════════════════════════════════════════════════
     * 网格渲染（master 页 AJAX 切换班级时用；class/grade 页为服务端渲染）
     * 结构与 _schedule_grid.html 宏保持一致
     * ══════════════════════════════════════════════════════════════ */
    function entryHtml(e, canEdit) {
        return '<div class="sch-item' + (e.entry_type === 'swap' ? ' sch-item-swap' : '')
            + '" data-entry-id="' + e.id + '" style="' + toneStyle(e.subject) + '"'
            + (canEdit ? ' draggable="true" title="拖到别的格子可换课（点击可编辑）"' : '')
            + '>'
            + '<div class="sch-subject">' + esc(e.subject)
            + (e.entry_type === 'swap' ? '<span class="sch-badge-swap">调</span>' : '')
            + (e.teaching_class ? '<span class="sch-badge-tc" title="走班课：'
                + esc(e.teaching_class) + '">走</span>' : '')
            + (e.week_badge ? '<span class="sch-badge-week">' + esc(e.week_badge) + '</span>' : '')
            + '</div>'
            + '<div class="sch-teacher">' + esc(e.teacher_name || '未指定') + '</div>'
            + (e.room ? '<div class="sch-room"><i class="bi bi-geo-alt"></i> ' + esc(e.room) + '</div>' : '')
            + '</div>';
    }

    /* 有课格子右上角的「再排一门」按钮（单/双周交替用） */
    function addMiniHtml(canEdit) {
        return canEdit
            ? '<span class="sch-add-mini" title="在此格再排一门（如单/双周交替）">+</span>'
            : '';
    }

    /* 取格子内的条目数组：兼容后端返回数组（单双周多条）或单个对象 */
    function cellItems(grid, pn, wd) {
        var v = ((grid || {})[pn] || {})[wd];
        if (!v) { return []; }
        return (Object.prototype.toString.call(v) === '[object Array]') ? v : [v];
    }

    function cellHtml(p, wd, items, canEdit, currentPeriod) {
        items = items || [];
        var cls = 'sch-cell ';
        if (items.length) {
            cls += 'sch-has sch-' + (items[0].entry_type || 'normal');
            if (items.length > 1) { cls += ' sch-multi'; }
        } else {
            cls += 'sch-empty';
        }
        if (canEdit) { cls += ' sch-editable'; }
        if (currentPeriod && currentPeriod === p.period_number) { cls += ' sch-current-col'; }
        var attrs = ' class="' + cls + '" data-period="' + p.period_number + '" data-weekday="' + wd + '"';
        var inner = '';
        if (items.length) {
            items.forEach(function (e) { inner += entryHtml(e, canEdit); });
            inner += addMiniHtml(canEdit);
        } else if (canEdit) {
            inner = '<span class="sch-add">+</span>';
        }
        return '<td' + attrs + '>' + inner + '</td>';
    }

    function renderGrid(container, periods, grid, canEdit, currentPeriod) {
        STATE.periods = periods || [];
        var head = '<tr><th class="sch-period-col">节次</th>';
        for (var wd = 1; wd <= 7; wd++) { head += '<th>' + WD_NAMES[wd - 1] + '</th>'; }
        head += '</tr>';
        var body = '';
        (periods || []).forEach(function (p) {
            var isBreak = p.period_type === 'break';
            body += '<tr class="' + (isBreak ? 'sch-break' : '') + '">'
                + '<th class="sch-period-head">'
                + '<div class="sch-period-name">' + esc(p.period_name)
                + ' <span class="text-muted fw-normal" style="font-size:10px">#' + p.period_number + '</span></div>'
                + '<div class="sch-period-time">' + esc(p.start_time || '--:--') + ' - ' + esc(p.end_time || '--:--') + '</div>'
                + '</th>';
            for (var wd = 1; wd <= 7; wd++) {
                // 「课间/午休」节次同样可排课（如午自习），不排除可编辑
                body += cellHtml(p, wd, cellItems(grid, p.period_number, wd),
                    canEdit, currentPeriod);
            }
            body += '</tr>';
        });
        container.innerHTML = '<div class="sch-grid-wrap"><table class="sch-grid">'
            + '<thead>' + head + '</thead><tbody>' + body + '</tbody></table></div>';
    }

    function gridEmpty(msg) {
        return '<div class="text-center text-muted py-5"><i class="bi bi-inbox" style="font-size:36px"></i>'
            + '<p class="small mt-2 mb-0">' + esc(msg || '暂无课表数据') + '</p></div>';
    }

    function gridSpinner() {
        return '<div class="text-center text-muted py-5"><div class="spinner-border text-primary mb-2"></div>'
            + '<p class="small mb-0">正在加载课表…</p></div>';
    }

    /* ══════════════════════════════════════════════════════════════
     * 大课表（master）：年级标签 → 班级 pills → AJAX 加载网格
     * ══════════════════════════════════════════════════════════════ */
    function initMaster() {
        var gc = CFG.gradeClasses || {};
        selectGrade(CFG.initGrade || Object.keys(gc)[0] || '');
        $('#gradeTabs').on('click', '.grade-tab', function () {
            $('#gradeTabs .grade-tab').removeClass('active');
            $(this).addClass('active');
            selectGrade($(this).data('grade'));
        });
        $('#classPills').on('click', '.class-pill', function () {
            $('#classPills .class-pill').removeClass('active').addClass('btn-outline-primary');
            $(this).removeClass('btn-outline-primary').addClass('active');
            loadClass($(this).data('grade'), $(this).data('class'));
        });
    }

    function selectGrade(g) {
        STATE.grade = g;
        var classes = (CFG.gradeClasses || {})[g] || [];
        var html = '';
        classes.forEach(function (cn, i) {
            html += '<button type="button" class="btn btn-sm py-0 class-pill '
                + (i === 0 ? 'btn-primary active' : 'btn-outline-primary')
                + '" data-grade="' + esc(g) + '" data-class="' + esc(cn) + '">' + esc(cn) + '</button>';
        });
        if (!classes.length) { html = '<span class="text-muted small">该年级暂无班级课表数据</span>'; }
        $('#classPills').html(html);
        updateGridTitle(g, classes.length ? classes[0] : '');
        if (classes.length) {
            loadClass(g, classes[0]);
        } else {
            STATE.className = '';
            $('#gridContainer').html(gridEmpty('请选择其他年级，或先导入/添加课程'));
        }
    }

    function updateGridTitle(g, cn) {
        var html = esc(CFG.termName || '大课表');
        if (g) { html += ' · ' + esc(g); }
        if (cn) { html += ' · ' + esc(cn); }
        if (CFG.week) { html += ' <span class="badge bg-info ms-1">第 ' + CFG.week + ' 周</span>'; }
        $('#gridTitle').html(html);
    }

    function loadClass(g, cn) {
        STATE.className = cn;
        updateGridTitle(g, cn);
        var box = $('#gridContainer');
        box.html(gridSpinner());
        if (!CFG.urls.classData) { box.html(gridEmpty()); return; }
        $.getJSON(CFG.urls.classData, withWeek({ grade: g, class_name: cn }))
            .done(function (res) {
                if (res && res.success) {
                    var d = res.data || {};
                    if (d.periods) { STATE.periods = d.periods; }
                    if (!d.total) {
                        box.html(gridEmpty('该班级暂无课表' + (CFG.canEdit ? '，点击网格空档「+」可添加' : '')));
                        return;
                    }
                    renderGrid(box[0], d.periods, d.grid, CFG.canEdit, null);
                } else {
                    box.html(gridEmpty((res && res.message) || '加载失败'));
                }
            })
            .fail(function () { box.html(gridEmpty('网络错误，加载失败')); });
    }

    /* ══════════════════════════════════════════════════════════════
     * 课表条目 添加/编辑 弹窗（master/grade/class 复用）
     * ══════════════════════════════════════════════════════════════ */
    function initEntryModal() {
        if (!$('#entryModal').length) { return; }
        $('#btnAddEntry').on('click', function () { openAdd(STATE.grade, STATE.className, null, null); });
        // 网格单元格点击（事件委托，兼容 AJAX 重渲染）
        // 同一格子可能有多条（单周/双周交替）：点条目即编辑该条；
        // 单条格子点空白处仍编辑该条（保持旧习惯）；空格或"多条+点空白"则新增。
        $(document).on('click', '.sch-cell.sch-editable', function (ev) {
            var $c = $(this);
            if ($(ev.target).hasClass('sch-add-mini')) {
                openAdd(STATE.grade, STATE.className, $c.data('weekday'), $c.data('period'));
                return;
            }
            var eid = $(ev.target).closest('.sch-item').data('entry-id');
            if (!eid) {
                var ids = $c.find('.sch-item').map(function () {
                    return $(this).data('entry-id');
                }).get();
                if (ids.length === 1) { eid = ids[0]; }
            }
            if (eid) { openEdit(eid); }
            else { openAdd(STATE.grade, STATE.className, $c.data('weekday'), $c.data('period')); }
        });
        $('#entryGrade').on('change', function () { fillClasses($(this).val(), ''); checkConflict(); });
        $('#entryClass, #entryWeekday, #entryPeriod, #entryTeacher').on('change', checkConflict);
        // 周次改动会影响冲突判定（单周课与双周课同格不算冲突）
        $('#entryWeekRange').on('change blur', checkConflict);
        $('#entrySaveBtn').on('click', saveEntry);
        $('#entryDeleteBtn').on('click', deleteEntry);
    }

    function modalInstance() {
        var el = document.getElementById('entryModal');
        return bootstrap.Modal.getOrCreateInstance(el);
    }

    function showInlineError(msg) {
        $('#entryConflictAlert').removeClass('d-none')
            .html('<i class="bi bi-exclamation-triangle"></i> ' + esc(msg || '操作失败'));
    }

    function clearInlineError() {
        $('#entryConflictAlert').addClass('d-none').empty();
    }

    /* 懒加载教师/班级下拉数据（只拉一次） */
    function ensureLookups() {
        var dfd = $.Deferred();
        var tasks = [];
        if (STATE.classes === null && CFG.urls.classes) {
            tasks.push($.getJSON(CFG.urls.classes)
                .done(function (r) { STATE.classes = (r && r.success) ? r.data : {}; })
                .fail(function () { STATE.classes = {}; }));
        }
        if (STATE.teachers === null && CFG.urls.teachers) {
            tasks.push($.getJSON(CFG.urls.teachers)
                .done(function (r) { STATE.teachers = (r && r.success) ? r.data : []; })
                .fail(function () { STATE.teachers = []; }));
        }
        if (!tasks.length) { dfd.resolve(); }
        else { $.when.apply($, tasks).always(function () { dfd.resolve(); }); }
        return dfd.promise();
    }

    function fillGrades(selected) {
        var $g = $('#entryGrade').empty().append('<option value="">--</option>');
        var grades = Object.keys(STATE.classes || {});
        // 合并当前上下文年级，避免下拉里没有正在编辑的年级
        if (selected && grades.indexOf(selected) < 0) { grades.push(selected); }
        grades.sort().forEach(function (g) {
            $g.append('<option value="' + esc(g) + '"' + (g === selected ? ' selected' : '') + '>' + esc(g) + '</option>');
        });
    }

    function fillClasses(grade, selected) {
        var $c = $('#entryClass').empty().append('<option value="">--</option>');
        var list = (STATE.classes || {})[grade] || [];
        if (selected && list.indexOf(selected) < 0) { list = list.concat([selected]); }
        list.forEach(function (cn) {
            $c.append('<option value="' + esc(cn) + '"' + (cn === selected ? ' selected' : '') + '>' + esc(cn) + '</option>');
        });
    }

    function fillTeachers(selectedUid) {
        var $t = $('#entryTeacher').empty().append('<option value="">-- 未指定 --</option>');
        (STATE.teachers || []).forEach(function (t) {
            $t.append('<option value="' + esc(t.uid) + '"' + (t.uid === selectedUid ? ' selected' : '')
                + '>' + esc(t.name) + '（' + esc(t.uid) + (t.subject ? ' · ' + esc(t.subject) : '') + '）</option>');
        });
    }

    function fillPeriods(selected) {
        var $p = $('#entryPeriod').empty();
        // 不过滤「课间/午休」：该类型节次同样可以排课（如午自习），仅加上类型提示
        var list = (STATE.periods || []).slice();
        if (!list.length) {
            for (var i = 1; i <= 13; i++) { list.push({ period_number: i, period_name: '第' + i + '节' }); }
        }
        list.forEach(function (p) {
            var isBreak = p.period_type === 'break';
            var label = p.period_name + ' (#' + p.period_number + ')'
                + (p.start_time ? ' ' + p.start_time : '')
                + (isBreak ? '（课间/午休）' : '');
            $p.append('<option value="' + p.period_number + '"'
                + (selected && Number(selected) === p.period_number ? ' selected' : '') + '>'
                + esc(label) + '</option>');
        });
    }

    function resetForm() {
        $('#entryForm')[0].reset();
        $('#entryId').val('');
        $('#entryRoom').val('');
        // 教学班页新增时默认带上当前教学班（走班课）；其它页留空＝行政班课
        $('#entryTeachingClass').val(CFG.pageType === 'teaching'
            ? (STATE.teachingClass || CFG.teachingClass || '') : '');
        $('#entryWeekRange').val('1-18');
        $('#entryNote').val('');
        $('#entrySubject').val('');
        clearInlineError();
    }

    function openAdd(grade, className, weekday, period) {
        STATE.editingId = null;
        $('#entryModalTitle').text('添加课程');
        $('#entryDeleteBtn').addClass('d-none');
        resetForm();
        ensureLookups().done(function () {
            fillGrades(grade || '');
            fillClasses(grade || '', className || '');
            fillTeachers('');
            fillPeriods(period);
            if (weekday) { $('#entryWeekday').val(String(weekday)); }
            checkConflict();
            modalInstance().show();
        });
    }

    function openEdit(eid) {
        if (!CFG.urls.entryDetail) { return; }
        var url = CFG.urls.entryDetail.replace(/\/entry\/\d+(?=\/|$)/, '/entry/' + eid);
        $.getJSON(url).done(function (res) {
            if (!res || !res.success || !res.data || !res.data.entry) { showInlineError((res && res.message) || '条目不存在'); return; }
            var e = res.data.entry;
            STATE.editingId = e.id;
            $('#entryModalTitle').text('编辑课程');
            $('#entryDeleteBtn').removeClass('d-none');
            resetForm();
            ensureLookups().done(function () {
                fillGrades(e.grade);
                fillClasses(e.grade, e.class_name);
                fillTeachers(e.teacher_uid || '');
                fillPeriods(e.period_number);
                $('#entryId').val(e.id);
                $('#entryWeekday').val(String(e.weekday));
                $('#entrySubject').val(e.subject || '');
                $('#entryRoom').val(e.room || '');
                $('#entryTeachingClass').val(e.teaching_class || '');
                $('#entryWeekRange').val(e.week_range || '1-18');
                $('#entryNote').val(e.note || '');
                checkConflict();
                modalInstance().show();
            });
        }).fail(function () { showInlineError('加载条目详情失败'); });
    }

    function checkConflict() {
        if (!CFG.urls.checkConflict || !CFG.sid) { return; }
        var grade = $('#entryGrade').val(), cn = $('#entryClass').val();
        var wd = $('#entryWeekday').val(), pn = $('#entryPeriod').val();
        var tuid = $('#entryTeacher').val();
        if (!wd || !pn) { return; }
        var params = { sid: CFG.sid, weekday: wd, period_number: pn };
        var wr = $.trim($('#entryWeekRange').val() || '');
        if (grade) { params.grade = grade; }
        if (cn) { params.class_name = cn; }
        if (tuid) { params.teacher_uid = tuid; }
        if (wr) { params.week_range = wr; }
        if (STATE.editingId) { params.exclude_entry_id = STATE.editingId; }
        $.getJSON(CFG.urls.checkConflict, params).done(function (res) {
            if (res && res.success && res.data && res.data.has_conflict) {
                var msgs = [];
                var cc = res.data.class_conflict, tc = res.data.teacher_conflict;
                if (cc) { msgs.push('该班此时段已有「' + cc.subject + '」'); }
                if (tc) { msgs.push('教师「' + (tc.teacher_name || tc.teacher_uid) + '」此时段在 ' + tc.grade + tc.class_name + ' 已有「' + tc.subject + '」'); }
                showInlineError('冲突：' + msgs.join('；'));
            } else {
                clearInlineError();
            }
        });
    }

    function collectPayload() {
        var tuid = $('#entryTeacher').val();
        var tname = tuid ? $('#entryTeacher option:selected').text().split('（')[0] : '';
        return {
            grade: $('#entryGrade').val(),
            class_name: $('#entryClass').val(),
            weekday: Number($('#entryWeekday').val()),
            period_number: Number($('#entryPeriod').val()),
            subject: $.trim($('#entrySubject').val()),
            teacher_uid: tuid || '',
            teacher_name: tname || '',
            room: $.trim($('#entryRoom').val()),
            teaching_class: $.trim($('#entryTeachingClass').val()),
            week_range: $.trim($('#entryWeekRange').val()) || '1-18',
            note: $.trim($('#entryNote').val())
        };
    }

    function saveEntry() {
        var p = collectPayload();
        if (!p.grade || !p.class_name || !p.subject) { showInlineError('年级、班级、学科为必填项'); return; }
        if (!p.period_number) { showInlineError('请选择节次'); return; }
        var isEdit = !!STATE.editingId;
        var url = isEdit
            ? CFG.urls.entryEditTpl.replace(/\/entry\/\d+\//, '/entry/' + STATE.editingId + '/')
            : CFG.urls.entryAdd;
        $('#entrySaveBtn').prop('disabled', true);
        $.ajax({ url: url, type: 'POST', contentType: 'application/json', dataType: 'json', data: JSON.stringify(p) })
            .done(function (res) {
                if (res && res.success) {
                    modalInstance().hide();
                    toast(isEdit ? '已保存修改' : '已添加课程', 'success');
                    refreshGrid();
                } else { showInlineError((res && res.message) || '保存失败'); $('#entrySaveBtn').prop('disabled', false); }
            })
            .fail(function (xhr) {
                var m = '保存失败';
                try { m = (xhr.responseJSON && xhr.responseJSON.message) || m; } catch (e) { }
                showInlineError(m); $('#entrySaveBtn').prop('disabled', false);
            });
    }

    function deleteEntry() {
        if (!STATE.editingId) { return; }
        if (!window.confirm('确认删除该课程条目？（软删除，可在变更历史追溯）')) { return; }
        var url = CFG.urls.entryDeleteTpl.replace(/\/entry\/\d+\//, '/entry/' + STATE.editingId + '/');
        $('#entryDeleteBtn').prop('disabled', true);
        $.ajax({ url: url, type: 'POST', dataType: 'json' })
            .done(function (res) {
                if (res && res.success) {
                    modalInstance().hide();
                    toast('已删除该课程', 'success');
                    refreshGrid();
                } else { showInlineError((res && res.message) || '删除失败'); $('#entryDeleteBtn').prop('disabled', false); }
            })
            .fail(function (xhr) {
                var m = '删除失败';
                try { m = (xhr.responseJSON && xhr.responseJSON.message) || m; } catch (e) { }
                showInlineError(m); $('#entryDeleteBtn').prop('disabled', false);
            });
    }

    /* ══════════════════════════════════════════════════════════════
     * 局部刷新：写操作成功后只重载网格与统计（不再整页 reload）
     * ══════════════════════════════════════════════════════════════ */
    function applyView(data) {
        if (!data) { return; }
        if (data.periods) { STATE.periods = data.periods; }
        var box = $('#gridContainer');
        if (!box.length) { return; }
        if (!data.total) {
            box.html(gridEmpty('暂无课表' + (CFG.canEdit ? '，点击空档「+」或拖动已有课程可排课' : '')));
        } else {
            renderGrid(box[0], data.periods || STATE.periods, data.grid || {}, CFG.canEdit, null);
        }
        updateStats(data);
    }

    /* 同步「共 N 节」、学科课时徽章、饼图（相关 DOM 不存在时静默跳过） */
    function updateStats(data) {
        if (!data) { return; }
        if (data.total != null && $('#gridTotal').length) { $('#gridTotal').text(data.total); }
        var box = $('#statBadges');
        if (box.length && data.stats) {
            var html = '';
            Object.keys(data.stats)
                .sort(function (a, b) { return data.stats[b] - data.stats[a]; })
                .forEach(function (k) {
                    var t = subjectTone(k);
                    html += '<span class="badge border" style="background:' + t.bg + ';color:' + t.fg
                        + ';border-color:' + t.c + '">' + esc(k) + ' × ' + data.stats[k] + '</span>';
                });
            box.html(html);
        }
        var pieEl = document.querySelector('[data-lazy-chart="pie"]');
        if (pieEl && pieEl.__chart && data.stats) {
            var arr = Object.keys(data.stats).map(function (k) { return { name: k, value: data.stats[k] }; });
            arr.sort(function (a, b) { return b.value - a.value; });
            if (arr.length) { pieEl.__chart.setOption({ series: [{ data: arr }] }); }
        }
    }

    function refreshGrid() {
        var t = CFG.pageType;
        if (t === 'master') { loadClass(STATE.grade, STATE.className); return; }
        if (t === 'room' && CFG.urls.roomData) { loadRoom(STATE.room || CFG.room); return; }
        if (t === 'teaching' && CFG.urls.teachingData) {
            loadTeaching(STATE.teachingClass || CFG.teachingClass); return;
        }
        if ((t === 'class' || t === 'grade') && CFG.urls.classData) {
            var params = withWeek({
                grade: CFG.grade || STATE.grade,
                class_name: CFG.className || STATE.className
            });
            $.getJSON(CFG.urls.classData, params)
                .done(function (res) {
                    if (res && res.success) { applyView(res.data); } else { window.location.reload(); }
                })
                .fail(function () { window.location.reload(); });
            return;
        }
        window.location.reload();
    }

    /* ══════════════════════════════════════════════════════════════
     * 教室视图（room）：切换教室 → AJAX 换网格，不整页刷新
     * ══════════════════════════════════════════════════════════════ */
    function loadRoom(room) {
        if (!room || !CFG.urls.roomData) { return; }
        STATE.room = room;
        var box = $('#gridContainer');
        box.html(gridSpinner());
        $.getJSON(CFG.urls.roomData, withWeek({ room: room }))
            .done(function (res) {
                if (res && res.success) {
                    applyView(res.data);
                    $('#roomTitle').text(room);
                } else {
                    box.html(gridEmpty((res && res.message) || '该教室暂无课表'));
                }
            })
            .fail(function () { box.html(gridEmpty('网络错误，加载失败')); });
    }

    /* 教学班视图（teaching）：切换教学班 → AJAX 换网格 */
    function loadTeaching(tc) {
        if (!tc || !CFG.urls.teachingData) { return; }
        STATE.teachingClass = tc;
        var box = $('#gridContainer');
        box.html(gridSpinner());
        $.getJSON(CFG.urls.teachingData, withWeek({ tc: tc }))
            .done(function (res) {
                if (res && res.success) {
                    applyView(res.data);
                    $('#tcTitle').text(tc);
                } else {
                    box.html(gridEmpty((res && res.message) || '该教学班暂无课表'));
                }
            })
            .fail(function () { box.html(gridEmpty('网络错误，加载失败')); });
    }

    function initTeachingSwitcher() {
        STATE.teachingClass = CFG.teachingClass || '';
        $('#tcSelect').on('change', function () {
            var tc = $(this).val();
            if (tc) { loadTeaching(tc); }
        });
        // 「排一节走班课」：默认把当前教学班填进弹窗
        $('#btnAddEntry').on('click', function () {
            if (STATE.teachingClass) { $('#entryTeachingClass').val(STATE.teachingClass); }
        });
    }

    function initRoomSwitcher() {
        STATE.room = CFG.room || '';
        $('#roomSelect').on('change', function () {
            var r = $(this).val();
            if (r) { loadRoom(r); }
        });
        // 「在此教室排课」：默认把教室填进弹窗
        $('#btnAddEntry').on('click', function () {
            if (STATE.room) { $('#entryRoom').val(STATE.room); }
        });
    }

    /* ══════════════════════════════════════════════════════════════
     * 网格导出 PNG（批次 D）：纯 canvas 手绘当前网格，无第三方依赖
     * ══════════════════════════════════════════════════════════════ */
    function fitText(ctx, text, maxW) {
        if (ctx.measureText(text).width <= maxW) { return text; }
        var s = String(text);
        while (s.length > 1 && ctx.measureText(s + '…').width > maxW) { s = s.slice(0, -1); }
        return s + '…';
    }

    function parseGridDom() {
        var table = document.querySelector('#gridContainer table.sch-grid');
        if (!table) { return null; }
        var head = [];
        $(table).find('thead th').each(function () { head.push($(this).text().trim()); });
        var rows = [];
        $(table).find('tbody tr').each(function () {
            var $tr = $(this);
            var $ph = $tr.find('.sch-period-head');
            var row = {
                break: $tr.hasClass('sch-break'),
                name: $ph.find('.sch-period-name').text().trim(),
                time: $ph.find('.sch-period-time').text().trim(),
                cells: []
            };
            $tr.find('td.sch-cell').each(function () {
                var items = [];
                $(this).find('.sch-item').each(function () {
                    var el = this;
                    var st = el.getAttribute('style') || '';
                    var c = /--sch:\s*([^;]+)/.exec(st);
                    var bg = /--sch-bg:\s*([^;]+)/.exec(st);
                    var fg = /--sch-fg:\s*([^;]+)/.exec(st);
                    var $sub = $(el).find('.sch-subject').clone();
                    $sub.find('.sch-badge-swap, .sch-badge-week').remove();
                    items.push({
                        subject: $sub.text().trim(),
                        swap: $(el).find('.sch-badge-swap').length > 0,
                        teacher: $(el).find('.sch-teacher').text().trim(),
                        room: $(el).find('.sch-room').text().trim(),
                        c: c ? c[1].trim() : '#94a3b8',
                        bg: bg ? bg[1].trim() : '#f1f5f9',
                        fg: fg ? fg[1].trim() : '#0f172a'
                    });
                });
                row.cells.push(items);
            });
            rows.push(row);
        });
        return { head: head, rows: rows };
    }

    function exportGridPng() {
        if (CFG.pageType === 'overview' || document.getElementById('ovContainer')) {
            exportOverviewPng();
            return;
        }
        var data = parseGridDom();
        if (!data || !data.rows.length) { toast('当前页面没有可导出的课表', 'danger'); return; }
        var title = $('.card-header').first().text().replace(/\s+/g, ' ').trim()
            || document.title.split('·')[0].trim();

        var leftW = 116, colW = 152, headH = 40, titleH = 52, footH = 34, pad = 14;
        var heights = data.rows.map(function (r) { return Math.max(62, r.cells.reduce(function (m, items) {
            return Math.max(m, items.length * 52);
        }, 0) + 10); });
        var totalH = data.rows.reduce(function (a, b) { return a + b; }, 0);
        var W = pad * 2 + leftW + colW * 7;
        var H = pad * 2 + titleH + headH + totalH + footH;
        var dpr = window.devicePixelRatio || 1;
        var cv = document.createElement('canvas');
        cv.width = W * dpr; cv.height = H * dpr;
        var ctx = cv.getContext('2d');
        ctx.scale(dpr, dpr);
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, W, H);
        ctx.textBaseline = 'middle';

        var x0 = pad, y = pad;
        // 标题
        ctx.fillStyle = '#0f172a';
        ctx.font = 'bold 18px "Microsoft YaHei", "PingFang SC", sans-serif';
        ctx.fillText(fitText(ctx, title, W - pad * 2), x0, y + titleH / 2);
        y += titleH;

        // 表头
        ctx.fillStyle = '#1e293b';
        ctx.fillRect(x0, y, leftW + colW * 7, headH);
        ctx.fillStyle = '#e2e8f0';
        ctx.font = 'bold 13px "Microsoft YaHei", "PingFang SC", sans-serif';
        ctx.textAlign = 'center';
        for (var c = 0; c < 7; c++) {
            ctx.fillText(data.head[c + 1] || '', x0 + leftW + colW * c + colW / 2, y + headH / 2);
        }
        ctx.textAlign = 'left';
        ctx.fillStyle = '#e2e8f0';
        ctx.fillText('节次', x0 + 10, y + headH / 2);
        y += headH;

        // 行
        data.rows.forEach(function (r, ri) {
            var rh = heights[ri];
            // 节次列
            ctx.fillStyle = r.break ? '#f1f5f9' : '#f8fafc';
            ctx.fillRect(x0, y, leftW, rh);
            ctx.fillStyle = '#334155';
            ctx.font = 'bold 12.5px "Microsoft YaHei", "PingFang SC", sans-serif';
            ctx.fillText(fitText(ctx, r.name, leftW - 16), x0 + 8, y + rh / 2 - 9);
            ctx.fillStyle = '#94a3b8';
            ctx.font = '11px "Microsoft YaHei", "PingFang SC", sans-serif';
            ctx.fillText(fitText(ctx, r.time, leftW - 16), x0 + 8, y + rh / 2 + 9);

            r.cells.forEach(function (items, ci) {
                var cx = x0 + leftW + colW * ci;
                ctx.fillStyle = r.break ? '#fbfdff' : '#ffffff';
                ctx.fillRect(cx, y, colW, rh);
                var iy = y + 6;
                items.forEach(function (it) {
                    var ih = Math.max(46, Math.floor((rh - 12) / Math.max(items.length, 1)) - 4);
                    ctx.fillStyle = it.bg;
                    ctx.fillRect(cx + 4, iy, colW - 8, ih);
                    ctx.fillStyle = it.c;
                    ctx.fillRect(cx + 4, iy, 3, ih);
                    ctx.fillStyle = it.fg;
                    ctx.font = 'bold 12.5px "Microsoft YaHei", "PingFang SC", sans-serif';
                    var label = it.subject + (it.swap ? '（调）' : '');
                    ctx.fillText(fitText(ctx, label, colW - 22), cx + 12, iy + 13);
                    ctx.fillStyle = '#64748b';
                    ctx.font = '11px "Microsoft YaHei", "PingFang SC", sans-serif';
                    if (it.teacher) { ctx.fillText(fitText(ctx, it.teacher, colW - 22), cx + 12, iy + 28); }
                    ctx.fillStyle = '#94a3b8';
                    ctx.font = '10.5px "Microsoft YaHei", "PingFang SC", sans-serif';
                    if (it.room) { ctx.fillText(fitText(ctx, it.room, colW - 22), cx + 12, iy + 41); }
                    iy += ih + 4;
                });
            });

            // 网格线
            ctx.strokeStyle = '#e2e8f0';
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(x0, y + rh + .5); ctx.lineTo(x0 + leftW + colW * 7, y + rh + .5);
            ctx.stroke();
            y += rh;
        });

        // 竖线
        ctx.beginPath();
        for (var i = 0; i <= 7; i++) {
            var lx = x0 + leftW + colW * i + .5;
            ctx.moveTo(lx, pad + titleH); ctx.lineTo(lx, y);
        }
        ctx.moveTo(x0 + .5, pad + titleH); ctx.lineTo(x0 + .5, y);
        ctx.stroke();

        // 页脚
        ctx.fillStyle = '#94a3b8';
        ctx.font = '11px "Microsoft YaHei", "PingFang SC", sans-serif';
        var stamp = new Date().toLocaleString('zh-CN', { hour12: false });
        ctx.fillText('导出时间：' + stamp + (CFG.week ? '　第 ' + CFG.week + ' 周' : ''), x0, y + footH / 2 + 4);

        cv.toBlob(function (blob) {
            var a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = (title.replace(/[\\/:*?"<>|\s]/g, '_') || '课表') + '.png';
            document.body.appendChild(a);
            a.click();
            a.remove();
            setTimeout(function () { URL.revokeObjectURL(a.href); }, 4000);
            toast('已导出图片', 'success');
        }, 'image/png');
    }

    /* ── 全校总课表导出 PNG：按年级分块、班级并列，纵向堆叠到一张图 ── */
    function parseOverviewDom() {
        var blocks = [];
        $('#ovContainer .ov-block').each(function () {
            var $b = $(this);
            var b = { grade: $b.data('grade') || '', classes: [], rows: [] };
            $b.find('thead tr.ov-class-head th').each(function (i) {
                if (i >= 2) { b.classes.push($(this).text().trim()); }
            });
            var curWeekday = '';
            $b.find('tbody tr').each(function () {
                var $tr = $(this);
                var $wd = $tr.find('.ov-weekday');
                if ($wd.length) { curWeekday = $wd.text().trim(); }
                var $p = $tr.find('.ov-period').clone();
                $p.find('small').remove();
                var row = {
                    weekday: curWeekday,
                    period: $p.text().trim(),
                    time: ($tr.find('.ov-period small').first().text() || '').trim(),
                    brk: $tr.hasClass('ov-break'),
                    cells: []
                };
                $tr.find('td.ov-cell').each(function () {
                    var items = [];
                    $(this).find('.ov-item').each(function () {
                        var st = this.getAttribute('style') || '';
                        var c = /--sch:\s*([^;]+)/.exec(st);
                        items.push({
                            subject: $(this).find('.ov-subj').text().trim(),
                            teacher: $(this).find('.ov-teacher').text().trim(),
                            room: $(this).find('.ov-room').text().trim(),
                            c: c ? c[1].trim() : '#94a3b8'
                        });
                    });
                    row.cells.push(items);
                });
                b.rows.push(row);
            });
            blocks.push(b);
        });
        return blocks;
    }

    function exportOverviewPng() {
        var blocks = parseOverviewDom();
        if (!blocks.length) { toast('当前页面没有可导出的总课表', 'danger'); return; }
        var W1 = 34, W2 = 56, WC = 74, headH = 24, rowH = 20, gap = 18, pad = 14, titleH = 44;
        var maxClasses = blocks.reduce(function (m, b) { return Math.max(m, b.classes.length); }, 1);
        var W = pad * 2 + W1 + W2 + WC * maxClasses;
        var H = pad * 2 + titleH + blocks.reduce(function (acc, b) {
            return acc + 26 + headH + b.rows.length * rowH + gap;
        }, 0);
        var dpr = window.devicePixelRatio || 1;
        var cv = document.createElement('canvas');
        cv.width = W * dpr; cv.height = H * dpr;
        var ctx = cv.getContext('2d');
        ctx.scale(dpr, dpr);
        ctx.fillStyle = '#fff';
        ctx.fillRect(0, 0, W, H);
        ctx.textBaseline = 'middle';

        var title = '全校总课表 · ' + $('.card-header').first().text().replace(/\s+/g, ' ').trim();
        ctx.fillStyle = '#0f172a';
        ctx.font = 'bold 16px "Microsoft YaHei", "PingFang SC", sans-serif';
        ctx.fillText(fitText(ctx, title, W - pad * 2), pad, pad + 14);

        var y = pad + titleH;
        blocks.forEach(function (b) {
            // 年级标题
            ctx.fillStyle = '#1e293b';
            ctx.font = 'bold 13px "Microsoft YaHei", "PingFang SC", sans-serif';
            ctx.fillText(fitText(ctx, b.grade + '（' + b.classes.length + ' 个班）', W - pad * 2),
                pad, y + 12);
            y += 26;

            // 表头
            ctx.fillStyle = '#eef2f7';
            ctx.fillRect(pad, y, W1 + W2 + WC * b.classes.length, headH);
            ctx.fillStyle = '#334155';
            ctx.font = 'bold 11px "Microsoft YaHei", "PingFang SC", sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText('星期', pad + W1 / 2, y + headH / 2);
            ctx.fillText('节次', pad + W1 + W2 / 2, y + headH / 2);
            b.classes.forEach(function (cn, i) {
                ctx.fillText(fitText(ctx, cn, WC - 6), pad + W1 + W2 + WC * i + WC / 2, y + headH / 2);
            });
            y += headH;

            // 数据行
            b.rows.forEach(function (r) {
                ctx.fillStyle = r.brk ? '#fafbfd' : '#fff';
                ctx.fillRect(pad, y, W1 + W2 + WC * b.classes.length, rowH);
                ctx.textAlign = 'center';
                ctx.fillStyle = '#475569';
                ctx.font = 'bold 10.5px "Microsoft YaHei", "PingFang SC", sans-serif';
                ctx.fillText(fitText(ctx, r.weekday, W1 - 4), pad + W1 / 2, y + rowH / 2);
                ctx.font = '9.5px "Microsoft YaHei", "PingFang SC", sans-serif';
                ctx.fillStyle = '#64748b';
                ctx.fillText(fitText(ctx, r.period, W2 - 4), pad + W1 + W2 / 2, y + rowH / 2);
                ctx.textAlign = 'left';
                r.cells.forEach(function (items, ci) {
                    var cx = pad + W1 + W2 + WC * ci;
                    var label = items.map(function (it) {
                        return it.subject + (it.teacher ? ' ' + it.teacher : '');
                    }).join(' / ');
                    if (label) {
                        ctx.fillStyle = items[0].c;
                        ctx.fillRect(cx + 2, y + 3, 2, rowH - 6);
                        ctx.fillStyle = '#0f172a';
                        ctx.font = '10px "Microsoft YaHei", "PingFang SC", sans-serif';
                        ctx.fillText(fitText(ctx, label, WC - 10), cx + 8, y + rowH / 2);
                    }
                });
                ctx.strokeStyle = '#e5eaf1';
                ctx.lineWidth = 1;
                ctx.beginPath();
                ctx.moveTo(pad, y + rowH + .5);
                ctx.lineTo(pad + W1 + W2 + WC * b.classes.length, y + rowH + .5);
                ctx.stroke();
                y += rowH;
            });
            y += gap;
        });

        cv.toBlob(function (blob) {
            var a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = '全校总课表_' + new Date().toISOString().slice(0, 10) + '.png';
            document.body.appendChild(a);
            a.click();
            a.remove();
            setTimeout(function () { URL.revokeObjectURL(a.href); }, 4000);
            toast('已导出总课表图片', 'success');
        }, 'image/png');
    }

    /* ══════════════════════════════════════════════════════════════
     * 分享链接（批次 D）：后端签名短链，7 天有效；打开仍需登录+查看权限
     * ══════════════════════════════════════════════════════════════ */
    function shareSchedule() {
        var url = CFG.urls && CFG.urls.share;
        if (!url) { toast('当前页面不支持分享', 'danger'); return; }
        var payload = {
            view: CFG.pageType,
            week: CFG.week || '',
            grade: CFG.grade || STATE.grade || '',
            class_name: CFG.className || STATE.className || '',
            room: CFG.room || STATE.room || '',
            uid: CFG.uid || ''
        };
        $.ajax({ url: url, type: 'POST', contentType: 'application/json',
                 dataType: 'json', data: JSON.stringify(payload) })
            .done(function (res) {
                if (!res || !res.success) { toast((res && res.message) || '生成失败', 'danger'); return; }
                var link = res.data.url;
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard.writeText(link).then(function () {
                        toast('分享链接已复制（7 天有效，打开需登录）', 'success');
                    }, function () { window.prompt('分享链接（7 天有效）', link); });
                } else {
                    window.prompt('分享链接（7 天有效）', link);
                }
            })
            .fail(function () { toast('生成分享链接失败', 'danger'); });
    }

    /* ══════════════════════════════════════════════════════════════
     * 拖拽换格调课（HTML5 DnD）：拖到别的格子 = 改该条目的星期/节次。
     * 目标格已有课时不禁 drop（单/双周交替本可共存），由后端按周次判定冲突，
     * 冲突时返回 message 用 toast 提示，网格保持不动。
     * ══════════════════════════════════════════════════════════════ */
    function initDragMove() {
        if (!CFG.canEdit) { return; }
        var dragged = null;

        $(document).on('dragstart', '.sch-item[draggable="true"]', function (ev) {
            dragged = $(this);
            $(this).addClass('sch-dragging');
            var dt = ev.originalEvent && ev.originalEvent.dataTransfer;
            if (dt) { dt.effectAllowed = 'move'; dt.setData('text/plain', String($(this).data('entry-id'))); }
        });

        $(document).on('dragend', '.sch-item', function () {
            $(this).removeClass('sch-dragging');
            $('.sch-cell').removeClass('sch-drop-hover sch-drop-blocked');
            dragged = null;
        });

        $(document).on('dragover', '.sch-cell.sch-editable', function (ev) {
            if (!dragged) { return; }
            ev.preventDefault();
            $(this).addClass($(this).find('.sch-item').length ? 'sch-drop-blocked' : 'sch-drop-hover');
            var dt = ev.originalEvent && ev.originalEvent.dataTransfer;
            if (dt) { dt.dropEffect = 'move'; }
        });

        $(document).on('dragleave', '.sch-cell', function () {
            $(this).removeClass('sch-drop-hover sch-drop-blocked');
        });

        $(document).on('drop', '.sch-cell.sch-editable', function (ev) {
            ev.preventDefault();
            $(this).removeClass('sch-drop-hover sch-drop-blocked');
            if (!dragged) { return; }
            var $cell = $(this);
            var wd = Number($cell.data('weekday')), pn = Number($cell.data('period'));
            var $src = dragged.closest('.sch-cell');
            if (Number($src.data('weekday')) === wd && Number($src.data('period')) === pn) { return; }
            moveEntry(dragged.data('entry-id'), wd, pn);
        });
    }

    function moveEntry(eid, weekday, period) {
        var detailUrl = (CFG.urls.entryDetail || '').replace(/\/entry\/\d+(?=\/|$)/, '/entry/' + eid);
        if (!detailUrl) { toast('当前页面不支持拖拽换课', 'danger'); return; }
        $.getJSON(detailUrl).done(function (res) {
            var e = res && res.success && res.data && res.data.entry;
            if (!e) { toast((res && res.message) || '读取条目失败', 'danger'); return; }
            var payload = {
                grade: e.grade, class_name: e.class_name,
                weekday: weekday, period_number: period,
                subject: e.subject, teacher_uid: e.teacher_uid || '',
                teacher_name: e.teacher_name || '', room: e.room || '',
                teaching_class: e.teaching_class || '',
                week_range: e.week_range || '1-18', note: e.note || ''
            };
            var url = (CFG.urls.entryEditTpl || '').replace(/\/entry\/\d+\//, '/entry/' + eid + '/');
            $.ajax({ url: url, type: 'POST', contentType: 'application/json', dataType: 'json',
                     data: JSON.stringify(payload) })
                .done(function (r) {
                    if (r && r.success) {
                        toast('已移到 ' + WD_NAMES[weekday - 1] + ' 第' + period + '节', 'success');
                        refreshGrid();
                    } else {
                        toast((r && r.message) || '换课失败（目标时段可能有冲突）', 'danger');
                    }
                })
                .fail(function (xhr) {
                    var m = '换课失败';
                    try { m = (xhr.responseJSON && xhr.responseJSON.message) || m; } catch (err) { }
                    toast(m, 'danger');
                });
        }).fail(function () { toast('读取条目失败', 'danger'); });
    }

    /* ══════════════════════════════════════════════════════════════
     * ECharts 懒加载（class/teacher 页学科课时占比饼图）
     * ══════════════════════════════════════════════════════════════ */
    function initCharts() {
        var els = document.querySelectorAll('[data-lazy-chart]');
        if (!els.length || typeof echarts === 'undefined') { return; }
        var io = new IntersectionObserver(function (entries) {
            entries.forEach(function (en) {
                if (en.isIntersecting) { drawPie(en.target); io.unobserve(en.target); }
            });
        }, { rootMargin: '120px' });
        Array.prototype.forEach.call(els, function (el) { io.observe(el); });
    }

    function drawPie(el) {
        var stats = CFG.stats || {};
        var data = Object.keys(stats).map(function (k) { return { name: k, value: stats[k] }; });
        data.sort(function (a, b) { return b.value - a.value; });
        var chart = echarts.init(el);
        el.__chart = chart;   // 供 updateStats() 在局部刷新后同步饼图
        if (!data.length) {
            el.innerHTML = '<div class="text-center text-muted small pt-5">暂无课时数据</div>';
            return;
        }
        chart.setOption({
            tooltip: { trigger: 'item', formatter: '{b}: {c} 节 ({d}%)' },
            legend: { type: 'scroll', orient: 'vertical', right: 4, top: 'center', textStyle: { fontSize: 11 } },
            series: [{
                type: 'pie', radius: ['40%', '70%'], center: ['36%', '50%'], data: data,
                label: { show: false }, emphasis: { label: { show: true, fontSize: 13, fontWeight: 'bold' } }
            }]
        });
        $(window).on('resize', function () { chart.resize(); });
    }

    /* ══════════════════════════════════════════════════════════════
     * 今日课表：时钟 + 30 秒自动刷新
     * ══════════════════════════════════════════════════════════════ */
    function initToday() {
        function tick() {
            var d = new Date();
            var pad = function (n) { return n < 10 ? '0' + n : '' + n; };
            $('#clockText').text(pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds()));
        }
        if ($('#clockText').length) { tick(); setInterval(tick, 1000); }
        var timer = null;
        function schedule() {
            if (timer) { clearInterval(timer); timer = null; }
            if ($('#autoRefresh').is(':checked')) {
                timer = setInterval(function () { window.location.reload(); }, 30000);
            }
        }
        $('#autoRefresh').on('change', schedule);
        schedule();
    }

    /* ══════════════════════════════════════════════════════════════
     * 节次配置：动态增删行
     * ══════════════════════════════════════════════════════════════ */
    function initPeriods() {
        if (!CFG.canEdit) { return; }
        var pt = CFG.periodTypes || {};
        var MAXP = 13;

        function refreshAddBtn() {
            var count = $('#periodsBody tr[data-period-row]').length;
            $('#btnAddPeriodRow').prop('disabled', count >= MAXP)
                .attr('title', count >= MAXP ? '一天最多 ' + MAXP + ' 节' : '');
        }

        $('#btnAddPeriodRow').on('click', function () {
            var used = {}, maxn = 0;
            $('#periodsBody tr[data-period-row]').each(function () {
                var n = parseInt($(this).find('[name=period_number]').val(), 10) || 0;
                if (n > 0) { used[n] = true; }
                if (n > maxn) { maxn = n; }
            });
            // 取「未占用的最小可用编号」，避免新增行与已有行重号（重号会互相覆盖）
            var n = 0;
            for (var i = 1; i <= MAXP; i++) { if (!used[i]) { n = i; break; } }
            if (!n) { window.alert('一天最多 ' + MAXP + ' 节，无法再添加'); return; }
            var opts = '';
            Object.keys(pt).forEach(function (k) { opts += '<option value="' + esc(k) + '">' + esc(pt[k]) + '</option>'; });
            var row = '<tr data-period-row>'
                + '<td><input type="number" name="period_number" class="form-control form-control-sm" min="1" max="13" value="' + n + '" required></td>'
                + '<td><input name="period_name" class="form-control form-control-sm" maxlength="20" value="第' + n + '节" required></td>'
                + '<td><input type="time" name="start_time" class="form-control form-control-sm"></td>'
                + '<td><input type="time" name="end_time" class="form-control form-control-sm"></td>'
                + '<td><select name="period_type" class="form-select form-select-sm">' + opts + '</select></td>'
                + '<td class="text-center"><button type="button" class="btn btn-sm btn-outline-danger py-0 px-1 btn-del-period" title="移除此节次（保存后生效）"><i class="bi bi-x-lg"></i></button></td>'
                + '</tr>';
            $('#periodsBody tr').not('[data-period-row]').remove();
            $('#periodsBody').append(row);
            refreshAddBtn();
        });
        $('#periodsBody').on('click', '.btn-del-period', function () {
            $(this).closest('tr').remove();
            refreshAddBtn();
        });
        refreshAddBtn();
    }

    /* ══════════════════════════════════════════════════════════════
     * 变更历史：快照弹窗
     * ══════════════════════════════════════════════════════════════ */
    function initVersions() {
        $(document).on('click', '.btn-snapshot', function () {
            var raw = $(this).attr('data-snapshot');
            var text = raw;
            try { text = JSON.stringify(JSON.parse(raw), null, 2); } catch (e) { }
            $('#snapshotContent').text(text);
            bootstrap.Modal.getOrCreateInstance(document.getElementById('snapshotModal')).show();
        });
    }

    /* ══════════════════════════════════════════════════════════════
     * 导入：提交时禁用按钮防重复
     * ══════════════════════════════════════════════════════════════ */
    function initImport() {
        $('#importForm').on('submit', function () {
            $('#importSubmitBtn').prop('disabled', true)
                .html('<span class="spinner-border spinner-border-sm"></span> 导入中…');
        });
    }

    /* ══════════════════════════════════════════════════════════════
     * 教师课表：切换教师下拉跳转
     * ══════════════════════════════════════════════════════════════ */
    function initTeacher() {
        $('#teacherJump').on('change', function () {
            var uid = $(this).val();
            if (!uid) { return; }
            var url = (CFG.teacherUrlTpl || '').replace('__UID__', encodeURIComponent(uid));
            if (!url) { return; }
            if (CFG.week) { url += (url.indexOf('?') >= 0 ? '&' : '?') + 'week=' + CFG.week; }
            window.location.href = url;
        });
    }

    /* ══════════════════════════════════════════════════════════════
     * 查课实时课表：日期/年级筛选跳转
     * ══════════════════════════════════════════════════════════════ */
    function initInspection() {
        function nav() {
            var date = $('#inspDate').val(), grade = $('#inspGrade').val();
            var base = CFG.periodUrlBase || CFG.todayUrl;
            if (!base) { return; }
            var u = new URL(base, window.location.origin);
            if (date) { u.searchParams.set('date', date); } else { u.searchParams.delete('date'); }
            if (grade) { u.searchParams.set('grade', grade); } else { u.searchParams.delete('grade'); }
            if (CFG.week) { u.searchParams.set('week', CFG.week); }
            if (CFG.sid) { u.searchParams.set('sid', CFG.sid); }
            window.location.href = u.toString();
        }
        $('#inspDate').on('change', nav);
        $('#inspGrade').on('change', nav);
    }

    /* ══════════════════════════════════════════════════════════════
     * 入口分发
     * ══════════════════════════════════════════════════════════════ */
    $(function () {
        readConfig();
        initTermSwitcher();
        // 输出工具（各课表页共用，按钮不存在时自动忽略）
        $(document).on('click', '#btnExportPng', exportGridPng);
        $(document).on('click', '#btnShareSchedule', shareSchedule);
        STATE.grade = CFG.grade || CFG.initGrade || '';
        STATE.className = CFG.className || '';
        if (CFG.periods) { STATE.periods = CFG.periods; }
        switch (CFG.pageType) {
            case 'master': initMaster(); initEntryModal(); initDragMove(); break;
            case 'grade': initEntryModal(); initCharts(); initDragMove(); break;
            case 'class': initEntryModal(); initCharts(); initDragMove(); break;
            case 'room': initEntryModal(); initDragMove(); initRoomSwitcher(); break;
            case 'teaching': initEntryModal(); initDragMove(); initTeachingSwitcher(); break;
            case 'teacher': initTeacher(); initCharts(); break;
            case 'today': initToday(); break;
            case 'periods': initPeriods(); break;
            case 'versions': initVersions(); break;
            case 'import': initImport(); break;
            case 'inspection': initInspection(); break;
            default: break;
        }
    });

})();
