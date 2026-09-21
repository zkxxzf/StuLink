/* StuLink v1.16.0 学期课表前端
 * 统一入口：各课表页底部注入 <script id="scheduleData" type="application/json"> 配置块。
 * 依赖：jQuery 3.7 + Bootstrap 5 bundle（base.html 已全局加载）；图表页额外加载 echarts.min.js。
 * CSRF：base.html 已 $.ajaxSetup 注入 X-CSRFToken，本文件的 $.ajax/$.getJSON 自动携带。
 * 设计约定见 app/templates/academic/_schedule_grid.html 与 _schedule_parts.html。
 */
(function () {
    'use strict';

    var CFG = {};                 // scheduleData 配置
    var STATE = {                 // 运行期状态
        grade: '', className: '',
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
    function entryHtml(e) {
        return '<div class="sch-item' + (e.entry_type === 'swap' ? ' sch-item-swap' : '')
            + '" data-entry-id="' + e.id + '">'
            + '<div class="sch-subject">' + esc(e.subject)
            + (e.entry_type === 'swap' ? '<span class="sch-badge-swap">调</span>' : '')
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
            items.forEach(function (e) { inner += entryHtml(e); });
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
            week_range: $.trim($('#entryWeekRange').val()) || '1-18',
            note: $.trim($('#entryNote').val())
        };
    }

    function saveEntry() {
        var p = collectPayload();
        if (!p.grade || !p.class_name || !p.subject) { showInlineError('年级、班级、学科为必填项'); return; }
        if (!p.period_number) { showInlineError('请选择节次'); return; }
        var url = STATE.editingId
            ? CFG.urls.entryEditTpl.replace(/\/entry\/\d+\//, '/entry/' + STATE.editingId + '/')
            : CFG.urls.entryAdd;
        $('#entrySaveBtn').prop('disabled', true);
        $.ajax({ url: url, type: 'POST', contentType: 'application/json', dataType: 'json', data: JSON.stringify(p) })
            .done(function (res) {
                if (res && res.success) { modalInstance().hide(); window.location.reload(); }
                else { showInlineError((res && res.message) || '保存失败'); $('#entrySaveBtn').prop('disabled', false); }
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
                if (res && res.success) { modalInstance().hide(); window.location.reload(); }
                else { showInlineError((res && res.message) || '删除失败'); $('#entryDeleteBtn').prop('disabled', false); }
            })
            .fail(function (xhr) {
                var m = '删除失败';
                try { m = (xhr.responseJSON && xhr.responseJSON.message) || m; } catch (e) { }
                showInlineError(m); $('#entryDeleteBtn').prop('disabled', false);
            });
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
        STATE.grade = CFG.grade || CFG.initGrade || '';
        STATE.className = CFG.className || '';
        if (CFG.periods) { STATE.periods = CFG.periods; }
        switch (CFG.pageType) {
            case 'master': initMaster(); initEntryModal(); break;
            case 'grade': initEntryModal(); initCharts(); break;
            case 'class': initEntryModal(); initCharts(); break;
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
