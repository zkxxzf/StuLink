# -*- coding: utf-8 -*-
"""表彰名单 Excel 导出（2026-10-19）：三个榜单各一个工作表。

口径与页面**完全同源**：直接复用 `honor_service.build` 的排名结果，导出端
只做「班级 + 关键词」过滤（与 `grades_honor.js` 的 data-class / data-key 同义），
不重新取数、不重新排名，并列规则不会在导出里变形。
"""
from openpyxl import Workbook

from app.modules.grades.services import xlsx_kit as kit


def _keep(row, cls, kw):
    """与前端过滤同义：班级精确匹配；关键词匹配「学号 姓名 班级」。"""
    if cls and (row.get('class_name') or '') != cls:
        return False
    if kw:
        key = ' '.join(str(row.get(k) or '')
                       for k in ('no', 'name', 'class_name'))
        if kw.lower() not in key.lower():
            return False
    return True


def _filter(rows, cls, kw):
    return [r for r in rows if _keep(r, cls, kw)]


def _score(row, subject):
    return kit.cell_value((row.get('scores') or {}).get(subject))


def _note(data, sheet, cls, kw):
    """每张表首行的口径说明（考试、分组维度、筛选、并列规则）。"""
    exam = data['exam']
    parts = [f"{sheet}｜{exam['name']}（{exam['date']}）"]
    cur = next((m for m in data['mode_options'] if m['key'] == data['mode']), None)
    if cur and sheet == '年级总分榜':
        parts.append(f"分组：{cur['label']}（{cur['desc']}）")
    parts.append('同分同名次、后延跳号；取满前 N 名后同分者仍全部列出')
    if cls or kw:
        parts.append('筛选：' + '；'.join(
            x for x in (f'班级 {cls}' if cls else '',
                        f'关键词「{kw}」' if kw else '') if x))
    return '口径：' + '；'.join(parts) + '。'


def _total_sheet(ws, data, cls, kw):
    """年级总分榜：分组 + 名次 + 学生 + 总分 + 各科。"""
    subjects = data['subjects']
    heads = (['分组', '名次', '学号', '姓名', '班级', '班型', '组合', '总分']
             + subjects)
    kit.write_note(ws, _note(data, '年级总分榜', cls, kw), len(heads))
    kit.write_head(ws, 2, heads)
    rows = []
    for group in data['groups']:
        for row in _filter(group['rows'], cls, kw):
            rows.append([group['label'], row['rank'], row.get('no') or '',
                         row.get('name') or '', row.get('class_name') or '',
                         row.get('class_type') or '',
                         row.get('selection') or '', row.get('total')]
                        + [_score(row, s) for s in subjects])
    if rows:
        kit.write_rows(ws, 3, rows,
                       numeric={2, 8} | set(range(9, 9 + len(subjects))))
    else:
        kit.write_empty(ws, 3, len(heads))
    kit.set_widths(ws, [10, 6, 12, 10, 10, 10, 14, 8]
                   + [7] * len(subjects))
    ws.freeze_panes = 'E3'


def _elite_total_sheet(ws, data, cls, kw):
    """卓越班总分榜：每班总分前 N（班名列在首列，便于筛选/透视）。"""
    subjects = data['subjects']
    heads = (['班级', '名次', '学号', '姓名', '班型', '组合', '总分']
             + subjects)
    kit.write_note(ws, _note(data, '卓越班总分', cls, kw), len(heads))
    kit.write_head(ws, 2, heads)
    rows = []
    for klass in data['elite']['classes']:
        if cls and klass['class_name'] != cls:
            continue
        for row in _filter(klass['total'], cls, kw):
            rows.append([klass['class_name'], row['rank'], row.get('no') or '',
                         row.get('name') or '', row.get('class_type') or '',
                         row.get('selection') or '', row.get('total')]
                        + [_score(row, s) for s in subjects])
    if rows:
        kit.write_rows(ws, 3, rows,
                       numeric={2, 7} | set(range(8, 8 + len(subjects))))
    else:
        kit.write_empty(ws, 3, len(heads))
    kit.set_widths(ws, [10, 6, 12, 10, 10, 14, 8] + [7] * len(subjects))
    ws.freeze_panes = 'C3'


def _elite_subject_sheet(ws, data, cls, kw):
    """卓越班单科榜：每班每科前 N（班名 + 科目两列便于筛选）。"""
    heads = ['班级', '科目', '名次', '学号', '姓名', '组合', '分数', '总分']
    kit.write_note(ws, _note(data, '卓越班单科', cls, kw), len(heads))
    kit.write_head(ws, 2, heads)
    rows = []
    for klass in data['elite']['classes']:
        if cls and klass['class_name'] != cls:
            continue
        for board in klass['subjects']:
            for row in _filter(board['rows'], cls, kw):
                rows.append([klass['class_name'], board['subject'],
                             row['rank'], row.get('no') or '',
                             row.get('name') or '',
                             row.get('selection') or '',
                             row.get('score'), row.get('total')])
    if rows:
        kit.write_rows(ws, 3, rows, numeric={3, 7, 8})
    else:
        kit.write_empty(ws, 3, len(heads))
    kit.set_widths(ws, [10, 8, 6, 12, 10, 14, 8, 8])
    ws.freeze_panes = 'C3'


def _subject_sheet(ws, data, cls, kw):
    """年级单科榜：各科全年级前 N。"""
    heads = ['科目', '名次', '学号', '姓名', '班级', '班型', '组合',
             '分数', '总分']
    kit.write_note(ws, _note(data, '年级单科榜', cls, kw), len(heads))
    kit.write_head(ws, 2, heads)
    rows = []
    for board in data['subject_board']:
        for row in _filter(board['rows'], cls, kw):
            rows.append([board['subject'], row['rank'], row.get('no') or '',
                         row.get('name') or '', row.get('class_name') or '',
                         row.get('class_type') or '',
                         row.get('selection') or '', row.get('score'),
                         row.get('total')])
    if rows:
        kit.write_rows(ws, 3, rows, numeric={2, 8, 9})
    else:
        kit.write_empty(ws, 3, len(heads))
    kit.set_widths(ws, [8, 6, 12, 10, 10, 10, 14, 8, 8])
    ws.freeze_panes = 'C3'


def build(data, cls='', kw=''):
    """构建导出工作簿：四个工作表（总分榜 / 卓越班总分 / 卓越班单科 / 单科榜）。"""
    wb = Workbook()
    ws = wb.active
    ws.title = '年级总分榜'
    _total_sheet(ws, data, cls, kw)
    _elite_total_sheet(wb.create_sheet('卓越班总分'), data, cls, kw)
    _elite_subject_sheet(wb.create_sheet('卓越班单科'), data, cls, kw)
    _subject_sheet(wb.create_sheet('年级单科榜'), data, cls, kw)
    return wb
