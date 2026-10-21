﻿﻿﻿﻿﻿/* StuLink v1.19.49.52 2026-10-20
   限时练分析页图表（懒加载版）：
   - data-type="stack"：各班各名次段的**人数堆积图**（互斥段，段和=班级人数）
   - data-type="bar"  ：各班**去差均分**柱状图（后端已按去差口径算好，见
     timed_analysis.subject_analysis）
   性能：一页有 9 科 × 2 张图 = 18 个 ECharts 实例，一次性初始化会明显卡顿，
   所以用 IntersectionObserver **滚到可视区才 init**（首屏通常只有 1~2 张）。
   Copyright (c) 2026 zkxxzf. Apache License 2.0 */
(function () {
    'use strict';
    if (!window.echarts) { return; }

    var STACK_COLORS = ['#2563eb', '#3b82f6', '#60a5fa', '#7dd3fc', '#38bdf8',
        '#22d3ee', '#2dd4bf', '#4ade80', '#a3e635', '#facc15',
        '#fb923c', '#f87171', '#cbd5e1'];
    var charts = [];

    // 2026-10-21 审计：ECharts 默认文字是深灰（#333），深色主题下面板是
    // #172033，对比度约 1.5:1 几乎看不见。统一从 --sl-* 变量取色，
    // 与 grades.js / student_query.js 的做法一致。
    function cssVar(name, fallback) {
        var v = getComputedStyle(document.documentElement)
            .getPropertyValue(name).trim();
        return v || fallback;
    }

    function axisColors() {
        return {
            text: cssVar('--sl-text', '#1e293b'),
            muted: cssVar('--sl-text-muted', '#64748b'),
            border: cssVar('--sl-border', '#e2e8f0')
        };
    }

    function axisBase(data, c) {
        return {
            xAxis: {
                type: 'category',
                data: data.classes || [],
                axisLabel: { interval: 0, rotate: 30, fontSize: 12,
                             color: c.muted },
                axisLine: { lineStyle: { color: c.border } }
            }
        };
    }

    function buildOption(el, data) {
        var type = el.getAttribute('data-type') || 'bar';
        var c = axisColors();
        if (type === 'stack') {
            return {
                color: STACK_COLORS,
                animation: false,
                textStyle: { color: c.text },
                // top 20 会把顶部「人数」轴名切掉，留 30
                grid: { left: 6, right: 14, top: 30, bottom: 6, containLabel: true },
                tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
                // 13 段图例在窄图里会挤成两行、还拖慢布局：靠 tooltip 看名字即可
                legend: { show: false },
                xAxis: axisBase(data, c).xAxis,
                yAxis: { type: 'value', name: '人数',
                         nameTextStyle: { fontSize: 12, color: c.muted },
                         axisLabel: { fontSize: 12, color: c.muted },
                         splitLine: { lineStyle: { color: c.border } } },
                series: (data.series || []).map(function (s) {
                    return { name: s.name, type: 'bar', stack: 'total',
                             data: s.data, barMaxWidth: 26 };
                })
            };
        }
        return {
            animation: false,
            textStyle: { color: c.text },
            grid: { left: 6, right: 14, top: 34, bottom: 6, containLabel: true },
            tooltip: { trigger: 'axis' },
            xAxis: axisBase(data, c).xAxis,
            yAxis: { type: 'value', name: '去差均分',
                     nameTextStyle: { fontSize: 12, color: c.muted },
                     axisLabel: { fontSize: 12, color: c.muted },
                     splitLine: { lineStyle: { color: c.border } } },
            series: [{
                type: 'bar',
                data: data.avgs || [],
                barMaxWidth: 24,
                itemStyle: { color: cssVar('--sl-chart-info', '#4f7cff'),
                             borderRadius: [3, 3, 0, 0] },
                label: { show: true, position: 'top', fontSize: 12,
                         color: c.text }
            }]
        };
    }

    function init(el) {
        if (el.getAttribute('data-ready')) { return; }
        el.setAttribute('data-ready', '1');
        var node = document.getElementById(el.getAttribute('data-chart'));
        if (!node) { return; }
        var data;
        try {
            data = JSON.parse(node.textContent);
        } catch (e) {
            return;
        }
        var chart = window.echarts.init(el, null, { renderer: 'canvas' });
        chart.setOption(buildOption(el, data));
        charts.push(chart);
    }

    var nodes = [].slice.call(
        document.querySelectorAll('.timed-chart[data-chart]'));

    if ('IntersectionObserver' in window) {
        var io = new IntersectionObserver(function (entries) {
            entries.forEach(function (en) {
                if (en.isIntersecting) {
                    io.unobserve(en.target);
                    init(en.target);
                }
            });
        }, { rootMargin: '300px 0px' });
        nodes.forEach(function (el) { io.observe(el); });
    } else {
        nodes.forEach(init);
    }

    var timer = null;
    window.addEventListener('resize', function () {
        if (timer) { clearTimeout(timer); }
        timer = setTimeout(function () {
            charts.forEach(function (c) { c.resize(); });
        }, 150);
    });
})();
