/* StuLink v1.19.51.64 2026-10-20
 * 成绩工作台 · 首页仪表盘：KPI / 最近考试 / 当前动态的定时刷新
 *
 * 数据来源只有一个：/grades/api/dashboard（与首屏服务端渲染同一个
 * dashboard_service.build_overview），因此刷新前后的口径必然一致。
 * 首屏内容由模板渲染，本脚本只做「局部更新 + 轮询」——
 * JS 挂了页面照样能看数。功能入口板的交互在 grades_home_entries.js。
 */
(function () {
    'use strict';

    var app = document.getElementById('gdApp');
    if (!app) { return; }

    var cfg = parseJson('gd-cfg', {});
    var data = parseJson('gd-data', {});
    var timer = null;
    var loading = false;

    function parseJson(id, fallback) {
        var el = document.getElementById(id);
        if (!el) { return fallback; }
        try { return JSON.parse(el.textContent); } catch (e) { return fallback; }
    }

    function $(id) { return document.getElementById(id); }
    function esc(s) { return window.escHtml ? window.escHtml(s) : String(s == null ? '' : s); }
    function setText(id, val) { var el = $(id); if (el) { el.textContent = (val == null ? '' : val); } }

    /* 最近考试行点击 → 进本场考试（有管理权限进成绩单，只读进分析页） */
    function examUrl(id) {
        if (app.getAttribute('data-can-edit') === '1' && cfg.exam_tpl) {
            return cfg.exam_tpl.replace(/0$/, id);
        }
        return cfg.analysis + '?exam=' + id;
    }

    function renderStageCards(payload) {
        var cards = payload.kpi.stage_cards || [];
        var els = document.querySelectorAll('.gd-stage-num');
        for (var i = 0; i < els.length; i++) {
            var name = els[i].getAttribute('data-stage');
            for (var j = 0; j < cards.length; j++) {
                if (cards[j].name === name) { els[i].textContent = cards[j].exams; }
            }
        }
    }

    function renderRecent(payload) {
        var body = $('gdRecentBody');
        if (!body) { return; }
        var rows = payload.recent_exams || [];
        if (!rows.length) {
            body.innerHTML = '<tr><td colspan="6" class="text-center text-muted py-3">暂无考试</td></tr>';
            return;
        }
        function badge(e) {
            var cls = e.status === 'imported' ? 'bg-success'
                : (e.status === 'dirty' ? 'bg-warning text-dark' : 'bg-secondary');
            return '<span class="badge ' + cls + '">' + esc(e.status_label) + '</span>';
        }
        body.innerHTML = rows.map(function (e) {
            return '<tr class="stu-link gd-exam-row" data-exam-id="' + esc(e.id) + '">'
                + '<td class="wrap">' + esc(e.name) + '</td>'
                + '<td>' + esc(e.grade) + '</td>'
                + '<td>' + esc(e.stage) + '</td>'
                + '<td class="text-nowrap">' + esc(e.date) + '</td>'
                + '<td>' + badge(e) + '</td>'
                + '<td class="num">' + (e.participants || 0) + '</td></tr>';
        }).join('');
    }

    function renderActivity(payload) {
        var box = $('gdActivity');
        if (!box) { return; }
        var rows = payload.activity || [];
        if (!rows.length) {
            box.innerHTML = '<li><span class="text-muted small py-3">暂无动态</span></li>';
            return;
        }
        box.innerHTML = rows.map(function (a) {
            return '<li>'
                + '<span class="gd-tl-ic text-' + esc(a.tone) + '"><i class="bi ' + esc(a.icon) + '"></i></span>'
                + '<div class="flex-grow-1"><div class="small">'
                + (a.url ? '<a class="text-reset text-decoration-none" href="'
                    + esc(a.url) + '">' + esc(a.title) + '</a>' : esc(a.title))
                + '</div>'
                + '<div class="text-muted small">' + esc(a.meta) + '</div></div>'
                + '<span class="gd-tl-time">' + esc(a.time) + '</span></li>';
        }).join('');
    }

    function render(payload) {
        data = payload;
        setText('gdKpiExams', payload.kpi.total_exams);
        setText('gdKpiParts', payload.kpi.participants);
        setText('gdKpiCerts', payload.kpi.cert_total);
        setText('gdKpiCertInvalid', payload.kpi.cert_invalid);
        setText('gdUpdated', payload.generated_at);
        renderStageCards(payload);
        renderRecent(payload);
        renderActivity(payload);
    }

    function setState(html, cls) {
        var el = $('gdState');
        if (!el) { return; }
        el.className = 'ms-1 ' + (cls || '');
        el.innerHTML = html;
    }

    /* ==================== 实时刷新 ==================== */
    function refresh(manual) {
        if (loading || !cfg.api) { return; }
        loading = true;
        setState('<i class="bi bi-arrow-repeat"></i> 更新中…', 'text-muted');
        fetch(cfg.api, { headers: { 'X-Requested-With': 'fetch' }, credentials: 'same-origin' })
            .then(function (r) {
                if (!r.ok) { throw new Error('HTTP ' + r.status); }
                return r.json();
            })
            .then(function (payload) {
                render(payload);
                setState('· 已同步', 'text-success');
                setTimeout(function () { setState('', ''); }, 4000);
            })
            .catch(function (err) {
                setState('<i class="bi bi-exclamation-triangle"></i> 刷新失败', 'text-danger');
                if (manual && window.stuToast) { window.stuToast('仪表盘刷新失败：' + err.message, 'err'); }
            })
            .then(function () { loading = false; });
    }

    function autoOn() {
        var box = $('gdAuto');
        return !box || box.checked;
    }

    function schedule() {
        if (timer) { clearInterval(timer); timer = null; }
        if (!autoOn()) { return; }
        var ms = (cfg.interval || 120000);
        timer = setInterval(function () {
            if (document.hidden) { return; }      // 页面在后台不拉数据，省资源
            refresh(false);
        }, ms);
    }

    /* ==================== 绑定 ==================== */
    var btn = $('gdRefresh');
    if (btn) { btn.addEventListener('click', function () { refresh(true); }); }

    var autoBox = $('gdAuto');
    if (autoBox) {
        try {
            var saved = localStorage.getItem('stulink-gd-auto');
            if (saved === '0') { autoBox.checked = false; }
        } catch (e) { /* 隐私模式：保持默认开启 */ }
        autoBox.addEventListener('change', function () {
            try { localStorage.setItem('stulink-gd-auto', autoBox.checked ? '1' : '0'); } catch (e) {}
            schedule();
        });
    }

    // 回到前台：距上次更新超过一个周期就补一次，保证"看到的数是新的"
    document.addEventListener('visibilitychange', function () {
        if (!document.hidden && autoOn()) { refresh(false); }
    });

    // 明细行点击 → 进本场考试（托管在 tbody，刷新重渲染后依然有效）
    var recentBody = $('gdRecentBody');
    if (recentBody) {
        recentBody.addEventListener('click', function (e) {
            var tr = e.target.closest ? e.target.closest('tr[data-exam-id]') : null;
            if (tr) { window.location.href = examUrl(tr.getAttribute('data-exam-id')); }
        });
    }

    render(data);
    schedule();
})();
