# StuLink v1.19.0 2026-10-11
# 考务：AI/规则 导入「考生安排 / 考场位置」（任意格式，免模板）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""考务导入路由

流程（不落服务端草稿：apply 时由前端把同一文件再传一次）：
  GET  /affairs/<aid>/ai-import          页面
  POST /affairs/<aid>/ai-import/analyze  上传（可多文件）→ 返回表头/样本/自动归类(+可选 AI 建议)
  POST /affairs/<aid>/ai-import/apply    按映射解析 → 预览(dry_run) 或 写入

写入内容：
  · 考生安排 → AffairStudent（学号/姓名/考试当时班级/选科/是否参加/考场/座号/考号）
            → 并按去重后的考场生成 AffairRoom
  · 考场位置 → 更新 AffairRoom 的 location（教室名称 ↔ 考场号）
"""
from __future__ import annotations

import json

from flask import render_template, request, jsonify
from flask_login import login_required, current_user

from app.extensions import db
from app.models.grades import ExamAffair
from app.modules.grades import bp
from app.modules.grades.services import ai_import_service as aisvc
from app.modules.grades.services import affair_import_service as afs
from app.modules.grades.routes.exam_affairs import _get_affair_checked
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation

MAX_FILES = 4
MAX_FILE_MB = 20

# 工作表用途（考务页口径）：成绩/分数线等不属于考务 → 不使用
_KIND_MAP = {'affair_students': 'students', 'affair_rooms': 'rooms'}


def _norm_room(no):
    return afs.norm_room(no)


def _guess_kind(headers):
    """猜这个 sheet 是「学生名单」/「考场位置」/「不使用」"""
    return _KIND_MAP.get(aisvc.guess_sheet_kind(headers), 'skip')


def _ai_cfg():
    raw = (request.form.get('ai') or '').strip()
    if not raw:
        return None
    try:
        d = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(d, dict) or not (d.get('api_key') or '').strip():
        return None
    return {'api_key': d['api_key'].strip(), 'base_url': (d.get('base_url') or '').strip(),
            'model': (d.get('model') or '').strip(), 'provider': (d.get('provider') or '').strip()}


def _read_sheets(files):
    sheets, errors = [], []
    for f in files:
        raw = f.read()
        if len(raw) > MAX_FILE_MB * 1024 * 1024:
            errors.append('%s 超过 %dMB，已跳过' % (f.filename, MAX_FILE_MB))
            continue
        try:
            pr = aisvc.probe(raw)
        except Exception as e:  # noqa: BLE001
            errors.append('%s 解析失败：%s' % (f.filename, str(e)[:80]))
            continue
        for sh in pr['sheets']:
            sh['file'] = f.filename
            sh['rows'] = aisvc.read_sheet_rows(raw, sh['name'], sh['header_row'])
            sheets.append(sh)
    return sheets, errors


@bp.route('/affairs/<int:aid>/ai-import')
@login_required
@perm_required('grades.edit')
def affair_ai_import(aid):
    affair = _get_affair_checked(aid)
    return render_template('grades/affairs/import.html', affair=affair,
                           fields=aisvc.AFFAIR_FIELD_LABELS)


@bp.route('/affairs/<int:aid>/ai-import/analyze', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_ai_import_analyze(aid):
    affair = _get_affair_checked(aid)
    files = [f for f in request.files.getlist('files') if f and f.filename]
    if not files:
        return jsonify(success=False, message='请先选择文件')
    if len(files) > MAX_FILES:
        return jsonify(success=False, message='一次最多 %d 个文件' % MAX_FILES)
    sheets, errors = _read_sheets(files)
    if not sheets:
        return jsonify(success=False, message='；'.join(errors) or '没有可用工作表')

    # 默认只把「字段最多」的那张学生表设为 students，其余学生表默认「座位表」（仅取容量）
    # 或跳过（如粘贴用等无考场/座号的打印表，避免重复串数据）
    stud = [s for s in sheets if _guess_kind(s['headers']) == 'students']
    best = None
    if stud:
        best = max(stud, key=lambda s: len(aisvc.classify_affair_headers(s['headers'])))
    kinds = {}
    for s in sheets:
        k = _guess_kind(s['headers'])
        if k == 'students' and s is not best:
            k = 'seats' if aisvc.has_seat_cols(s['headers']) else 'skip'
        kinds[s['name']] = k

    # 可选 AI 建议
    suggest, ai_err = {}, ''
    cfg = _ai_cfg()
    if cfg:
        from app.modules.grades.services import ai_service
        try:
            resolved = ai_service.resolve_key(current_user, cfg)
            system, user = aisvc.build_affair_ai_prompt({'sheets': sheets}, affair)
            ok, payload, _sec = ai_service._chat_completions(
                resolved, [{'role': 'system', 'content': system},
                           {'role': 'user', 'content': user}],
                timeout=90, max_tokens=2000)
            if ok:
                try:
                    text = payload['choices'][0]['message']['content']
                except (KeyError, IndexError, TypeError):
                    text = ''
                suggest = aisvc.parse_ai_mapping(text) or {}
                if not suggest:
                    ai_err = 'AI 返回内容无法解析为映射，请手工指定列'
            else:
                ai_err = 'AI 调用失败：%s' % str(payload)[:160]
        except Exception as e:  # noqa: BLE001
            ai_err = 'AI 调用异常：%s' % str(e)[:160]
    else:
        ai_err = '未检测到本机 AI Key，已用系统自动识别（可在下方手工调整）'

    sugg_by_name = {}
    for s in (suggest.get('sheets') or []):
        if s.get('name'):
            sugg_by_name[s['name']] = s.get('fields') or {}

    return jsonify(success=True, files=[f.filename for f in files],
                   sheets=[{'name': s['name'], 'file': s.get('file', ''),
                            'header_row': s['header_row'],
                            'header_rows': s.get('header_rows', []),
                            'headers': s['headers'][:150],
                            'kind': kinds.get(s['name'], 'skip'),
                            'auto': aisvc.classify_affair_headers(s['headers']),
                            'ai': sugg_by_name.get(s['name']) or {},
                            'sample': s['sample'][:2]} for s in sheets],
                   ai_error=ai_err, errors=errors)


@bp.route('/affairs/<int:aid>/ai-import/apply', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_ai_import_apply(aid):
    affair = _get_affair_checked(aid)
    files = [f for f in request.files.getlist('files') if f and f.filename]
    if not files:
        return jsonify(success=False, message='请重新选择文件（导入需再次上传同一文件）')
    try:
        spec = json.loads(request.form.get('spec') or '[]')
    except (TypeError, ValueError):
        spec = []
    dry = (request.form.get('dry_run') or '') == '1'
    replace = (request.form.get('replace') or '1') == '1'

    sheets, errors = _read_sheets(files)
    by_name = {}
    for s in sheets:
        by_name.setdefault(s['name'], s)
    payload = []
    for it in (spec or []):
        sh = by_name.get(it.get('name'))
        if sh is not None:
            payload.append({'_sheet': sh, 'mapping': it.get('mapping') or {},
                            'kind': it.get('kind') or 'skip'})
    if not payload:
        return jsonify(success=False, message='没有可用工作表')

    rows, rooms, errors = afs.parse(payload)
    rows, rerr = afs.resolve_student_no(affair, rows)
    errors.extend(rerr)
    n_attend = sum(1 for r in rows if r.get('attend') is not False)
    n_seat = sum(1 for r in rows if r.get('room'))
    room_set = {afs.norm_room(r['room']) for r in rows if r.get('room')} | set(rooms or {})
    preview = {'students': len(rows), 'attend': n_attend, 'seated': n_seat,
               'rooms': len(room_set),
               'room_locations': sum(1 for v in (rooms or {}).values() if v.get('location')),
               'room_caps': sum(1 for v in (rooms or {}).values() if v.get('capacity')),
               'errors': len(errors)}
    if dry:
        return jsonify(success=True, preview=preview, errors=errors[:20])
    if not rows and not rooms:
        return jsonify(success=False, message='未解析出任何考生/考场，请检查列映射',
                       errors=errors[:20])

    try:
        added, updated, removed, n_rooms = afs.apply_arrangement(affair, rows, rooms, replace)
        db.session.commit()
        log_operation(current_user, '导入', '考务批次', affair.id,
                      'AI导入考生安排：考生%d（参加%d/编排%d）考场%d 新增%d/更新%d/移除%d'
                      % (len(rows), n_attend, n_seat, n_rooms, added, updated, removed),
                      module='grades')
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        return jsonify(success=False, message='写入失败已回滚：%s' % str(e)[:160],
                       errors=errors[:20])
    return jsonify(success=True, applied=True, preview=preview,
                   added=added, updated=updated, removed=removed, errors=errors[:20])
