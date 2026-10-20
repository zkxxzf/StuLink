﻿/* StuLink v1.19.49.48 2026-10-20
   限时练分析页图表（懒加载版）：
   - data-type="stack"：各班各名次段的**人数堆积图**（互斥段，段和=班级人数）
   - data-type="bar"  ：各班**平均分**柱状图
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

    function axisBase(data) {
        return {
            xAxis: {
                type: 'category',
                data: data.classes || [],
                axisLabel: { interval: 0, rotate: 30, fontSize: 12 }
            }
        };
    }

    function buildOption(el, data) {
        var type = el.getAttribute('data-type') || 'bar';
        if (type === 'stack') {
            return {
                color: STACK_COLORS,
                animation: false,
                grid: { left: 6, right: 14, top: 20, bottom: 6, containLabel: true },
                tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
                // 13 段图例在窄图里会挤成两行、还拖慢布局：靠 tooltip 看名字即可
                legend: { show: false },
                xAxis: axisBase(data).xAxis,
                yAxis: { type: 'value', name: '人数', nameTextStyle: { fontSize: 12 },
                         axisLabel: { fontSize: 12 } },
                series: (data.series || []).map(function (s) {
                    return { name: s.name, type: 'bar', stack: 'total',
                             data: s.data, barMaxWidth: 26 };
                })
            };
        }
        return {
            animation: false,
            grid: { left: 6, right: 14, top: 34, bottom: 6, containLabel: true },
            tooltip: { trigger: 'axis' },
            xAxis: axisBase(data).xAxis,
            yAxis: { type: 'value', name: '均分', nameTextStyle: { fontSize: 12 },
                     axisLabel: { fontSize: 12 } },
            series: [{
                type: 'bar',
                data: data.avgs || [],
                barMaxWidth: 24,
                itemStyle: { color: '#4f7cff', borderRadius: [3, 3, 0, 0] },
                label: { show: true, position: 'top', fontSize: 12 }
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
