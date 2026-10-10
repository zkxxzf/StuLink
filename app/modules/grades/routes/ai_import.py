# StuLink v1.19.0 2026-10-10
# 成绩管理：AI 智能导入（任意格式表 → AI 列映射 → 规则解析入库）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""AI 智能导入路由（挂在 grades 蓝图）

三条接口：
  GET  /exams/<id>/ai-import          页面
  POST /exams/<id>/ai-import/analyze   上传（可多文件）→ 存草稿 → AI 给列映射建议
  POST /exams/<id>/ai-import/apply     按确认后的映射解析 → 预览(dry_run) 或 写入

关键设计：
  · AI 只吃「表头 + 3 行样本」，全量解析由确定性代码完成（不漏行）
  · 支持一次传多个文件/多 sheet：一个像赋分表、一个像原始表 → **自动合并成双轨**
  · 未配置本机 AI Key 时，analyze 不报错，返回空建议 + 提示“手工指定列映射”
"""
from __future__ import annotations

import json
import uuid

from flask import render_template, request, jsonify, redirect, url_for, flash
from flask_login import login_required, current_user

from app.extensions import db
from app.models.grades import Exam, SUBJECTS, TOTAL_SUBJECT
from app.modules.grades import bp
from app.modules.grades.services import ai_import_service as aisvc
from app.modules.grades.services import import_service, store_service, ranking, tab_service
from app.modules.grades.services.exam_guard import assert_exam_visible
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation

MAX_AI_FILES = 4
MAX_FILE_MB = 20


def _ai_cfg_from_request():
    """读取前端本机（localStorage）传来的 AI 配置；不落库

    analyze 走 multipart（要传文件），AI 配置以表单字段 ai=<json> 携带；
    apply 走 JSON，走 body.ai。两者都兼容。
    """
    ai = None
    raw = (request.form.get('ai') or '').strip()
    if raw:
        try:
            d = json.loads(raw)
            ai = d if isinstance(d, dict) else None
        except (json.JSONDecodeError, TypeError):
            ai = None
    if ai is None:
        d = request.get_json(silent=True) or {}
        ai = d.get('ai') if isinstance(d.get('ai'), dict) else {}
    key = (ai.get('api_key') or '').strip()
    if not key:
        return None
    return {'api_key': key, 'base_url': (ai.get('base_url') or '').strip(),
            'model': (ai.get('model') or '').strip(),
            'provider': (ai.get('provider') or '').strip(),
            # v1.19.0 可选：输出上限 / 输入上限 / 思考强度（本机设置，不落库）
            'max_tokens': ai.get('max_tokens') or '',
            'max_input': ai.get('max_input') or '',
            'reasoning_effort': ai.get('reasoning_effort') or ''}


@bp.route('/exams/<int:exam_id>/ai-import')
@login_required
@perm_required('grades.import')
def exam_ai_import(exam_id):
    exam = assert_exam_visible(exam_id)
    return render_template('grades/ai_import.html', exam=exam,
                           subjects=SUBJECTS, score_kinds=aisvc.SCORE_KIND_LABEL)


@bp.route('/exams/<int:exam_id>/ai-import/analyze', methods=['POST'])
@login_required
@perm_required('grades.import')
def exam_ai_import_analyze(exam_id):
    exam = assert_exam_visible(exam_id)
    files = [f for f in request.files.getlist('files') if f and f.filename]
    if not files:
        return jsonify(success=False, message='请先选择文件（可多选：赋分表 + 原始表一起发）')
    if len(files) > MAX_AI_FILES:
        return jsonify(success=False, message='一次最多 %d 个文件' % MAX_AI_FILES)

    sheets, errors = [], []
    for f in files:
        raw = f.read()
        if len(raw) > MAX_FILE_MB * 1024 * 1024:
            errors.append('%s 超过 %dMB，已跳过' % (f.filename, MAX_FILE_MB))
            continue
        try:
            pr = aisvc.probe(raw)
        except Exception as e:
            errors.append('%s 解析失败：%s' % (f.filename, str(e)[:80]))
            continue
        for sh in pr['sheets']:
            sh['file'] = f.filename
            # 解析需要全量行：这里一次性读出并随草稿存好，apply 时无需再上传
            sh['rows'] = aisvc.read_sheet_rows(raw, sh['name'], sh['header_row'])
            sheets.append(sh)
    if not sheets:
        return jsonify(success=False, message='；'.join(errors) or '没有可用工作表')

    # 草稿落库（复用既有 import_draft/import_token 机制）
    token = str(uuid.uuid4())
    draft = {'mode': 'ai', 'sheets': sheets,
             'fname': '、'.join(f.filename for f in files),
             'errors': errors}
    exam.import_draft = json.dumps(draft, ensure_ascii=False)
    exam.import_token = token
    db.session.commit()

    # AI 建议（未配置 Key → 空建议 + 手工映射提示）
    suggest, ai_err = {}, ''
    cfg = _ai_cfg_from_request()
    if cfg:
        from app.modules.grades.services import ai_service
        try:
            resolved = ai_service.resolve_key(current_user, cfg)
            system, user = aisvc.build_ai_prompt({'sheets': sheets}, exam)
            ok, payload, _sec = ai_service._chat_completions(
                resolved, [{'role': 'system', 'content': system},
                           {'role': 'user', 'content': user}],
                timeout=60, max_tokens=1200)
            if ok:
                text = ''
                try:
                    text = payload['choices'][0]['message']['content']
                except (KeyError, IndexError, TypeError):
                    text = ''
                suggest = aisvc.parse_ai_mapping(text) or {}
                if not suggest:
                    ai_err = 'AI 返回内容无法解析为映射，请手工指定列'
            else:
                ai_err = 'AI 调用失败：%s' % str(payload)[:160]
        except Exception as e:
            ai_err = 'AI 调用异常：%s' % str(e)[:160]
    else:
        ai_err = '未检测到本机 AI Key，已跳过 AI 识别，请在下方手工指定每列的用途'

    return jsonify(success=True, token=token,
                   files=[f.filename for f in files],
                   sheets=[{'name': s['name'], 'file': s.get('file', ''),
                            'header_row': s['header_row'], 'headers': s['headers'][:60],
                            'sample': s['sample'][:2]} for s in sheets],
                   suggest=suggest, ai_error=ai_err, errors=errors)


def _merge_rows(sheets_payload, exam):
    """把多个 sheet 的行合并成标准 rows（支持赋分表 + 原始表两文件合一）

    sheets_payload: [{name, mapping, score_kind}]  —— score_kind 为该表的角色：
        converted=这份是赋分（进主分）/ raw=这份是原始（进 raw_score）/ both=同表双轨
    返回 (rows, errors)
    """
    by_no, errors = {}, []
    for item in sheets_payload or []:
        sh = item.get('_sheet')
        if sh is None:
            continue
        kind = item.get('score_kind') or 'converted'
        rows, errs = aisvc.rows_from_mapping(sh, item.get('mapping') or {}, kind, exam)
        errors.extend(errs)
        as_raw = (kind == 'raw')
        for r in rows:
            if as_raw:
                # 这份是“原始成绩表”：它给的分数就是原始分，不进主分
                r['raw_subjects'] = dict(r['subjects'])
                r['raw_total'], r['total'] = r['total'], None
                r['subjects'] = {}
            tgt = by_no.setdefault(r['no'], {
                'no': r['no'], 'line': r['line'], 'subjects': {}, 'raw_subjects': {},
                'total': None, 'raw_total': None,
                'name': '', 'class_name': '', 'exam_no': ''})
            for k, v in (r['subjects'] or {}).items():
                if v is not None:
                    tgt['subjects'][k] = v
            for k, v in (r['raw_subjects'] or {}).items():
                if v is not None:
                    tgt['raw_subjects'][k] = v
            if r.get('total') is not None and tgt['total'] is None:
                tgt['total'] = r['total']
            if r.get('raw_total') is not None and tgt['raw_total'] is None:
                tgt['raw_total'] = r['raw_total']
            for fld in ('name', 'class_name', 'exam_no'):
                if r.get(fld) and not tgt[fld]:
                    tgt[fld] = r[fld]
    rows = list(by_no.values())
    # 只有原始分、没有主分的学生 → 主分顶上（避免整批没有分数可比）
    for r in rows:
        if not r['subjects'] and r['raw_subjects']:
            r['subjects'] = dict(r['raw_subjects'])
            r['raw_subjects'] = {}
            if r['total'] is None:
                r['total'] = r['raw_total']
    return rows, errors


@bp.route('/exams/<int:exam_id>/ai-import/apply', methods=['POST'])
@login_required
@perm_required('grades.import')
def exam_ai_import_apply(exam_id):
    exam = assert_exam_visible(exam_id)
    data = request.get_json(silent=True) or {}
    token = (data.get('token') or '').strip()
    if not exam.import_token or token != exam.import_token or not exam.import_draft:
        return jsonify(success=False, message='导入批次已失效，请重新上传')
    try:
        draft = json.loads(exam.import_draft)
    except (json.JSONDecodeError, TypeError):
        return jsonify(success=False, message='草稿数据异常，请重新上传')

    sheets = draft.get('sheets') or []
    by_name = {}
    for s in sheets:
        by_name.setdefault(s['name'], s)
    payload = []
    for item in (data.get('sheets') or []):
        sh = by_name.get(item.get('name'))
        if sh is None:
            continue
        sh = dict(sh)
        payload.append({'_sheet': sh, 'mapping': item.get('mapping') or {},
                        'score_kind': item.get('score_kind') or 'converted'})
    if not payload:
        return jsonify(success=False, message='请至少为一个工作表指定列映射')

    rows, errors = _merge_rows(payload, exam)
    if not rows:
        return jsonify(success=False,
                       message='未解析出任何成绩行，请检查列映射（或改用标准模板导入）',
                       errors=errors[:20])

    # 补齐学生快照（姓名/班级/班型/方向…）并统计
    unmatched = import_service._attach_student_info(exam, rows)
    errors.extend(unmatched)
    n_subj = sum(len(r['subjects']) for r in rows)
    n_raw = sum(len(r['raw_subjects']) for r in rows)
    preview = {
        'students': len(rows),
        'subject_cells': n_subj,
        'raw_cells': n_raw,
        'with_exam_no': sum(1 for r in rows if r.get('exam_no')),
        'errors': len(errors),
    }
    if data.get('dry_run'):
        return jsonify(success=True, preview=preview, errors=errors[:20])

    # 正式写入（复用既有导入管道；整场覆盖式，避免与旧数据混在一起）
    parsed = {'headers': {'no': True}, 'rows': rows}
    mode = 'B' if (data.get('mode') or 'B') == 'B' else 'A'
    try:
        summary = store_service.apply_import(exam, parsed, mode=mode)
        # 原始分单独回填（store_service 只处理主分/总分）
        raw_updated = _write_raw_scores(exam, rows)
        ranking.recalc_exam(exam_id)
        from app.modules.grades.services import teacher_snapshot_service as tss
        tss.snapshot_exam(exam, source='import')
        exam.status = 'imported'
        exam.import_draft = None
        exam.import_token = None
        exam.import_info = json.dumps({
            'fname': draft.get('fname', ''), 'mode': 'AI' + mode,
            'summary': summary, 'raw_cells': raw_updated,
            'errors': len(errors), 'source': 'ai',
            'operator': current_user.real_name,
            'time': __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }, ensure_ascii=False)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, message='写入失败已回滚：%s' % str(e)[:160])
    tab_service.clear_exam_cache(exam_id)
    log_operation(current_user, '导入', '考试', exam_id,
                  'AI 智能导入 %s：%s' % (draft.get('fname', ''),
                                       json.dumps(summary, ensure_ascii=False)),
                  module='grades')
    return jsonify(success=True, applied=True, summary=summary,
                   raw_cells=raw_updated, errors=errors[:20])


def _write_raw_scores(exam, rows):
    """把原始分写入 exam_scores.raw_score（store_service 不处理该列）"""
    from app.models.grades import ExamScore
    n = 0
    for r in rows:
        raw = r.get('raw_subjects') or {}
        if not raw and r.get('raw_total') is None:
            continue
        for sub, val in list(raw.items()) + [(TOTAL_SUBJECT, r.get('raw_total'))]:
            if val is None:
                continue
            row = ExamScore.query.filter_by(exam_id=exam.id, student_no=r['no'],
                                            subject=sub).first()
            if row is not None and row.raw_score != val:
                row.raw_score = val
                n += 1
    return n
