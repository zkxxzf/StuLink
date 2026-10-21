﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿/* StuLink v1.19.62.64 2026-10-20
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
    // 导出图片固定白底，所以配色也要固定成浅色（深色主题下 --sl-* 是浅字，
    // 直接导出会变成白底白字）。
    var LIGHT = { text: '#1e293b', muted: '#64748b',
                  border: '#e2e8f0', info: '#4f7cff' };
    var charts = [];

    // 2026-10-21 审计：ECharts 默认文字是深灰（#333），深色主题下面板是
    // #172033，对比度约 1.5:1 几乎看不见。统一从 --sl-* 变量取色，
    // 与 grades.js / student_query.js 的做法一致。
    function cssVar(name, fallback) {
        var v = getComputedStyle(document.documentElement)
            .getPropertyValue(name).trim();
        return v || fallback;
    }

    function axisColors(forceLight) {
        if (forceLight) { return LIGHT; }
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

    /* 每根柱（每个班）的总人数：各段相加，用来判断「这一段够不够高」 */
    function stackTotals(data) {
        var series = data.series || [];
        return (data.classes || []).map(function (_, ci) {
            return series.reduce(function (sum, s) {
                return sum + ((s.data && s.data[ci]) || 0);
            }, 0);
        });
    }

    /* 段内数字的颜色：亮底（黄 / 浅蓝 / 浅灰）用深字，深底用白字。
       按亮度取，比写死白字稳 —— 配色表一改也不会变成「白字压白底」。 */
    function inkOn(hex) {
        var m = /^#([0-9a-f]{6})$/i.exec(String(hex || ''));
        if (!m) { return '#fff'; }
        var v = parseInt(m[1], 16);
        var r = (v >> 16) & 255, g = (v >> 8) & 255, b = v & 255;
        return (r * 0.299 + g * 0.587 + b * 0.114) > 150 ? '#1e293b' : '#fff';
    }

    /* 2026-10-21 用户口径：堆积图里也要看得到数字，不能只能悬浮。
       图只有 250px 宽、段又多（前10线…全员 + 未入层），全标会互相压字：
       · 本段人数不到最高柱的 3%（且不足 2 人）就不标，精确值看 tooltip；
       · 字号 10（轴标签 12），窄柱里两位数字刚好放得下。 */
    function stackSeries(data, minSeg) {
        return (data.series || []).map(function (s, i) {
            return {
                name: s.name, type: 'bar', stack: 'total',
                data: s.data, barMaxWidth: 26,
                label: {
                    show: true, position: 'inside', fontSize: 10,
                    color: inkOn(STACK_COLORS[i % STACK_COLORS.length]),
                    formatter: function (p) {
                        return (p.value >= minSeg) ? p.value : '';
                    }
                }
            };
        });
    }

    function stackOption(data, c) {
        var totals = stackTotals(data);
        var maxTotal = totals.reduce(function (m, v) {
            return Math.max(m, v);
        }, 0);
        var minSeg = Math.max(2, Math.ceil(maxTotal * 0.03));
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
            series: stackSeries(data, minSeg)
        };
    }

    function buildOption(el, data, forceLight) {
        var type = el.getAttribute('data-type') || 'bar';
        var c = axisColors(forceLight);
        if (type === 'stack') { return stackOption(data, c); }
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
                itemStyle: { color: forceLight ? LIGHT.info
                                                 : cssVar('--sl-chart-info',
                                                          '#4f7cff'),
                             borderRadius: [3, 3, 0, 0] },
                label: { show: true, position: 'top', fontSize: 12,
                         color: c.text }
            }]
        };
    }

    function init(el) {
        if (el.__timedChart) { return el.__timedChart; }
        var node = document.getElementById(el.getAttribute('data-chart'));
        if (!node) { return null; }
        var data;
        try {
            data = JSON.parse(node.textContent);
        } catch (e) {
            return null;
        }
        var chart = window.echarts.init(el, null, { renderer: 'canvas' });
        chart.setOption(buildOption(el, data));
        el.__timedData = data;
        el.__timedChart = chart;
        charts.push(chart);
        return chart;
    }

    /* 导出白底 PNG 给「整科一张图」用（不碰页面上那张图）：
       ① 浅色配色：白底要深字，深色主题下直接截屏会白底白字；
       ② 420×520：12px 轴标签在 250px 窄栏里会挤成一片。
       做法是建一个屏幕外的临时实例，出图后立刻 dispose，页面完全无感。 */
    var EXPORT_W = 420;
    var EXPORT_H = 520;

    function dataOf(el) {
        if (el.__timedData) { return el.__timedData; }
        var node = document.getElementById(el.getAttribute('data-chart'));
        if (!node) { return null; }
        try {
            return JSON.parse(node.textContent);
        } catch (e) {
            return null;
        }
    }

    function exportURL(el) {
        var data = dataOf(el);
        if (!data) { return ''; }
        var box = document.createElement('div');
        box.style.cssText = 'position:absolute;left:-99999px;top:0;width:' +
            EXPORT_W + 'px;height:' + EXPORT_H + 'px;';
        document.body.appendChild(box);
        var chart = window.echarts.init(box, null, { renderer: 'canvas' });
        chart.setOption(buildOption(el, data, true));
        var url = chart.getDataURL({ type: 'png', pixelRatio: 2,
                                     backgroundColor: '#ffffff' });
        chart.dispose();
        document.body.removeChild(box);
        return url;
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

    // 供「整科一张图」调用：exportURL() 出白底 PNG（临时实例，不动本页图表）
    window.timedAnalysisCharts = { exportURL: exportURL };
})();
