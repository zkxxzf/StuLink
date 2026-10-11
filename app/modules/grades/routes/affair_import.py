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
from app.models import Student
from app.models.grades import ExamAffair, AffairRoom, AffairStudent
from app.modules.grades import bp
from app.modules.grades.services import ai_import_service as aisvc
from app.modules.grades.utils import normalize_class_name
from app.modules.grades.routes.exam_affairs import _get_affair_checked
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation

MAX_FILES = 4
MAX_FILE_MB = 20


def _norm_room(no):
    s = str(no or '').strip()
    return s.zfill(2) if s.isdigit() else s


def _guess_kind(headers):
    """猜这个 sheet 是「学生名单」还是「考场位置」还是「不使用」"""
    a = aisvc.classify_affair_headers(headers)
    if (a.get('room_loc') is not None and a.get('room') is not None
            and 'name' not in a and 'no' not in a and 'exam_no' not in a):
        return 'rooms'
    if any(k in a for k in ('no', 'name', 'exam_no')):
        return 'students'
    return 'skip'


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

    # 默认只把「字段最多」的那张学生表设为 students，其余学生表默认跳过（避免桌签等重复表混入）
    stud = [s for s in sheets if _guess_kind(s['headers']) == 'students']
    best = None
    if stud:
        best = max(stud, key=lambda s: len(aisvc.classify_affair_headers(s['headers'])))
    kinds = {}
    for s in sheets:
        k = _guess_kind(s['headers'])
        if k == 'students' and s is not best:
            k = 'skip'
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


def _resolve_no(affair, rows):
    """把只有考号/姓名的行解析出学号（考号→学号、姓名+班级）"""
    need = [r for r in rows if not r.get('no')]
    if not need:
        return rows, []
    no_map, name_map = {}, {}
    for s in AffairStudent.query.filter_by(affair_id=affair.id).all():
        if s.exam_number:
            no_map.setdefault(s.exam_number, s.student_no)
        if s.name and s.class_name:
            name_map.setdefault((s.name, normalize_class_name(s.class_name)), s.student_no)
    for st in Student.query.filter_by(grade=affair.grade).all():
        if st.name and st.class_name:
            name_map.setdefault((st.name, normalize_class_name(st.class_name)), st.student_number)
    errs, miss = [], 0
    for r in rows:
        if r.get('no'):
            continue
        cand = no_map.get(r.get('exam_no')) if r.get('exam_no') else None
        if not cand:
            nm = r.get('name') or ''
            cls = normalize_class_name(r.get('class_name') or '')
            cand = name_map.get((nm, cls)) if (nm and cls) else None
        if cand:
            r['no'] = cand
        else:
            miss += 1
            errs.append({'line': r.get('line', 0),
                         'no': r.get('exam_no') or r.get('name') or '',
                         'reason': '未匹配到学号（考号/姓名+班级）'})
    if miss:
        rows[:] = [r for r in rows if r.get('no')]
    return rows, errs


def _parse(payload):
    """按前端提交的 sheets 解析 → (students, rooms, errors)"""
    students, rooms, errors = {}, {}, []
    for item in payload or []:
        kind = (item.get('kind') or 'skip')
        sh = item.get('_sheet')
        if sh is None or kind == 'skip':
            continue
        rows, errs = aisvc.rows_from_affair_mapping(sh, item.get('mapping') or {})
        errors.extend(errs)
        if kind == 'rooms':
            for r in rows:
                rn = _norm_room(r.get('room') or r.get('no'))
                loc = r.get('room_loc') or r.get('name') or ''
                if rn and loc:
                    rooms[rn] = loc
        else:                                     # students
            for r in rows:
                key = r.get('no') or r.get('exam_no') or (r.get('name') or '') + '|' + (r.get('class_name') or '')
                students[key] = r
    return list(students.values()), rooms, errors


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

    rows, rooms, errors = _parse(payload)
    rows, rerr = _resolve_no(affair, rows)
    errors.extend(rerr)
    n_attend = sum(1 for r in rows if r.get('attend') is not False)
    n_seat = sum(1 for r in rows if r.get('room'))
    preview = {'students': len(rows), 'attend': n_attend, 'seated': n_seat,
               'rooms': len({_norm_room(r['room']) for r in rows if r.get('room')}) or len(rooms),
               'room_locations': len(rooms), 'errors': len(errors)}
    if dry:
        return jsonify(success=True, preview=preview, errors=errors[:20])
    if not rows and not rooms:
        return jsonify(success=False, message='未解析出任何考生/考场，请检查列映射',
                       errors=errors[:20])

    try:
        # 1) 考场：先把已有批次考场建索引
        exist_rooms = {r.room_no: r for r in AffairRoom.query.filter_by(affair_id=affair.id).all()}
        room_nos = {_norm_room(r['room']) for r in rows if r.get('room')}
        for rn in sorted(room_nos):
            row = exist_rooms.get(rn)
            if row is None:
                row = AffairRoom(affair_id=affair.id, room_no=rn, capacity=30, subject='默认')
                db.session.add(row)
                exist_rooms[rn] = row
            if rooms.get(rn):
                row.location = rooms[rn]
        for rn, loc in rooms.items():            # 位置表里、但没人坐的考场也建出来
            if rn not in exist_rooms:
                db.session.add(AffairRoom(affair_id=affair.id, room_no=rn,
                                          location=loc, capacity=30, subject='默认'))

        # 2) 名单：upsert
        exist = {s.student_no: s for s in AffairStudent.query.filter_by(affair_id=affair.id).all()}
        added = updated = 0
        keep = set()
        for r in rows:
            no = r['no']
            keep.add(no)
            st = exist.get(no)
            if st is None:
                st = AffairStudent(affair_id=affair.id, student_no=no)
                db.session.add(st)
                exist[no] = st
                added += 1
            else:
                updated += 1
            st.name = r.get('name') or st.name
            st.class_name = normalize_class_name(r.get('class_name') or '') or st.class_name
            st.subject_selection = r.get('selection') or st.subject_selection or ''
            st.subject = r.get('selection') or '默认'
            st.is_attend = (r.get('attend') is not False)
            st.exam_number = r.get('exam_no') or st.exam_number
            st.room_no = _norm_room(r.get('room')) or None
            try:
                st.seat_no = int(str(r.get('seat')).strip()) if str(r.get('seat') or '').strip() else None
            except ValueError:
                st.seat_no = None
        removed = 0
        if replace:
            for no, st in list(exist.items()):
                if no not in keep:
                    db.session.delete(st)
                    removed += 1
        if room_nos:
            affair.status = 'arranged'
        db.session.commit()
        log_operation(current_user, '导入', '考务批次', affair.id,
                      'AI导入考生安排：考生%d（参加%d/编排%d）考场%d 新增%d/更新%d/移除%d'
                      % (len(rows), n_attend, n_seat, len(room_nos), added, updated, removed),
                      module='grades')
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        return jsonify(success=False, message='写入失败已回滚：%s' % str(e)[:160],
                       errors=errors[:20])
    return jsonify(success=True, applied=True, preview=preview,
                   added=added, updated=updated, removed=removed, errors=errors[:20])
