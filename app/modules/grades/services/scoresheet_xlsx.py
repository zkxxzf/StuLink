# -*- coding: utf-8 -*-
"""成绩单 Excel 导出（2026-10-21）：一行一个学生，列与页面一致。

数据由 `routes/exams.py::_exam_page_rows(all_rows=True)` 提供 —— 与成绩单
页面同一个取数/排序/班级收敛函数。本模块只做排版，不重新取数、不重算
排名，也不判断权限，导出结果必然与页面所见同口径。

首行合并写口径说明（考试、筛选、排序、人数、符号含义），表头从第 2 行起；
本场存在原始分时，另起「原始分」工作表（主表仍与页面一致只放赋分）。
"""
from openpyxl import Workbook

from app.modules.grades.services import xlsx_kit as kit

# 表头排序键 → 中文（写进首行说明，保证「导出 = 页面当前所见」可复核）
SORT_LABEL = {
    'no': '学号', 'name': '姓名', 'exam_no': '考号', 'cls': '班级',
    'dir': '方向', 'sel': '选科', 'total': '总分', 'rank_dir': '方向排',
    'rank_class': '班排', 'move': '进退步',
}
_IDENT_HEADS = ['学号', '姓名', '考号', '班级', '方向', '选科',
                '方向排', '进退步', '班排', '总分']
_IDENT_WIDTHS = [12, 9, 12, 9, 6, 13, 7, 8, 7, 8]
_IDENT_NUMERIC = {7, 9, 10}          # 方向排 / 班排 / 总分
DASH = kit.DASH


def _move_text(mv):
    """进退步：↑N=进步 N 名（名次数字变小）、↓N=退步、0=持平、—=无可比。"""
    if mv is None:
        return DASH
    if mv < 0:
        return f'↑{-mv}'
    if mv > 0:
        return f'↓{mv}'
    return '0'


def _sort_label(key):
    """排序键 → 中文列名（科目排序键形如 's:语文'）。"""
    if key.startswith('s:'):
        return key[2:]
    return SORT_LABEL.get(key) or key


def build_note(exam, meta):
    """首行口径：考试 / 范围 / 排序 / 人数 / 符号含义。"""
    name = exam.name or ''
    # 考试名通常已含年级（如「2026级202610月考」），只在不含时补年级前缀
    if exam.grade and exam.grade not in name:
        name = f'{exam.grade} {name}'
    parts = [f'成绩单｜{name}']
    if exam.exam_date:
        parts.append(f'考试日期 {exam.exam_date:%Y-%m-%d}')
    scope = []
    if meta.get('cls'):
        scope.append(f"班级 {meta['cls']}")
    if meta.get('dir'):
        scope.append(f"{meta['dir']}方向")
    parts.append('范围：' + ('、'.join(scope) if scope else '全部学生'))
    if meta.get('sort_key'):
        way = '升序' if meta.get('order') == 'asc' else '从高到低'
        parts.append(f"排序：{_sort_label(meta['sort_key'])}（{way}）")
    parts.append(f"共 {meta.get('total', 0)} 人")
    parts.append('成绩列「—」=该科缺考、未选=不在该生选科组合内、'
                 '↑N=进步 N 名、↓N=退步 N 名')
    return '口径：' + '；'.join(parts) + '。'


def _has_raw(students):
    """本场是否有原始分（有才另起「原始分」表，避免整屏空列）。"""
    for stu in students:
        if stu.get('total_raw') is not None:
            return True
        for cell in (stu.get('subjects') or {}).values():
            if cell.get('raw') is not None:
                return True
    return False


def _subject_value(stu, subject, field):
    """单科取值：有分给分数；未选写「未选」；缺考写「—」（与页面区分一致）。"""
    cell = (stu.get('subjects') or {}).get(subject)
    if cell and cell.get(field) is not None:
        return cell[field]
    if subject in (stu.get('unselected') or set()):
        return '未选'
    return DASH


def _ident(stu):
    """身份列（学号…选科）+ 方向排 / 进退步 / 班排 / 总分。"""
    return [stu.get('no') or '', stu.get('name') or '',
            stu.get('exam_no') or '', stu.get('class_name') or '',
            stu.get('direction') or '', stu.get('selection') or '',
            stu.get('rank') or DASH, _move_text(stu.get('move')),
            stu.get('rank_class') or DASH,
            stu.get('total') if stu.get('total') is not None else DASH]


def _write_sheet(ws, note, heads, rows, numeric, widths):
    """统一排版：说明行 + 表头 + 表体 + 列宽 + 冻结窗格（学号/姓名固定）。"""
    kit.write_note(ws, note, len(heads))
    kit.write_head(ws, 2, heads)
    if rows:
        kit.write_rows(ws, 3, rows, numeric=numeric)
    else:
        kit.write_empty(ws, 3, len(heads), '（当前筛选没有学生）')
    kit.set_widths(ws, widths)
    ws.freeze_panes = 'C3'


def _score_sheet(ws, students, subjects, note):
    """赋分表：与页面列一一对应（总分 + 各科赋分）。"""
    heads = _IDENT_HEADS + list(subjects)
    rows = [_ident(s) + [_subject_value(s, sub, 'score') for sub in subjects]
            for s in students]
    numeric = _IDENT_NUMERIC | set(range(11, 11 + len(subjects)))
    _write_sheet(ws, note, heads, rows, numeric,
                 _IDENT_WIDTHS + [7] * len(subjects))


def _raw_sheet(ws, students, subjects, note):
    """原始分表：同身份列 + 总分原始分 + 各科原始分（仅本场有原分时生成）。"""
    heads = _IDENT_HEADS[:-1] + ['总分原始分'] + list(subjects)
    rows = []
    for s in students:
        raw_total = s.get('total_raw')
        rows.append(_ident(s)[:-1]
                    + [raw_total if raw_total is not None else DASH]
                    + [_subject_value(s, sub, 'raw') for sub in subjects])
    numeric = _IDENT_NUMERIC | set(range(11, 11 + len(subjects)))
    _write_sheet(ws, note.replace('成绩单｜', '原始分｜', 1), heads, rows,
                 numeric, _IDENT_WIDTHS + [7] * len(subjects))


def build(exam, students, subjects, meta):
    """构建成绩单工作簿（赋分表 + 可选原始分表）。"""
    wb = Workbook()
    ws = wb.active
    ws.title = '成绩单'
    note = build_note(exam, meta)
    _score_sheet(ws, students, subjects, note)
    if _has_raw(students):
        _raw_sheet(wb.create_sheet('原始分'), students, subjects, note)
    return wb
