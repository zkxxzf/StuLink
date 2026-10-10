/* 我的成绩页交互（2026-10-10）
   学生明细抽屉 + 同年级各班均分对比 + 历次考试走势（ECharts）。
   依赖：jQuery / bootstrap / ECharts —— 由模板在 base.html 之后经 extra_js 引入，
   脚本本身不假设加载顺序（顶层只注册事件，DOM 就绪后再初始化）。 */
(function () {
    'use strict';

    var DETAIL_URL = '/workbench/api/grade-detail';
    var TREND_URL = '/workbench/api/grade-trend';
    var charts = [];

    function txt(v) {
        return (v === null || v === undefined || v === '') ? '-' : v;
    }

    function esc(s) {
        return String(s === null || s === undefined ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function statCard(label, value, cls) {
        return '<div class="col-6 col-md-3"><div class="border rounded px-2 py-1 text-center">' +
            '<div class="small text-muted">' + label + '</div>' +
            '<div class="fw-bold ' + (cls || '') + '">' + value + '</div>' +
            '</div></div>';
    }

    // ── 学生明细抽屉 ───────────────────────────────────────────
    var drawerEl = document.getElementById('gradeDetailDrawer');
    var drawer = (drawerEl && window.bootstrap) ? new window.bootstrap.Offcanvas(drawerEl) : null;
    var peersChart = null;
    var pendingPeers = null;

    if (drawerEl) {
        // 抽屉动画结束后再画图，否则容器宽度为 0 导致图表挤成一条线
        drawerEl.addEventListener('shown.bs.offcanvas', function () {
            if (pendingPeers) {
                renderPeers(pendingPeers.peers, pendingPeers.gradeAvg);
                pendingPeers = null;
            }
            if (peersChart) { peersChart.resize(); }
        });
    }

    $(document).on('click', '.btn-grade-detail', function () {
        if (!drawer) { return; }
        var $btn = $(this);
        var peers = [];
        try { peers = JSON.parse(this.dataset.peers || '[]'); } catch (e) { peers = []; }
        pendingPeers = { peers: peers, gradeAvg: parseFloat($btn.data('grade-avg')) || null };

        $('#gdTitle').text($btn.data('exam-name') + ' · ' + $btn.data('class-full') + ' · ' + $btn.data('subject'));
        $('#gdSub').text('加载中…');
        $('#gdStats').html('');
        $('#gdRows').html('');
        $('#gdHint').text('');
        drawer.show();

        $.getJSON(DETAIL_URL, {
            exam_id: $btn.data('exam-id'),
            class_name: $btn.data('class'),
            subject: $btn.data('subject')
        }).done(function (res) {
            renderDetail(res);
        }).fail(function (xhr) {
            $('#gdSub').text('');
            var msg = (xhr.responseJSON && xhr.responseJSON.error) || '明细加载失败';
            $('#gdHint').html('<span class="text-danger"><i class="bi bi-exclamation-circle"></i> ' + esc(msg) + '</span>');
        });
    });

    function renderDetail(res) {
        var st = res.stats || {};
        $('#gdSub').text((res.exam_date ? res.exam_date + ' · ' : '') + '参考 ' + txt(st.count) + ' 人');
        $('#gdStats').html(
            statCard('平均分', txt(st.avg), 'text-primary') +
            statCard('最高分', txt(st.max), 'text-success') +
            statCard('最低分', txt(st.min), 'text-danger') +
            statCard('及格率', st.pass_rate === null || st.pass_rate === undefined ? '-' : st.pass_rate + '%')
        );

        var rows = res.rows || [];
        if (!rows.length) {
            $('#gdRows').html('<tr><td colspan="4" class="text-center text-muted py-3">暂无成绩记录</td></tr>');
            return;
        }
        var full = Number(res.full || 100);
        var html = '';
        rows.forEach(function (r) {
            var scoreCls = '';
            if (r.score !== null && r.score !== undefined) {
                if (r.score >= full * 0.85) { scoreCls = 'text-success fw-bold'; }
                else if (r.score < full * 0.6) { scoreCls = 'text-danger'; }
            }
            html += '<tr><td>' + txt(r.rank) + '</td>' +
                '<td>' + esc(r.student_name) + '</td>' +
                '<td class="text-muted small">' + esc(r.student_no) + '</td>' +
                '<td class="text-end ' + scoreCls + '">' + txt(r.score) + '</td></tr>';
        });
        $('#gdRows').html(html);
    }

    function renderPeers(peers, gradeAvg) {
        var dom = document.getElementById('gdPeers');
        if (!dom || !window.echarts) { return; }
        var $note = $('#gdPeersNote');
        if (!peers.length) {
            dom.innerHTML = '<div class="text-muted small text-center pt-5">暂无同年级对比数据</div>';
            $note.text('');
            return;
        }

        // 同年级班级很多时（部分学校一个年级上百个班）整图画不下，
        // 只保留：年级前列 5 个 + 本班及其前后各 3 名，并如实注明总数。
        var shown = peers;
        if (peers.length > 16) {
            var keep = {};
            var i;
            for (i = 0; i < Math.min(5, peers.length); i++) { keep[i] = 1; }
            peers.forEach(function (p, idx) {
                if (!p.mine) { return; }
                for (var k = Math.max(0, idx - 3);
                     k <= Math.min(peers.length - 1, idx + 3); k++) { keep[k] = 1; }
            });
            shown = peers.filter(function (p, idx) { return keep[idx]; });
            $note.text('（同年级共 ' + peers.length + ' 个班，仅显示前列与邻近班级）');
        } else {
            $note.text('');
        }

        if (peersChart && peersChart.getDom() !== dom) { peersChart.dispose(); peersChart = null; }
        if (!peersChart) {
            peersChart = window.echarts.init(dom);
            charts.push(peersChart);
        }
        peersChart.setOption({
            tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
            grid: { left: 8, right: 32, top: 8, bottom: 8, containLabel: true },
            xAxis: { type: 'value', name: '均分', nameTextStyle: { fontSize: 10 }, axisLabel: { fontSize: 10 } },
            yAxis: {
                type: 'category', inverse: true,
                data: shown.map(function (p) { return p.class_name; }),
                axisLabel: { fontSize: 10 }
            },
            series: [{
                type: 'bar', barMaxWidth: 14,
                data: shown.map(function (p) {
                    return { value: p.avg, itemStyle: { color: p.mine ? '#6366f1' : '#cbd5e1' } };
                }),
                label: { show: true, position: 'right', fontSize: 10, formatter: '{c}' },
                markLine: (gradeAvg === null || gradeAvg === undefined) ? undefined : {
                    silent: true, symbol: 'none',
                    lineStyle: { type: 'dashed', color: '#94a3b8' },
                    label: { formatter: '年级均值 ' + gradeAvg, fontSize: 10, position: 'insideEndTop' },
                    data: [{ xAxis: gradeAvg }]
                }
            }]
        });
        peersChart.resize();
    }

    // ── 历次考试走势 ───────────────────────────────────────────
    function shorten(s) { return (s && s.length > 9) ? s.slice(0, 9) + '…' : (s || ''); }

    function initTrends() {
        $('.trend-chart').each(function () {
            var dom = this;
            $.getJSON(TREND_URL, {
                class_name: dom.dataset.class,
                subject: dom.dataset.subject,
                grade: dom.dataset.grade,
                limit: 8
            }).done(function (res) {
                var pts = res.points || [];
                if (!window.echarts) { return; }
                if (pts.length < 2) {
                    dom.innerHTML = '<div class="text-muted small text-center pt-5">考试次数不足，暂无法展示走势</div>';
                    return;
                }
                var chart = window.echarts.init(dom);
                charts.push(chart);
                chart.setOption({
                    tooltip: { trigger: 'axis' },
                    legend: { top: 0, textStyle: { fontSize: 10 } },
                    grid: { left: 8, right: 8, top: 28, bottom: 4, containLabel: true },
                    xAxis: {
                        type: 'category',
                        data: pts.map(function (p) { return p.exam_name; }),
                        axisLabel: { fontSize: 10, formatter: shorten }
                    },
                    yAxis: [
                        { type: 'value', name: '均分', scale: true, nameTextStyle: { fontSize: 10 }, axisLabel: { fontSize: 10 } },
                        { type: 'value', name: '及格率%', min: 0, max: 100, nameTextStyle: { fontSize: 10 }, axisLabel: { fontSize: 10 } }
                    ],
                    series: [
                        {
                            name: '均分', type: 'line', smooth: true, symbolSize: 6,
                            data: pts.map(function (p) { return p.avg; }),
                            itemStyle: { color: '#6366f1' }, areaStyle: { opacity: 0.08 }
                        },
                        {
                            name: '及格率', type: 'line', smooth: true, symbolSize: 6, yAxisIndex: 1,
                            data: pts.map(function (p) { return p.pass_rate; }),
                            itemStyle: { color: '#f59e0b' }, lineStyle: { type: 'dashed' }
                        }
                    ]
                });
            }).fail(function () {
                dom.innerHTML = '<div class="text-muted small text-center pt-5">趋势数据加载失败</div>';
            });
        });
    }

    $(window).on('resize', function () {
        charts.forEach(function (c) { try { c.resize(); } catch (e) { /* 图表已销毁 */ } });
    });

    $(function () { initTrends(); });
})();
