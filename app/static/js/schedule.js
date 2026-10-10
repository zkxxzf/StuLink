/* StuLink v1.18.9.2 学期课表前端
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
     * 网格渲染（class/grade 页 AJAX 换班/改周次时重绘时用）
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

    /* 2026-10-10：大课表（master）页已下线，其独占的 initMaster / selectGrade /
       updateGridTitle / loadClass 一并删除。原职能（年级标签 → 班级 pills → AJAX 换班
       加载网格）改由「全校总课表」看全校、「年级课表 / 班级课表」看单班并编辑。 */

    /* ══════════════════════════════════════════════════════════════
     * 课表条目 添加/编辑 弹窗（grade/class 复用）
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
            // 兜底：该学期还没定义节次时，按内置默认模板的节数（13 = 早读+上午5+下午4+晚自习3）
            // 占位。这不是"最多 13 节"的限制 —— 节次数量由学校自定义（2026-10-10 去上限）
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
        $('#entryTeachingClass').val('');   // 走班教学班视图已删（2026-10-10），留空＝行政班课
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
        if (t === 'room' && CFG.urls.roomData) { loadRoom(STATE.room || CFG.room); return; }
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

    /* ── 全校总课表导出 PNG（2026-10-10 竖版定版）：行＝节次（左列作息分组、
         次列节次名）、列＝班级（按年级分组），与页面 .ovv-* 结构一致 ── */
    function parseOverviewMatrix() {
        var $t = $('#ovContainer .ovv-table').first();
        if (!$t.length) { return null; }
        var m = { grades: [], columns: [], rows: [] };
        // 年级分组表头（跨列）
        $t.find('thead tr.ovv-grade-row th.ovv-grade').each(function () {
            m.grades.push({
                label: $(this).find('.ovv-grade-t').first().text().trim(),
                span: parseInt($(this).attr('colspan') || '1', 10)
            });
        });
        // 班级列（名称 + 班型/选科小字）
        $t.find('thead tr.ovv-class-row th.ovv-class').each(function () {
            var $th = $(this);
            m.columns.push({
                name: $th.clone().find('.ovv-cmeta').remove().end().text().trim(),
                meta: ($th.find('.ovv-cmeta').first().text() || '').trim()
            });
        });
        // 节次行：首列作息分组是 rowspan，只在组首行出现 → 向下继承
        var group = '', groupStart = false;
        $t.find('tbody tr.ovv-row').each(function () {
            var $tr = $(this);
            var $g = $tr.find('th.ovv-group').first();
            if ($g.length) { group = $g.text().replace(/\s+/g, ''); groupStart = true; }
            var $p = $tr.find('th.ovv-period').first();
            var $name = $p.find('.ovv-pname').clone();
            $name.find('.ovv-cur-flag').remove();
            var row = {
                group: group,
                groupStart: groupStart,
                name: $name.text().trim(),
                time: ($p.find('.ovv-ptime').text() || '').replace(/\s+/g, ' ').trim(),
                brk: $tr.hasClass('ovv-break-row'),
                cells: []
            };
            groupStart = false;
            $tr.find('td.ovv-cell').each(function () {
                var items = [];
                $(this).find('.ovw-item').each(function () {
                    var st = this.getAttribute('style') || '';
                    var c = /--sch:\s*([^;]+)/.exec(st);
                    var bg = /--sch-bg:\s*([^;]+)/.exec(st);
                    var fg = /--sch-fg:\s*([^;]+)/.exec(st);
                    var $sub = $(this).find('.ovw-subj').clone();
                    $sub.find('.ovw-flag').remove();
                    items.push({
                        subject: $sub.text().trim(),
                        swap: $(this).find('.ovw-flag-swap').length > 0,
                        teacher: $(this).find('.ovw-teacher').text().trim(),
                        c: c ? c[1].trim() : '#94a3b8',
                        bg: bg ? bg[1].trim() : '#f1f5f9',
                        fg: fg ? fg[1].trim() : '#0f172a'
                    });
                });
                row.cells.push(items);
            });
            m.rows.push(row);
        });
        return m.columns.length ? m : null;
    }

    function exportOverviewPng() {
        var m = parseOverviewMatrix();
        if (!m || !m.rows.length) { toast('当前页面没有可导出的总课表', 'danger'); return; }

        var pad = 18, titleH = 44, footH = 28;
        var gradeRowH = 30, classRowH = 46, rowH = 46, itemH = 34;
        var groupW = 28, perW = 86, classW = 104;
        var axisW = groupW + perW;

        function rowHeight(row) {
            var n = row.cells.reduce(function (a, its) { return Math.max(a, its.length); }, 0);
            return Math.max(rowH, n * (itemH + 4) + 10);
        }
        var tableH = m.rows.reduce(function (a, r) { return a + rowHeight(r); }, 0);
        var W = pad * 2 + axisW + m.columns.length * classW;
        var H = pad * 2 + titleH + gradeRowH + classRowH + footH + tableH;

        var dpr = window.devicePixelRatio || 1;
        var cv = document.createElement('canvas');
        cv.width = W * dpr; cv.height = H * dpr;
        var ctx = cv.getContext('2d');
        ctx.scale(dpr, dpr);
        ctx.fillStyle = '#fff';
        ctx.fillRect(0, 0, W, H);
        ctx.textBaseline = 'middle';

        var dayLabel = ($('#ovContainer').data('dayLabel') || '').toString();
        var title = (document.title || '全校总课表').trim()
            + (dayLabel ? ' · ' + dayLabel : '')
            + (CFG.week ? ' · 第 ' + CFG.week + ' 周' : '');
        ctx.fillStyle = '#0f172a';
        ctx.font = 'bold 19px "Microsoft YaHei", "PingFang SC", sans-serif';
        ctx.textAlign = 'left';
        ctx.fillText(fitText(ctx, title, W - pad * 2), pad, pad + 16);

        var GROUP_BG = { '早读': '#fef3c7', '上午': '#e0f2fe', '下午': '#ffedd5',
                         '晚自习': '#e0e7ff', '课间': '#f1f5f9' };
        var GRADE_BG = ['#1d4ed8', '#475569', '#0d9488'];

        var top = pad + titleH;
        // ── 表头：左轴（作息/节次）+ 年级行 + 班级行 ──
        ctx.fillStyle = '#eef2f7';
        ctx.fillRect(pad, top, axisW, gradeRowH + classRowH);
        ctx.fillStyle = '#334155';
        ctx.font = 'bold 12px "Microsoft YaHei", "PingFang SC", sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('作息', pad + groupW / 2, top + (gradeRowH + classRowH) / 2 - 9);
        ctx.fillText('节次', pad + groupW + perW / 2, top + (gradeRowH + classRowH) / 2 - 9);
        var cx = pad + axisW;
        m.grades.forEach(function (g, gi) {
            var gw = g.span * classW;
            ctx.fillStyle = GRADE_BG[gi % 3];
            ctx.fillRect(cx, top, gw, gradeRowH);
            ctx.fillStyle = '#fff';
            ctx.font = 'bold 13px "Microsoft YaHei", "PingFang SC", sans-serif';
            ctx.fillText(fitText(ctx, g.label, gw - 10), cx + gw / 2, top + gradeRowH / 2);
            cx += gw;
        });
        m.columns.forEach(function (c, ci) {
            var x = pad + axisW + ci * classW;
            ctx.fillStyle = '#f8fafc';
            ctx.fillRect(x, top + gradeRowH, classW, classRowH);
            ctx.fillStyle = '#1d4ed8';
            ctx.font = 'bold 12.5px "Microsoft YaHei", "PingFang SC", sans-serif';
            ctx.fillText(fitText(ctx, c.name, classW - 10), x + classW / 2,
                top + gradeRowH + (c.meta ? 15 : classRowH / 2));
            if (c.meta) {
                ctx.fillStyle = '#94a3b8';
                ctx.font = '9.5px "Microsoft YaHei", "PingFang SC", sans-serif';
                ctx.fillText(fitText(ctx, c.meta, classW - 10), x + classW / 2,
                    top + gradeRowH + 29);
            }
        });
        ctx.textAlign = 'left';
        var y = top + gradeRowH + classRowH;

        // ── 数据行：一节次一行，作息分组纵向合并 ──
        m.rows.forEach(function (row, ri) {
            var rh = rowHeight(row);
            if (row.groupStart) {
                var span = 1;
                for (var k = ri + 1; k < m.rows.length; k++) {
                    if (m.rows[k].groupStart) { break; }
                    span++;
                }
                var gh = 0;
                for (var j = ri; j < ri + span; j++) { gh += rowHeight(m.rows[j]); }
                ctx.fillStyle = GROUP_BG[row.group] || '#f1f5f9';
                ctx.fillRect(pad, y, groupW, gh);
                if (row.group) {
                    ctx.save();
                    ctx.translate(pad + groupW / 2, y + gh / 2);
                    ctx.rotate(-Math.PI / 2);
                    ctx.fillStyle = '#475569';
                    ctx.font = 'bold 12px "Microsoft YaHei", "PingFang SC", sans-serif';
                    ctx.textAlign = 'center';
                    ctx.fillText(row.group, 0, 0);
                    ctx.restore();
                    ctx.textAlign = 'left';
                }
            }
            ctx.fillStyle = row.brk ? '#f1f5f9' : '#f8fafc';
            ctx.fillRect(pad + groupW, y, perW, rh);
            ctx.fillStyle = '#334155';
            ctx.font = 'bold 12.5px "Microsoft YaHei", "PingFang SC", sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText(fitText(ctx, row.name, perW - 8), pad + groupW + perW / 2, y + 14);
            if (row.time) {
                ctx.fillStyle = '#94a3b8';
                ctx.font = '9.5px "Microsoft YaHei", "PingFang SC", sans-serif';
                ctx.fillText(fitText(ctx, row.time, perW - 8), pad + groupW + perW / 2, y + 28);
            }
            ctx.textAlign = 'left';
            row.cells.forEach(function (items, ci) {
                var x = pad + axisW + ci * classW;
                if (row.brk) {
                    ctx.fillStyle = '#f8fafc';
                    ctx.fillRect(x, y, classW, rh);
                }
                var iy = y + 5;
                items.forEach(function (it) {
                    var ih = Math.min(itemH,
                        Math.floor((rh - 10) / Math.max(items.length, 1)) - 4);
                    ctx.fillStyle = it.bg;
                    ctx.fillRect(x + 4, iy, classW - 8, ih);
                    ctx.fillStyle = it.c;
                    ctx.fillRect(x + 4, iy, 3, ih);
                    ctx.fillStyle = it.fg;
                    ctx.font = 'bold 12.5px "Microsoft YaHei", "PingFang SC", sans-serif';
                    ctx.fillText(fitText(ctx, it.subject + (it.swap ? '（调）' : ''), classW - 20),
                        x + 12, iy + 12);
                    if (it.teacher) {
                        ctx.fillStyle = '#64748b';
                        ctx.font = '10.5px "Microsoft YaHei", "PingFang SC", sans-serif';
                        ctx.fillText(fitText(ctx, it.teacher, classW - 20), x + 12, iy + 25);
                    }
                    iy += ih + 4;
                });
            });
            ctx.strokeStyle = '#eef2f7';
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(pad, y + rh + .5);
            ctx.lineTo(pad + axisW + m.columns.length * classW, y + rh + .5);
            ctx.stroke();
            y += rh;
        });

        // ── 竖线 + 外框 ──
        ctx.strokeStyle = '#e2e8f0';
        ctx.beginPath();
        ctx.moveTo(pad + groupW + .5, top);
        ctx.lineTo(pad + groupW + .5, y);
        ctx.moveTo(pad + axisW + .5, top);
        ctx.lineTo(pad + axisW + .5, y);
        m.columns.forEach(function (c, ci) {
            if (ci === m.columns.length - 1) { return; }
            var lx = pad + axisW + (ci + 1) * classW;
            ctx.moveTo(lx + .5, top);
            ctx.lineTo(lx + .5, y);
        });
        ctx.stroke();
        ctx.strokeStyle = '#cbd5e1';
        ctx.strokeRect(pad + .5, top + .5, axisW + m.columns.length * classW, y - top);

        ctx.textAlign = 'left';
        ctx.fillStyle = '#94a3b8';
        ctx.font = '11px "Microsoft YaHei", "PingFang SC", sans-serif';
        ctx.fillText('导出时间：' + new Date().toLocaleString('zh-CN', { hour12: false }),
            pad, H - pad - 6);

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
     * 节次配置：动态增删行
     * ══════════════════════════════════════════════════════════════ */
    function initPeriods() {
        if (!CFG.canEdit) { return; }
        var pt = CFG.periodTypes || {};
        /* 2026-10-10：原 `MAXP = 13` 与"加满就禁用按钮 + 一天最多 13 节"的提示已删 ——
           一天几节由学校自己定。新行取「未占用的最小可用编号」：有 N 行时 1..N+1 里
           必然有空号，所以不需要任何上限，也不会与已有行重号（重号会互相覆盖）。 */

        /* 时长口径（2026-10-10 用户给定）：一般每节课就是 40 / 45 / 50 分钟 ——
           正课 40 或 45、晚自习 50，课间 10 分钟。这里做两件事：
             ① 行内实时显示时长，偏离常规且不是「课间/午休」时标黄提醒（只提示，不拦）；
             ② 「改时长…」= 开始时间 + 所选分钟数 → 自动算出结束时间，省得自己数。 */
        var STD_MINS = [40, 45, 50];

        function toMin(v) {
            if (!v) { return null; }
            var a = String(v).split(':');
            if (a.length < 2) { return null; }
            return (parseInt(a[0], 10) || 0) * 60 + (parseInt(a[1], 10) || 0);
        }

        function fmtMin(m) {
            m = Math.min(1439, Math.max(0, m));   // 跨天没意义，夹在当天 23:59
            return (m < 600 ? '0' : '') + Math.floor(m / 60) + ':' + ((m % 60) < 10 ? '0' : '') + (m % 60);
        }

        function updateDur(row) {
            var $r = $(row);
            var a = toMin($r.find('[name=start_time]').val());
            var b = toMin($r.find('[name=end_time]').val());
            var badge = $r.find('.period-dur');
            if (!badge.length) { return; }
            if (a === null || b === null || b <= a) {
                badge.text('—')
                     .attr('class', 'badge period-dur bg-light text-dark border')
                     .attr('title', (a !== null && b !== null) ? '结束时间要晚于开始时间' : '');
                return;
            }
            var dur = b - a;
            var isBreak = $r.find('[name=period_type]').val() === 'break';
            var off = STD_MINS.indexOf(dur) < 0 && !isBreak;
            badge.text(dur + ' 分钟')
                 .attr('class', 'badge period-dur ' + (off ? 'bg-warning text-dark' : 'bg-light text-dark border'))
                 .attr('title', off ? '常规时长是 40 / 45 / 50 分钟（正课 40 或 45、晚自习 50），'
                                      + '当前 ' + dur + ' 分钟' : '');
        }

        function bindRow(row) {
            var $r = $(row);
            $r.find('[name=start_time],[name=end_time],[name=period_type]')
              .on('input change', function () { updateDur(row); });
            $r.find('.period-dur-sel').on('change', function () {
                var v = parseInt($(this).val(), 10);
                $(this).val('');
                if (!v) { return; }
                var a = toMin($r.find('[name=start_time]').val());
                if (a === null) {
                    window.alert('请先填这一节的开始时间，再选时长');
                    $r.find('[name=start_time]').focus();
                    return;
                }
                $r.find('[name=end_time]').val(fmtMin(a + v));
                updateDur(row);
            });
            updateDur(row);
        }

        function nextFreeNumber() {
            var used = {};
            $('#periodsBody tr[data-period-row]').each(function () {
                var n = parseInt($(this).find('[name=period_number]').val(), 10) || 0;
                if (n > 0) { used[n] = true; }
            });
            var i = 1;
            while (used[i]) { i++; }
            return i;
        }

        $('#periodsBody tr[data-period-row]').each(function () { bindRow(this); });

        $('#btnAddPeriodRow').on('click', function () {
            var n = nextFreeNumber();
            var opts = '';
            Object.keys(pt).forEach(function (k) { opts += '<option value="' + esc(k) + '">' + esc(pt[k]) + '</option>'; });
            var row = '<tr data-period-row>'
                + '<td><input type="number" name="period_number" class="form-control form-control-sm" min="1" value="' + n + '" required></td>'
                + '<td><input name="period_name" class="form-control form-control-sm" maxlength="20" value="第' + n + '节" required></td>'
                + '<td><input type="time" name="start_time" class="form-control form-control-sm"></td>'
                + '<td><input type="time" name="end_time" class="form-control form-control-sm"></td>'
                + '<td class="text-nowrap"><div class="d-flex align-items-center gap-1">'
                + '<span class="badge period-dur bg-light text-dark border">—</span>'
                + '<select class="form-select form-select-sm period-dur-sel" style="width:94px" title="快捷设置时长：按所选分钟数自动算出结束时间">'
                + '<option value="">改时长…</option><option value="40">40 分钟</option>'
                + '<option value="45">45 分钟</option><option value="50">50 分钟</option></select>'
                + '</div></td>'
                + '<td><select name="period_type" class="form-select form-select-sm">' + opts + '</select></td>'
                + '<td class="text-center"><button type="button" class="btn btn-sm btn-outline-danger py-0 px-1 btn-del-period" title="移除此节次（保存后生效）"><i class="bi bi-x-lg"></i></button></td>'
                + '</tr>';
            $('#periodsBody tr').not('[data-period-row]').remove();
            $('#periodsBody').append(row);
            bindRow($('#periodsBody tr[data-period-row]').last()[0]);
        });
        $('#periodsBody').on('click', '.btn-del-period', function () {
            $(this).closest('tr').remove();
        });
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
            var base = CFG.periodUrlBase;
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
     * 全校总课表：编辑模式切换（2026-10-10 新增）
     * - 默认只读：不挂任何点击/拖拽，无法修改；
     * - 点「编辑」进入可编辑态（点格加/改课程、拖动换班换节次），再点「完成编辑」退出；
     * - 状态记忆在 URL（?edit=1），保存后整页回显仍在编辑态。
     * ══════════════════════════════════════════════════════════════ */
    function initOverviewEdit() {
        var $btn = $('#btnToggleEdit');
        // 无权限 / 无课表（空态无网格）时：不提供编辑入口
        if (!$btn.length || !CFG.canEdit || !$('#ovContainer').length) { return; }
        var editOn = false;

        function setMode(on) {
            editOn = !!on;
            $('#ovContainer').toggleClass('ovv-edit-mode', editOn);
            $btn.attr('data-edit', editOn ? '1' : '0')
                .toggleClass('btn-primary', editOn)
                .toggleClass('btn-outline-primary', !editOn)
                .html(editOn
                    ? '<i class="bi bi-check2-circle"></i> 完成编辑'
                    : '<i class="bi bi-pencil-square"></i> 编辑');
            // 只有编辑态才允许拖拽（只读态不可拖 → 无法修改）
            $('#ovContainer .ovv-cell .ovw-item').attr('draggable', editOn ? 'true' : null);
            try {   // 记住编辑态到 URL，保存后整页刷新仍停留编辑模式
                var u = new URL(window.location.href);
                if (editOn) { u.searchParams.set('edit', '1'); }
                else { u.searchParams.delete('edit'); }
                window.history.replaceState({}, '', u.toString());
            } catch (e) { /* 老浏览器忽略 */ }
        }

        // 初始：URL 带 edit=1 且当前是只读页（?readonly）时仍进入编辑态
        setMode(new URL(window.location.href).searchParams.get('edit') === '1');
        $btn.on('click', function () { setMode(!editOn); });

        // 点击格子：点到课程 → 编辑该课程；点空白 → 在该格新增课程
        $('#ovContainer').on('click', 'td.ovv-cell.ovv-editable', function (ev) {
            if (!editOn) { return; }
            var $cell = $(this);
            var $item = $(ev.target).closest('.ovw-item[data-entry-id]');
            if ($item.length) { openEdit($item.data('entry-id')); return; }
            openAdd(String($cell.data('grade') || ''), String($cell.data('class') || ''),
                    CFG.day, Number($cell.data('period')));
        });

        // 拖拽换格：横向换班、纵向换节次（移动 = 改该条目的年级/班级/节次）
        var dragged = null;
        $('#ovContainer').on('dragstart', '.ovw-item[draggable="true"]', function (ev) {
            if (!editOn) { ev.preventDefault(); return; }
            dragged = $(this);
            $(this).addClass('ovv-dragging');
            var dt = ev.originalEvent && ev.originalEvent.dataTransfer;
            if (dt) { dt.effectAllowed = 'move'; dt.setData('text/plain', String($(this).data('entry-id'))); }
        });
        $('#ovContainer').on('dragend', '.ovw-item', function () {
            $(this).removeClass('ovv-dragging');
            $('#ovContainer td.ovv-cell').removeClass('ovv-drop-hover');
            dragged = null;
        });
        $('#ovContainer').on('dragover', 'td.ovv-cell.ovv-editable', function (ev) {
            if (!editOn || !dragged) { return; }
            ev.preventDefault();
            $(this).addClass('ovv-drop-hover');
            var dt = ev.originalEvent && ev.originalEvent.dataTransfer;
            if (dt) { dt.dropEffect = 'move'; }
        });
        $('#ovContainer').on('dragleave', 'td.ovv-cell', function () {
            $(this).removeClass('ovv-drop-hover');
        });
        $('#ovContainer').on('drop', 'td.ovv-cell.ovv-editable', function (ev) {
            ev.preventDefault();
            $(this).removeClass('ovv-drop-hover');
            if (!editOn || !dragged) { return; }
            var $cell = $(this);
            var grade = String($cell.data('grade') || '');
            var cn = String($cell.data('class') || '');
            var pn = Number($cell.data('period'));
            var $src = dragged.closest('td.ovv-cell');
            // 拖回原格（同班同节次）不处理
            if ($src.length && String($src.data('class')) === cn && Number($src.data('period')) === pn) { return; }
            moveEntryTo(dragged.data('entry-id'), grade, cn, CFG.day, pn);
        });
    }

    /* 把某条目整体移动到「年级+班级+星期+节次」（编辑接口一次写全，含换班） */
    function moveEntryTo(eid, grade, className, weekday, period) {
        var detailUrl = (CFG.urls.entryDetail || '').replace(/\/entry\/\d+(?=\/|$)/, '/entry/' + eid);
        if (!detailUrl) { toast('当前页面不支持拖拽换课', 'danger'); return; }
        $.getJSON(detailUrl).done(function (res) {
            var e = res && res.success && res.data && res.data.entry;
            if (!e) { toast((res && res.message) || '读取条目失败', 'danger'); return; }
            var payload = {
                grade: grade, class_name: className,
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
                    if (r && r.success) { toast('已移动课程', 'success'); refreshGrid(); }
                    else { toast((r && r.message) || '移动失败（目标时段可能冲突）', 'danger'); }
                })
                .fail(function (xhr) {
                    var m = '移动失败';
                    try { m = (xhr.responseJSON && xhr.responseJSON.message) || m; } catch (err) { }
                    toast(m, 'danger');
                });
        }).fail(function () { toast('读取条目失败', 'danger'); });
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
            /* 2026-10-10：'master'（大课表）分支已随该页下线删除 */
            /* 2026-10-10：全校总课表可开启编辑模式（点格加/改课程、拖拽换格） */
            case 'overview': initEntryModal(); initOverviewEdit(); break;
            case 'grade': initEntryModal(); initCharts(); initDragMove(); break;
            case 'class': initEntryModal(); initCharts(); initDragMove(); break;
            case 'room': initEntryModal(); initDragMove(); initRoomSwitcher(); break;
            case 'teacher': initTeacher(); initCharts(); break;
            case 'periods': initPeriods(); break;
            case 'versions': initVersions(); break;
            case 'import': initImport(); break;
            case 'inspection': initInspection(); break;
            default: break;
        }
    });

})();
