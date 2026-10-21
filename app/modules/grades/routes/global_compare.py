# StuLink v1.19.62.64 2026-10-10
# 全局对比：班级 × 学科宽表（去差均分 + 各分数线），全科与分科自适配
#  - 页面/接口门槛与「成绩汇报区」一致（_guard_report：管理员/校领导/年级长）
#  - Excel 导出与页面同口径（复用 global_compare_service 的同一份缓存数据）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import io
from datetime import datetime

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from flask import (jsonify, render_template, request, send_file,
                   flash, redirect, url_for)
from flask_login import current_user, login_required

from app.models.grades import TOTAL_SUBJECT
from app.modules.grades import bp
from app.modules.grades.routes.report import _cached, _guard_report, _visible_exam
from app.modules.grades.services import global_compare_service as ld
from app.utils.decorators import perm_required
from app.utils.export_helpers import xl_row   # M-4：公式注入防护
from app.utils.helpers import log_operation

# 固定列（两级表头下 rowSpan=2）；班主任已并入「总分」格下方，不再单列
# 2026-10-15 新增「组合」列（方向之后）：本行选科组合分布（读成绩行快照，人数降序）
# 2026-10-20 用户口径：「班级人数 / 参考人数 / 去差后」三列并排（页面、打印、导出同口径）
_BASE_HEADS = ['年级', '班级', '班级人数', '参考人数', '去差后',
               '方向', '组合', '属性']


def _base_heads(data):
    """按本场数据决定固定列（2026-10-17 用户口径）。

    高一未选科时整场没有方向/组合 → 这两列不导出（页面、打印版同口径）；
    payload 里缺字段（旧缓存）时按「有」处理，保持原行为。
    """
    heads = ['年级', '班级', '班级人数', '参考人数', '去差后']
    if data.get('has_direction', True) is not False:
        heads.append('方向')
    if data.get('has_selection', True) is not False:
        heads.append('组合')
    heads.append('属性')
    return heads


@bp.route('/global-compare')
@login_required
@perm_required('grades.view')
def global_compare_page():
    """全局对比页（从成绩分析页或侧边栏进入，可携 exam_id）"""
    _guard_report()
    return render_template('grades/global_compare.html')


@bp.route('/global-compare/print')
@login_required
@perm_required('grades.view')
def global_compare_print():
    """全局对比**打印版**（2026-10-16 用户要求：「这个页面也要给我设计一下打印的，
    现在的打印版很明显不合适的」）。

    直接用浏览器打印页面时整表被挤成一团（34 列硬塞进纵向 A4，字号被压到看不清）。
    这里做服务端渲染的专用版：**横向 A4**、固定列精简（年级只写一次、方向变分组行）、
    每科＝「去差均分 + 每个层一列（格内"人数上/率下"）」，表头跨页重复。
    数据口径与页面**完全同源**（同一个 global_compare_report，逐生按自身选科组合取线）。
    """
    _guard_report()
    raw = (request.args.get('exam') or request.args.get('exam_id') or '').strip()
    exam_id = int(raw) if raw.isdigit() else None
    if exam_id is None:
        flash('请从「全局对比」页进入（需带 ?exam=<考试 id>）', 'warning')
        return redirect(url_for('grades.index'))
    _visible_exam(exam_id)
    direction = (request.args.get('direction') or '').strip()
    # 2026-10-16 第七轮审计修复（P-M1/M2）：键名与 JSON 接口同源（grades_report_ld_）
    # —— 原键 `grades_gc_print_*` 不在 invalidate_exam_cache 的任何失效前缀内
    # （改分/划线后最长 900s 仍可打印旧数），且与 /api/global-compare/report 各存
    # 一份缓存把同一份 payload 算两遍。同一个 builder，直接共用一把键。
    content = _cached(f'grades_report_ld_{exam_id}_{direction}',
                      lambda: ld.global_compare_report(exam_id, direction))
    if content.get('error'):
        flash(content['error'], 'warning')
        return redirect(url_for('grades.index', exam=exam_id))
    # 2026-10-16 第九轮审计（UI-U04）：打印版原先遍历**全部层** —— 真实库有 8 场
    # 「限时练」是 12 层名次分段，会排出 134 列（横向 A4 完全不可读且数字被裁）。
    # 改为与页面同款：跟随页面勾选（lines 白名单过滤），缺省用 default_lines，并封顶。
    avail = content.get('layers') or []
    want = [n for n in (request.args.get('lines') or '').split(',') if n in avail]
    lines = (want or content.get('default_lines') or avail)[:_PRINT_MAX_LINES]
    return render_template('grades/global_compare_print.html', gc=content,
                           lines=lines, exam_id=exam_id,
                           now=datetime.now().strftime('%Y-%m-%d %H:%M'))


@bp.route('/api/global-compare/report')
@login_required
@perm_required('grades.view')
def api_global_compare_report():
    """全局对比数据 JSON（direction 为空 = 全部方向）"""
    _guard_report()
    exam_id = request.args.get('exam_id', type=int)
    direction = (request.args.get('direction') or '').strip()
    _visible_exam(exam_id)
    data = _cached(f'grades_report_ld_{exam_id}_{direction}',
                   lambda: ld.global_compare_report(exam_id, direction))
    return jsonify(success='error' not in data, data=data,
                   message=data.get('error') if 'error' in data else '')


# ============ Excel 导出（两行表头 + 小计/总计行，与页面所见一致） ============

_HF = Font(bold=True, color='FFFFFF')
_HFL = PatternFill(start_color='1F4E79', end_color='1F4E79', fill_type='solid')
_HF2 = Font(bold=True, color='1F4E79')
_HFL2 = PatternFill(start_color='DCE6F1', end_color='DCE6F1', fill_type='solid')
_SUB_FILL = PatternFill(start_color='F1F5F9', end_color='F1F5F9', fill_type='solid')
_GRAND_FILL = PatternFill(start_color='E2E8F0', end_color='E2E8F0', fill_type='solid')
_TB = Border(left=Side(style='thin'), right=Side(style='thin'),
             top=Side(style='thin'), bottom=Side(style='thin'))
_CENTER = Alignment(horizontal='center', vertical='center', wrap_text=True)

# 2026-10-16 用户口径「全局对比的 excel 导出时没有注释解释」→ 首行加**口径注释行**
# （跨全部列合并 + 自动换行 + 灰字小号）。文案与页面顶部口径说明一致；打印版版面更窄
# （不印教师小字、层数封顶），故两边详略不同，勿宣称"逐字同源"。
_GC_NOTE = (
    '口径：①「班级人数 / 参考人数 / 去差后」＝名册在册人数 / 参加考试人数（有总分）/ '
    '去差后人数（＝min(参考人数, 班级人数 − 班型去差名额)；缺考先顶替去差名额，'
    '参考人数不足则不去差）；混组合班按组合分别去差、人数多的组合先拿名额；'
    '②「去差均分」＝按班型剔除总分末尾 N 人（卓越班 2 人）后的均分，'
    '其下方括号＝全体均分（不剔人；与去差一致时不显示）；'
    '③混选科组合的班级合计 / 方向小计 / 全年级行不给均分（组合线不同、混算无可比性）'
    '显示「—」，均分看其下按组合明细行（不加粗、底色随合计行）；'
    '④「上线」单元格＝人数 / 上线率，按每生各自选科组合的同名单科线逐生判定；'
    '⑤格内下方小字：总分格＝班主任、其余格＝该科任课教师（本场考试当时的快照）。'
)
_NOTE_FONT = Font(size=9, color='595959')
_NOTE_ALIGN = Alignment(horizontal='left', vertical='center', wrap_text=True)
_ROW0 = 4      # 数据起始行：1 注释 + 2/3 两行表头
# 打印版层数封顶（2026-10-16 审计 UI-U04）：横向 A4 每科 1+5 列仍可读，
# 再多（真实库的 12 层名次分段）会把表格压成不可读
_PRINT_MAX_LINES = 5
# 导出字重两档（PERF-P10：原先每格 new Font，31 行 × 36 列 = 1116 次）
_FONT_BOLD = Font(bold=True)
_FONT_PLAIN = Font(bold=False)


def _safe_fname(name):
    """下载文件名清洗（2026-10-16 审计 SEC-D01）：原只替换 `/`，
    反斜杠 / `..` / 控制符仍会进 Content-Disposition。"""
    import re
    s = re.sub(r'[\\/:*?"<>|\r\n\t\x00-\x1f]', '_', str(name or ''))
    return s.strip(' .') or '未命名'


def _fmt_online(n, rate):
    """上线单元格：'12 / 32%'；无该层单科线时 '—'"""
    if n is None:
        return '—'
    return f'{n} / {rate if rate is not None else 0}%'


def _sel_text(selections):
    """组合列文本：一个组合一行（`物化地 35`，与页面竖排一致）；无组合（未分科）→ '—'"""
    if not selections:
        return '—'
    return '\n'.join(f'{s["name"]} {s["count"]}' for s in selections)


def _row_values(row, subjects, lines, heads=None):
    """一行的单元格值：固定列 + 每科（去差均分 + 各层上线）。

    总分格下方显示班主任；其余科目格下方显示该科任课教师（均为本场快照）。
    heads 决定固定列（无方向/无组合的场次不出这两列，见 _base_heads）。
    """
    heads = heads or _BASE_HEADS
    vals = [row.get('grade', ''), row.get('class_name', ''),
            row.get('enrolled', row.get('count', '')),
            row.get('exam_count', row.get('count', '')),
            row.get('trim_count', row.get('count', ''))]
    if '方向' in heads:
        vals.append(row.get('direction', ''))
    if '组合' in heads:
        vals.append(_sel_text(row.get('selections')))
    vals.append(row.get('class_type', ''))
    cells = row.get('cells') or {}
    for sub in subjects:
        c = cells.get(sub) or {}
        extra = ((row.get('headteacher') or '') if sub == TOTAL_SUBJECT
                 else (c.get('teacher') or ''))
        v = c.get('trim_avg')
        s = v if v is not None else '—'
        if row.get('hide_avg'):
            # 跨组合的合计行：不给混算均分（2026-10-16 用户口径），只保留上线率列
            s = '—'
        else:
            # 2026-10-16：附全体均分（不去差，不剔人）——与页面同款，括号内一行
            # （注意用拼接赋值：s 可能是 float，`s += str` 会 TypeError）
            all_v = c.get('avg')
            if all_v is not None and all_v != v:
                s = f'{s}\n({all_v})'
        vals.append(f'{s}\n{extra}' if extra else s)
        for name in lines:
            ln = (c.get('lines') or {}).get(name) or {}
            vals.append(_fmt_online(ln.get('n'), ln.get('rate')))
    return vals


def _build_workbook(data, lines):
    """构建导出工作簿：口径注释行 + 两行表头 + 数据行。

    列 = 固定列 + 每科（去差均分 + 所选各层上线）。2026-10-16 用户口径
    「全局对比的 excel 导出时没有注释解释」→ 首行加**口径注释行**（跨全部列合并、
    自动换行、灰字小号），说明与页面顶部、打印版底部那句口径完全同源。
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '全局对比'
    subjects = data['subjects']
    heads = _base_heads(data)      # 2026-10-17：无方向/无组合的场次不出这两列
    per = 1 + len(lines)   # 每科列数：去差均分 + 各层
    ncol = len(heads) + len(subjects) * per

    # 第 1 行：口径注释（横向拉满整表宽 → 合并 + 自动换行）
    nc = ws.cell(row=1, column=1, value=_GC_NOTE)
    nc.font, nc.alignment = _NOTE_FONT, _NOTE_ALIGN
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncol)
    ws.row_dimensions[1].height = 30

    # 第 2 行：固定列 rowSpan=2 + 科目组 colSpan=per
    heads1 = list(heads)
    for sub in subjects:
        heads1.extend([sub] + [''] * (per - 1))
    for ci, v in enumerate(xl_row(heads1), start=1):
        ws.cell(row=2, column=ci, value=v)
    # 第 3 行：指标名（固定列位置留空）
    heads2 = [''] * len(heads)
    for _sub in subjects:
        heads2.append('去差均分')
        heads2.extend(ld.line_label(n) for n in lines)
    for ci, v in enumerate(xl_row(heads2), start=1):
        ws.cell(row=3, column=ci, value=v)

    # 合并固定列表头（竖向）+ 科目组表头（横向）
    for ci in range(1, len(heads) + 1):
        ws.merge_cells(start_row=2, start_column=ci, end_row=3, end_column=ci)
    for gi, _sub in enumerate(subjects):
        c0 = len(heads) + gi * per + 1
        ws.merge_cells(start_row=2, start_column=c0, end_row=2, end_column=c0 + per - 1)

    # 数据行：组内班级（含班级合计 + 组合明细）→ 方向小计（+ 按组合明细）→ 末尾总计
    # 2026-10-16：班级与小计都按选科组合拆（combo_rows），与页面/打印版三处同源；
    # 全年级总计的 combo_rows 恒为 []（与各方向小计重复，不再列）
    body = []      # (values, kind)  kind: normal/subtotal/grand
    for g in data['groups']:
        for r in g['rows']:
            body.append((r, 'normal'))
        sub = g.get('subtotal')
        if sub:
            body.append((sub, 'subtotal'))
            for r in (sub.get('combo_rows') or []):
                body.append((r, 'subtotal'))
    if data.get('grand'):
        body.append((data['grand'], 'grand'))
        for r in (data['grand'].get('combo_rows') or []):
            body.append((r, 'grand'))
    for i, (r, kind) in enumerate(body):
        ri = _ROW0 + i
        for ci, v in enumerate(xl_row(_row_values(r, subjects, lines, heads)),
                               start=1):
            ws.cell(row=ri, column=ci, value=v)

    # 样式
    for ci in range(1, ncol + 1):
        c = ws.cell(row=2, column=ci)
        c.font, c.fill, c.alignment, c.border = _HF, _HFL, _CENTER, _TB
        c2 = ws.cell(row=3, column=ci)
        c2.font, c2.fill, c2.alignment, c2.border = _HF2, _HFL2, _CENTER, _TB
    for ri, (r, kind) in enumerate(body, start=_ROW0):
        fill = _SUB_FILL if kind == 'subtotal' else (_GRAND_FILL if kind == 'grand' else None)
        for ci in range(1, ncol + 1):
            c = ws.cell(row=ri, column=ci)
            c.border, c.alignment = _TB, _CENTER
            # 2026-10-16 用户口径「分组的统计不用加粗，其他的都加粗。做区分」：
            # 表体默认加粗（班级行 / 班级合计行 / 小计 / 总计），只有按选科组合
            # 拆出的明细行不加粗（与页面 tr.combo、打印版 tr.combo 三处一致）
            c.font = _FONT_BOLD if not r.get('is_combo') else _FONT_PLAIN
            if fill is not None:
                c.fill = fill
        # 班级行带任课教师/班主任（成绩下方换行）、组合列多组合时换行 → 加高行，避免截断
        cells = r.get('cells') or {}
        height = 0
        if (r.get('headteacher')
                or any((cells.get(s) or {}).get('teacher') for s in subjects)):
            height = 30
        # 全体均分另占一行（去差均分 + (全体均分) + 教师 = 3 行）→ 加高
        if any((cells.get(s) or {}).get('avg') is not None
               and (cells.get(s) or {}).get('avg') != (cells.get(s) or {}).get('trim_avg')
               for s in subjects):
            height = max(height, 42)
        nsel = len(r.get('selections') or [])
        if nsel > 1:
            height = max(height, 15.5 * nsel + 3)
        if height:
            ws.row_dimensions[ri].height = height
    # 冻结：前 6 列 + 注释放一行、两行表头一起冻住（拉到右侧看分数线时不迷失）
    ws.freeze_panes = 'G4'
    widths = [8, 12, 10, 8, 14, 10]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
    for i in range(len(_BASE_HEADS) + 1, ncol + 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = 11
    return wb


@bp.route('/api/global-compare/export')
@login_required
@perm_required('grades.view')
def api_global_compare_export():
    """导出全局对比 xlsx（与页面同口径；复用同一份缓存数据）

    lines：逗号分隔的层名（页面当前勾选的分数线）；缺省/无有效值时用默认层。
    """
    _guard_report()
    exam_id = request.args.get('exam_id', type=int)
    direction = (request.args.get('direction') or '').strip()
    exam = _visible_exam(exam_id)
    data = _cached(f'grades_report_ld_{exam_id}_{direction}',
                   lambda: ld.global_compare_report(exam_id, direction))
    if 'error' in data:
        return jsonify(success=False, message=data['error']), 400
    avail = data.get('layers') or []
    want = [n for n in (request.args.get('lines') or '').split(',') if n in avail]
    lines = want or data.get('default_lines') or avail
    buf = io.BytesIO()
    _build_workbook(data, lines).save(buf)
    buf.seek(0)
    log_operation(current_user, '导出', '全局对比', exam_id,
                  f'{exam.name} {direction or "全部"}', module='grades')
    fname = f'全局对比_{_safe_fname(exam.name)}.xlsx'
    return send_file(buf, as_attachment=True, download_name=fname,
                     mimetype='application/vnd.openxmlformats-officedocument'
                              '.spreadsheetml.sheet')
