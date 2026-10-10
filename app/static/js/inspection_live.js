/* StuLink v1.18.8.0 · 查课核对页（2026-10-09 改版）
 * 页面版式：与全校总课表同款矩阵 —— 行＝班级、列＝节次，一天所有节次一次铺开。
 * 交互：点格子弹出标记面板（正常/迟到/缺课/调课/其他 + 备注）→
 *       POST /inspection/mark 写回 inspection_records，就地更新徽标与"覆盖率"。
 *       支持"未标记的全部正常"一键巡课；筛选（日期/年级）走服务端渲染整页刷新。
 */
(function () {
    'use strict';
    var cfg = {};
    try { cfg = JSON.parse(document.getElementById('liveData').textContent || '{}'); } catch (e) { cfg = {}; }
    var $ = window.jQuery;
    if (!$) { return; }

    var RESULT_TEXT = { normal: '正常', late: '迟到', absent: '缺课', swap: '调课', other: '其他' };

    function notify(msg, type) {
        if (typeof window.toast === 'function') { window.toast(msg, type || 'success'); return; }
        // 2026-10-10：轻量 toast（单击即标记会高频触发，不能用 alert 打断巡课）
        var color = type === 'danger' ? '#dc2626' : (type === 'warning' ? '#b45309'
            : (type === 'secondary' ? '#64748b' : '#059669'));
        var $t = $('<div></div>').text(msg).css({
            position: 'fixed', top: '72px', left: '50%', transform: 'translateX(-50%)',
            zIndex: 3000, background: '#fff', color: color, border: '1px solid ' + color,
            borderLeft: '4px solid ' + color, borderRadius: '8px', padding: '8px 14px',
            boxShadow: '0 6px 20px rgba(15,23,42,.12)', fontSize: '13px', maxWidth: '80vw'
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
        if (extra) { Object.keys(extra).forEach(function (k) { p[k] = extra[k]; }); }
        nav(p);
    }
    $('#datePick').on('change', function () { navNow(); });
    $('#gradePick').on('change', function () { navNow(); });
    $('#btnToday').on('click', function () { navNow({ date: cfg.today }); });
    $('#btnPrint').on('click', function () { window.print(); });

    /* ── 覆盖率计数 ── */
    function recount() {
        var total = $('#liveContainer .ovw-item[data-check]').length;
        var done = $('#liveContainer .ovw-item[data-check!=""]').length;
        $('#statChecked').text(done);
        $('#statExpected').text(total);
        $('#statRate').text(total ? Math.floor(done * 100 / total) + '%' : '0%');
    }

    /* ── 就地更新一格 ── */
    function cellSelector(c) {
        return '#liveContainer .ovw-item[data-grade="' + c.grade + '"]'
            + '[data-class="' + c.class_name + '"]'
            + '[data-period="' + c.period_number + '"]';
    }
    function applyResult($el, result, note) {
        var label = RESULT_TEXT[result] || '';
        $el.attr('data-check', result || '');
        $el.find('.ovw-chk').attr('class', 'ovw-chk ovw-chk-' + (result || 'none'))
            .text(label || '未查');
        var t = ($el.data('subject') || '') + ' ' + ($el.data('teacher') || '');
        t += result ? (' · 已标记' + label + (note ? '（' + note + '）' : ''))
            : ' · 点此标记查课结果';
        $el.attr('title', t);
    }

    function post(cells, ok) {
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
        });
    }

    /* ── 快速标记（2026-10-10 改版）──────────────────────────────────
       单击 = 正常；双击 = 迟到；右键 = 更多（缺课 / 调课 / 迟到说明 / 其他 / 撤销）。
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
        post([cellPayload($el, result, note)], function () {
            $el.data('note', note || '');
            applyResult($el, result, note || '');
            if (result) { notify('已标记：' + (RESULT_TEXT[result] || result)); }
            else { notify('已撤销该格标记', 'warning'); }
        });
    }

    // 单击 vs 双击：单击延迟 260ms 执行，期间若再来一击则判为双击（迟到）
    var clickTimer = null;
    $('#liveContainer').on('click', '.ovw-item[data-check]', function () {
        var $el = $(this);
        if (clickTimer) {
            clearTimeout(clickTimer); clickTimer = null;
            markCell($el, 'late', $el.data('note') || '');
            return;
        }
        clickTimer = setTimeout(function () {
            clickTimer = null;
            markCell($el, 'normal', $el.data('note') || '');
        }, 260);
    });

    /* ── 右键菜单：缺课 / 调课 / 迟到说明 / 其他 / 撤销 ── */
    var $menu = null;
    function closeMenu() { if ($menu) { $menu.remove(); $menu = null; } }
    function openMenu($el, x, y) {
        closeMenu();
        $menu = $('<div class="chk-menu"></div>').css({ left: x + 'px', top: y + 'px' });
        var items = [
            ['normal', '正常', 'success'],
            ['late', '迟到', 'warning'],
            ['late-note', '迟到说明…（如迟到 5 分钟）', 'warning'],
            ['absent', '缺课', 'danger'],
            ['swap', '调课', 'primary'],
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
    }
    $('#liveContainer').on('contextmenu', '.ovw-item[data-check]', function (ev) {
        ev.preventDefault();
        openMenu($(this), ev.pageX, ev.pageY);
    });
    $(document).on('click', function () { closeMenu(); });
    $(window).on('scroll', closeMenu);
    $(document).on('keydown', function (e) { if (e.keyCode === 27) { closeMenu(); } });

    /* ── 一键：当前页面未标记的全部正常（巡课一圈点一下）── */
    $('#btnMarkAllNormal').on('click', function () {
        var cells = [];
        $('#liveContainer .ovw-item[data-check=""]').each(function () {
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
        });
    });

    /* ── 本节保存 + 通知领导（2026-10-10）──
       选中某一节次：本节所有班都查完 → 直接保存并提示；
       还有未查的 → 弹窗让用户决定是否把情况站内通知课表管理员（领导）。 */
    function periodCells(pn) {
        return $('#liveContainer .ovw-item[data-check][data-period="' + pn + '"]');
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
        var pn = $('#periodPick').val();
        if (!pn) { notify('请先选择节次', 'secondary'); return; }
        var label = $('#periodPick option:selected').text();
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
})();
