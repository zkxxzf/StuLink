# -*- coding: utf-8 -*-
"""生成 import_service.parse_score_excel 认识的中间 xlsx。

只做一件「翻译」：把「化学/化赋」这种成对列拆成
「化学（赋分，主分）+ 化学原始分（进 raw_score）」，
其余列名与网页模板 A 完全一致，避免另写一套解析。
"""
import openpyxl
from openpyxl import Workbook

SUBJECTS = ['语文', '数学', '外语', '物理', '历史', '化学', '生物',
            '政治', '地理']
RAW_SUBJECTS = ['化学', '生物', '政治', '地理']


def _headers(with_raw):
    """中间表表头。"""
    head = ['学号', '姓名', '班级', '总分'] + SUBJECTS
    if not with_raw:
        return head
    out = head[:4]
    for sub in SUBJECTS:
        out.append(sub)
        if sub in RAW_SUBJECTS:
            out.append('%s原始分' % sub)
    return out


def _row(rec, with_raw):
    """一条记录 → 一行单元格值。"""
    vals = [rec['student_no'], rec['name'], rec['class_name'], rec['total']]
    for sub in SUBJECTS:
        vals.append(rec['subjects'].get(sub))
        if with_raw and sub in RAW_SUBJECTS:
            vals.append(rec['raw'].get(sub))
    return vals


def write_book(records, path, with_raw):
    """把记录列表写成 xlsx，返回写入行数。"""
    wb = Workbook()
    ws = wb.active
    ws.title = '成绩'
    ws.append(_headers(with_raw))
    for rec in records:
        ws.append(_row(rec, with_raw))
    wb.save(path)
    return len(records)
