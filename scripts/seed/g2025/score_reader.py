# -*- coding: utf-8 -*-
"""读取成绩 Excel 的「纯分」工作表，产出统一记录。

表头（第 1 行，固定 18 列）：
    考号 姓名 班级 总赋 总分 语文 数学 外语 物理 历史
    化学 化赋 生物 生赋 政治 政赋 地理 地赋

口径：
* subjects = 赋分口径（化/生/政/地取「*赋」列；语数外物历只有一列），
  给 import_service 当主分（score）；
* raw = 化/生/政/地的原始分，落 exam_scores.raw_score；
* total = 「总赋」（赋分总分），为 None 时回落「总分」。
"""
import openpyxl

DIR_OF_SHEET = {'全科纯分': '', '物理类纯分': '物理', '历史类纯分': '历史'}

SUB_COL = {'语文': 5, '数学': 6, '外语': 7, '物理': 8, '历史': 9,
           '化学': 11, '生物': 13, '政治': 15, '地理': 17}
RAW_COL = {'化学': 10, '生物': 12, '政治': 14, '地理': 16}
TOTAL_FU_COL = 3
TOTAL_RAW_COL = 4


def _num(value):
    """单元格转 float；空、非数值返回 None。"""
    if value is None or value == '':
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value):
    """单元格转去空白字符串。"""
    return '' if value is None else str(value).strip()


def _one_row(row, direction):
    """把一行 Excel 转成成绩记录；无姓名返回 None。"""
    name = _text(row[1])
    if not name:
        return None
    subjects, raw = {}, {}
    for sub, idx in SUB_COL.items():
        val = _num(row[idx]) if idx < len(row) else None
        if val is None and sub in RAW_COL:
            val = _num(row[RAW_COL[sub]])
        subjects[sub] = val
    for sub, idx in RAW_COL.items():
        raw[sub] = _num(row[idx]) if idx < len(row) else None
    total_fu = _num(row[TOTAL_FU_COL])
    total_raw = _num(row[TOTAL_RAW_COL])
    return {
        'exam_no': _text(row[0]),
        'name': name,
        'class_name': _text(row[2]),
        'total': total_fu if total_fu is not None else total_raw,
        'subjects': subjects,
        'raw': raw,
        'direction': direction,
    }


def _read_sheet(ws, direction):
    """读取一个成绩页，返回记录列表。"""
    out = []
    for row in ws.iter_rows(min_row=2, max_col=18, values_only=True):
        rec = _one_row(row, direction)
        if rec is not None:
            out.append(rec)
    return out


def read_scores(path, sheets):
    """读取若干成绩页，返回记录列表（按页顺序）。

    同一学号在两页都出现时以先出现者为准（正常数据不会重复）。
    """
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out, seen = [], set()
    for sheet in sheets:
        if sheet not in wb.sheetnames:
            continue
        for rec in _read_sheet(wb[sheet], DIR_OF_SHEET.get(sheet, '')):
            key = (rec['name'], rec['class_name'])
            if key in seen:
                continue
            seen.add(key)
            rec['sheet'] = sheet
            out.append(rec)
    wb.close()
    return out


def has_raw(path, sheets):
    """判断该场是否同时给了原始分（化/生/政/地 两列不同）。"""
    for rec in read_scores(path, sheets):
        for sub, idx in RAW_COL.items():
            fu = rec['subjects'].get(sub)
            rw = rec['raw'].get(sub)
            if fu is not None and rw is not None and abs(fu - rw) > 0.01:
                return True
    return False
