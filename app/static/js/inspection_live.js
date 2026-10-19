/* StuLink v1.18.8.0 · 查课核对页（2026-10-09 改版）
 * 页面版式：双视图 ——「全天矩阵」（行＝班级、列＝节次，一天所有节次一次铺开）
 *           与「单节课表」（只看一节，按年级铺成班级卡片墙）。
 * 交互：单击=正常 / 双击=迟到 / 右键=缺勤·调课·考试等（2026-10-13 起「缺课」改叫「缺勤」，
 *       并新增「考试」：该格是考试而非正常上课，不计课时工作量、也不算违纪/迟到）→
 *       POST /inspection/mark 写回 inspection_records，就地更新徽标与"覆盖率"。
 *       2026-10-14：**两个视图共用这套标记交互**（单节卡片同样可单击/双击/右键）；
 *       单节卡片上的「换人」按钮才是发起代课 / 调课申请（原为点卡片打开，已让位给标记）。
 *       支持"未标记的全部正常"一键巡课；筛选（日期/年级）走服务端渲染整页刷新。
 */
(function () {
    'use strict';
    var cfg = {};
    try { cfg = JSON.parse(document.getElementById('liveData').textContent || '{}'); } catch (e) { cfg = {}; }
    var $ = window.jQuery;
    if (!$) { return; }

    // 2026-10-13：'缺课'统一改为'缺勤'；新增 'exam'（考试：该格是考试而非正常上课，
    // 不计课时工作量、也不算违纪/迟到 —— 考试期间有正常考务工作量）
    var RESULT_TEXT = { normal: '正常', late: '迟到', absent: '缺勤', swap: '调课',
                        exam: '考试', other: '其他' };

    /* 2026-10-16：改用全局 stuToast（base.html 已全局引 grades.css）——
       原来那次判断的是不存在的 window.toast，实际每次都走自拼的「写死白底」toast，
       深色主题下很突兀；这里做类型映射（本页 success/danger/warning/secondary
       → stuToast 的 ok/err/warn），无 stuToast 时兜底仍用 --sl-* 变量配色。 */
    function notify(msg, type) {
        var t = (type === 'danger') ? 'err'
            : (type === 'warning' || type === 'secondary') ? 'warn' : 'ok';
        if (typeof window.stuToast === 'function') { window.stuToast(msg, t); return; }
        var color = (t === 'err') ? 'var(--sl-soft-danger-fg)'
            : (t === 'warn') ? 'var(--sl-soft-amber-fg)' : 'var(--sl-soft-success-fg)';
        var $t = $('<div></div>').text(msg).css({
            position: 'fixed', top: '72px', left: '50%', transform: 'translateX(-50%)',
            zIndex: 3000, background: 'var(--sl-surface)', color: color,
            border: '1px solid ' + color, borderLeft: '4px solid ' + color,
            borderRadius: '8px', padding: '8px 14px', boxShadow: 'var(--sl-shadow)',
            fontSize: '13px', maxWidth: '80vw'
        });
        $('body').append($t);
        setTimeout(function () { $t.fadeOut(220, function () { $(this).remove(); }); }, 1600);
    }

    /* ── 时钟 ── */
    function pad(n) { return (n < 10 ? '0' : '') + n; }
    function tickClock() {
        var el = document.getElementById('clockHM');
        if (el) {
            var d = new Date();
            el.textContent = pad(d.getHours()) + ':' + pad(d.getMinutes());
        }
    }
    tickClock();
    window.setInterval(tickClock, 30000);

    /* ── 筛选：日期 / 年级（服务端渲染，页面本身只有几条 SQL）── */
    function nav(params) {
        var q = [];
        Object.keys(params).forEach(function (k) {
            if (params[k] !== '' && params[k] !== null && params[k] !== undefined) {
                q.push(encodeURIComponent(k) + '=' + encodeURIComponent(params[k]));
            }
        });
        window.location.href = cfg.pageUrl + (q.length ? '?' + q.join('&') : '');
    }
    function navNow(extra) {
        var p = { date: $('#datePick').val() || cfg.today, grade: $('#gradePick').val() || '' };
        // 2026-10-16 修复：原先只带 {date, grade} → 在单节视图里改日期 / 点「今天」会静默
        // 跳回「全天矩阵」并丢掉当前节次（巡课最高频的两个操作，用户反复迷路）。
        if (cfg.view === 'period') {
            p.view = 'period';
            var pn = $('#povPeriodPick').val();
            if (pn) { p.period = pn; }
        }
        if (extra) { Object.keys(extra).forEach(function (k) { p[k] = extra[k]; }); }
        nav(p);
    }
    $('#datePick').on('change', function () { navNow(); });
    $('#gradePick').on('change', function () { navNow(); });
    $('#btnToday').on('click', function () { navNow({ date: cfg.today }); });
    $('#btnPrint').on('click', function () { window.print(); });

    /* ── 覆盖率计数 ──
       2026-10-14：矩阵格子与单节卡片共用同一套计数（两个视图不会同时出现，
       单节视图下也能就地刷新"已标记 / 应查 / 覆盖率"）。 */
    function recount() {
        // 2026-10-16 修复：只读账号（无 academic.edit）的卡片不带 data-check，
        // 跑一遍会把服务端渲染的真实「已标记/应查/覆盖率」覆写成 0 / 0 / 0%
        // （给领导看的完成度仪表盘直接归零）。无标记权限就保持服务端值。
        if (!cfg.canMark) { return; }
        var $cells = $('.ovw-item[data-check], .pov-card[data-check]');
        var total = $cells.length;
        var done = $cells.filter('[data-check!=""]').length;
        $('#statChecked').text(done);
        $('#statExpected').text(total);
        $('#statRate').text(total ? Math.floor(done * 100 / total) + '%' : '0%');
    }

    /* ── 就地更新一格（矩阵格子 / 单节卡片同款 data 属性）── */
    function cellSelector(c) {
        var key = '[data-grade="' + c.grade + '"]'
            + '[data-class="' + c.class_name + '"]'
            + '[data-period="' + c.period_number + '"]';
        return '#liveContainer .ovw-item' + key + ', #povContainer .pov-card' + key;
    }
    function applyResult($el, result, note) {
        var label = RESULT_TEXT[result] || '';
        $el.attr('data-check', result || '');
        // 2026-10-16：单节卡片的**状态色**（底色 + 左侧色条）要跟着标记结果走 ——
        // 原先只换徽标文字，卡片外观停在"未查"，扫一眼还以为是没查的班。
        if ($el.hasClass('pov-card')) {
            $el.removeClass('chk-none chk-normal chk-late chk-absent chk-swap chk-exam chk-other')
                .addClass('chk-' + (result || 'none'));
        }
        $el.find('.ovw-chk').attr('class', 'ovw-chk ovw-chk-' + (result || 'none'))
            .text(label || '未查');
        var t = ($el.data('subject') || '') + ' ' + ($el.data('teacher') || '');
        t += result ? (' · 已标记' + label + (note ? '（' + note + '）' : ''))
            : ' · 点此标记查课结果';
        $el.attr('title', t);
    }

    function post(cells, ok, always) {
        if (!cells || !cells.length) { return; }
        $.ajax({
            url: cfg.markUrl,
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ inspect_date: cfg.date, cells: cells })
        }).done(function (res) {
            if (res && res.success) {
                (res.cells || []).forEach(function (c) {
                    var $el = $(cellSelector(c));
                    if ($el.length) { applyResult($el, c.result, c.note); }
                });
                recount();
                if (ok) { ok(res); }
            } else {
                notify((res && res.message) || '标记失败', 'danger');
            }
        }).fail(function (xhr) {
            var msg = '标记失败';
            try { msg = JSON.parse(xhr.responseText).message || msg; } catch (e) { /* 忽略解析失败 */ }
            notify(msg, 'danger');
        }).always(function () {
            if (always) { always(); }
        });
    }

    /* ── 快速标记（2026-10-10 改版）──────────────────────────────────
       单击 = 正常；双击 = 迟到；右键 = 更多（缺勤 / 调课 / 考试 / 迟到说明 / 其他 / 撤销）。
       原先每次都要"点格子 → 弹面板 → 再点按钮"，二次操作太慢；现在单/双击直达，
       需要写原因的（如迟到几分钟）走右键。 */
    function cellPayload($el, result, note) {
        return {
            grade: $el.data('grade'), class_name: $el.data('class'),
            period_number: $el.data('period'), entry_id: $el.data('entry'),
            subject: $el.data('subject'), teacher_uid: $el.data('uid'),
            teacher_name: $el.data('teacher'), result: result, note: note || ''
        };
    }

    function markCell($el, result, note) {
        if (!$el || !$el.length) { return; }
        // 2026-10-16：点击立即给回执（单击要等 260ms 双击判定 + 一次 RTT 才有反馈，
        // 巡课时用户会以为没点上而重复点 → 又踩「连点误标」）
        $el.addClass('is-pending');
        post([cellPayload($el, result, note)], function () {
            $el.data('note', note || '');
            // post() 的 done 里已按 res.cells 调过 applyResult → 这里不再重复渲染一遍。
            // 且高频巡课（一圈 30+ 下）不再每次弹 toast，只在「撤销 / 例外状态」时提示。
            if (!result) { notify('已撤销该格标记', 'warning'); }
            else if (result !== 'normal') { notify('已标记：' + (RESULT_TEXT[result] || result)); }
        }, function () { $el.removeClass('is-pending'); });
    }

    var suppressClick = false;   // 触屏长按已弹菜单 → 吞掉紧随其后的那次 click
    // 单击 vs 双击：单击延迟 260ms 执行，期间若**同一张卡/格**再来一击则判为双击（迟到）
    // 2026-10-14：矩阵格子（.ovw-item）与单节卡片（.pov-card）共用这套交互
    // 2026-10-16 修复：timer 原先是**一个全局变量** → 260ms 内先点 A 卡再点 B 卡时，
    //   A 的「正常」被 clearTimeout 静默丢弃（A 永远没标记），B 被误标成「迟到」；
    //   巡课连续快速点多个班恰恰是常态，同时造成"漏查"和"误标"两种最不能错的错。
    //   现在 timer 挂在元素上（只有同一元素上的第二击才算双击）。
    $(document).on('click', '.ovw-item[data-check], .pov-card[data-check]', function () {
        if (suppressClick) { suppressClick = false; return; }   // 长按已开菜单，吞掉这一击
        var $el = $(this);
        var t = $el.data('clickTimer');
        if (t) {
            clearTimeout(t); $el.removeData('clickTimer');
            markCell($el, 'late', $el.data('note') || '');
            return;
        }
        // 单击=正常：不带旧备注（原来把「迟到 5 分钟」这类说明一并继承到"正常"上）
        $el.data('clickTimer', setTimeout(function () {
            $el.removeData('clickTimer');
            markCell($el, 'normal', '');
        }, 260));
    });

    /* 2026-10-16：键盘可达（原来卡片只能鼠标点；矩阵格子虽有 tabindex 也没有处理器）
       Enter / 空格＝正常，与单击同义；右键菜单里的其余状态仍走菜单。 */
    $(document).on('keydown', '.pov-card[data-check]', function (ev) {
        if (ev.key !== 'Enter' && ev.key !== ' ' && ev.key !== 'Spacebar') { return; }
        ev.preventDefault();
        markCell($(this), 'normal', '');
    });

    /* ── 右键菜单：正常 / 缺勤 / 迟到（含说明）/ 调课 / 考试 / 其他 / 撤销 ── */
    var $menu = null;
    function closeMenu() { if ($menu) { $menu.remove(); $menu = null; } }
    function openMenu($el, x, y) {
        closeMenu();
        $menu = $('<div class="chk-menu"></div>').css({ left: x + 'px', top: y + 'px' });
        var items = [
            ['normal', '正常', 'success'],
            ['absent', '缺勤', 'danger'],
            ['late', '迟到', 'warning'],
            ['late-note', '迟到说明…（如迟到 5 分钟）', 'warning'],
            ['swap', '调课', 'primary'],
            ['exam', '考试', 'info'],
            ['other', '其他', 'secondary'],
            ['clear', '撤销标记', 'secondary']
        ];
        items.forEach(function (it) {
            $('<button type="button" class="chk-menu-item"></button>')
                .addClass('text-' + it[2])
                .text(it[1])
                .on('click', function () {
                    var key = it[0];
                    closeMenu();
                    if (key === 'clear') { markCell($el, '', ''); return; }
                    if (key === 'late-note') {
                        var n = window.prompt('迟到说明（如：迟到 5 分钟）', $el.data('note') || '');
                        if (n === null) { return; }
                        markCell($el, 'late', n);
                        return;
                    }
                    markCell($el, key, $el.data('note') || '');
                })
                .appendTo($menu);
        });
        $('body').append($menu);
        // 2026-10-16：靠右 / 靠下的卡片上右键，200px 宽的菜单会溢出视口（出横向滚动条或被裁）
        var w = $menu.outerWidth(), h = $menu.outerHeight();
        var sx = window.pageXOffset || 0, sy = window.pageYOffset || 0;
        if (x + w > sx + window.innerWidth - 4) {
            $menu.css('left', Math.max(sx + 4, sx + window.innerWidth - w - 4) + 'px');
        }
        if (y + h > sy + window.innerHeight - 4) {
            $menu.css('top', Math.max(sy + 4, sy + window.innerHeight - h - 4) + 'px');
        }
    }
    $(document).on('contextmenu', '.ovw-item[data-check], .pov-card[data-check]', function (ev) {
        // 「换人」按钮上右键不弹查课菜单（左键已 stopPropagation，右键此前漏了）
        if ($(ev.target).closest('.pov-act').length) { return; }
        ev.preventDefault();
        openMenu($(this), ev.pageX, ev.pageY);
    });
    /* 2026-10-16：触屏（iPad / 手机巡课）没有右键 → 长按 500ms 打开同一菜单，
       否则「缺勤 / 调课 / 考试 / 撤销」在触屏上完全不可达。 */
    var pressTimer = null, pressMoved = false;
    $(document).on('touchstart', '.ovw-item[data-check], .pov-card[data-check]', function (ev) {
        if ($(ev.target).closest('.pov-act').length) { return; }
        var $el = $(this);
        var tt = ev.originalEvent && ev.originalEvent.touches && ev.originalEvent.touches[0];
        var x = tt ? tt.pageX : 0, y = tt ? tt.pageY : 0;
        pressMoved = false;
        pressTimer = setTimeout(function () {
            pressTimer = null;
            if (pressMoved) { return; }
            suppressClick = true;
            openMenu($el, x, y);
        }, 500);
    });
    $(document).on('touchmove', function () { pressMoved = true; });
    $(document).on('touchend touchcancel', function () {
        if (pressTimer) { clearTimeout(pressTimer); pressTimer = null; }
    });
    $(document).on('click', function () { closeMenu(); });
    $(window).on('scroll', closeMenu);
    $(document).on('keydown', function (e) { if (e.keyCode === 27) { closeMenu(); } });

    /* ── 一键：当前页面未标记的全部正常（巡课一圈点一下）──
       2026-10-14：单节视图同样可用 ——「当前页面」就是这一节，一次把未查的班全标正常。 */
    $('#btnMarkAllNormal').on('click', function () {
        var cells = [];
        $('.ovw-item[data-check=""], .pov-card[data-check=""]').each(function () {
            var $el = $(this);
            cells.push({
                grade: $el.data('grade'), class_name: $el.data('class'),
                period_number: $el.data('period'), entry_id: $el.data('entry'),
                subject: $el.data('subject'), teacher_uid: $el.data('uid'),
                teacher_name: $el.data('teacher'), result: 'normal'
            });
        });
        if (!cells.length) { notify('当前页面上没有未标记的格子', 'secondary'); return; }
        if (!window.confirm('把当前页面上 ' + cells.length + ' 个未标记格子全部标为「正常」？')) { return; }
        post(cells, function (res) {
            notify('已标记 ' + (res.updated || 0) + ' 格为正常');
            // 2026-10-16：留一条退路 —— 一键之后不必逐张右键撤销（误触成本从
            // "N 次右键"降到"点一下"）。按钮平时 d-none，批量之后才出现。
            lastBatch = cells;
            showUndo(res.updated || cells.length);
        });
    });

    /* ── 撤销上一次批量（2026-10-16）── */
    var lastBatch = null;
    function showUndo(n) {
        var $b = $('#btnUndoBatch');
        if (!$b.length || !n) { return; }
        $('#undoBatchN').text(n);
        $b.removeClass('d-none');
    }
    function hideUndo() {
        $('#btnUndoBatch').addClass('d-none');
        lastBatch = null;
    }
    $('#btnUndoBatch').on('click', function () {
        if (!lastBatch || !lastBatch.length) { hideUndo(); return; }
        if (!window.confirm('撤销上一次批量：把这 ' + lastBatch.length + ' 个格子改回「未查」？')) {
            return;
        }
        var cells = lastBatch.map(function (c) { return $.extend({}, c, { result: '' }); });
        post(cells, function (res) {
            notify('已撤销 ' + (res.updated || 0) + ' 格', 'warning');
            hideUndo();
        });
    });

    /* ── 本节保存 + 通知领导（2026-10-10）──
       选中某一节次：本节所有班都查完 → 直接保存并提示；
       还有未查的 → 弹窗让用户决定是否把情况站内通知课表管理员（领导）。 */
    /* 2026-10-16：单节视图同样支持「保存本节 / 通知领导」——
       卡片与矩阵格子共用同一套 data 属性，选择器扩到 #povContainer .pov-card 即可；
       节次来源：单节视图取当前节次（下拉），矩阵视图取「本节」下拉。 */
    function currentSavePeriod() {
        if (cfg.view === 'period') {
            return $('#povPeriodPick').val() || cfg.povPeriod;
        }
        return $('#periodPick').val();
    }
    function currentSaveLabel(pn) {
        var sel = (cfg.view === 'period') ? '#povPeriodPick option:selected'
                                          : '#periodPick option:selected';
        var t = $(sel).text();
        return t || ('第' + pn + '节');
    }
    function periodCells(pn) {
        return $('#liveContainer .ovw-item[data-check][data-period="' + pn + '"], '
            + '#povContainer .pov-card[data-check][data-period="' + pn + '"]');
    }
    function periodStat(pn) {
        var $cells = periodCells(pn);
        var unchecked = [];
        $cells.each(function () {
            var $el = $(this);
            if (!($el.attr('data-check') || '')) {
                unchecked.push({ grade: $el.data('grade'), class_name: $el.data('class') });
            }
        });
        return { expected: $cells.length, unchecked: unchecked,
                 checked: $cells.length - unchecked.length };
    }

    var saveTarget = null;
    function doNotify(stat) {
        $.ajax({ url: cfg.notifyUrl, type: 'POST', contentType: 'application/json',
                 data: JSON.stringify({ inspect_date: cfg.date, period: saveTarget.pn,
                                        grade: $('#gradePick').val() || '',
                                        expected: stat.expected, checked: stat.checked,
                                        unchecked: stat.unchecked }) })
            .done(function (res) {
                if (res && res.success) { notify(res.message || '已通知领导', 'warning'); }
                else { notify((res && res.message) || '通知失败', 'danger'); }
            })
            .fail(function (xhr) {
                var m = '通知失败';
                try { m = (xhr.responseJSON && xhr.responseJSON.message) || m; } catch (e) { /* 忽略 */ }
                notify(m, 'danger');
            });
    }

    $('#btnSavePeriod').on('click', function () {
        var pn = currentSavePeriod();
        if (!pn) { notify('请先选择节次', 'secondary'); return; }
        var label = currentSaveLabel(pn);
        var stat = periodStat(pn);
        if (!stat.expected) { notify('本节没有排课，无需保存', 'secondary'); return; }
        if (!stat.unchecked.length) {
            notify('本节「' + label + '」' + stat.expected + ' 个班已全部查完，已保存', 'success');
            return;
        }
        saveTarget = { pn: pn, label: label };
        $('#savePeriodText').html('本节「' + label + '」共 <b>' + stat.expected + '</b> 个班，'
            + '已查 <b>' + stat.checked + '</b> 个，还有 <b>' + stat.unchecked.length + '</b> 个未查。'
            + '<div class="text-muted mt-1">是否把未查情况通知领导（有课表管理权限的账号）？</div>');
        if (window.bootstrap && window.bootstrap.Modal) {
            window.bootstrap.Modal.getOrCreateInstance(document.getElementById('savePeriodModal')).show();
        } else { doNotify(stat); }
    });
    $('#btnSaveNoNotify').on('click', function () {
        if (window.bootstrap && window.bootstrap.Modal) {
            window.bootstrap.Modal.getOrCreateInstance(document.getElementById('savePeriodModal')).hide();
        }
        notify('已暂存（未通知领导）', 'secondary');
    });
    $('#btnSaveNotify').on('click', function () {
        if (!saveTarget) { return; }
        var stat = periodStat(saveTarget.pn);
        if (window.bootstrap && window.bootstrap.Modal) {
            window.bootstrap.Modal.getOrCreateInstance(document.getElementById('savePeriodModal')).hide();
        }
        doNotify(stat);
    });

    recount();

    /* ═══════════════════════════════════════════════════════════════════
       单节课表视图（2026-10-13）：点卡片上的「换人」按钮 → 就地发起「代课 / 调课」
         - 只选教师                = 代课（时间班级都不动，exchange_kind='cover'）
         - 再选这位教师的某一节课  = 调课（两节课**换上课时间**，
                                    2026-10-14 起不再换班级 / 教室 / 学生）
       提交走 POST /academic/inspection/quick-swap：服务端以**原任课教师**为申请人落库，
       与「请人代课 / 调课申请」页同一条「对方确认 → 教务审批 → 执行生效」链路。
       独立 IIFE + 原生 fetch / DOM，不依赖 jQuery；按钮点击 stopPropagation，
       与上面的"单击卡片=标记查课"互不干扰（2026-10-14）。
       ═══════════════════════════════════════════════════════════════════ */
    (function () {
        'use strict';
        var pov = {};
        try { pov = JSON.parse(document.getElementById('liveData').textContent || '{}'); } catch (e) { pov = {}; }
        if (!pov.canSchedule) { return; }               // 无权限：纯展示
        var box = document.getElementById('povContainer');
        if (!box) { return; }                           // 当前不在单节课表视图

        var WEEK = { 1: '周一', 2: '周二', 3: '周三', 4: '周四', 5: '周五', 6: '周六', 7: '周日' };
        var cur = null;              // 当前操作的班级行
        var teachers = null;         // 教师清单缓存
        var modal = null;

        function el(id) { return document.getElementById(id); }
        function escHtml(s) {
            return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
                return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
            });
        }

        /* ── 节次切换：与日期 / 年级一致，走整页刷新（服务端渲染）── */
        function gotoPeriod(step) {
            var sel = el('povPeriodPick');
            if (!sel) { return; }
            if (step) {
                var i = sel.selectedIndex + step;
                if (i < 0 || i >= sel.options.length) { return; }
                sel.selectedIndex = i;
            }
            window.location.href = pov.pageUrl + '?view=period&date=' + encodeURIComponent(pov.date)
                + '&period=' + encodeURIComponent(sel.value)
                + (pov.grade ? '&grade=' + encodeURIComponent(pov.grade) : '');
        }
        el('povPeriodPick').addEventListener('change', function () { gotoPeriod(0); });
        el('povPrevPeriod').addEventListener('click', function () { gotoPeriod(-1); });
        el('povNextPeriod').addEventListener('click', function () { gotoPeriod(1); });

        /* ── 教师清单 / 某位教师的课（复用调课模块既有接口）── */
        function loadTeachers() {
            if (teachers) { return Promise.resolve(teachers); }
            return fetch(pov.teachersUrl, { headers: { 'Accept': 'application/json' } })
                .then(function (r) { return r.json(); })
                .then(function (res) { teachers = (res && res.data && res.data.teachers) || []; return teachers; })
                .catch(function () { return (teachers = []); });
        }

        function loadPartnerEntries(uid) {
            var sel = el('swPartnerEntry');
            sel.innerHTML = '<option value="">加载中…</option>';
            fetch(pov.entriesUrl + '?uid=' + encodeURIComponent(uid),
                  { headers: { 'Accept': 'application/json' } })
                .then(function (r) { return r.json(); })
                .then(function (res) {
                    var list = (res && res.data && res.data.entries) || [];
                    if (!list.length) { sel.innerHTML = '<option value="">该教师本学期暂无其它课</option>'; return; }
                    var opts = ['<option value="">请选择他/她的某一节课…</option>'];
                    list.forEach(function (e) {
                        opts.push('<option value="' + escHtml(e.id) + '">' + escHtml(
                            (e.weekday_text || WEEK[e.weekday] || '') + ' 第' + e.period_number + '节'
                            + ' · ' + (e.grade || '') + (e.class_name || '') + ' · ' + (e.subject || ''))
                            + '</option>');
                    });
                    sel.innerHTML = opts.join('');
                })
                .catch(function () { sel.innerHTML = '<option value="">加载失败</option>'; });
        }

        /* ── 打开弹窗 ── */
        function openModal(card) {
            // 2026-10-14：卡片 data 属性与矩阵格子对齐（data-cls/subj/tch 等旧名已撤）
            cur = {
                entry: card.getAttribute('data-entry'),
                cls: (card.getAttribute('data-grade') || '') + (card.getAttribute('data-class') || ''),
                subj: card.getAttribute('data-subject') || '',
                tch: card.getAttribute('data-teacher') || '',
                tuid: card.getAttribute('data-uid') || '',
                room: card.getAttribute('data-room') || ''
            };
            el('swTargetClass').textContent = cur.cls + ' · 第 ' + (pov.povPeriod || '') + ' 节';
            el('swTargetInfo').textContent = cur.subj + ' · 当前任课：' + (cur.tch || '未指定')
                + (cur.room ? ' · 教室 ' + cur.room : '');
            el('swReason').value = '';
            el('swDate').value = pov.date || '';
            el('swPermanent').checked = false;
            el('swEntryWrap').classList.add('d-none');
            el('swPartnerEntry').innerHTML = '<option value="">请先选择教师</option>';
            el('swKindCover').checked = true;
            el('swTeacherLabel').textContent = '代课教师';
            el('swHint').textContent = '';
            loadTeachers().then(function (list) {
                var opts = ['<option value="">请选择教师…</option>'];
                list.forEach(function (t) {
                    if (t.uid === cur.tuid) { return; }        // 不能选原任课教师本人
                    opts.push('<option value="' + escHtml(t.uid) + '">' + escHtml(t.name || t.uid)
                        + '（' + t.count + ' 节）</option>');
                });
                el('swTeacher').innerHTML = opts.join('');
            });
            if (!modal) { modal = new bootstrap.Modal(el('swapModal')); }
            modal.show();
        }

        function onKindChange() {
            var ex = el('swKindExchange').checked;
            el('swEntryWrap').classList.toggle('d-none', !ex);
            el('swTeacherLabel').textContent = ex ? '对调教师' : '代课教师';
            if (ex && el('swTeacher').value) { loadPartnerEntries(el('swTeacher').value); }
        }

        function submit() {
            if (!cur) { return; }
            var kind = el('swKindExchange').checked ? 'exchange' : 'cover';
            var uid = el('swTeacher').value;
            if (!uid) {
                el('swHint').textContent = '请先选择' + (kind === 'exchange' ? '对调' : '代课') + '教师';
                return;
            }
            var reason = (el('swReason').value || '').trim();
            if (!reason) { el('swHint').textContent = '请填写事由'; return; }
            var payload = {
                entry_id: cur.entry, kind: kind, partner_uid: uid, reason: reason,
                is_permanent: el('swPermanent').checked, swap_date: el('swDate').value
            };
            if (kind === 'exchange') {
                if (!el('swPartnerEntry').value) {
                    el('swHint').textContent = '请再选中对方的具体一节课';
                    return;
                }
                // 2026-10-14：只换上课时间（不换班级 / 教室 / 学生）
                payload.partner_entry_id = el('swPartnerEntry').value;
                payload.swap_time = true;
                payload.swap_class = false;
            }
            var btn = el('swSubmit');
            btn.disabled = true;
            el('swHint').textContent = '提交中…';
            fetch(pov.submitUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json', 'Accept': 'application/json',
                    'X-CSRFToken': window.csrfToken || ''   // jQuery 有 ajaxSetup 钩子，fetch 必须手动带
                },
                body: JSON.stringify(payload)
            }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j || {} }; }); })
              .then(function (res) {
                  btn.disabled = false;
                  if (!res.ok || !res.j.success) {
                      el('swHint').textContent = res.j.message || '提交失败';
                      return;
                  }
                  modal.hide();
                  notify(res.j.message || '已提交申请', 'success');
              })
              .catch(function () {
                  btn.disabled = false;
                  el('swHint').textContent = '网络错误，请重试';
              });
        }

        /* 2026-10-14：卡片本身交给上面的矩阵交互做查课标记（单击=正常…），
           代课 / 调课改由卡片上的「换人」按钮打开弹窗；按钮点击要拦住冒泡，
           否则会被 document 上的标记委托当成"单击=正常"。 */
        box.addEventListener('click', function (ev) {
            var btn = ev.target.closest ? ev.target.closest('.pov-act') : null;
            if (!btn) { return; }
            ev.stopPropagation();
            var card = btn.closest('.pov-card');
            if (card && card.getAttribute('data-entry')) { openModal(card); }
        });
        Array.prototype.forEach.call(document.querySelectorAll('input[name="swKind"]'), function (r) {
            r.addEventListener('change', onKindChange);
        });
        el('swTeacher').addEventListener('change', function () {
            if (el('swKindExchange').checked && el('swTeacher').value) {
                loadPartnerEntries(el('swTeacher').value);
            }
        });
        el('swSubmit').addEventListener('click', submit);
    })();
})();
