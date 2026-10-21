/* StuLink v1.19.49.50 2026-10-21
   限时练分析页：把一张 HTML 表格画成离屏 Canvas（整科图片合成时复用）。
   用法：var t = window.timedTableCanvas(tableEl); // {canvas, width, height}
   尺寸均为 CSS 像素，canvas 内部已按 DPR 放大，直接 drawImage 即可。
   Copyright (c) 2026 zkxxzf. Apache License 2.0 */
(function () {
    'use strict';
    var FONT = '-apple-system, "Segoe UI", "Microsoft YaHei", sans-serif';
    var HEAD_H = 40;
    var ROW_H = 34;
    var FOOT_H = 38;
    var DPR = 2;

    function textOf(cell) {
        return (cell.innerText || cell.textContent || '')
            .replace(/\s+/g, ' ').trim();
    }

    /* 取表头 / 表体 / 合计行。
       表头优先取 thead；模板漏写 thead 时（浏览器会塞进隐式 tbody），
       退回「第一个 tbody 的首行且首个单元格是 th」，否则列宽算不出来。 */
    function collect(table) {
        var head = [], rows = [], foot = [];
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
        if (headRow) { head = [].map.call(headRow.cells, textOf); }
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

    /* 列宽 = 表头 / 各行文字最大宽度 + 内边距，首列至少 100，其它至少 64 */
    function columnWidths(data) {
        return data.head.map(function (h, i) {
            var w = measure(h, '600 14px ' + FONT);
            [data.foot].concat(data.rows).forEach(function (row) {
                if (row && row[i]) {
                    w = Math.max(w, measure(row[i], '14px ' + FONT));
                }
            });
            var min = i === 0 ? 100 : 64;
            return Math.max(min, Math.min(150, Math.ceil(w) + 22));
        });
    }

    /* 画一行，返回下一行的 y */
    function drawRow(ctx, cells, widths, totalW, y, height, opts) {
        var options = opts || {};
        ctx.fillStyle = options.fill || '#ffffff';
        ctx.fillRect(0, y, totalW, height);
        ctx.strokeStyle = '#e2e8f0';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(0, y + height);
        ctx.lineTo(totalW, y + height);
        ctx.stroke();
        ctx.font = (options.bold ? '600 ' : '') + '14px ' + FONT;
        ctx.fillStyle = options.color || '#1e293b';
        var x = 0;
        cells.forEach(function (text, i) {
            if (i >= widths.length) { return; }
            var align = i === 0 ? 'left' : 'right';
            ctx.textAlign = align;
            var tx = align === 'left' ? x + 12 : x + widths[i] - 12;
            ctx.fillText(text || '', tx, y + height / 2,
                Math.max(10, widths[i] - 24));
            x += widths[i];
        });
        return y + height;
    }

    function render(table) {
        var data = collect(table);
        var widths = columnWidths(data);
        var width = widths.reduce(function (a, b) { return a + b; }, 0);
        var rowsH = Math.max(data.rows.length, 1) * ROW_H;
        var height = HEAD_H + rowsH + (data.foot.length ? FOOT_H : 0);
        var canvas = document.createElement('canvas');
        canvas.width = Math.ceil(width * DPR);
        canvas.height = Math.ceil(height * DPR);
        var ctx = canvas.getContext('2d');
        ctx.scale(DPR, DPR);
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, width, height);
        ctx.textBaseline = 'middle';

        var y = 0;
        y = drawRow(ctx, data.head, widths, width, y, HEAD_H,
            { fill: '#eef2f7', bold: true, color: '#334155' });
        data.rows.forEach(function (row, idx) {
            y = drawRow(ctx, row, widths, width, y, ROW_H,
                { fill: idx % 2 ? '#f8fafc' : '#ffffff' });
        });
        if (!data.rows.length) {
            ctx.fillStyle = '#94a3b8';
            ctx.textAlign = 'center';
            ctx.font = '14px ' + FONT;
            ctx.fillText('暂无数据', width / 2, y + ROW_H / 2);
            y += ROW_H;
        }
        if (data.foot.length) {
            y = drawRow(ctx, data.foot, widths, width, y, FOOT_H,
                { fill: '#f1f5f9', bold: true });
        }
        return { canvas: canvas, width: width, height: height };
    }

    window.timedTableCanvas = render;
})();
