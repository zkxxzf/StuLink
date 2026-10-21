/* StuLink v1.19.50.59 2026-10-21
   限时练分析页：**一科一张图**——人数 + 去差均分，两组「图 + 表」合成一张 PNG。
   2026-10-21 用户口径：一科就是一个整体，不再按单张表各出一个「下载图片」；
   卡片头部两个动作共用同一张图：「复制图片」（剪贴板，可直接粘贴给微信/Word）
   与「下载图片」（存 PNG）。
   依赖：timed_tbl_canvas.js（表格转 Canvas）、timed_analysis_charts.js（图表）。
   Copyright (c) 2026 zkxxzf. Apache License 2.0 */
(function () {
    'use strict';
    var FONT = '-apple-system, "Segoe UI", "Microsoft YaHei", sans-serif';
    var PAD = 28;
    var TITLE_H = 92;
    var LABEL_H = 34;
    var GAP = 22;
    var SECTION_GAP = 24;
    var FOOTER_H = 44;
    // 与 timed_analysis_charts.js 的导出尺寸一致：按 1:1 贴图，字不会被缩小
    var CHART_W = 420;
    var DPR = 2;

    function toast(msg, tone) {
        if (typeof window.stuToast === 'function') { window.stuToast(msg, tone); }
        else { window.alert(msg); }
    }

    function toBlob(canvas, cb) {
        if (canvas.toBlob) { canvas.toBlob(cb, 'image/png'); return; }
        var url = canvas.toDataURL('image/png');
        var bin = atob(url.split(',')[1]);
        var arr = new Uint8Array(bin.length);
        for (var i = 0; i < bin.length; i++) { arr[i] = bin.charCodeAt(i); }
        cb(new Blob([arr], { type: 'image/png' }));
    }

    function download(canvas, name) {
        toBlob(canvas, function (blob) {
            if (!blob) { toast('生成图片失败', 'err'); return; }
            var url = URL.createObjectURL(blob);
            var a = document.createElement('a');
            a.href = url;
            a.download = (name || '限时练分析') + '.png';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
        });
    }

    /* 复制到剪贴板：需要安全上下文（https 或 localhost）+ ClipboardItem。
       局域网站点走 http 时浏览器直接不给接口，明确提示改用「下载图片」。 */
    function copyImage(canvas) {
        if (!navigator.clipboard || !window.ClipboardItem ||
                typeof ClipboardItem === 'undefined') {
            toast('当前浏览器/HTTP 环境不支持复制图片，请用「下载图片」', 'warn');
            return;
        }
        toBlob(canvas, function (blob) {
            if (!blob) { toast('生成图片失败', 'err'); return; }
            var item;
            try {
                item = new ClipboardItem({ 'image/png': blob });
            } catch (e) {
                toast('当前浏览器不支持复制图片，请用「下载图片」', 'warn');
                return;
            }
            navigator.clipboard.write([item]).then(function () {
                toast('已复制图片，可直接粘贴');
            }).catch(function () {
                toast('复制失败（浏览器未授权剪贴板），请用「下载图片」', 'err');
            });
        });
    }

    /* 取图表的白底 PNG（导出时图表会临时切成浅色配色，见 charts 的 exportURL） */
    function chartImage(el) {
        return new Promise(function (resolve) {
            if (!el) { resolve(null); return; }
            var api = window.timedAnalysisCharts;
            var url = api && api.exportURL ? api.exportURL(el) : '';
            if (!url) { resolve(null); return; }
            var img = new Image();
            img.onload = function () { resolve(img); };
            img.onerror = function () { resolve(null); };
            img.src = url;
        });
    }

    function placeholder(ctx, x, y, w, h) {
        ctx.fillStyle = '#f8fafc';
        ctx.fillRect(x, y, w, h);
        ctx.strokeStyle = '#e2e8f0';
        ctx.strokeRect(x + 0.5, y + 0.5, w - 1, h - 1);
        ctx.fillStyle = '#94a3b8';
        ctx.font = '13px ' + FONT;
        ctx.textAlign = 'center';
        ctx.fillText('图表未加载', x + w / 2, y + h / 2);
    }

    function stamp() {
        var now = new Date();
        return now.getFullYear() + '-' +
            String(now.getMonth() + 1).padStart(2, '0') + '-' +
            String(now.getDate()).padStart(2, '0') + ' ' +
            String(now.getHours()).padStart(2, '0') + ':' +
            String(now.getMinutes()).padStart(2, '0');
    }

    /* 版式：标题 / [段名 + 图 + 表] × 2 / 页脚，整块居中 */
    function layout(meta, tables, imgs) {
        var sections = [
            { label: meta.cntLabel || '人数', table: tables[0], img: imgs[0] },
            { label: meta.avgLabel || '去差均分',
              table: tables[1], img: imgs[1] }
        ];
        var tableW = Math.max(tables[0].width, tables[1].width);
        var contentW = CHART_W + GAP + tableW;
        var W = Math.max(900, contentW + PAD * 2);
        var left = Math.round((W - contentW) / 2);
        var heights = sections.map(function (s) {
            var ch = s.img
                ? Math.round(CHART_W * s.img.height / s.img.width) : 200;
            return Math.max(ch, s.table.height);
        });
        var H = TITLE_H + LABEL_H * 2 + SECTION_GAP + FOOTER_H +
            heights[0] + heights[1];
        var canvas = document.createElement('canvas');
        canvas.width = Math.ceil(W * DPR);
        canvas.height = Math.ceil(H * DPR);
        var ctx = canvas.getContext('2d');
        ctx.scale(DPR, DPR);
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, W, H);
        ctx.textBaseline = 'middle';
        ctx.textAlign = 'left';

        ctx.fillStyle = '#0f172a';
        ctx.font = '600 22px ' + FONT;
        ctx.fillText(meta.title || '限时练单科分析', PAD, 42);
        ctx.fillStyle = '#64748b';
        ctx.font = '13px ' + FONT;
        ctx.fillText(meta.subtitle || '', PAD, 70, W - PAD * 2);

        var y = TITLE_H;
        sections.forEach(function (s, idx) {
            ctx.textAlign = 'left';
            ctx.fillStyle = '#334155';
            ctx.font = '600 15px ' + FONT;
            ctx.fillText(s.label, left, y + LABEL_H / 2);
            var labelW = ctx.measureText(s.label).width;
            ctx.strokeStyle = '#e2e8f0';
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(left + labelW + 12, y + LABEL_H / 2);
            ctx.lineTo(left + contentW, y + LABEL_H / 2);
            ctx.stroke();
            y += LABEL_H;

            if (s.img) {
                var chartH = heights[idx];
                ctx.fillStyle = '#ffffff';
                ctx.fillRect(left, y, CHART_W, chartH);
                ctx.drawImage(s.img, left, y, CHART_W, chartH);
                ctx.strokeStyle = '#e2e8f0';
                ctx.strokeRect(left + 0.5, y + 0.5, CHART_W - 1, chartH - 1);
            } else {
                placeholder(ctx, left, y, CHART_W, heights[idx]);
            }
            ctx.drawImage(s.table.canvas, left + CHART_W + GAP, y,
                s.table.width, s.table.height);
            y += heights[idx] + (idx === 0 ? SECTION_GAP : 0);
        });

        ctx.textAlign = 'left';
        ctx.fillStyle = '#94a3b8';
        ctx.font = '11px ' + FONT;
        ctx.fillText('StuLink · 成绩管理（限时练）', PAD, H - 20);
        ctx.textAlign = 'right';
        ctx.fillText(stamp(), W - PAD, H - 20);
        return canvas;
    }

    /* 合成一张整科图片；失败时 reject（调用方统一 toast） */
    function build(meta) {
        return new Promise(function (resolve, reject) {
            var card = document.getElementById(meta.getAttribute('data-card') || '');
            var cntTable = document.getElementById(
                meta.getAttribute('data-cnt-table') || '');
            var avgTable = document.getElementById(
                meta.getAttribute('data-avg-table') || '');
            if (!card || !cntTable || !avgTable || !window.timedTableCanvas) {
                reject(new Error('tables missing'));
                return;
            }
            var tables = [window.timedTableCanvas(cntTable),
                          window.timedTableCanvas(avgTable)];
            var charts = [
                card.querySelector('[data-shot-chart="cnt"]'),
                card.querySelector('[data-shot-chart="avg"]')
            ];
            Promise.all(charts.map(chartImage)).then(function (imgs) {
                resolve(layout({
                    title: meta.getAttribute('data-title') || '',
                    subtitle: meta.getAttribute('data-sub') || '',
                    cntLabel: meta.getAttribute('data-cnt-label'),
                    avgLabel: meta.getAttribute('data-avg-label')
                }, tables, imgs));
            }, reject);
        });
    }

    function run(meta, action) {
        var buttons = meta.querySelectorAll('button');
        [].forEach.call(buttons, function (b) { b.disabled = true; });
        build(meta).then(function (canvas) {
            if (action === 'copy') { copyImage(canvas); }
            else { download(canvas, meta.getAttribute('data-file')); }
        }).catch(function () {
            toast('表格还没准备好，请稍后重试', 'err');
        }).then(function () {
            [].forEach.call(buttons, function (b) { b.disabled = false; });
        });
    }

    [].forEach.call(document.querySelectorAll('[data-timed-action]'),
        function (btn) {
            btn.addEventListener('click', function () {
                var meta = btn.closest('[data-timed-meta]');
                if (!meta) { return; }
                run(meta, btn.getAttribute('data-timed-action'));
            });
        });
})();
