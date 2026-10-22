/* StuLink · AI 报告分享（2026-10-22）
 * 交互：AI 工作台报告区的「分享」→ 选有效期 → 生成链接 → 复制 / 预览 / 撤销。
 * 依赖：jQuery + bootstrap（站内 base.html 已引入）；接口路径来自 #aiStudioCfg。
 * 安全：链接由后端生成（token 不可枚举），前端只做展示与复制，不自行拼 token。
 * Copyright (c) 2026 zkxxzf. Apache License 2.0 */
(function () {
    'use strict';
    if (!window.jQuery) { return; }
    var $ = window.jQuery;

    var cfg = {};
    try {
        cfg = JSON.parse((document.getElementById('aiStudioCfg') || {}).textContent || '{}');
    } catch (e) { cfg = {}; }

    var REPORTS = cfg.reports_url || '/grades/ai/reports';
    var DAY_CHOICES = { 7: 1, 30: 1, 90: 1, 0: 1 };   // 0 = 长期

    var state = { rid: null, days: 30, busy: false };

    function toast(msg, type) {
        if (typeof window.stuToast === 'function') { window.stuToast(msg, type || 'ok'); }
        else if (type === 'err') { window.alert(msg); }
    }
    function csrf() { return (typeof window.csrfToken === 'string') ? window.csrfToken : ''; }
    function modalEl() { return document.getElementById('aiShareModal'); }
    function showModal() {
        if (window.bootstrap && bootstrap.Modal) {
            bootstrap.Modal.getOrCreateInstance(modalEl()).show();
        } else {
            $(modalEl()).addClass('show d-block');
        }
    }
    function reportId() { return $('#aiProgress').attr('data-report-id') || ''; }

    /* 两个板块互斥：未生成 → 选有效期；已生成 → 展示链接与统计 */
    function showSetup(on) {
        $('#aiShareSetup').toggleClass('d-none', !on);
        $('#aiShareReady').toggleClass('d-none', on);
    }

    function renderShare(share) {
        if (!share || !share.path) { showSetup(true); return; }
        $('#aiShareUrl').val(window.location.origin + share.path);
        var parts = ['已访问 ' + (share.view_count || 0) + ' 次'];
        if (share.last_viewed_at) { parts.push('最近 ' + share.last_viewed_at); }
        parts.push(share.permanent ? '长期有效' : (share.expires_at + ' 到期'));
        $('#aiShareStat').text(parts.join(' · '));
        showSetup(false);
    }

    function fail(x, fallback) {
        var msg = (x && x.responseJSON && x.responseJSON.message) || fallback;
        if (x && x.status) { msg += '（HTTP ' + x.status + '）'; }
        toast(msg, 'err');
    }

    function loadState(rid) {
        $.getJSON(REPORTS + '/' + encodeURIComponent(rid) + '/share', function (res) {
            var share = res && res.data ? res.data.share : null;
            renderShare(share);
        }).fail(function (x) { fail(x, '读取分享状态失败'); });
    }

    function openModal() {
        var rid = reportId();
        if (!rid) { toast('报告生成后才能分享', 'warn'); return; }
        state.rid = rid;
        $('#aiShareUrl').val('');
        $('#aiShareStat').text('');
        showSetup(true);
        showModal();
        loadState(rid);
    }

    function createLink() {
        var rid = state.rid;
        if (!rid || state.busy) { return; }
        state.busy = true;
        $('#aiShareCreate').prop('disabled', true);
        $.ajax({
            url: REPORTS + '/' + encodeURIComponent(rid) + '/share',
            method: 'POST',
            headers: { 'X-CSRFToken': csrf() },
            data: { days: state.days }
        }).done(function (res) {
            if (!res || !res.success) { toast((res && res.message) || '生成失败', 'err'); return; }
            renderShare(res.data && res.data.share);
            toast(res.data && res.data.created ? '链接已生成，点「复制」发给对方' : '已复用现有链接', 'ok');
        }).fail(function (x) {
            fail(x, '生成链接失败');
        }).always(function () {
            state.busy = false;
            $('#aiShareCreate').prop('disabled', false);
        });
    }

    function copyLink() {
        var url = $('#aiShareUrl').val() || '';
        if (!url) { return; }
        if (window.AiTaskUI && typeof window.AiTaskUI.copyText === 'function') {
            window.AiTaskUI.copyText(url);
            toast('已复制链接', 'ok');
            return;
        }
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(url).then(function () { toast('已复制链接', 'ok'); })
                .catch(function () { toast('复制失败，请手动选中复制', 'warn'); });
        } else {
            toast('浏览器不支持自动复制，请手动选中复制', 'warn');
        }
    }

    function revokeLink() {
        var rid = state.rid;
        if (!rid) { return; }
        if (!window.confirm('撤销后这条链接立即失效，已打开的人刷新也看不到了。确认撤销？')) { return; }
        $.ajax({
            url: REPORTS + '/' + encodeURIComponent(rid) + '/share/revoke',
            method: 'POST',
            headers: { 'X-CSRFToken': csrf() }
        }).done(function (res) {
            if (!res || res.success === false) { toast((res && res.message) || '撤销失败', 'err'); return; }
            $('#aiShareUrl').val('');
            $('#aiShareStat').text('');
            showSetup(true);
            toast('链接已失效', 'ok');
        }).fail(function (x) { fail(x, '撤销失败'); });
    }

    $(function () {
        if (!modalEl()) { return; }

        // ai_studio.js 每次切换任务/生成完成都会触发：据此启用/禁用分享按钮
        $(document).on('ai:report-changed', function (ev, rid) {
            var has = !(rid === undefined || rid === null || rid === '');
            $('#aiShare').prop('disabled', !has)
                .attr('title', has ? '分享这份报告（生成链接）' : '报告生成后才能分享');
            if (!has) { state.rid = null; }
        });

        $('#aiShare').on('click', openModal);
        $('.ai-share-days').on('click', '.ai-share-day', function () {
            var days = Number($(this).attr('data-days'));
            if (!DAY_CHOICES[days]) { days = 30; }
            state.days = days;
            $('.ai-share-day').removeClass('active');
            $(this).addClass('active');
        });
        $('#aiShareCreate').on('click', createLink);
        $('#aiShareCopy').on('click', copyLink);
        $('#aiShareOpen').on('click', function () {
            var url = $('#aiShareUrl').val();
            if (url) { window.open(url, '_blank', 'noopener'); }
        });
        $('#aiShareRevoke').on('click', revokeLink);
    });
})();
