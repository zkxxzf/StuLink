# StuLink v1.19.62.64 2026-10-20
# 限时练：AI 智能导入（只让模型做「哪一列是什么」）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""限时练 AI 导入路由。

AI 只回答列映射；逐行解析、分数校验、缺考摒弃、学生匹配全部走
``timed_import`` / ``timed_match`` 的确定性代码。Key 走浏览器本机。
"""
import json

from flask import request
from flask_login import current_user, login_required

from app.extensions import db
from app.modules.grades import bp
from app.modules.grades.routes.timed_upload import read_upload, subject_from_form
from app.modules.grades.services import (timed_ai, timed_analysis,
                                         timed_import, timed_match,
                                         timed_service)
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation


def _ai_cfg():
    """读取前端本机 AI 配置（只用于本次调用，不落库）"""
    raw = (request.form.get('ai') or '').strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    key = (data.get('api_key') or '').strip() if isinstance(data, dict) else ''
    if not key:
        return None
    return {k: data.get(k) for k in ('api_key', 'base_url', 'model', 'provider',
                                     'max_tokens', 'max_input',
                                     'reasoning_effort')}


@bp.route('/timed/<int:tid>/ai/analyze', methods=['POST'])
@login_required
@perm_required('grades.import')
def timed_ai_analyze(tid):
    """上传文件 → 表头 + 样本 + 自动/智能列映射（JSON）"""
    exam = timed_service.assert_visible(tid)
    timed_service.assert_grade_writable(exam.grade)
    try:
        subject = subject_from_form()
        raw = read_upload()
        info = timed_ai.probe(raw)
    except timed_import.TimedParseError as e:
        return {'success': False, 'message': str(e)}
    allowed = timed_service.importable_subjects(exam.grade)
    if allowed is not None and subject not in allowed:
        return {'success': False,
                'message': f'无权导入「{subject}」：只能导入本人任教学科'}
    mapping, ai_error = info['auto'], ''
    cfg = _ai_cfg()
    if cfg:
        from app.modules.grades.services import ai_service
        cfg = ai_service.resolve_key(current_user, cfg)
        if cfg:
            ok, payload, _sec = ai_service._chat_completions(
                cfg, timed_ai.build_messages(info, subject))
            if ok:
                try:
                    content = payload['choices'][0]['message']['content']
                except (KeyError, IndexError, TypeError):
                    content = ''
                mapping = timed_ai.parse_ai_json(content) or mapping
            else:
                ai_error = str(payload)[:200]
    return {'success': True, 'sheet': info['sheet'],
            'header_row': info['header_row'], 'headers': info['headers'],
            'sample': info['sample'], 'auto': info['auto'],
            'mapping': mapping, 'ai_error': ai_error,
            'fields': [{'key': k, 'name': v}
                       for k, v in timed_ai.FIELD_NAMES.items()]}


@bp.route('/timed/<int:tid>/ai/apply', methods=['POST'])
@login_required
@perm_required('grades.import')
def timed_ai_apply(tid):
    """按列映射解析：dry_run=1 只预览，否则整科覆盖落库"""
    exam = timed_service.assert_visible(tid)
    timed_service.assert_grade_writable(exam.grade)
    try:
        subject = subject_from_form()
        raw = read_upload()
        mapping = json.loads(request.form.get('mapping') or '{}')
        parsed = timed_ai.apply_mapping(raw, mapping, subject, exam.grade)
        rows, errors, mstats = timed_match.match_students(
            parsed['rows'], exam.grade)
    except (timed_import.TimedParseError, ValueError) as e:
        return {'success': False, 'message': str(e)}
    allowed = timed_service.importable_subjects(exam.grade)
    if allowed is not None and subject not in allowed:
        return {'success': False,
                'message': f'无权导入「{subject}」：只能导入本人任教学科'}
    diff = timed_match.preview_diff(exam.id, subject, rows)
    if not rows and request.form.get('dry_run') != '1':
        # 整科覆盖前必须保证本次至少匹配到 1 人，否则会把该科清空
        return {'success': False, 'message': '本次没有匹配到任何学生，已取消导入'}
    if request.form.get('dry_run') == '1':
        return {'success': True, 'preview': {
            'subject': subject, 'rows': rows[:50], 'errors': errors[:50],
            'stats': {**parsed['stats'], **mstats}, 'diff': diff,
            'total_rows': len(rows)}}
    try:
        timed_service.delete_scores_of(exam.id, subject)
        db.session.flush()
        timed_analysis.save_subject(exam, subject, rows)
        exam.status = 'imported'
        # 与模板导入同一收尾：旧预检草稿/批次号必须失效，
        # 否则老师回到旧预览页点确认会用旧数据覆盖本次结果。
        exam.import_draft = None
        exam.import_token = None
        timed_service.renumber_grade(exam.grade)     # 与脚本导入同口径
        db.session.commit()
    except Exception as e:                              # noqa: BLE001
        db.session.rollback()
        return {'success': False, 'message': f'导入失败：{e}'}
    timed_analysis.invalidate(exam.id)                  # 必须在 commit 之后清
    log_operation(current_user, 'AI导入', '限时练成绩', exam.id,
                  f'{exam.grade} {exam.name} · {subject} {len(rows)} 人',
                  module='grades')
    return {'success': True, 'applied': len(rows),
            'stats': {**parsed['stats'], **mstats}, 'diff': diff}
