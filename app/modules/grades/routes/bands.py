# StuLink v1.18.0.0 2026-09-23
# 分层/划线配置：模板应用（四层/高考线）、手动编辑、人数预览
# 划线维度：方向 × 学科（subject='总分' 为总分线，学科名为单科线，两者独立）
# Copyright (c) 2026 zkxxzf
import logging
import math
import os

from flask import render_template, request, jsonify, current_app
from flask_login import login_required, current_user
from app.extensions import db
from app.models.grades import Exam, ExamBand, BandTemplate, SUBJECTS, TOTAL_SUBJECT
from app.modules.grades import bp
from app.modules.grades.services import stats_service as st
from app.modules.grades.utils import delete_cache_prefix, invalidate_exam_cache
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation

# 模板定义：[(层名, ratio%)]
TEMPLATES = {
    '4': [('优秀', 20), ('良好', 60), ('及格', 95), ('待提升', 0)],
    'gaokao': [('清北', 3), ('985', 8), ('211', 20), ('特控', 60), ('本科', 95), ('未上线', 0)],
}


def _log():
    """划线专用日志：写 StuLink/logs/bands.log，同时输出到 Flask 默认日志（stderr）

    排查保存失败时看它即可（实时跟踪）：
        Get-Content StuLink\\logs\\bands.log -Tail 50 -Wait
    """
    lg = logging.getLogger('stulink.bands')
    if not lg.handlers:
        lg.setLevel(logging.INFO)
        lg.propagate = True          # 同时走 Flask 默认日志（控制台 / startup_err.log）
        try:
            log_dir = os.path.join(current_app.root_path, '..', 'logs')
            os.makedirs(log_dir, exist_ok=True)
            fh = logging.FileHandler(os.path.join(log_dir, 'bands.log'),
                                     encoding='utf-8')
            fh.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s',
                                              '%m-%d %H:%M:%S'))
            lg.addHandler(fh)
        except Exception:            # 日志不可写不能影响业务
            pass
    return lg


def _is_subject(value):
    return value == TOTAL_SUBJECT or value in SUBJECTS


def _scope_scores(data, direction, subject):
    """（方向 × 学科）参考人群的分数列表"""
    if subject == TOTAL_SUBJECT:
        return [t['score'] for t in data.totals_of(direction=direction)]
    return list(data.scores_of_subject(subject, direction=direction))


def _ratio_from_sorted(desc, ratio):
    """由已降序排好的分数换算下界（名次 ≤ n*ratio% 档最低分）；无参考返回 0"""
    if not desc:
        return 0.0
    if ratio <= 0:
        return 0.0
    if ratio >= 100:
        return desc[-1]
    n = len(desc)
    idx = max(0, int(math.ceil(n * ratio / 100.0)) - 1)
    return desc[min(idx, n - 1)]


def _ratio_to_score(data, direction, subject, ratio):
    """按参考人数比例换算下界；无参考返回 0"""
    return _ratio_from_sorted(sorted(_scope_scores(data, direction, subject), reverse=True),
                              ratio)


def _rank_to_score(data, direction, subject, rank):
    """按名次换算下界：第 rank 名（1 起）的分数；参考人数不足时取最后一名。

    例：填 50 → 取该（方向×学科）第 50 名的分数作为该层下界，
    即「前 50 名」这一层。落库与比例模式一样换算成实际分数线（统一存 score）。
    """
    desc = sorted(_scope_scores(data, direction, subject), reverse=True)
    if not desc:
        return 0.0
    try:
        r = int(rank)
    except (TypeError, ValueError):
        return 0.0
    return desc[min(max(r - 1, 0), len(desc) - 1)]


def _band_counts(data, direction, subject, bands):
    """bands: [(name, lower)]（seq 升序，最高层在前）→ 每层人数（含占比）
    取首个 lower<=score 的层（高分落入高层）
    """
    counts = [0] * len(bands)
    total = 0
    for s in _scope_scores(data, direction, subject):
        if s is None:
            continue
        total += 1
        for i, (_name, lower) in enumerate(bands):
            if s >= lower:
                counts[i] += 1
                break
        else:
            if counts:          # 低于最低层（层下界应=0，理论不出现）
                counts[-1] += 1
    return counts, total


@bp.route('/exams/<int:exam_id>/bands')
@login_required
@perm_required('grades.settings')
def bands_page(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    data = st.cached_exam_data(exam_id)
    # 选科后为 ['物理','历史']；选科前无方向 → ['']（空串=全体，不分方向）
    directions = data.directions or ['']
    # 各组实际开考学科（矩阵列）：四选二导致同方向学生选科不同，取并集
    dir_subjects = {d: [s for s in SUBJECTS
                        if data.scores_of_subject(s, direction=d or None)]
                    for d in directions}
    return render_template('grades/bands.html', exam=exam, directions=directions,
                           dir_subjects=dir_subjects,
                           total_count=len(data.total_rows))


def _bands_payload(exam_id, direction, subject, data=None):
    """返回该（方向 × 学科）bands（含预览）JSON 列表

    data 可传入已构造好的 ExamData 以复用（批量接口用，避免逐列重复加载明细行）。
    """
    rows = (ExamBand.query.filter_by(exam_id=exam_id, subject=subject)
            .filter(ExamBand.direction.in_([direction, '']))
            .order_by(ExamBand.seq.asc()).all())
    if not rows:
        return []
    # 以该方向实际 bands 为准（'' 通用组也展开到该方向显示）
    real = [r for r in rows if r.direction == direction]
    if not real:
        real = rows
    bands = [(r.name, r.lower_value) for r in real]
    data = data or st.cached_exam_data(exam_id)
    counts, total = _band_counts(data, direction, subject, bands)
    # 比例模式预览：排序一次复用，避免每层都重排全量分数
    sorted_desc = None
    out = []
    for i, r in enumerate(real):
        if r.lower_mode == 'ratio':
            if sorted_desc is None:
                sorted_desc = sorted(_scope_scores(data, direction, subject), reverse=True)
            preview = _ratio_from_sorted(sorted_desc, r.lower_value)
        else:
            preview = r.lower_value
        out.append({
            'id': r.id, 'seq': r.seq, 'name': r.name, 'lower_mode': r.lower_mode,
            'lower_value': r.lower_value, 'preview_score': preview,
            'count': counts[i] if i < len(counts) else 0,
            'ratio': st.fmt_rate(counts[i], total) if i < len(counts) else None,
        })
    return out


@bp.route('/api/bands')
@login_required
@perm_required('grades.settings')
def bands_get():
    exam_id = request.args.get('exam_id', type=int)
    direction = request.args.get('direction', '').strip()
    subject = request.args.get('subject', '').strip() or TOTAL_SUBJECT
    Exam.query.get_or_404(exam_id)
    if not _is_subject(subject):
        return jsonify(success=False, message='学科无效'), 400
    return jsonify(success=True, data=_bands_payload(exam_id, direction, subject))


@bp.route('/api/bands/matrix')
@login_required
@perm_required('grades.settings')
def bands_matrix():
    """一次返回全部（方向 × 学科）划线，供划线页整表回填

    原实现：前端对每一列发一次 /api/bands，后端每列都要重建 ExamData
    （一场考试约 1 万行明细），2 个方向 × 多科就是十几次全量加载，是划线页慢的主因。
    改为单次请求、全程共用一份 ExamData。
    """
    exam_id = request.args.get('exam_id', type=int)
    Exam.query.get_or_404(exam_id)
    data = st.cached_exam_data(exam_id)
    directions = data.directions or ['']
    out = {}
    for d in directions:
        subs = [s for s in SUBJECTS if data.scores_of_subject(s, direction=d or None)]
        for sub in [TOTAL_SUBJECT] + subs:
            out[f'{d}|{sub}'] = _bands_payload(exam_id, d, sub, data=data)
    return jsonify(success=True, data=out)


def _save_bands(exam_id, direction, subject, payload):
    """payload: [{seq,name,lower_mode,lower_value}]（seq 升序=最高层在前，下界分数依次降低）
    ratio 模式在保存时按当前参考人数换算为实际分数线落库（统一存 score）
    """
    data = st.ExamData(exam_id)
    items = []
    seen_seq = set()
    prev_lower = None
    for b in payload:
        seq = int(b['seq'])
        name = (b.get('name') or '').strip()
        mode = b.get('lower_mode') in ('score', 'ratio', 'rank') and b['lower_mode'] or 'score'
        try:
            value = float(b.get('lower_value'))
        except (TypeError, ValueError):
            return None, f'第 {seq} 层下界值不是数字'
        if not name or seq < 1 or seq in seen_seq:
            return None, '层名不能为空且序号不能重复'
        # 三种下界方式各自的取值范围：分数≥0；比例 0-100；名次≥1
        if mode == 'ratio' and (value < 0 or value > 100):
            return None, f'第 {seq} 层（{name}）比例需在 0-100 之间'
        if mode == 'rank' and value < 1:
            return None, f'第 {seq} 层（{name}）名次需 ≥ 1'
        if mode == 'score' and value < 0:
            return None, f'第 {seq} 层（{name}）下界分数不能为负'
        if mode == 'ratio':
            # 按（方向×学科）参考人数比例换算为分数线
            value = _ratio_to_score(data, direction, subject, value)
        elif mode == 'rank':
            # 按名次换算为分数线（「前 N 名」）
            value = _rank_to_score(data, direction, subject, value)
        # 修复：允许相邻两层下界相等——不同比例换算落在同一同分段时必然相等
        #（同分学生无法用分数线切分，等界=高层优先收录）；仅拦截逆序（高于上一层）
        if prev_lower is not None and value > prev_lower:
            return None, f'第 {seq} 层（{name}）下界需不高于上一层（{prev_lower}）'
        prev_lower = value
        seen_seq.add(seq)
        items.append({'seq': seq, 'name': name, 'lower_mode': 'score',
                      'lower_value': value})
    # 覆盖保存（仅覆盖该 方向×学科 组合）
    ExamBand.query.filter_by(exam_id=exam_id, direction=direction,
                             subject=subject).delete()
    for it in items:
        db.session.add(ExamBand(exam_id=exam_id, direction=direction, subject=subject,
                                seq=it['seq'], name=it['name'],
                                lower_mode=it['lower_mode'],
                                lower_value=it['lower_value']))
    return items, None


@bp.route('/api/bands/save', methods=['POST'])
@login_required
@perm_required('grades.settings')
def bands_save():
    data = request.get_json(silent=True) or {}
    exam_id = int(data.get('exam_id') or 0)
    direction = (data.get('direction') or '').strip()
    subject = (data.get('subject') or '').strip() or TOTAL_SUBJECT
    exam = Exam.query.get_or_404(exam_id)
    if direction not in ('物理', '历史'):
        return jsonify(success=False, message='方向无效'), 400
    if not _is_subject(subject):
        return jsonify(success=False, message='学科无效'), 400
    items, err = _save_bands(exam_id, direction, subject, data.get('bands') or [])
    if err:
        _log().warning('单个保存失败 exam_id=%s %s·%s：%s', exam_id, direction, subject, err)
        return jsonify(success=False, message=err), 400
    db.session.commit()
    _log().info('单个保存成功 exam_id=%s %s·%s：%d 层 %s', exam_id, direction, subject,
                len(items), [(i['name'], i['lower_value']) for i in items])
    invalidate_exam_cache(exam_id)
    log_operation(current_user, '划线', '考试', exam_id,
                  f'{exam.name} {direction}·{subject}分层保存', module='grades')
    return jsonify(success=True, data=_bands_payload(exam_id, direction, subject))


@bp.route('/api/bands/batch', methods=['POST'])
@login_required
@perm_required('grades.settings')
def bands_batch():
    """一次性批量划线：按「层 ×（方向 × 学科）」矩阵写入

    请求体：
      exam_id        考试 id
      mode           'score' 固定分数 / 'ratio' 人数比例（比例按该方向该科自身排名换算）
      scope          'per_subject' 分科逐格 / 'all' 全科统一（一套值套用到所有学科）
      include_total  是否含总分列（含总分时每个方向多一列 总分）
      layers         scope=all       → [{seq, name, value}]
                     scope=分科      → [{seq, name, values: {'物理|语文': 120, ...}}]
    留空的格子不划线；该（方向×学科）无参考学生时自动跳过。
    """
    data = request.get_json(silent=True) or {}
    exam_id = int(data.get('exam_id') or 0)
    exam = Exam.query.get_or_404(exam_id)
    mode = data.get('mode') if data.get('mode') in ('score', 'ratio', 'rank') else 'score'
    scope = 'all' if data.get('scope') == 'all' else 'per_subject'
    include_total = bool(data.get('include_total'))
    layers = data.get('layers') or []
    if not layers:
        return jsonify(success=False, message='没有可保存的层'), 400

    _log().info('=== 批量划线请求 exam_id=%s 用户=%s mode=%s scope=%s 含总分=%s 层数=%s',
                exam_id, current_user.username, mode, scope, include_total, len(layers))
    edata = st.ExamData(exam_id)
    directions = edata.directions or ['']     # 选科前：空串=全体
    applied, skipped = [], []
    for direction in directions:
        subs = [s for s in SUBJECTS
                if edata.scores_of_subject(s, direction=direction or None)]
        if include_total:
            subs = [TOTAL_SUBJECT] + subs
        for sub in subs:
            label = f'{direction or "全体"}·{sub}'
            payload = []
            for lay in layers:
                try:
                    seq = int(lay.get('seq'))
                except (TypeError, ValueError):
                    _log().warning('  层序号无效：%r（层名 %s）', lay.get('seq'), lay.get('name'))
                    return jsonify(success=False, message='层序号无效'), 400
                name = (lay.get('name') or '').strip()
                if scope == 'all':
                    value = lay.get('value')
                else:
                    value = (lay.get('values') or {}).get(f'{direction}|{sub}')
                if value is None or value == '':
                    continue        # 该格未填 → 该组合不划线
                payload.append({'seq': seq, 'name': name, 'lower_mode': mode,
                                'lower_value': value})
            if not payload:
                _log().info('  跳过 %s：未填任何下界值', label)
                skipped.append(label)      # 整列未填
                continue
            if not _scope_scores(edata, direction, sub):
                _log().info('  跳过 %s：无参考学生', label)
                skipped.append(label)      # 该组合无参考学生
                continue
            items, err = _save_bands(exam_id, direction, sub, payload)
            if err:
                db.session.rollback()
                _log().warning('  校验失败 %s：%s（提交值 %s）', label, err,
                               [(p['name'], p['lower_value']) for p in payload])
                return jsonify(success=False, message=f'{label}：{err}'), 400
            _log().info('  写入 %s：%s', label,
                        [(i['name'], i['lower_value']) for i in items])
            applied.append(label)
    if not applied:
        db.session.rollback()
        _log().warning('结果：无组合可写入（跳过 %d 个：%s）', len(skipped), skipped)
        return jsonify(success=False,
                       message='没有可写入的组合：请至少填写一个格子（该组合无参考学生时会被跳过）'), 400
    # 考试 ↔ 分层模板绑定：后续成绩分析（分层人数、各班上线人数/上线率、自由表的「层」）
    # 都按该模板的层来算。传了 template_id 才处理（0＝解除绑定），老调用方不受影响。
    if 'template_id' in data:
        try:
            tpl_id = int(data.get('template_id') or 0)
        except (TypeError, ValueError):
            tpl_id = 0
        if tpl_id:
            tpl_obj = BandTemplate.query.get(tpl_id)
            if tpl_obj:
                exam.set_band_template(tpl_obj.id, tpl_obj.name)
        else:
            exam.set_band_template(None)
        db.session.add(exam)

    db.session.commit()
    _log().info('结果：成功 %d 个组合，跳过 %d 个 %s', len(applied), len(skipped), skipped)
    invalidate_exam_cache(exam_id)
    log_operation(current_user, '划线', '考试', exam_id,
                  f'{exam.name} 批量划线（{mode}，'
                  f'{"全科统一" if scope == "all" else "分科"}，{len(applied)} 个组合）',
                  module='grades')
    msg = f'批量划线完成：{len(applied)} 个（方向×学科）组合'
    if skipped:
        msg += f'；跳过 {len(skipped)} 个（未填写或无参考学生）'
    return jsonify(success=True, message=msg, applied=applied, skipped=skipped)


@bp.route('/api/bands/apply', methods=['POST'])
@login_required
@perm_required('grades.settings')
def bands_apply():
    data = request.get_json(silent=True) or {}
    exam_id = int(data.get('exam_id') or 0)
    tpl = data.get('template') or '4'
    both = bool(data.get('both'))
    all_subjects = bool(data.get('all_subjects'))
    exam = Exam.query.get_or_404(exam_id)
    if tpl not in TEMPLATES:
        return jsonify(success=False, message='模板不存在'), 400
    edata = st.ExamData(exam_id)
    directions = edata.directions or ['物理', '历史']
    targets = directions if both else ([data.get('direction')]
                                       if data.get('direction') in directions else [])
    if not targets:
        return jsonify(success=False, message='请先选择方向（或勾选应用到全部方向）'), 400
    # 学科范围：默认仅总分；all_subjects 时覆盖总分 + 本场全部开考学科
    if all_subjects:
        subs = [TOTAL_SUBJECT] + [s for s in SUBJECTS if edata.scores_of_subject(s)]
    else:
        subs = []
        for raw in (data.get('subjects') or []):
            s = (raw or '').strip()
            if not s:
                continue
            if not _is_subject(s):
                return jsonify(success=False, message=f'学科无效：{s}'), 400
            subs.append(s)
        if not subs:
            subs = [TOTAL_SUBJECT]
    applied, skipped = [], []
    for direction in targets:
        for sub in subs:
            if not _scope_scores(edata, direction, sub):
                skipped.append(f'{direction}·{sub}')   # 该组合无参考学生
                continue
            payload = []
            for seq, (name, ratio) in enumerate(TEMPLATES[tpl], start=1):
                # 最低层（待提升/未上线）下界=0 固定分数，其余按比例换算为分数
                if seq == len(TEMPLATES[tpl]):
                    payload.append({'seq': seq, 'name': name, 'lower_mode': 'score',
                                    'lower_value': 0})
                else:
                    payload.append({'seq': seq, 'name': name, 'lower_mode': 'ratio',
                                    'lower_value': float(ratio)})
            items, err = _save_bands(exam_id, direction, sub, payload)
            if err:
                db.session.rollback()
                return jsonify(success=False, message=f'{direction}·{sub}: {err}'), 400
            applied.append(f'{direction}·{sub}')
    if not applied:
        db.session.rollback()
        return jsonify(success=False, message='所选范围没有参考学生，未应用'), 400
    db.session.commit()
    invalidate_exam_cache(exam_id)
    log_operation(current_user, '划线', '考试', exam_id,
                  f'{exam.name} 应用模板 {tpl}（{"全部方向" if both else "、".join(targets)}'
                  f' × {len(subs)}个学科）', module='grades')
    msg = f'分层模板已应用：{len(applied)} 个（方向×学科）组合，分数线按各自参考人群换算'
    if skipped:
        msg += f'；跳过无参考学生的组合 {len(skipped)} 个'
    return jsonify(success=True, message=msg)
