# StuLink v1.19.57.64 2026-10-20
# 限时练导入的公共上传校验（模板导入与 AI 导入共用）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""上传文件读取 + 科目校验（无路由，只被两个导入路由模块调用）。"""
import csv
import io

import openpyxl
from flask import request

from app.models.grades import SUBJECTS
from app.modules.grades.services import timed_import
from app.utils.upload_guard import validate_upload

MAX_FILE_MB = 5
EXCEL_EXTS = ['xlsx', 'xls', 'csv']


def _csv_to_xlsx(raw):
    """CSV → xlsx 字节。

    2026-10-21：浏览器油猴脚本直接把智学网成绩拼成 CSV 传上来（前端生成
    xlsx 要带一个 1MB 的库），下游解析统一走 openpyxl，所以在这里转一次。
    编码先按 UTF-8（含 BOM），失败再按 GB18030（Excel 另存的中文 CSV）。
    """
    text = None
    for enc in ('utf-8-sig', 'gb18030'):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise timed_import.TimedParseError('CSV 编码无法识别，请另存为 UTF-8')
    wb = openpyxl.Workbook()
    ws = wb.active
    for line in csv.reader(io.StringIO(text)):
        ws.append(line)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def read_upload():
    """读取上传文件 → bytes；无文件 / 超限抛 TimedParseError

    `.csv` 在这里转成 xlsx 再返回，下游（模板导入 / AI 导入）无需感知。
    """
    file = request.files.get('file')
    if not file or not file.filename:
        raise timed_import.TimedParseError('请先选择文件')
    raw = file.read()
    # 传 stream 才做 magic 嗅探（与其余导入入口同口径；CSV 无签名，天然放行）
    ok, err = validate_upload(file.filename, allowed_exts=EXCEL_EXTS,
                              stream=io.BytesIO(raw))
    if not ok:
        raise timed_import.TimedParseError(err)
    if len(raw) > MAX_FILE_MB * 1024 * 1024:
        raise timed_import.TimedParseError(f'文件超过 {MAX_FILE_MB}MB')
    if file.filename.lower().endswith('.csv'):
        raw = _csv_to_xlsx(raw)
    return raw


def subject_from_form():
    """表单里的科目：只认九科之一"""
    subject = (request.form.get('subject') or '').strip()
    if subject not in SUBJECTS:
        raise timed_import.TimedParseError('请选择要导入的科目')
    return subject
