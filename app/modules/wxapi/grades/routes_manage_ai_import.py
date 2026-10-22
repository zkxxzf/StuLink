# StuLink v1.19.65.64 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序 AI 智能导入：任意格式成绩表 → 自动列识别 → 写入。

与网页 `/exams/<id>/ai-import` 同一套解析（`ai_import_service.probe/read_sheet_rows/
classify_headers` + `ai_import._merge_rows`），差异只在**不做多文件/识图**：
小程序一次一个 Excel，列映射用自动识别结果，写入前二次确认（整场覆盖）。
"""
import io
import json
import uuid

from flask import g, request

from app.extensions import db
from app.models.grades import SUBJECTS, Exam
from app.modules.grades.routes import ai_import as ai_route
from app.modules.grades.services import ai_import_service as aisvc
from app.modules.grades.services import (import_service, ranking, store_service,
                                        tab_service)
from app.modules.grades.services.exam_guard import assert_exam_visible
from app.modules.wxapi import bp
from app.modules.wxapi.core.guards import wx_perm_required
from app.modules.wxapi.core.response import wx_err, wx_ok
from app.utils.helpers import log_operation
from app.utils.upload_guard import validate_upload

PERM = 'grades.import'
MAX_FILE_MB = 10


@bp.route('/grades/ai-import/analyze', methods=['POST'])
@wx_perm_required(PERM)
def wx_ai_import_analyze():
    """上传一个 Excel：解析全部工作表并给出列识别建议。"""
    data = request.form or {}
    try:
        exam_id = int(data.get('exam_id') or 0)
    except (TypeError, ValueError):
        return wx_err('参数不正确：exam_id 需为数字')
    exam = db.session.get(Exam, exam_id)
    if exam is None:
        return wx_err('考试不存在', 404)
    assert_exam_visible(exam_id)
    f = request.files.get('file')
    if not f or not f.filename:
        return wx_err('请选择 Excel 文件')
    raw = f.read()
    if len(raw) > MAX_FILE_MB * 1024 * 1024:
        return wx_err(f'文件超过 {MAX_FILE_MB}MB，请拆分后再导入')
    ok, msg = validate_upload(f.filename, allowed_exts=['xlsx', 'xls'],
                              stream=io.BytesIO(raw))
    if not ok:
        return wx_err(msg)
    try:
        probe = aisvc.probe(raw)
    except Exception as e:
        return wx_err('表格解析失败：%s' % str(e)[:120])
    sheets, errors = [], []
    for sh in probe['sheets']:
        sh['file'] = f.filename
        try:
            sh['rows'] = aisvc.read_sheet_rows(raw, sh['name'], sh['header_row'])
        except Exception as e:
            errors.append('%s 读取失败：%s' % (sh['name'], str(e)[:60]))
            continue
        sheets.append(sh)
    if not sheets:
        return wx_err('；'.join(errors) or '没有可用工作表')
    token = str(uuid.uuid4())
    draft = {'mode': 'ai', 'sheets': sheets, 'fname': f.filename,
             'errors': errors, 'image_schedules': [], 'image_notes': ''}
    text = json.dumps(draft, ensure_ascii=False)
    if len(text.encode('utf-8')) > 20 * 1024 * 1024:
        return wx_err('表格数据量过大（超过 20MB），请减少工作表后分批导入')
    exam.import_draft = text
    exam.import_token = token
    db.session.commit()
    return wx_ok({
        'token': token,
        'fname': f.filename,
        'sheets': [{
            'name': s['name'],
            'header_row': s['header_row'],
            'headers': s['headers'][:40],
            'rows': len(s.get('rows') or []),
            'auto': s.get('auto') or {},
            'affair_auto': s.get('affair_auto') or {},
        } for s in sheets],
        'errors': errors,
    })


@bp.route('/grades/ai-import/apply', methods=['POST'])
@wx_perm_required(PERM)
def wx_ai_import_apply():
    """按自动识别列写入成绩（整场覆盖，需 confirmed=1）。"""
    payload = request.get_json(silent=True) or {}
    try:
        exam_id = int(payload.get('exam_id') or 0)
    except (TypeError, ValueError):
        return wx_err('参数不正确：exam_id 需为数字')
    exam = db.session.get(Exam, exam_id)
    if exam is None:
        return wx_err('考试不存在', 404)
    assert_exam_visible(exam_id)
    token = (payload.get('token') or '').strip()
    if not exam.import_token or token != exam.import_token or not exam.import_draft:
        return wx_err('导入批次已失效，请重新上传')
    try:
        draft = json.loads(exam.import_draft)
    except (TypeError, ValueError):
        return wx_err('草稿数据异常，请重新上传')
    by_name = {}
    for s in draft.get('sheets') or []:
        by_name.setdefault(s['name'], s)
    items, picked = [], []
    for item in (payload.get('sheets') or []):
        sh = by_name.get(item.get('name'))
        if sh is None:
            continue
        kind = item.get('score_kind') or 'converted'
        if kind == 'skip':
            continue
        mapping = sh.get('auto') or {}
        if not mapping.get('subjects'):
            continue
        items.append({'_sheet': sh, 'mapping': mapping, 'score_kind': kind})
        picked.append(sh['name'])
    if not items:
        return wx_err('没有可写入的成绩表（工作表缺少可识别的学号/科目列）')
    if not bool(payload.get('confirmed')):
        return wx_ok({'need_confirm': True,
                      'sheets': picked,
                      'message': '将整场覆盖已有成绩'}, '需要确认')
    rows, errors = ai_route._merge_rows(items, exam)
    rows, e2 = ai_route._resolve_student_no(exam, rows)
    errors.extend(e2 or [])
    if not rows:
        return wx_err('未解析出成绩行：' + '；'.join((errors or [])[:3]))
    unmatched = import_service._attach_student_info(exam, rows)
    errors.extend(unmatched or [])
    subjects = [s for s in SUBJECTS
                if any(s in (r.get('subjects') or {}) for r in rows)]
    parsed = {'headers': {'no': True, 'subjects': subjects}, 'rows': rows}
    try:
        summary = store_service.apply_import(exam, parsed, mode='B')
        raw_updated = ai_route._write_raw_scores(exam, rows)
        ranking.recalc_exam(exam_id)
        from app.modules.grades.services import teacher_snapshot_service as tss
        tss.snapshot_exam(exam, source='import')
        exam.status = 'imported'
        exam.import_draft = None
        exam.import_token = None
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return wx_err('写入失败已回滚：%s' % str(e)[:160])
    tab_service.clear_exam_cache(exam_id)
    log_operation(g.wx_user, '导入', '考试', exam_id,
                  '小程序 AI 导入：%d 人 / %d 科'
                  % (summary.get('students', 0), len(subjects)),
                  module='grades')
    return wx_ok({
        'sheets': picked,
        'students': summary.get('students', 0),
        'subjects': subjects,
        'raw_cells': raw_updated,
        'errors': errors[:20],
    }, '导入完成')
