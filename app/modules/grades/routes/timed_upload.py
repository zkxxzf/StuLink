# StuLink v1.19.49.52 2026-10-20
# 限时练导入的公共上传校验（模板导入与 AI 导入共用）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""上传文件读取 + 科目校验（无路由，只被两个导入路由模块调用）。"""
import io

from flask import request

from app.models.grades import SUBJECTS
from app.modules.grades.services import timed_import
from app.utils.upload_guard import validate_upload

MAX_FILE_MB = 5
EXCEL_EXTS = ['xlsx', 'xls']


def read_upload():
    """读取上传文件 → bytes；无文件 / 超限抛 TimedParseError"""
    file = request.files.get('file')
    if not file or not file.filename:
        raise timed_import.TimedParseError('请先选择文件')
    raw = file.read()
    # 传 stream 才做 magic 嗅探（与其余导入入口同口径）
    ok, err = validate_upload(file.filename, allowed_exts=EXCEL_EXTS,
                              stream=io.BytesIO(raw))
    if not ok:
        raise timed_import.TimedParseError(err)
    if len(raw) > MAX_FILE_MB * 1024 * 1024:
        raise timed_import.TimedParseError(f'文件超过 {MAX_FILE_MB}MB')
    return raw


def subject_from_form():
    """表单里的科目：只认九科之一"""
    subject = (request.form.get('subject') or '').strip()
    if subject not in SUBJECTS:
        raise timed_import.TimedParseError('请选择要导入的科目')
    return subject
