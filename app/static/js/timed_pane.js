/* StuLink v1.19.49.50 2026-10-21
   限时练「学生成绩 / 学生分析」局部刷新：
   - 点班级、翻页、改每页条数只换 #timedPane，不再整页重载
     （整页重载会重新渲染侧栏与整个框架、闪白、丢滚动位置）；
   - 鼠标划过班级按钮时**预取**，点击基本秒开；
   - 地址栏同步（pushState），浏览器前进/后退也走局部刷新。
   Copyright (c) 2026 zkxxzf. Apache License 2.0 */
(function () {
    'use strict';
    var PREF_KEY = 'stulink.pref';
    var CACHE_MS = 30000;
    var CACHE = {};        // partial url -> {html, at}
    var PENDING = {};      // 正在预取的 url，避免重复请求

    function pane() { return document.getElementById('timedPane'); }

    function readPref() {
        try { return JSON.parse(localStorage.getItem(PREF_KEY) || '{}'); }
        catch (e) { return {}; }
    }

    function writePrefSize(n) {
        var p = readPref();
        p.size = parseInt(n, 10) || 50;
        try { localStorage.setItem(PREF_KEY, JSON.stringify(p)); } catch (e) { /* 忽略 */ }
    }

    function partialUrl(url) {
        var u = new URL(url, location.href);
        u.searchParams.set('partial', '1');
        return u;
    }

    function pageUrl(u) {
        var d = new URL(u.toString());
        d.searchParams.delete('partial');
        return d.pathname + d.search + d.hash;
    }

    /* ===== 表头吸顶（2026-10-21 用户口径：往下翻表头不能消失）=====
       表格包在 .table-responsive 里（overflow-x:auto 会建立滚动容器），
       CSS 的 position:sticky 在那个容器里不跟着页面滚，所以这里克隆一层
       fixed 表头：页面滚过原表头就显示，列宽 / 横向滚动 / 主题底色同步。 */
    var SH = { box: null, table: null, scroller: null, top: 56 };

    function dropSticky() {
        if (SH.box && SH.box.parentNode) {
            SH.box.parentNode.removeChild(SH.box);
        }
        SH.box = SH.table = SH.scroller = null;
    }

    function buildSticky() {
        dropSticky();
        var root = pane();
        var table = root && root.querySelector('table.timed-tbl');
        var thead = table && table.querySelector('thead');
        if (!table || !thead) { return; }
        var sc = table.parentNode;
        while (sc && sc !== document.body &&
               !(sc.classList && sc.classList.contains('table-responsive'))) {
            sc = sc.parentNode;
        }
        if (!sc || sc === document.body) { sc = table.parentNode; }

        var box = document.createElement('div');
        box.className = 'timed-sticky-head';
        // 定位写内联：万一样式表是旧缓存（没有 .timed-sticky-head 规则），
        // 纯 appendChild 的 div 会落在 body 末尾 —— 表头跑到页面最下面。
        var nav = document.querySelector('nav.navbar');
        SH.top = nav ? Math.round(nav.getBoundingClientRect().height) : 56;
        box.style.cssText = [
            'position:fixed', 'top:' + SH.top + 'px', 'left:0',
            'width:0', 'height:0', 'z-index:120', 'overflow:hidden',
            'pointer-events:none', 'display:none',
            'box-shadow:0 3px 8px rgba(0,0,0,.16)'
        ].join(';');
        var inner = document.createElement('div');
        inner.className = 'timed-sticky-inner';
        var clone = table.cloneNode(false);
        clone.appendChild(thead.cloneNode(true));

        var ths = thead.querySelectorAll('th');
        var cths = clone.querySelectorAll('thead th');
        var total = 0;
        for (var i = 0; i < ths.length; i++) {
            if (!cths[i]) { break; }
            var w = ths[i].getBoundingClientRect().width;
            total += w;
            cths[i].style.width = w + 'px';
        }
        if (!total) { return; }
        clone.style.tableLayout = 'fixed';
        clone.style.width = total + 'px';
        inner.appendChild(clone);
        box.appendChild(inner);

        // 深色/浅色主题都跟随表头自身的底色，避免吸顶层透出下面的内容
        var bg = '';
        try { bg = getComputedStyle(ths[0]).backgroundColor; } catch (e) { /* 忽略 */ }
        if (!bg || bg === 'rgba(0, 0, 0, 0)' || bg === 'transparent') {
            try { bg = getComputedStyle(document.body).backgroundColor; }
            catch (e2) { bg = '#fff'; }
        }
        if (bg) { box.style.backgroundColor = bg; }
        document.body.appendChild(box);
        SH.box = box;
        SH.table = table;
        SH.scroller = sc;
    }

    function syncSticky() {
        if (!SH.box || !SH.table) { return; }
        if (!document.body.contains(SH.table)) { dropSticky(); return; }
        var navH = SH.top || 56;             // 顶部导航固定高度
        var head = SH.table.querySelector('thead').getBoundingClientRect();
        var tbl = SH.table.getBoundingClientRect();
        var show = head.top < navH && tbl.bottom > navH + 40;
        SH.box.style.display = show ? 'block' : 'none';
        if (!show) { return; }
        var left = Math.max(tbl.left, 0);
        var width = Math.min(tbl.width, window.innerWidth - left);
        SH.box.style.left = left + 'px';
        SH.box.style.width = Math.max(width, 0) + 'px';
        SH.box.style.height = head.height + 'px';
        var dx = SH.scroller ? SH.scroller.scrollLeft : 0;
        SH.box.firstChild.style.transform = 'translateX(' + (-dx) + 'px)';
    }

    function afterPaint() {
        buildSticky();
        syncSticky();
    }

    /* 把返回的 <div id="timedPane"> 换进去 */
    function paint(html) {
        var box = document.createElement('div');
        box.innerHTML = html;
        var fresh = box.querySelector('#timedPane');
        var cur = pane();
        if (!fresh || !cur || !cur.parentNode) { return false; }
        cur.parentNode.replaceChild(fresh, cur);
        afterPaint();
        return true;
    }

    function loading(on) {
        var cur = pane();
        if (!cur) { return; }
        cur.classList.toggle('timed-pane-loading', !!on);
    }

    function load(url, push) {
        var u = partialUrl(url);
        var key = u.toString();
        var hit = CACHE[key];
        if (hit && Date.now() - hit.at < CACHE_MS && paint(hit.html)) {
            if (push) { history.pushState({ pane: 1 }, '', pageUrl(u)); }
            return;
        }
        loading(true);
        fetch(key, { credentials: 'same-origin',
                     headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then(function (r) {
                if (!r.ok) { throw new Error(r.status); }
                return r.text();
            })
            .then(function (html) {
                CACHE[key] = { html: html, at: Date.now() };
                loading(false);
                if (!paint(html)) { location.href = pageUrl(u); return; }
                if (push) { history.pushState({ pane: 1 }, '', pageUrl(u)); }
            })
            .catch(function () {
                loading(false);
                location.href = pageUrl(u);       // 兜底：退回整页跳转
            });
    }

    function prefetch(url) {
        var u = partialUrl(url);
        var key = u.toString();
        var hit = CACHE[key];
        if ((hit && Date.now() - hit.at < CACHE_MS) || PENDING[key]) { return; }
        PENDING[key] = true;
        fetch(key, { credentials: 'same-origin',
                     headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then(function (r) { return r.ok ? r.text() : null; })
            .then(function (html) {
                if (html) { CACHE[key] = { html: html, at: Date.now() }; }
            })
            .catch(function () { /* 预取失败无所谓，点击时再取 */ })
            .then(function () { delete PENDING[key]; });
    }

    document.addEventListener('click', function (e) {
        var a = e.target.closest ? e.target.closest('a[data-pane-nav]') : null;
        if (!a || e.defaultPrevented || e.button !== 0) { return; }
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) { return; }
        if (a.target && a.target !== '_self') { return; }
        // 2026-10-22：分页「上一页」在第一页时是 li.disabled —— 视觉不可点，
        // 但链接还在，点下去会请求 page=0；这里直接吞掉，别再发无意义请求。
        if (a.closest && a.closest('.disabled')) { e.preventDefault(); return; }
        e.preventDefault();
        load(a.href, true);
    });

    document.addEventListener('mouseover', function (e) {
        var a = e.target.closest ? e.target.closest('a[data-pane-nav]') : null;
        if (a) { prefetch(a.href); }
    });

    window.addEventListener('popstate', function () { load(location.href, false); });

    // 表头吸顶：页面滚动 / 表格横向滚动 / 窗口尺寸变化时同步
    window.addEventListener('scroll', syncSticky, { passive: true });
    window.addEventListener('resize', afterPaint);
    document.addEventListener('scroll', function (e) {
        if (SH.scroller && e.target === SH.scroller) { syncSticky(); }
    }, true);

    // 每页条数：跟着「我的设置 → 列表每页条数」，改了局部刷新并记住
    document.addEventListener('change', function (e) {
        if (!e.target || e.target.id !== 'pageSize') { return; }
        var u = new URL(location.href);
        u.searchParams.set('per', e.target.value);
        u.searchParams.delete('page');
        writePrefSize(e.target.value);
        load(u.toString(), true);
    });

    // 首次整页打开时按偏好补一次 per（地址里已经带 per 就不动）
    var sel = document.getElementById('pageSize');
    if (sel && !new URL(location.href).searchParams.get('per')) {
        var pref = readPref();
        if (pref.size && String(pref.size) !== sel.value) {
            var u2 = new URL(location.href);
            u2.searchParams.set('per', pref.size);
            u2.searchParams.delete('page');
            location.replace(u2.toString());
        }
    }

    // 首次打开：建好吸顶表头（DOM 还没解析完就等 DOMContentLoaded）
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', afterPaint);
    } else {
        afterPaint();
    }
})();
