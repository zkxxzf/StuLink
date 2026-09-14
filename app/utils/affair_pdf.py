# v1.12.2 考务 A4 打印版 PDF 生成（reportlab，中文用内置 STSong-Light CID 字体）
#   - desk_cards_pdf：考试桌签小标签，一页 3 栏 × 15 行 = 45 张，裁开贴桌
#   - seating_list_sections：v1.12.2 分组版名单——一个班/一个考场从新的一页开始
# 目标：替代网页版桌签预览（1500 人渲染会卡死），浏览器 PDF 查看器可直接打印/另存
from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                Paragraph, PageBreak)

_FONT = 'STSong-Light'
_registered = False


def _ensure_font():
    global _registered
    if not _registered:
        pdfmetrics.registerFont(UnicodeCIDFont(_FONT))
        _registered = True


def desk_cards_pdf(students, room_loc_fn=None):
    """考试桌签 PDF：一页 A4 排 3 栏 × 15 行 = 45 张小标签（贴桌用），
    每张两行：① 表头行「学号/考号 | 姓名 | 班级 | 考场-座号」② 数据行。
    v1.12.2：标签之间留空白间隙，间隙中央画虚线裁切线，方便线下裁剪；
    一个考场从新的一页开始（超 45 人自动续页），页顶小字标注考场号便于分拣。
    返回 PDF 字节流 BytesIO。"""
    _ensure_font()
    from reportlab.pdfgen import canvas
    buf = BytesIO()
    W, H = A4
    c = canvas.Canvas(buf, pagesize=A4)

    M = 8 * mm                       # 页边距
    TOP = M + 6 * mm                 # 顶部多留 6mm 放考场号标注
    COLS, ROWS = 3, 15               # v1.12.2：一页 3 栏 × 15 行 = 45 张
    GAP_X, GAP_Y = 2.5 * mm, 2 * mm  # 标签间空白间隙（容纳裁切损耗）
    gw = 0.4                         # 网格线宽
    card_w = (W - 2 * M - (COLS - 1) * GAP_X) / COLS      # 单张宽 ≈ 63mm
    block_h = (H - TOP - M - (ROWS - 1) * GAP_Y) / ROWS   # 单张高 ≈ 16.5mm
    row_h = block_h / 2.0            # 表头/数据 两行等分
    # 卡内四列宽（学号/考号 最宽，容纳 10+ 位考号）
    col_w = [card_w * 0.38, card_w * 0.21, card_w * 0.16, card_w * 0.25]

    def cell_text(x, y, w, h, text, size, bold_gap=0):
        c.setFont(_FONT, size)
        c.drawCentredString(x + w / 2, y + h / 2 - size * 0.36 + bold_gap, str(text))

    def draw_card(ox, oy, s):
        """在 (ox, oy)=标签左下角 绘制一张两行桌签（实线边框，裁线在间隙虚线处）"""
        c.setStrokeColor(colors.black)
        c.setLineWidth(gw)
        rn = s.room_no or ''
        try:
            seat = '%02d' % int(s.seat_no) if s.seat_no not in (None, '') else ''
        except (TypeError, ValueError):
            seat = str(s.seat_no or '')
        vals = [s.exam_number or '', s.name or '', s.class_name or '',
                f"{rn}-{seat}" if rn or seat else '']
        # 整格边框 + 表头/数据分隔横线
        c.rect(ox, oy, card_w, block_h)
        y_data = oy + block_h - row_h          # 数据行底边 y
        c.line(ox, y_data, ox + card_w, y_data)
        # 竖向分隔线（整格两行都分隔）
        xx = ox
        for w in col_w[:-1]:
            xx += w
            c.line(xx, oy, xx, oy + block_h)
        # 表头文字（v1.12.2：45 张/页，8pt 为可读下限）
        heads = ['学号/考号', '姓名', '班级', '考场-座号']
        xx = ox
        for w, t in zip(col_w, heads):
            cell_text(xx, y_data, w, row_h, t, 8)
            xx += w
        # 数据文字（姓名略大）
        xx = ox
        for w, v, sz in zip(col_w, vals, (9, 10, 9, 9)):
            cell_text(xx, oy, w, row_h, v, sz)
            xx += w

    def cut_lines():
        """标签间隙中央的虚线裁切线：沿虚线下刀，两侧标签边框都保留"""
        c.saveState()
        c.setStrokeColor(colors.HexColor('#666666'))
        c.setLineWidth(0.3)
        c.setDash(2, 2)
        for ci in range(COLS - 1):                       # 栏间纵线
            x = M + (ci + 1) * card_w + ci * GAP_X + GAP_X / 2
            c.line(x, M, x, H - TOP)
        for ri in range(ROWS - 1):                       # 行间横线
            y = H - TOP - (ri + 1) * block_h - ri * GAP_Y - GAP_Y / 2
            c.line(M, y, W - M, y)
        c.restoreState()

    # v1.12.2：按考场分组，一个考场从新的一页开始；考场内按座号排序
    rooms = {}
    for s in students:
        rooms.setdefault(s.room_no or '未分配', []).append(s)
    for rn in sorted(rooms):
        chunk_all = sorted(rooms[rn], key=lambda s: s.seat_no or 0)
        for pi in range(0, len(chunk_all), COLS * ROWS):
            chunk = chunk_all[pi:pi + COLS * ROWS]
            cut_lines()
            # 页顶标注：考场号 + 位置（有 room_loc_fn 时）+ 续页序号
            c.setFont(_FONT, 8)
            c.setFillColor(colors.HexColor('#475569'))
            note = f'考场 {rn}'
            if room_loc_fn:
                loc = room_loc_fn(rn)
                if loc:
                    note += f'　{loc}'
            if len(chunk_all) > COLS * ROWS:
                note += f'　（{pi // (COLS * ROWS) + 1}/{(len(chunk_all) - 1) // (COLS * ROWS) + 1} 页）'
            c.drawString(M, H - M - 1, note)
            c.setFillColor(colors.black)
            for idx, s in enumerate(chunk):
                col, row = idx % COLS, idx // COLS      # 先行后列（与 Excel 一致）
                ox = M + col * (card_w + GAP_X)
                oy = H - TOP - (row + 1) * block_h - row * GAP_Y
                draw_card(ox, oy, s)
            c.showPage()
    c.save()
    buf.seek(0)
    return buf


def seating_list_sections(doc_title, headers, sections, widths_mm=None):
    """分组版名单 PDF：v1.12.2——一个班 / 一个考场从新的一页开始（页内表格自动分页）。

    sections: [(分组小标题, 行数据列表), ...]；doc_title 为文首大标题。
    返回 BytesIO。"""
    _ensure_font()
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=14 * mm, bottomMargin=14 * mm)
    styles = getSampleStyleSheet()
    title_st = ParagraphStyle('t2', parent=styles['Normal'], fontName=_FONT,
                              fontSize=14, alignment=TA_CENTER, spaceAfter=6)
    sec_st = ParagraphStyle('s2', parent=styles['Normal'], fontName=_FONT,
                            fontSize=11, spaceBefore=2, spaceAfter=4)
    cell_st = ParagraphStyle('c2', parent=styles['Normal'], fontName=_FONT,
                             fontSize=9, leading=12)
    story = [Paragraph(doc_title, title_st)]
    for i, (sec_title, rows) in enumerate(sections):
        if i:
            story.append(PageBreak())     # 每组从新的一页开始
        story.append(Paragraph(sec_title, sec_st))
        if not rows:
            story.append(Paragraph('（本组无学生）', cell_st))
            continue
        data = [[Paragraph(str(h), cell_st) for h in headers]]
        for r in rows:
            data.append([Paragraph(str(x) if x is not None else '', cell_st) for x in r])
        if widths_mm:
            col_w = [w * mm for w in widths_mm]
        else:
            col_w = [doc.width / len(headers)] * len(headers)
        tbl = Table(data, colWidths=col_w, repeatRows=1)
        tbl.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), _FONT),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f1f5f9')),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        story.append(tbl)
    doc.build(story)
    buf.seek(0)
    return buf
