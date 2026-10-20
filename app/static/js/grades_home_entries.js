/* StuLink v1.19.49.45 2026-10-20
 * 成绩工作台 · 首页「功能入口板」交互：
 *   ① 需要考试的入口 → 先弹考试列表，选完再跳目标页面；
 *   ② 自定义入口 → 勾选显示 / 上下排序，保存到个人偏好。
 * 全部走事件委托，服务端渲染的 DOM 改了不用改脚本。
 */
(function () {
    'use strict';

    var cfg = readCfg();
    var pick = { tpl: '', filter: 'all' };

    function readCfg() {
        var el = document.getElementById('gd-cfg');
        if (!el) { return {}; }
        try { return JSON.parse(el.textContent) || {}; } catch (e) { return {}; }
    }

    function $(id) { return document.getElementById(id); }

    function toast(msg, kind) {
        if (window.stuToast) { window.stuToast(msg, kind || 'ok'); }
    }

    /* ==================== ① 选考试 ==================== */

    function filterExamRows() {
        var body = $('gdExamBody');
        var box = $('gdExamSearch');
        if (!body) { return; }
        var kw = ((box && box.value) || '').trim().toLowerCase();
        var rows = body.querySelectorAll('tr.gd-pick-exam');
        var shown = 0;
        for (var i = 0; i < rows.length; i++) {
            var row = rows[i];
            var okStatus = pick.filter !== 'imported'
                || row.getAttribute('data-imported') === '1';
            var text = row.getAttribute('data-search') || '';
            var ok = okStatus && (!kw || text.indexOf(kw) >= 0);
            row.style.display = ok ? '' : 'none';
            if (ok) { shown += 1; }
        }
        var empty = $('gdExamEmpty');
        if (empty) {
            if (shown > 0) { empty.classList.add('d-none'); }
            else { empty.classList.remove('d-none'); }
        }
    }

    function openExamModal(link) {
        pick.tpl = link.getAttribute('data-exam-tpl') || '';
        pick.filter = link.getAttribute('data-exam-filter') || 'all';
        var name = $('gdExamEntry');
        if (name) {
            var title = link.getAttribute('data-exam-title') || '';
            name.textContent = title ? '· ' + title : '';
        }
        var box = $('gdExamSearch');
        if (box) { box.value = ''; }
        filterExamRows();
        if (window.bootstrap && window.bootstrap.Modal) {
            window.bootstrap.Modal.getOrCreateInstance($('gdExamModal')).show();
        }
    }

    function bindEntries() {
        var board = document.querySelector('.gd-board');
        if (board) {
            board.addEventListener('click', function (e) {
                var link = e.target.closest ? e.target.closest('a[data-exam-tpl]') : null;
                if (!link) { return; }
                e.preventDefault();
                openExamModal(link);
            });
        }
        var body = $('gdExamBody');
        if (body) {
            body.addEventListener('click', function (e) {
                var row = e.target.closest ? e.target.closest('tr.gd-pick-exam') : null;
                if (!row || row.style.display === 'none') { return; }
                var id = row.getAttribute('data-id');
                if (id && pick.tpl) {
                    window.location.href = pick.tpl.replace('__EXAM__', id);
                }
            });
        }
        var search = $('gdExamSearch');
        if (search) { search.addEventListener('input', filterExamRows); }
    }

    /* ==================== ② 自定义入口 ==================== */

    function openPrefs() {
        if (window.bootstrap && window.bootstrap.Modal) {
            window.bootstrap.Modal.getOrCreateInstance($('gdEntryModal')).show();
        }
    }

    function moveRow(btn, dir) {
        var row = btn.closest('.gd-pref-row');
        if (!row) { return; }
        var sib = dir < 0 ? row.previousElementSibling : row.nextElementSibling;
        if (!sib || !sib.classList.contains('gd-pref-row')) { return; }
        if (dir < 0) { row.parentNode.insertBefore(row, sib); }
        else { row.parentNode.insertBefore(sib, row); }
        btn.blur();
    }

    function collect() {
        var editor = $('gdEntryEditor');
        var order = [];
        var hidden = [];
        if (!editor) { return { order: order, hidden: hidden }; }
        var rows = editor.querySelectorAll('.gd-pref-row');
        for (var i = 0; i < rows.length; i++) {
            var key = rows[i].getAttribute('data-key');
            var on = rows[i].querySelector('.gd-pref-on');
            if (!key) { continue; }
            order.push(key);
            if (!on || !on.checked) { hidden.push(key); }
        }
        return { order: order, hidden: hidden };
    }

    function postPrefs(payload) {
        return fetch(cfg.entries_save, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': cfg.csrf || '' },
            body: JSON.stringify(payload)
        }).then(function (r) {
            if (!r.ok) { throw new Error('HTTP ' + r.status); }
            return r.json();
        });
    }

    function savePrefs(btn) {
        if (btn) { btn.disabled = true; }
        postPrefs(collect()).then(function () {
            toast('入口已保存，正在刷新…');
            setTimeout(function () { window.location.reload(); }, 350);
        }).catch(function (err) {
            if (btn) { btn.disabled = false; }
            toast('保存失败：' + err.message, 'err');
        });
    }

    function resetPrefs(btn) {
        if (!window.confirm('恢复默认入口（全部显示、按默认顺序）？')) { return; }
        if (btn) { btn.disabled = true; }
        postPrefs({ order: [], hidden: [] }).then(function () {
            window.location.reload();
        }).catch(function (err) {
            if (btn) { btn.disabled = false; }
            toast('恢复失败：' + err.message, 'err');
        });
    }

    function bindPrefs() {
        var open = $('gdEntryCustomize');
        if (open) { open.addEventListener('click', openPrefs); }
        var editor = $('gdEntryEditor');
        if (editor) {
            editor.addEventListener('click', function (e) {
                var btn = e.target.closest ? e.target.closest('.gd-move') : null;
                if (!btn) { return; }
                moveRow(btn, parseInt(btn.getAttribute('data-move'), 10));
            });
        }
        var save = $('gdEntrySave');
        if (save) {
            save.addEventListener('click', function () { savePrefs(save); });
        }
        var reset = $('gdEntryReset');
        if (reset) {
            reset.addEventListener('click', function () { resetPrefs(reset); });
        }
    }

    bindEntries();
    bindPrefs();
})();
