# StuLink v1.19.49.39 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序成绩导入（标准模板）。

两步与网页一致：
1. `POST /grades/manage/import/upload`（multipart）：校验文件 → `parse_score_excel`
   预检 → 差异预览 → 批次 token 落到 `exam.import_draft/import_token`；
2. `POST /grades/manage/import/confirm`：按 token 取回批次 → `apply_import`
   （重算排名、任课快照、清缓存、操作日志，与网页同一条链路）。

「不参加考试」名单默认剔除，确认时可勾选一并导入（`include_skip`）。
"""
import io
import json
import uuid
from datetime import datetime

from flask import current_app, g, request

from app.extensions import db
from app.modules.grades.routes.exams import _preview_diff
from app.modules.grades.services import (import_attend, import_service,
                                        ranking, store_service, tab_service)
from app.modules.grades.services.import_service import ParseError
from app.modules.wxapi import bp
from app.modules.wxapi.core.guards import wx_perm_required
from app.modules.wxapi.core.response import wx_err, wx_ok
from app.modules.wxapi.grades.routes_grades_stats import _visible_exam
from app.utils.helpers import log_operation
from app.utils.upload_guard import validate_upload

PERM = 'grades.import'
MAX_ERRORS = 20


@bp.route('/grades/manage/import/upload', methods=['POST'])
@wx_perm_required(PERM)
def wx_import_upload():
    """上传成绩表并预检（form: exam_id / file / remove_missing）。"""
    exam, err = _visible_exam(request.form.get('exam_id'))
    if err:
        return err
    file = request.files.get('file')
    if not file or not file.filename:
        return wx_err('请选择要上传的 Excel 文件')
    ok, msg = validate_upload(file.filename, allowed_exts=['xlsx', 'xls'],
                              stream=file.stream)
    if not ok:
        return wx_err('%s（当前文件：%s）' % (msg, file.filename))
    mode = 'A'
    remove_missing = request.form.get('remove_missing') == '1'
    try:
        parsed = import_service.parse_score_excel(
            io.BytesIO(file.read()), exam)
    except ParseError as e:
        return wx_err(str(e))
    except Exception:
        current_app.logger.exception('小程序成绩解析异常')
        return wx_err('文件解析失败，请确认是标准模板格式')

    skip_map = import_attend.non_attend_map(exam.id)
    kept_rows, skip_students = import_attend.split_rows(
        parsed['rows'], skip_map)
    diff = _preview_diff(
        exam, {'headers': parsed['headers'], 'rows': kept_rows}, mode)
    token = str(uuid.uuid4())
    exam.import_draft = json.dumps({
        'fname': file.filename, 'mode': mode,
        'remove_missing': remove_missing,
        'headers': parsed['headers'], 'rows': parsed['rows'],
        'errors': parsed['errors'], 'stats': parsed['stats'],
        'template': parsed['template'], 'diff': diff,
        'created_at': datetime.now().isoformat(),
        'skip_nos': [s['no'] for s in skip_students],
        'skip_students': skip_students,
    }, ensure_ascii=False, default=str)
    exam.import_token = token
    db.session.commit()
    return wx_ok({
        'token': token,
        'fname': file.filename,
        'stats': parsed['stats'],
        'diff': diff,
        'error_count': len(parsed['errors']),
        'errors': parsed['errors'][:MAX_ERRORS],
        'skip_students': skip_students,
    }, '预检完成，请核对差异后确认导入')


@bp.route('/grades/manage/import/confirm', methods=['POST'])
@wx_perm_required(PERM)
def wx_import_confirm():
    """确认导入（json: exam_id / token / include_skip）。"""
    data = request.get_json(silent=True) or {}
    exam, err = _visible_exam(data.get('exam_id'))
    if err:
        return err
    token = (data.get('token') or '').strip()
    if (not exam.import_draft or not exam.import_token
            or exam.import_token != token):
        return wx_err('导入批次已失效，请重新上传')
    try:
        draft = json.loads(exam.import_draft)
    except (json.JSONDecodeError, TypeError):
        return wx_err('导入批次数据异常，请重新上传')
    rows = draft['rows']
    skip_nos = set(draft.get('skip_nos') or [])
    include_skip = bool(data.get('include_skip'))
    if skip_nos and not include_skip:
        rows = [r for r in rows if r.get('no') not in skip_nos]
    parsed = {'headers': draft['headers'], 'rows': rows}
    mode = draft.get('mode', 'A')
    remove_missing = draft.get('remove_missing', False)
    try:
        summary = store_service.apply_import(
            exam, parsed, mode=mode, remove_missing=remove_missing)
        ranking.recalc_exam(exam.id)
        from app.modules.grades.services import teacher_snapshot_service as tss
        n_tch = tss.snapshot_exam(exam, source='import')
        exam.status = 'imported'
        # 2026-10-20 第十六轮：import_token 一次性（对照网页 exams.py 的
        # P1 M-17）—— 原来确认后不作废，同一 token 可无限重放；
        # mode A + remove_missing 会重复删除"文件里没有"的学生，
        # 把别人后续录入的成绩反复回滚。
        exam.import_token = None
        exam.import_draft = None
        exam.import_info = json.dumps({
            'fname': draft.get('fname', ''), 'mode': mode,
            'summary': summary, 'errors': len(draft.get('errors', [])),
            'teacher_snapshot': n_tch,
            'operator': g.wx_user.real_name,
            'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }, ensure_ascii=False)
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception('小程序成绩导入失败')
        return wx_err('导入失败，数据已回滚（详情已记入系统日志）')
    tab_service.clear_exam_cache(exam.id)
    skipped = len(skip_nos) if (skip_nos and not include_skip) else 0
    log_operation(g.wx_user, '导入', '考试', exam.id,
                  '%s %s模式 差异%s（小程序）' % (
                      exam.name, mode,
                      json.dumps(summary, ensure_ascii=False)),
                  module='grades')
    return wx_ok({
        'summary': summary,
        'skipped': skipped,
        'snapshot': n_tch,
    }, '导入完成')
