/* StuLink v1.19.62.65 2026-10-21
   全局「教师选择器」：所有页面凡是下拉里是教师姓名的，统一升级为可搜索浮层。
   Copyright (c) 2026 zkxxzf. Apache License 2.0

   需求（2026-10-21 用户口径）：
   1. 下拉里的教师按**姓氏首字母**排序（拼音 A→Z）；
   2. 支持输入**姓名 / 全拼 / 每字首字母**搜索；
   3. 浮层在页面底部时**向上弹出**，且高度按可视区裁剪（列表内部滚动，绝不被截断）。

   用法：
   · 自动：页面加载后扫描所有 <select>，若其中选项大多是教师姓名则自动升级；
     也可在 select 上加 data-teacher-pick="1" 强制升级、data-no-pick="1" 排除。
   · 手动：slTP.attachSelect(selectEl)（JS 动态渲染的表格用）
   · 弹层：slTP.open({anchor, current, items, allowClear, title, onPick, onClear})
*/
(function () {
    'use strict';

    var CSS = [
        '.sl-picker{position:fixed;z-index:2090;width:260px;padding:8px;display:flex;',
        'flex-direction:column;background:var(--sl-surface,#fff);color:var(--sl-text,#1e293b);',
        'border:1px solid var(--sl-border,#e2e8f0);border-radius:10px;',
        'box-shadow:0 12px 32px rgba(15,30,61,.22);}',
        '.sl-picker .pk-title{font-size:.72rem;color:var(--sl-text-muted,#64748b);',
        'margin:0 2px 6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}',
        '.sl-picker .pk-list{flex:1 1 auto;min-height:0;overflow:auto;margin-top:6px;}',
        '.sl-picker .pk-opt{display:flex;align-items:center;gap:.4rem;padding:.26rem .45rem;',
        'border-radius:6px;cursor:pointer;font-size:.82rem;line-height:1.4;}',
        '.sl-picker .pk-opt:hover,.sl-picker .pk-opt.on{background:var(--sl-surface-2,#f1f5f9);}',
        '.sl-picker .pk-opt.sel{font-weight:600;}',
        '.sl-picker .pk-opt .pk-sub{margin-left:auto;font-size:.7rem;',
        'color:var(--sl-text-muted,#64748b);}',
        '.sl-picker .pk-empty{padding:.6rem 0;text-align:center;font-size:.78rem;',
        'color:var(--sl-text-muted,#64748b);}',
        '.sl-tp-btn{text-align:left;text-overflow:ellipsis;white-space:nowrap;overflow:hidden;',
        'cursor:pointer;background-image:none;}',
        '.sl-tp-btn.sl-tp-empty{color:var(--sl-text-muted,#94a3b8);}'
    ].join('');

    function injectCss() {
        if (document.getElementById('sl-tp-css')) { return; }
        var st = document.createElement('style');
        st.id = 'sl-tp-css';
        st.textContent = CSS;
        document.head.appendChild(st);
    }

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (ch) {
            return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch];
        });
    }

    // ==================== 教师目录（姓名 → 拼音码） ====================
    var DIR = null;          // {byName: {name: {py, ini}}}
    var DIR_P = null;
    var DIR_URL = '/grades/api/teacher-options';

    function loadDir() {
        if (DIR_P) { return DIR_P; }
        DIR_P = fetch(DIR_URL, {credentials: 'same-origin'})
            .then(function (r) { return r.json(); })
            .then(function (json) {
                var by = {};
                var users = (json && json.data && json.data.users) || [];
                users.forEach(function (u) {
                    if (!u || !u.name) { return; }
                    by[u.name] = {py: u.py || '', ini: u.ini || ''};
                });
                DIR = {byName: by};
                return DIR;
            })
            .catch(function () { DIR = {byName: {}}; return DIR; });
        return DIR_P;
    }

    function stripParen(text) {
        // 「张三（zhangsan）」→「张三」；「张三(班主任)」→「张三」
        return String(text || '').replace(/[（(][^）)]*[）)]\s*$/, '').trim();
    }

    function codesOf(text) {
        var name = stripParen(text);
        var d = DIR && DIR.byName[name];
        return d || {py: '', ini: ''};
    }

    // ==================== 浮层 ====================
    var PK = null;

    function norm(s) {
        return String(s == null ? '' : s).trim().toLowerCase().replace(/[\s·]+/g, '');
    }

    // 匹配打分：全等 > 姓名前缀 > 全拼前缀 > 首字母前缀 > 包含；0 = 不匹配
    function score(it, q) {
        if (!q) { return 1; }
        var nm = norm(it.label), py = it.py || '', ini = it.ini || '';
        if (nm === q) { return 100; }
        if (nm.indexOf(q) === 0) { return 90; }
        if (py.indexOf(q) === 0) { return 80; }
        if (ini.indexOf(q) === 0) { return 70; }
        if (nm.indexOf(q) >= 0) { return 60; }
        if (py.indexOf(q) >= 0) { return 50; }
        if (ini.indexOf(q) >= 0) { return 40; }
        return 0;
    }

    function close() {
        if (PK && PK.box) { PK.box.remove(); }
        PK = null;
        document.removeEventListener('mousedown', onOutside, true);
        document.removeEventListener('keydown', onKey, true);
        window.removeEventListener('scroll', onScroll, true);
        window.removeEventListener('resize', close);
    }

    function onScroll(ev) {
        if (PK && PK.box && PK.box.contains(ev.target)) { return; }
        close();
    }

    function onOutside(ev) {
        if (PK && PK.box && !PK.box.contains(ev.target) &&
            !(PK.anchor && PK.anchor.contains && PK.anchor.contains(ev.target))) {
            close();
        }
    }

    function draw() {
        var box = PK.box;
        var html = PK.shown.length ? PK.shown.map(function (it, i) {
            return '<div class="pk-opt' + (i === PK.idx ? ' on' : '')
                + (!it._clear && String(it.key) === String(PK.current) ? ' sel' : '')
                + '" data-i="' + i + '">'
                + '<span>' + esc(it.label) + '</span>'
                + (!it._clear && it.sub ? '<span class="pk-sub">' + esc(it.sub) + '</span>' : '')
                + '</div>';
        }).join('') : '<div class="pk-empty">没有匹配的人</div>';
        box.querySelector('.pk-list').innerHTML = html;
        box.querySelector('.pk-title').textContent = PK.title || '';
        var on = box.querySelector('.pk-opt.on');
        if (on && on.scrollIntoView) { on.scrollIntoView({block: 'nearest'}); }
    }

    function render() {
        var q = norm(PK.input.value);
        var shown = PK.items.map(function (it) { return {it: it, s: score(it, q)}; })
            .filter(function (x) { return x.s > 0; })
            .sort(function (a, b) {
                // 有输入时按匹配度；无输入时保持传入顺序（服务端已按姓氏首字母排好）
                if (q) { return b.s - a.s || (a.it.label < b.it.label ? -1 : 1); }
                return 0;
            })
            .slice(0, 60)
            .map(function (x) { return x.it; });
        if (PK.allowClear && !q) {
            shown.unshift({key: '', label: '— 不指定（清除）—', _clear: true});
        }
        PK.shown = shown;
        PK.idx = (q || !PK.allowClear) ? 0 : Math.min(1, shown.length - 1);
        draw();
        place();          // 列表高度会变，位置跟着重算
    }

    // 定位：优先向下，下方放不下且上方更宽裕 → 向上；高度按可视区裁剪，列表内部滚动
    function place() {
        if (!PK || !PK.box || !PK.anchor) { return; }
        var box = PK.box;
        var r = PK.anchor.getBoundingClientRect();
        var margin = 8, gap = 4;
        var vh = window.innerHeight, vw = window.innerWidth;
        var w = Math.min(260, vw - margin * 2);
        box.style.width = Math.round(w) + 'px';
        box.style.left = Math.round(Math.max(margin, Math.min(r.left, vw - w - margin))) + 'px';

        // 先按自然高度量一次（不设 max-height），再决定方向与可用高度
        box.style.maxHeight = 'none';
        var natural = box.scrollHeight || 320;
        var below = vh - r.bottom - gap - margin;
        var above = r.top - gap - margin;
        var want = Math.min(natural, 340);
        var up = below < want && above > below;
        var avail = Math.max(120, up ? above : below);
        var h = Math.min(natural, 340, avail);

        box.style.maxHeight = Math.round(h) + 'px';
        var top = up ? (r.top - gap - h) : (r.bottom + gap);
        top = Math.max(margin, Math.min(top, vh - h - margin));
        box.style.top = Math.round(top) + 'px';
    }

    function pick(i) {
        var it = PK.shown[i];
        if (!it) { return; }
        var cb = it._clear ? PK.onClear : PK.onPick;
        close();
        if (cb) { cb(it); }
    }

    function onKey(ev) {
        if (!PK) { return; }
        if (ev.key === 'Escape') { ev.preventDefault(); close(); return; }
        if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
            if (!PK.shown.length) { return; }
            ev.preventDefault();
            PK.idx = (PK.idx + (ev.key === 'ArrowDown' ? 1 : -1) + PK.shown.length) % PK.shown.length;
            draw();
            return;
        }
        if (ev.key === 'Enter') { ev.preventDefault(); pick(PK.idx); }
    }

    function open(cfg) {
        close();
        injectCss();
        var box = document.createElement('div');
        box.className = 'sl-picker';
        box.innerHTML = '<div class="pk-title"></div>'
            + '<input type="text" class="form-control form-control-sm pk-inp" autocomplete="off"'
            + ' aria-label="搜索教师" placeholder="姓名 / 全拼 / 首字母">'
            + '<div class="pk-list"></div>';
        document.body.appendChild(box);
        PK = {box: box, input: box.querySelector('.pk-inp'),
              items: (cfg.items || []).filter(function (it) { return it && it.label; }),
              current: cfg.current == null ? '' : String(cfg.current),
              allowClear: !!cfg.allowClear, title: cfg.title || '',
              onPick: cfg.onPick, onClear: cfg.onClear,
              anchor: cfg.anchor || null, shown: [], idx: 0};
        PK.input.addEventListener('input', render);
        box.querySelector('.pk-list').addEventListener('click', function (ev) {
            var el = ev.target.closest('.pk-opt');
            if (el) { pick(parseInt(el.dataset.i, 10)); }
        });
        box.querySelector('.pk-list').addEventListener('mouseover', function (ev) {
            var el = ev.target.closest('.pk-opt');
            if (!el) { return; }
            PK.idx = parseInt(el.dataset.i, 10);
            box.querySelectorAll('.pk-opt').forEach(function (x, i) {
                x.classList.toggle('on', i === PK.idx);
            });
        });
        document.addEventListener('mousedown', onOutside, true);
        document.addEventListener('keydown', onKey, true);
        window.addEventListener('scroll', onScroll, true);
        window.addEventListener('resize', close);
        render();
        PK.input.focus();
    }

    // ==================== 原生 <select> → 可搜索 ====================
    function optionLabel(o) {
        return (o.textContent || o.text || '').trim();
    }

    function attachSelect(sel) {
        if (!sel || sel.dataset.slTp === '1') { return; }
        if (sel.multiple || sel.disabled) { return; }
        sel.dataset.slTp = '1';

        var btn = document.createElement('button');
        btn.type = 'button';
        // 保留原 select 的类（尺寸/宽度/颜色），外观与原生下拉一致（右侧箭头也保留）
        btn.className = 'form-select form-select-sm sl-tp-btn'
            + (sel.className ? ' ' + sel.className : '');
        btn.setAttribute('aria-haspopup', 'listbox');
        sel.classList.add('d-none');
        sel.parentNode.insertBefore(btn, sel);

        function refresh() {
            var o = sel.options[sel.selectedIndex];
            var txt = o ? optionLabel(o) : '';
            btn.textContent = txt || '—';
            btn.classList.toggle('sl-tp-empty', !txt || !o.value);
            btn.title = txt || '';
        }

        btn.addEventListener('click', function (ev) {
            ev.preventDefault();
            ev.stopPropagation();
            var items = [], clearItem = null;
            Array.prototype.forEach.call(sel.options, function (o) {
                var txt = optionLabel(o);
                if (!txt) { return; }
                if (!o.value) {
                    clearItem = {key: '', label: txt, _clear: true};
                    return;
                }
                var c = codesOf(txt);
                items.push({key: o.value, label: txt, py: c.py, ini: c.ini});
            });
            // 用户口径：教师按**姓氏首字母**排（拼音全拼升序，等价于首字母分组）
            items.sort(function (a, b) {
                var ka = a.py || norm(a.label), kb = b.py || norm(b.label);
                return ka < kb ? -1 : (ka > kb ? 1 : 0);
            });
            if (clearItem) { items.unshift(clearItem); }
            open({
                anchor: btn,
                current: sel.value,
                items: items,
                allowClear: false,     // 清除项已作为 select 的「未指定」选项存在
                title: sel.dataset.title || sel.getAttribute('data-placeholder') || '',
                onPick: function (it) {
                    sel.value = it.key;
                    sel.dispatchEvent(new Event('change', {bubbles: true}));
                    refresh();
                },
                onClear: function () {
                    sel.value = '';
                    sel.dispatchEvent(new Event('change', {bubbles: true}));
                    refresh();
                }
            });
        });
        sel.addEventListener('change', refresh);
        // 选项被页面重新渲染时同步显示文本
        try {
            new MutationObserver(refresh).observe(sel, {childList: true});
        } catch (e) { /* 老浏览器忽略 */ }
        refresh();
    }

    // 判断一个 select 是否「在选老师」：选项文本大多能在教师目录里命中
    function looksLikeTeacherSelect(sel) {
        if (sel.dataset.noPick === '1') { return false; }
        if (sel.dataset.teacherPick === '1') { return true; }
        if (!DIR || !DIR.byName) { return false; }
        var total = 0, hit = 0;
        Array.prototype.forEach.call(sel.options, function (o) {
            var txt = optionLabel(o);
            if (!txt || !o.value) { return; }
            total++;
            if (DIR.byName[stripParen(txt)]) { hit++; }
        });
        return total >= 2 && hit / total >= 0.6;
    }

    function scan(root) {
        var scope = root || document;
        var list = scope.querySelectorAll ? scope.querySelectorAll('select') : [];
        Array.prototype.forEach.call(list, function (sel) {
            if (sel.dataset.slTp === '1' || sel.dataset.noPick === '1') { return; }
            if (looksLikeTeacherSelect(sel)) { attachSelect(sel); }
        });
    }

    function init() {
        injectCss();
        loadDir().then(function () {
            scan(document);
            try {
                new MutationObserver(function (muts) {
                    muts.forEach(function (m) {
                        Array.prototype.forEach.call(m.addedNodes || [], function (n) {
                            if (!n || n.nodeType !== 1) { return; }
                            if (n.tagName === 'SELECT') {
                                if (looksLikeTeacherSelect(n)) { attachSelect(n); }
                            } else if (n.querySelectorAll) {
                                scan(n);
                            }
                        });
                    });
                }).observe(document.body, {childList: true, subtree: true});
            } catch (e) { /* 忽略 */ }
        });
    }

    window.slTP = {open: open, close: close, attachSelect: attachSelect,
                   scan: scan, loadDir: loadDir};

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
