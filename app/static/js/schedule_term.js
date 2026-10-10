/* StuLink Task#27 学期周期维度前端
 * 独立文件，避免与 schedule.js 冲突。使用原生 JS + fetch，不依赖 jQuery 解析顺序。
 * 通过页面底部 <script id="scheduleData" type="application/json"> 的 pageType 分派：
 *   dates    —— 学期日期配置：实时预览各教学周对应日期区间
 *   calendar —— 学期校历：自动滚动定位到当前周
 *   compare  —— 跨学期对比：A/B 同选校验
 *   today    —— 今日课表：从 current-context 接口刷新学期/周次上下文条
 * 另外全局处理 #weekSelect 周次选择器联动（grade/class/teacher/today/overview 通用）。
 */
(function () {
    'use strict';

    function readCfg() {
        var el = document.getElementById('scheduleData');
        if (!el) { return {}; }
        try { return JSON.parse(el.textContent || '{}'); } catch (e) { return {}; }
    }

    function pad(n) { return n < 10 ? '0' + n : '' + n; }

    function fmtDate(d) {
        return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
    }

    function addDays(dateObj, days) {
        var d = new Date(dateObj.getTime());
        d.setDate(d.getDate() + days);
        return d;
    }

    /* ── 周次选择器联动：切换 week 查询参数并刷新 ───────────────── */
    function initWeekSwitcher() {
        var sel = document.getElementById('weekSelect');
        if (!sel) { return; }
        sel.addEventListener('change', function () {
            var url = new URL(window.location.href);
            var v = sel.value;
            if (v) { url.searchParams.set('week', v); }
            else { url.searchParams.delete('week'); }
            window.location.href = url.toString();
        });
    }

    /* ── 日期配置页：实时预览教学周对应日期 ─────────────────────── */
    function initDatesPreview() {
        var box = document.getElementById('datesPreview');
        if (!box) { return; }
        var startEl = document.getElementById('startDate');
        var weeksEl = document.getElementById('totalWeeks');
        var offEl = document.getElementById('weekOffset');
        if (!startEl) { return; }

        function render() {
            var raw = startEl.value;
            if (!raw) {
                box.innerHTML = '<div class="text-muted small text-center py-4">填写开学日期后自动预览各教学周对应的日期区间</div>';
                return;
            }
            var parts = raw.split('-');
            var start = new Date(parseInt(parts[0], 10), parseInt(parts[1], 10) - 1, parseInt(parts[2], 10));
            var total = parseInt((weeksEl && weeksEl.value) || '20', 10) || 20;
            var off = parseInt((offEl && offEl.value) || '0', 10) || 0;
            if (total > 30) { total = 30; }
            // 2026-10-10：周次按**自然周（周一~周日）**划分 —— 先退到开学日所在周的周一，
            // 开学非周一时第 1 周就是不完整周（开学日 ~ 该周周日），不再整体漂移。
            var wd = start.getDay(); // 0=周日 1=周一
            var anchor = addDays(start, -((wd === 0 ? 7 : wd) - 1));
            var tip = (wd !== 1)
                ? '<div class="alert alert-info py-1 small mb-2"><i class="bi bi-info-circle"></i> 开学第一天是星期' + '日一二三四五六'.charAt(wd) + '，第 1 周按自然周计算：到 ' + fmtDate(addDays(start, 7 - ((wd === 0 ? 7 : wd) - 1) - 1)) + '（周日）结束，第 2 周从下周一（' + fmtDate(addDays(anchor, 7)) + '）开始。</div>'
                : '';
            var today = fmtDate(new Date());
            var html = [tip, '<table class="table table-sm mb-0"><thead><tr><th style="width:70px">周次</th><th>日期区间（周一 ~ 周日）</th></tr></thead><tbody>'];
            for (var w = 1; w <= total; w++) {
                var rawMonday = addDays(anchor, (w - 1 + off) * 7);
                // 第 1 周从开学日算起（开学前的几天还没开始上课）
                var monday = (w === 1 && rawMonday < start) ? start : rawMonday;
                var sunday = addDays(rawMonday, 6);
                var isCur = (today >= fmtDate(monday) && today <= fmtDate(sunday));
                html.push('<tr class="' + (isCur ? 'table-success' : '') + '">');
                html.push('<td class="fw-bold">第 ' + w + ' 周' + (isCur ? ' <span class="badge bg-success">本周</span>' : '') + '</td>');
                html.push('<td class="small text-muted">' + fmtDate(monday) + ' ~ ' + fmtDate(sunday) + '</td>');
                html.push('</tr>');
            }
            html.push('</tbody></table>');
            box.innerHTML = html.join('');
        }

        startEl.addEventListener('change', render);
        if (weeksEl) { weeksEl.addEventListener('input', render); }
        if (offEl) { offEl.addEventListener('input', render); }
        render();
    }

    /* ── 校历页：滚动定位到当前周 ───────────────────────────────── */
    function initCalendar() {
        var row = document.querySelector('.cal-row-current');
        if (row && row.scrollIntoView) {
            try { row.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
            catch (e) { row.scrollIntoView(); }
        }
    }

    /* ── 对比页：A/B 同选校验 ───────────────────────────────────── */
    function initCompare() {
        var form = document.getElementById('compareForm');
        if (!form) { return; }
        form.addEventListener('submit', function (ev) {
            var a = form.querySelector('[name="sid_a"]');
            var b = form.querySelector('[name="sid_b"]');
            if (a && b && a.value && b.value && a.value === b.value) {
                ev.preventDefault();
                window.alert('请选择两个不同的学期进行对比。');
            }
        });
    }

    function boot() {
        var cfg = readCfg();
        initWeekSwitcher();
        switch (cfg.pageType) {
            case 'dates': initDatesPreview(); break;
            case 'calendar': initCalendar(); break;
            case 'compare': initCompare(); break;
            default: break;
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();
