/* ============================================================
 * 实时课表 · 查课核对（v1.16.0）
 * 节次切换 / 日期年级切换 / AJAX 局部刷新 / 当前节次高亮 / 实时时钟 / 打印
 * 依赖 jQuery（在 {% block extra_js %} 中加载，jQuery 已就绪）
 * ============================================================ */
(function () {
    'use strict';

    var cfg = null;
    var periods = [];          // [{number,name,start,end}]
    var timerClock = null;
    var timerNow = null;
    var loading = false;

    /* ---------- 工具 ---------- */
    function esc(s) {
        if (s === null || s === undefined) { return ''; }
        return String(s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function pad(n) { return (n < 10 ? '0' : '') + n; }
    function hhmm(d) { return pad(d.getHours()) + ':' + pad(d.getMinutes()); }
    function todayISO() {
        var d = new Date();
        return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
    }
    // 依据起止时间计算当前节次号；无匹配返回 null
    function calcCurrentPeriod(now) {
        var hm = hhmm(now);
        var started = null;
        for (var i = 0; i < periods.length; i++) {
            var p = periods[i];
            if (p.start && p.end) {
                if (p.start <= hm && hm <= p.end) { return p.number; }
                if (p.start <= hm) { started = p.number; }
            }
        }
        return started;
    }

    /* ---------- 渲染 ---------- */
    function renderRow(r) {
        var empty = !r.subject;
        var cls = empty ? ' class="row-empty"' : '';
        var teacher = r.subject ? esc(r.teacher_name) : '—';
        var room = esc(r.room) || '—';
        var note = r.is_temp_swap ? '<span class="badge badge-swap">调课</span>' : '';
        return '<tr' + cls + '>'
            + '<td class="fw-semibold">' + esc(r.class_name) + '</td>'
            + '<td>' + (empty ? '无课' : esc(r.subject)) + '</td>'
            + '<td>' + teacher + '</td>'
            + '<td>' + room + '</td>'
            + '<td>' + note + '</td>'
            + '</tr>';
    }

    function renderGrades(grades) {
        var keys = Object.keys(grades || {});
        if (!keys.length) {
            return '<div class="card"><div class="card-body text-center text-muted py-5">'
                + '<i class="bi bi-calendar-x" style="font-size:38px"></i>'
                + '<p class="mt-2 mb-0">该节次暂无课表数据</p></div></div>';
        }
        var html = '';
        keys.forEach(function (g) {
            var rows = grades[g] || [];
            html += '<div class="grade-block">'
                + '<div class="grade-title"><i class="bi bi-mortarboard"></i> ' + esc(g)
                + ' <span class="cnt">共 ' + rows.length + ' 个班</span></div>'
                + '<div class="table-responsive">'
                + '<table class="table table-sm table-bordered align-middle mb-0 live-table">'
                + '<thead class="table-light"><tr>'
                + '<th style="width:90px">班级</th><th>学科</th><th style="width:130px">授课教师</th>'
                + '<th style="width:150px">教室</th><th style="width:90px">备注</th>'
                + '</tr></thead><tbody>';
            rows.forEach(function (r) { html += renderRow(r); });
            html += '</tbody></table></div></div>';
        });
        return html;
    }

    function updateHeadMeta(d) {
        // 顶部日期/星期/节次文案
        var sub = $('.live-head .small').first();
        if (sub.length && d) {
            var txt = sub.text();
            // 保留学期名，替换日期与节次段
            var termName = (txt.split(' · ')[0] || '').trim();
            var wk = d.weekday_text ? (' ' + d.weekday_text) : '';
            sub.text(termName + ' · ' + d.date + wk + ' · 第 ' + d.period + ' 节');
        }
    }

    /* ---------- 数据加载 ---------- */
    function load(keepPeriod) {
        if (!cfg || loading) { return; }
        loading = true;
        var params = {
            date: $('#datePick').val() || todayISO(),
            period: keepPeriod || $('.period-btn.active').data('period') || cfg.currentPeriod || 1
        };
        var grade = $('#gradePick').val();
        if (grade) { params.grade = grade; }

        $('#liveContainer').css('opacity', 0.45);
        $.getJSON(cfg.apiUrl, params).done(function (res) {
            if (res && res.success && res.data) {
                $('#liveContainer').html(renderGrades(res.data.grades));
                updateHeadMeta(res.data);
                // 同步激活态到实际返回节次
                setActivePeriod(res.data.period);
            } else {
                $('#liveContainer').html('<div class="alert alert-warning m-3">'
                    + esc((res && res.message) || '加载失败') + '</div>');
            }
        }).fail(function () {
            $('#liveContainer').html('<div class="alert alert-danger m-3">加载实时课表失败，请重试</div>');
        }).always(function () {
            $('#liveContainer').css('opacity', 1);
            loading = false;
        });
    }

    function setActivePeriod(pn) {
        $('.period-btn').removeClass('active');
        $('.period-btn[data-period="' + pn + '"]').addClass('active');
    }

    /* ---------- 时钟与当前节次高亮 ---------- */
    function tickClock() {
        var now = new Date();
        $('#clockHM').text(hhmm(now));
        var cp = calcCurrentPeriod(now);
        $('#clockPeriod').text(cp ? ('· 第' + cp + '节') : '· 课间');
        // 当前节次按钮高亮（is-now）
        $('.period-btn').removeClass('is-now');
        if (cp) { $('.period-btn[data-period="' + cp + '"]').addClass('is-now'); }
    }

    /* ---------- 事件绑定 ---------- */
    function bindEvents() {
        $('#periodBar').on('click', '.period-btn', function () {
            var pn = $(this).data('period');
            if ($(this).hasClass('active')) { return; }
            setActivePeriod(pn);
            load(pn);
        });
        $('#gradePick').on('change', function () { load(); });
        $('#datePick').on('change', function () { load(); });
        $('#btnToday').on('click', function () {
            $('#datePick').val(todayISO());
            load();
        });
        $('#btnPrint').on('click', function () { window.print(); });
    }

    /* ---------- 初始化 ---------- */
    $(function () {
        var raw = $('#liveData').text();
        if (!raw) { return; }
        try { cfg = JSON.parse(raw); } catch (e) { cfg = null; }
        if (!cfg) { return; }
        periods = cfg.periods || [];

        bindEvents();
        tickClock();
        // 实时时钟：每 30s 更新时间与当前节次高亮
        timerClock = setInterval(tickClock, 30000);
    });

    $(window).on('beforeunload', function () {
        if (timerClock) { clearInterval(timerClock); }
        if (timerNow) { clearInterval(timerNow); }
    });
})();
