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

from flask import render_template, request, jsonify, redirect, url_for, flash, current_app
from flask_login import login_required, current_user

from app.extensions import db
from app.models.grades import Exam, SUBJECTS, TOTAL_SUBJECT
from app.modules.grades import bp
from app.modules.grades.services import ai_import_service as aisvc
from app.modules.grades.services import import_service, store_service, ranking, tab_service
from app.modules.grades.services.exam_guard import assert_exam_visible
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation

MAX_AI_FILES = 8
# 2026-10-12 安全审计 M-6：原先这里是 20MB，而 Flask 层 MAX_CONTENT_LENGTH 只有 16MB，
# 「len(raw) > 20MB」这个分支**永远走不到**（死代码），却给人"已设上限"的错觉。
# 真正的风险是 MAX_AI_FILES 个文件同时进内存 + 全量行 json.dumps 写 import_draft。
# 现把单文件降到 5MB，并对**所有文件总字节数**设上限（见 _apply_draft）。
MAX_FILE_MB = 5
MAX_TOTAL_MB = 40
MAX_DRAFT_MB = 8          # import_draft（TEXT 列）落库上限，超出说明表太大，要求分批


def _exam_has_scores(exam_id):
    """本场是否已有成绩（用于 mode=B 整场覆盖的二次确认判定）"""
    from app.models.grades import ExamScore
    return db.session.query(ExamScore.id).filter_by(exam_id=exam_id).first() is not None


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
                           subjects=SUBJECTS, score_kinds=aisvc.SCORE_KIND_LABEL,
                           affair_fields=aisvc.AFFAIR_FIELD_LABELS)


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
    # 2026-10-12 安全审计 M-6：多文件同时进内存，须对**总字节数**设上限
    # （单文件 5MB × 8 = 40MB，与下方 MAX_TOTAL_MB 对齐）
    _total = 0
    for f in files:
        f.stream.seek(0, 2)
        _total += f.stream.tell()
        f.stream.seek(0)
    if _total > MAX_TOTAL_MB * 1024 * 1024:
        return jsonify(success=False,
                       message='文件总大小超过 %dMB，请分批导入' % MAX_TOTAL_MB)

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
    # 2026-10-12 安全审计 M-6：草稿是 json.dumps 后写 TEXT 列，
    # 8 张大表可能产生数百 MB → 直接拖垮 SQLite。落库前先量长度。
    draft_text = json.dumps(draft, ensure_ascii=False)
    if len(draft_text.encode('utf-8')) > MAX_DRAFT_MB * 1024 * 1024:
        return jsonify(success=False,
                       message='表格数据量过大（超过 %dMB），请减少工作表或分行数后分批导入'
                               % MAX_DRAFT_MB)
    exam.import_draft = draft_text
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
                timeout=90, max_tokens=3000)
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

    # 工作表用途：成绩 / 分数线 / 考生安排 / 考场位置 / 座位表 / 不使用
    # 考生安排只保留「字段最全」的一张；其余学生表若带考场/座号 → 当座位表（仅取容量），
    # 否则（如粘贴用等打印表）不使用，防串数据
    kinds = {s['name']: aisvc.guess_sheet_kind(s['headers']) for s in sheets}
    stud = [s for s in sheets if kinds[s['name']] == 'affair_students']
    best = (max(stud, key=lambda s: len(aisvc.classify_affair_headers(s['headers'])))
            if stud else None)
    for s in sheets:
        if kinds[s['name']] == 'affair_students' and s is not best:
            kinds[s['name']] = ('affair_seats' if aisvc.has_seat_cols(s['headers'])
                                else 'skip')

    return jsonify(success=True, token=token,
                   files=[f.filename for f in files],
                   sheets=[{'name': s['name'], 'file': s.get('file', ''),
                            'header_row': s['header_row'],
                            'header_rows': s.get('header_rows', []),
                            'headers': s['headers'][:150],
                            'auto': aisvc.classify_headers(s['headers']),
                            'affair_auto': aisvc.classify_affair_headers(s['headers']),
                            'kind': kinds.get(s['name'], 'skip'),
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
        if kind == 'skip':          # 用户标记「不使用此工作表」（如分析表/打印表）
            continue
        rows, errs = aisvc.rows_from_mapping(sh, item.get('mapping') or {}, kind, exam)
        errors.extend(errs)
        as_raw = (kind == 'raw')
        for r in rows:
            if as_raw:
                # 这份是“原始成绩表”：它给的分数就是原始分，不进主分
                r['raw_subjects'] = dict(r['subjects'])
                r['raw_total'], r['total'] = r['total'], None
                r['subjects'] = {}
            tgt = by_no.setdefault(r['no'] or ('@' + (r.get('exam_no') or '')), {
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


def _resolve_student_no(exam, rows):
    """把「只有考号、没有学号」的行解析出学号（联考导出常见）

    优先级：① 考号 → 学号（本场考务名单 exam_number / 本场已导入成绩的 exam_no）
            ② 姓名 + 班级 → 学号（本场考务名单、本年级学籍）
    仍解析不出的行剔除并进 errors。返回 (rows, errors)。
    """
    from app.models import Student
    from app.models.grades import ExamAffair, AffairStudent, ExamScore
    from app.modules.grades.utils import normalize_class_name
    if not any(not (r.get('no') or '').strip() for r in rows):
        return rows, []
    no_map, name_map = {}, {}
    # 2026-10-12 数据可信度审计（§3.6-3）：同班同名（双胞胎/改名重复）时，
    # 原先 name_map.setdefault(...) 只保留第一个学号 → 第二个学生的成绩被
    # **静默挂到第一个人头上**，而且第二人根本不出现在 errors 里。
    # 改为收集全部候选，命中多个时按"歧义"处理：不猜，报错要求人工指定。
    name_all = {}
    aff = (ExamAffair.query.filter_by(exam_id=exam.id)
           .order_by(ExamAffair.id.desc()).first())
    if aff:
        for s in AffairStudent.query.filter_by(affair_id=aff.id).all():
            if s.exam_number:
                no_map.setdefault(s.exam_number, s.student_no)
            if s.name and s.class_name:
                k = (s.name, normalize_class_name(s.class_name))
                name_all.setdefault(k, set()).add(s.student_no)
    for r in ExamScore.query.filter(ExamScore.exam_id == exam.id,
                                    ExamScore.exam_no.isnot(None)).all():
        if r.exam_no:
            no_map.setdefault(r.exam_no, r.student_no)
    for st in Student.query.filter_by(grade=exam.grade).all():
        if st.name and st.class_name:
            k = (st.name, normalize_class_name(st.class_name))
            name_all.setdefault(k, set()).add(st.student_number)
    for k, v in name_all.items():
        name_map[k] = sorted(v)[0] if len(v) == 1 else None
    errs, miss = [], 0
    for r in rows:
        if (r.get('no') or '').strip():
            continue
        en = (r.get('exam_no') or '').strip()
        cand = no_map.get(en) if en else None
        if not cand:
            nm = (r.get('name') or '').strip()
            cls = normalize_class_name(r.get('class_name') or '')
            k = (nm, cls) if (nm and cls) else None
            if k and k in name_all:
                if name_map[k] is None:
                    # 同名多人：明确报错，绝不猜
                    errs.append({'line': r.get('line', 0), 'no': en or nm,
                                 'reason': '姓名+班级在本年级存在多名学生（%s），'
                                           '无法确定学号，请改用学号列或考号列导入'
                                           % '、'.join(sorted(name_all[k]))})
                    miss += 1
                    continue
                cand = name_map[k]
        if cand:
            r['no'] = cand
        else:
            miss += 1
            errs.append({'line': r.get('line', 0), 'no': en or (r.get('name') or ''),
                         'reason': '按考号/姓名+班级未匹配到学号（可先导入本场考务）'})
    if miss:
        rows[:] = [r for r in rows if (r.get('no') or '').strip()]
    return rows, errs


def _apply_affair(exam, items, dry=False):
    """把「考生安排 / 考场位置」表写到本考试的考务批次（没有则自动创建）。

    items: [{_sheet, mapping, kind: 'students'|'rooms'}]
    返回 (info, errors)
    """
    from app.models.grades import ExamAffair
    from app.modules.grades.services import affair_import_service as afs
    affair = ExamAffair.query.filter_by(exam_id=exam.id).first()
    created = False
    if affair is None:
        affair = ExamAffair(name='%s 考务' % exam.name, grade=exam.grade,
                            exam_date=exam.exam_date, exam_id=exam.id)
        db.session.add(affair)
        db.session.flush()
        created = True
    arows, rooms, errors = afs.parse(items)
    arows, rerr = afs.resolve_student_no(affair, arows)
    errors.extend(rerr)
    info = {'students': len(arows), 'attended': sum(1 for r in arows if r.get('attend') is not False),
            'seated': sum(1 for r in arows if r.get('room')), 'rooms': len(rooms),
            'created': created, 'affair_id': affair.id,
            'affair_name': affair.name}
    if dry:
        return info, errors
    if arows or rooms:
        info['added'], info['updated'], info['removed'], info['rooms'] = \
            afs.apply_arrangement(affair, arows, rooms, True)
    else:
        info['added'] = info['updated'] = info['removed'] = 0
    return info, errors


def _apply_bands(exam, band_items, dry=False):
    """解析「各科分数线」表 → 写 ExamBand（按 方向×学科 覆盖），并按线名绑定分层模板。

    返回 (info, errors)。info: {subjects, bands, lines, template, applied}
    """
    from app.models.grades import BandTemplate
    from app.modules.grades.routes.bands import _save_bands
    parsed, errors = {}, []
    for it in band_items:
        sh = it.get('_sheet')
        if sh is None:
            continue
        got, errs = aisvc.parse_bands(sh)
        errors.extend(errs or [])
        for k, lst in (got or {}).items():
            parsed[k] = (parsed.get(k) or []) + lst
    if not parsed:
        return None, errors
    names = []
    for _k, lst in parsed.items():
        for nm, _v in lst:
            if nm not in names:
                names.append(nm)
    info = {'subjects': len(parsed), 'bands': sum(len(v) for v in parsed.values()),
            'lines': names, 'template': '', 'applied': []}
    if dry:
        info['applied'] = ['%s·%s' % (d or '全体', s) for (d, s) in parsed]
        return info, errors
    for (direction, subject), lst in parsed.items():
        ordered = sorted(lst, key=lambda x: -x[1])       # 分数降序 → seq 1..n（seq1=最高线）
        payload = [{'seq': i + 1, 'name': nm, 'lower_mode': 'score', 'lower_value': v}
                   for i, (nm, v) in enumerate(ordered)]
        _items, err = _save_bands(exam.id, direction, subject, payload)
        if err:
            errors.append({'line': 0, 'no': '',
                           'reason': '%s·%s：%s' % (direction or '全体', subject, err)})
            return None, errors
        info['applied'].append('%s·%s' % (direction or '全体', subject))
    # 分层模板：按线名序列 find-or-create 并绑定到本考试
    tpl = next((t for t in BandTemplate.query.all() if t.names() == names), None)
    if tpl is None:
        base = ('、'.join(names) or '分数线')[:50]
        nm = base
        i = 1
        while BandTemplate.query.filter_by(name=nm).first():
            i += 1
            nm = ('%s(%d)' % (base, i))[:50]
        tpl = BandTemplate(name=nm, lower_mode='score',
                           layers_json=json.dumps([{'name': n, 'ratio': None} for n in names],
                                                  ensure_ascii=False),
                           remark='由分数线文件导入', is_builtin=False)
        db.session.add(tpl)
        db.session.flush()
    exam.set_band_template(tpl.id, tpl.name)
    db.session.add(exam)
    db.session.commit()
    info['template'] = tpl.name
    return info, errors


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

    # 按工作表用途分流：成绩 / 分数线（→分层） / 考生安排·考场位置·座位表（→本场考务）
    _AFF_KINDS = ('affair_students', 'affair_rooms', 'affair_seats')
    score_items = [it for it in payload
                   if (it.get('score_kind') or 'converted')
                   not in ('bands', 'skip') + _AFF_KINDS]
    band_items = [it for it in payload if (it.get('score_kind') or '') == 'bands']
    aff_items = [{'_sheet': it['_sheet'], 'mapping': it.get('mapping'),
                  'kind': {'affair_students': 'students', 'affair_rooms': 'rooms',
                           'affair_seats': 'seats'}.get(it.get('score_kind'))}
                 for it in payload
                 if (it.get('score_kind') or '') in _AFF_KINDS]
    dry = bool(data.get('dry_run'))

    errors = []
    band_info = None
    affair_info = None
    if band_items:
        band_info, berr = _apply_bands(exam, band_items, dry=dry)
        errors.extend(berr or [])
        if berr and any('：' in (e.get('reason') or '') for e in berr):
            db.session.rollback()
            return jsonify(success=False, message='分数线写入失败：%s' % berr[0]['reason'],
                           errors=errors[:20])
    if aff_items:
        affair_info, aerr = _apply_affair(exam, aff_items, dry=dry)
        errors.extend(aerr or [])

    rows = []
    if score_items:
        rows, e1 = _merge_rows(score_items, exam)
        errors.extend(e1)
        rows, rerr = _resolve_student_no(exam, rows)      # 只有考号时按考号/姓名反查学号
        errors.extend(rerr)
    if not rows and not band_info and not affair_info:
        return jsonify(success=False,
                       message='未解析出任何成绩/分数线/考务，请检查列映射与工作表用途',
                       errors=errors[:20])

    # 补齐学生快照（姓名/班级/班型/方向…）并统计
    if rows:
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
    if band_info:
        preview['bands'] = band_info
    if affair_info:
        preview['affair'] = affair_info
    if dry:
        return jsonify(success=True, preview=preview, errors=errors[:20])
    # 2026-10-12 数据可信度审计（§3.4）：AI 导入默认 mode='B' **整场覆盖**，
    # 而页面「确认导入」可不点预览直接写入 → 一次误点就把整场成绩清空重导。
    # 这里对齐删除考试的口径：本场已有成绩 + 模式 B → 必须输入当前账号密码确认。
    if rows and mode == 'B' and _exam_has_scores(exam_id):
        if not bool(data.get('confirmed')):
            return jsonify(success=False, need_confirm=True,
                           message='本场已有成绩，模式 B（整场重置）将清空后重导，'
                                   '请输入当前账号密码确认')
        uname = (data.get('username') or '').strip()
        pwd = data.get('password') or ''
        if uname != current_user.username or not current_user.check_password(pwd):
            return jsonify(success=False, need_confirm=True,
                           message='账号或密码不正确，已取消整场重置')
    if not rows:
        # 只导入了分数线 / 考务（没有成绩可写）
        exam.import_draft = None
        exam.import_token = None
        db.session.commit()
        parts = []
        if band_info:
            parts.append('分数线 %d 方向×学科 / %d 条线（模板 %s）'
                         % (band_info['subjects'], band_info['bands'], band_info['template']))
        if affair_info:
            parts.append('考务 考生%d（编排%d）考场%d%s'
                         % (affair_info['students'], affair_info['seated'], affair_info['rooms'],
                            '（新建批次）' if affair_info.get('created') else ''))
        log_operation(current_user, '导入', '考试', exam_id,
                      'AI 导入（无成绩）：' + '；'.join(parts), module='grades')
        out = {'success': True, 'applied': True, 'summary': {'non_score_only': True},
               'preview': preview, 'errors': errors[:20]}
        if band_info:
            out['bands'] = band_info
        if affair_info:
            out['affair'] = affair_info
        return jsonify(**out)

    # 正式写入（复用既有导入管道；整场覆盖式，避免与旧数据混在一起）
    # store_service.apply_import 需要 headers['subjects']（它按这些科目列遍历写入），
    # 之前只给了 {'no': True} → KeyError 'subjects'，导致导入阶段整批回滚。
    file_subjects = [s for s in SUBJECTS
                     if any(s in (r.get('subjects') or {}) for r in rows)]
    parsed = {'headers': {'no': True, 'subjects': file_subjects}, 'rows': rows}
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
            'bands': band_info, 'errors': len(errors), 'source': 'ai',
            'operator': current_user.real_name,
            'time': __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }, ensure_ascii=False)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        # 2026-10-12 质量审计 P1-5：原实现只回给前端 160 字符、服务端无任何日志，
        # 生产环境无法排查。同模式的 exams.py / teachers.py 都有 exc_info=True。
        current_app.logger.error('AI 导入写入失败 exam_id=%s: %s', exam_id, e, exc_info=True)
        return jsonify(success=False, message='写入失败已回滚：%s' % str(e)[:160])
    tab_service.clear_exam_cache(exam_id)
    log_operation(current_user, '导入', '考试', exam_id,
                  'AI 智能导入 %s：%s%s%s' % (draft.get('fname', ''),
                                          json.dumps(summary, ensure_ascii=False),
                                          ('；分数线 %d 条（模板 %s）'
                                           % (band_info['bands'], band_info['template']))
                                          if band_info else '',
                                          ('；考务 考生%d/考场%d'
                                           % (affair_info['added'], affair_info['rooms']))
                                          if affair_info else ''),
                  module='grades')
    out = {'success': True, 'applied': True, 'summary': summary,
           'raw_cells': raw_updated, 'errors': errors[:20]}
    if band_info:
        out['bands'] = band_info
    if affair_info:
        out['affair'] = affair_info
    return jsonify(**out)


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
