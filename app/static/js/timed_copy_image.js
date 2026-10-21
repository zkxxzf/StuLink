/* StuLink v1.19.49.50 2026-10-20
   限时练分析页：把**某一科的表格**画成 PNG 下载（Canvas 自绘，无第三方依赖）。
   分析页现在一屏多科，所以按按钮的 data-table 定位表格，每科一个按钮。
   Copyright (c) 2026 zkxxzf. Apache License 2.0 */
(function () {
    'use strict';
    var FONT = '-apple-system, "Segoe UI", "Microsoft YaHei", sans-serif';
    var PAD = 28;
    var TITLE_H = 92;
    var HEAD_H = 40;
    var ROW_H = 34;
    var FOOT_H = 38;
    var FOOTER_H = 48;

    function textOf(cell) {
        return (cell.innerText || cell.textContent || '').replace(/\s+/g, ' ').trim();
    }

    function collect(table) {
        var head = [], rows = [], foot = [];
        // 2026-10-21 审计修复：表头优先取 thead；没有 thead 时（模板漏写会被浏览器
        // 塞进隐式 tbody）退回「第一个 tbody 的首行且单元格是 th」，否则 head 为空
        // → 列宽算不出来 → 导出的是只有标题的空图。
        var headRow = (table.tHead && table.tHead.rows[0]) || null;
        var bodyRows = [];
        [].forEach.call(table.tBodies, function (tb) {
            [].forEach.call(tb.rows, function (tr) { bodyRows.push(tr); });
        });
        if (!headRow && bodyRows.length) {
            var first = bodyRows[0];
            if (first.cells.length && first.cells[0].tagName === 'TH') {
                headRow = bodyRows.shift();
            }
        }
        if (headRow) {
            head = [].map.call(headRow.cells, textOf);
        }
        bodyRows.forEach(function (tr) {
            rows.push([].map.call(tr.cells, textOf));
        });
        if (table.tFoot && table.tFoot.rows[0]) {
            foot = [].map.call(table.tFoot.rows[0].cells, textOf);
        }
        return { head: head, rows: rows, foot: foot };
    }

    function measure(text, font) {
        var ctx = measure.ctx || (measure.ctx = document.createElement('canvas')
            .getContext('2d'));
        ctx.font = font;
        return ctx.measureText(text || '').width;
    }

    function columnWidths(data) {
        var widths = [];
        data.head.forEach(function (h, i) {
            var w = measure(h, '600 14px ' + FONT);
            [data.foot].concat(data.rows).forEach(function (row) {
                if (row && row[i]) { w = Math.max(w, measure(row[i], '14px ' + FONT)); }
            });
            var min = i === 0 ? 100 : 64;
            widths.push(Math.max(min, Math.min(150, Math.ceil(w) + 22)));
        });
        return widths;
    }

    function draw(table, meta) {
        var data = collect(table);
        var widths = columnWidths(data);
        var tableW = widths.reduce(function (a, b) { return a + b; }, 0);
        var W = Math.max(780, tableW + PAD * 2);
        var bodyH = Math.max(data.rows.length, 1) * ROW_H;
        var H = TITLE_H + HEAD_H + bodyH +
            (data.foot.length ? FOOT_H : 0) + FOOTER_H;
        var dpr = Math.max(2, window.devicePixelRatio || 1);
        var canvas = document.createElement('canvas');
        canvas.width = W * dpr;
        canvas.height = H * dpr;
        var ctx = canvas.getContext('2d');
        ctx.scale(dpr, dpr);

        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, W, H);

        ctx.textBaseline = 'middle';
        ctx.textAlign = 'left';
        ctx.fillStyle = '#0f172a';
        ctx.font = '600 22px ' + FONT;
        ctx.fillText(meta.title || '限时练单科分析', PAD, 42);
        ctx.fillStyle = '#64748b';
        ctx.font = '13px ' + FONT;
        ctx.fillText(meta.subtitle || '', PAD, 70);

        var left = (W - tableW) / 2;
        var y = TITLE_H;

        function drawRow(cells, height, options) {
            var opts = options || {};
            ctx.fillStyle = opts.fill || '#ffffff';
            ctx.fillRect(left, y, tableW, height);
            ctx.strokeStyle = '#e2e8f0';
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(left, y + height);
            ctx.lineTo(left + tableW, y + height);
            ctx.stroke();
            var x = left;
            cells.forEach(function (text, i) {
                if (i >= widths.length) { return; }
                var align = i === 0 ? 'left' : 'right';
                ctx.textAlign = align;
                ctx.font = (opts.bold ? '600 ' : '') + (opts.size || '14px') + ' ' + FONT;
                ctx.fillStyle = opts.color || '#1e293b';
                var tx = align === 'left' ? x + 12 : x + widths[i] - 12;
                ctx.fillText(text || '', tx, y + height / 2,
                    Math.max(10, widths[i] - 24));
                x += widths[i];
            });
            y += height;
        }

        drawRow(data.head, HEAD_H, { fill: '#eef2f7', bold: true, color: '#334155' });
        data.rows.forEach(function (row, idx) {
            drawRow(row, ROW_H, { fill: idx % 2 ? '#f8fafc' : '#ffffff' });
        });
        if (!data.rows.length) {
            ctx.fillStyle = '#94a3b8';
            ctx.font = '14px ' + FONT;
            ctx.textAlign = 'center';
            ctx.fillText('暂无数据', left + tableW / 2, y + ROW_H / 2);
            y += ROW_H;
        }
        if (data.foot.length) {
            drawRow(data.foot, FOOT_H, { fill: '#f1f5f9', bold: true });
        }

        ctx.textAlign = 'left';
        ctx.fillStyle = '#94a3b8';
        ctx.font = '11px ' + FONT;
        ctx.fillText('StuLink · 成绩管理（限时练）', PAD, H - 22);
        ctx.textAlign = 'right';
        var now = new Date();
        var stamp = now.getFullYear() + '-' +
            String(now.getMonth() + 1).padStart(2, '0') + '-' +
            String(now.getDate()).padStart(2, '0') + ' ' +
            String(now.getHours()).padStart(2, '0') + ':' +
            String(now.getMinutes()).padStart(2, '0');
        ctx.fillText(stamp, W - PAD, H - 22);
        return canvas;
    }

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

    function shoot(table, meta) {
        toBlob(draw(table, meta), function (blob) {
            if (!blob) { toast('生成图片失败', 'err'); return; }
            var url = URL.createObjectURL(blob);
            var a = document.createElement('a');
            a.href = url;
            a.download = (meta.filename || '限时练分析') + '.png';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
        });
    }

    [].forEach.call(document.querySelectorAll('[data-timed-shot]'), function (btn) {
        btn.addEventListener('click', function () {
            var table = document.getElementById(btn.getAttribute('data-table'));
            if (!table) { toast('找不到表格', 'err'); return; }
            shoot(table, {
                title: btn.getAttribute('data-title') || '限时练单科分析',
                subtitle: btn.getAttribute('data-sub') || '',
                filename: btn.getAttribute('data-file') || '限时练分析'
            });
        });
    });
})();
