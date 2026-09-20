<<<<<<< HEAD
# StuLink v1.13.2 2026-09-15
=======
# StuLink v1.9.3 2026-09-19
>>>>>>> 4934b4a0230dda5c541daf86b4b3dcc06612141c
# 成绩汇报区交付物导出：把前端表格数据渲染为 PowerPoint（python-pptx）
# 前端 _rpExportPptx 收集每张表的单元格文本与颜色（rgb(r,g,b) 内联样式解析后传入），
# 本模块按「一表一页、超行数自动分页」生成 16:9 幻灯片，表头绿底、数据行白底、细灰框。
#
# 母版继承方案（v1.13.2 最终版）：
#   使用 report_template_clean.pptx —— 由 scripts/make_pptx_clean_template.py 从
#   郑开学校原始母版 report_template.pptx 制备：删除了模板自带的 8 张示例 slide，
#   但完整保留 slideLayout（含 Logo/背景装饰图）、slideMaster、media、theme。
#   python-pptx 加载干净模板 → add_slide → save 全程走标准流程，
#   Content_Types / presentation.xml / 所有 rels 由 python-pptx 自动维护，
#   不会像手工 zipfile 重建那样产生悬空引用导致 Office/WPS 拒开。
#   （经实测：python-pptx load→add_slide→save 不会丢失 layout 上的装饰形状，
#    此前"装饰丢失"的判断源于模板路径写错导致走了无模板 fallback。）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import io
import os
import re

from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.oxml.ns import qn

# ===== 模板路径 =====
# services/report_export_service.py → 往上 3 级到 app/ → templates/pptx/
_APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
# 干净模板（无示例 slide，保留 layout/master/media）——导出时实际加载
_TPL_CLEAN = os.path.join(_APP_DIR, 'templates', 'pptx', 'report_template_clean.pptx')
# 原始母版（含示例 slide）——仅作制备源，不直接加载
_TPL_ORIG = os.path.join(_APP_DIR, 'templates', 'pptx', 'report_template.pptx')

# ===== 颜色常量（与网页端 PPT 干净版风格一致）=====
GREEN_HDR = RGBColor(0x70, 0xAD, 0x47)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
DEFAULT_TXT = RGBColor(0x22, 0x22, 0x22)
# v1.13.2 PPT 干净版：边框用淡灰 #D8D8D8，贴近 Excel 观感
BORDER_GRAY = RGBColor(0xD8, 0xD8, 0xD8)
# 前端可能漏过来的网页装饰性底色（交替底纹），后端兜底过滤
_DECOR_BGS = {RGBColor(0xF3, 0xF5, 0xF9)}

_RGB_RE = re.compile(r'rgba?\((\d+),(\d+),(\d+)')
# 每页最多数据行（表头不计；超过则续页）
_MAX_ROWS = 26


def _parse_color(css, default=None):
    """解析前端传来的 rgb(r,g,b) / rgba(r,g,b,a) 内联颜色串。"""
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
    """设置单元格底色；color=None 表示清除填充（白底）。"""
    tcPr = cell._tc.get_or_add_tcPr()
    for old in tcPr.findall(qn('a:solidFill')):
        tcPr.remove(old)
    if color is None:
        return
    fill = tcPr.makeelement(qn('a:solidFill'), {})
    srgb = tcPr.makeelement(qn('a:srgbClr'),
                            {'val': '%02X%02X%02X' % (color[0], color[1], color[2])})
    fill.append(srgb)
    tcPr.insert(0, fill)


def _set_border(cell, width_pt=0.5):
    """v1.13.2 PPT 干净版：0.5pt 淡灰框 #D8D8D8"""
    tcPr = cell._tc.get_or_add_tcPr()
    # 先清掉旧边框设置，避免重复
    for side in ('a:lnL', 'a:lnR', 'a:lnT', 'a:lnB'):
        for old in tcPr.findall(qn(side)):
            tcPr.remove(old)
    elems = []
    for side in ('a:lnL', 'a:lnR', 'a:lnT', 'a:lnB'):
        e = tcPr.makeelement(qn(side), {'w': str(Emu(Pt(width_pt)))})
        fill = tcPr.makeelement(qn('a:solidFill'), {})
        srgb = tcPr.makeelement(qn('a:srgbClr'),
                                {'val': '%02X%02X%02X' % (BORDER_GRAY[0],
                                                          BORDER_GRAY[1],
                                                          BORDER_GRAY[2])})
        fill.append(srgb)
        e.append(fill)
        elems.append(e)
    for e in reversed(elems):
        tcPr.insert(0, e)


def _add_slide(prs, layout, title, sub, header, rows):
    """一页：标题 + 副标题（灰色小字）+ 表格。继承 layout 的母版样式。"""
    slide = prs.slides.add_slide(layout)

    # 标题 & 副标题位置（留出母版 Logo / 页脚）
    # 幻灯片 13.33×7.5 in，页脚区域 top≈6.25 in
    tb = slide.shapes.add_textbox(Inches(0.45), Inches(0.18),
                                 Inches(12.4), Inches(0.6))
    tf = tb.text_frame
    tf.margin_left = Emu(0)
    tf.margin_right = Emu(0)
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.LEFT
    tf.text = title
    p.font.size = Pt(20)
    p.font.bold = True
    p.font.name = '微软雅黑'
    p.font.color.rgb = RGBColor(0x1F, 0x4E, 0x79)
    if sub:
        run = p.add_run()
        run.text = '　　' + sub
        run.font.size = Pt(12)
        run.font.bold = False
        run.font.name = '微软雅黑'
        run.font.color.rgb = RGBColor(0x88, 0x88, 0x88)

    n_cols = max(len(header), 1)
    n_rows = len(rows) + 1   # +1 表头
    left = Inches(0.45)
    top = Inches(0.85)       # 标题下方
    width = Inches(12.4)
    # 高度：表头 ~0.34 in + 每数据行 ~0.28 in；不能压到页脚区域（top≈6.25 in）
    tbl_h = Inches(0.34 + 0.28 * len(rows))
    max_h = Inches(6.2) - top
    if tbl_h > max_h:
        tbl_h = max_h

    shape = slide.shapes.add_table(n_rows, n_cols, left, top, width, tbl_h)
    table = shape.table
    for c in range(n_cols):
        table.columns[c].width = Emu(int(width / n_cols))

    def fill_row(r, cells, is_header):
        for c in range(n_cols):
            cell = table.cell(r, c)
            data = cells[c] if c < len(cells) else {}
            cell.text = str(data.get('text', ''))
            para = cell.text_frame.paragraphs[0]
            para.alignment = PP_ALIGN.CENTER
            para.font.size = Pt(11 if is_header else 10)
            para.font.bold = bool(data.get('bold')) or is_header
            para.font.name = '微软雅黑'
            color = _parse_color(data.get('color'))
            para.font.color.rgb = color or (WHITE if is_header else DEFAULT_TXT)
            if is_header:
                _cell_fill(cell, GREEN_HDR)
            else:
                bg = _parse_color(data.get('bg'))
                if bg and bg not in _DECOR_BGS:
                    _cell_fill(cell, bg)
                else:
                    _cell_fill(cell, None)   # 白底
            _set_border(cell)
            cell.margin_top = cell.margin_bottom = Pt(2)
            cell.margin_left = Pt(4)
            cell.margin_right = Pt(4)

    fill_row(0, header, True)
    for i, row in enumerate(rows):
        fill_row(i + 1, row, False)


def build_pptx(tables):
    """tables: [{title, sub, rows: [[{text,color,bg,bold}]]}] → pptx 字节流

    加载干净模板（report_template_clean.pptx，layout/master/media 完整、
    无示例 slide），python-pptx 标准 add_slide → save，母版样式自动继承。
    干净模板缺失时回退原始模板（运行时删示例页）→ 最终回退空白演示文稿。
    """
    tpl_path = None
    if os.path.exists(_TPL_CLEAN):
        tpl_path = _TPL_CLEAN
    elif os.path.exists(_TPL_ORIG):
        tpl_path = _TPL_ORIG

    if tpl_path is None:
        # 无任何模板：全新空白演示文稿（无母版装饰，仅保证可导出）
        # 注：母版文件本身不在仓库里（体积/版权原因），需先用原始母版执行
        # scripts/make_pptx_clean_template.py 制备 report_template_clean.pptx。
        try:
            from flask import current_app
            current_app.logger.warning(
                'PPT 母版缺失（%s / %s 都不存在），本次导出退化为无装饰空白稿',
                _TPL_CLEAN, _TPL_ORIG)
        except Exception:
            pass
        prs = Presentation()
        prs.slide_width = Inches(13.333)
        prs.slide_height = Inches(7.5)
        layout = prs.slide_layouts[6]
    else:
        prs = Presentation(tpl_path)
        if tpl_path == _TPL_ORIG:
            # 回退路径：运行时删除原始模板自带的示例 slide
            # （python-pptx 的 drop_rel 会同步清理 Content_Types 与 rels，无悬空引用）
            xml_slides = prs.slides._sldIdLst
            for sldId in list(xml_slides):
                prs.part.drop_rel(sldId.get(qn('r:id')))
                xml_slides.remove(sldId)
        layout = prs.slide_layouts[0]   # '1_空白'（含 Logo/背景装饰）

    for t in tables:
        rows = t.get('rows') or []
        if not rows:
            continue
        header, body = rows[0], rows[1:]
        title = (t.get('title') or '成绩汇报')[:80]
        sub = (t.get('sub') or '')[:120]
        if len(body) <= _MAX_ROWS:
            _add_slide(prs, layout, title, sub, header, body)
        else:
            total_parts = (len(body) + _MAX_ROWS - 1) // _MAX_ROWS
            for part, start in enumerate(range(0, len(body), _MAX_ROWS)):
                suffix = '' if part == 0 else f'（{part + 1}/{total_parts}）'
                _add_slide(prs, layout, title + suffix, sub, header,
                           body[start:start + _MAX_ROWS])

    buf = io.BytesIO()
    prs.save(buf)
    buf.seek(0)
    return buf.read()
