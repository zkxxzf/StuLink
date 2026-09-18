# StuLink v1.13.1 2026-09-15
# 成绩汇报区交付物导出：把前端表格数据渲染为 PowerPoint（python-pptx）
# 前端 _rpExportPptx 收集每张表的单元格文本与颜色（rgb(r,g,b) 内联样式解析后传入），
# 本模块按「一表一页、超行数自动分页」生成 16:9 幻灯片，表头绿底、隔行浅底、红绿字保留。
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import io
import re

from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.oxml.ns import qn

# 幻灯片尺寸（16:9）
SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)
MARGIN = Inches(0.3)
# 每页最多数据行（表头行不计；超过则自动续页并重复表头）
MAX_ROWS = 26
# 颜色常量（与网页端 PPT 风格一致）
BLUE = RGBColor(0x2B, 0x5F, 0x9E)
GREEN_HDR = RGBColor(0x70, 0xAD, 0x47)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
ALT_BG = RGBColor(0xF3, 0xF5, 0xF9)
DEFAULT_TXT = RGBColor(0x22, 0x22, 0x22)

_RGB_RE = re.compile(r'rgba?\((\d+),(\d+),(\d+)')


def _parse_color(css, default=None):
    """'rgb(192,0,0)' / 'rgba(255,0,0,0.5)' → RGBColor；transparent/解析失败返回 default"""
    if not css or 'transparent' in css:
        return default
    m = _RGB_RE.search(css.replace(' ', ''))
    if not m:
        return default
    try:
        return RGBColor(*(int(g) for g in m.groups()))
    except ValueError:
        return default


def _cell_fill(cell, color):
    """单元格底色（python-pptx 无高级 API，直接写 XML solidFill）"""
    if color is None:
        return
    tcPr = cell._tc.get_or_add_tcPr()
    for old in tcPr.findall(qn('a:solidFill')):
        tcPr.remove(old)
    fill = tcPr.makeelement(qn('a:solidFill'), {})
    srgb = tcPr.makeelement(qn('a:srgbClr'), {'val': '%02X%02X%02X' % (color[0], color[1], color[2])})
    fill.append(srgb)
    tcPr.insert(0, fill)


def _set_border(cell, width_pt=1.25):
    """四边细蓝框，贴近网页表格观感"""
    tcPr = cell._tc.get_or_add_tcPr()
    elems = []
    for side in ('a:lnL', 'a:lnR', 'a:lnT', 'a:lnB'):
        e = tcPr.makeelement(qn(side), {'w': str(Emu(Pt(width_pt)))})
        fill = tcPr.makeelement(qn('a:solidFill'), {})
        srgb = tcPr.makeelement(qn('a:srgbClr'), {'val': '%02X%02X%02X' % (BLUE[0], BLUE[1], BLUE[2])})
        fill.append(srgb)
        e.append(fill)
        elems.append(e)
    for e in reversed(elems):   # lnL/lnR/lnT/lnB 需插到 tcPr 最前且保持顺序
        tcPr.insert(0, e)


def _add_slide(prs, title, sub, header, rows):
    """一页：标题 + 一张表。rows 为已按 MAX_ROWS 截断的数据行。"""
    slide = prs.slides.add_slide(prs.slide_layouts[6])   # 空白版式
    tb = slide.shapes.add_textbox(MARGIN, Inches(0.12), SLIDE_W - MARGIN * 2, Inches(0.62))
    tf = tb.text_frame
    tf.text = title
    p = tf.paragraphs[0]
    p.font.size = Pt(20)
    p.font.bold = True
    p.font.color.rgb = RGBColor(0x1F, 0x4E, 0x79)
    if sub:
        run = p.add_run()
        run.text = '　' + sub
        run.font.size = Pt(12)
        run.font.bold = False
        run.font.color.rgb = RGBColor(0x88, 0x88, 0x88)

    n_cols = max(len(header), 1)
    n_rows = len(rows) + 1
    tbl_w = SLIDE_W - MARGIN * 2
    tbl_h = min(SLIDE_H - Inches(0.9), Inches(0.34) * n_rows)
    shape = slide.shapes.add_table(n_rows, n_cols, MARGIN, Inches(0.82), tbl_w, tbl_h)
    table = shape.table
    # 列宽均分（python-pptx 需要逐列设置）
    for c in range(n_cols):
        table.columns[c].width = Emu(int(tbl_w / n_cols))

    def fill_row(r, cells, is_header):
        for c in range(n_cols):
            cell = table.cell(r, c)
            data = cells[c] if c < len(cells) else {}
            cell.text = str(data.get('text', ''))
            para = cell.text_frame.paragraphs[0]
            para.alignment = PP_ALIGN.CENTER
            para.font.size = Pt(11 if is_header else 10)
            para.font.bold = bool(data.get('bold')) or is_header
            color = _parse_color(data.get('color'))
            para.font.color.rgb = color or (WHITE if is_header else DEFAULT_TXT)
            bg = GREEN_HDR if is_header else _parse_color(data.get('bg'))
            _cell_fill(cell, bg)
            _set_border(cell)
            cell.margin_top = cell.margin_bottom = Pt(2)

    fill_row(0, header, True)
    for i, row in enumerate(rows):
        fill_row(i + 1, row, False)


def build_pptx(tables):
    """tables: [{title, sub, rows: [[{text,color,bg,bold}]]}] → pptx 字节流"""
    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H
    for t in tables:
        rows = t.get('rows') or []
        if not rows:
            continue
        header = rows[0]
        body = rows[1:]
        title = (t.get('title') or '成绩汇报')[:80]
        sub = (t.get('sub') or '')[:120]
        if len(body) <= MAX_ROWS:
            _add_slide(prs, title, sub, header, body)
        else:
            for part, start in enumerate(range(0, len(body), MAX_ROWS)):
                chunk = body[start:start + MAX_ROWS]
                _add_slide(prs, f'{title}（{part + 1}）', sub, header, chunk)
    buf = io.BytesIO()
    prs.save(buf)
    buf.seek(0)
    return buf.read()
