# StuLink v1.9.0 2026-09-03
# 分层/划线配置：模板应用（四层/高考线）、手动编辑、人数预览
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import math

from flask import render_template, request, jsonify, abort
from flask_login import login_required, current_user
from app.extensions import db
from app.models.grades import Exam, ExamBand
from app.modules.grades import bp
from app.modules.grades.services import stats_service as st
from app.modules.grades.utils import delete_cache_prefix
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation

# 模板定义：[(层名, ratio%)]
TEMPLATES = {
    '4': [('优秀', 20), ('良好', 60), ('及格', 95), ('待提升', 0)],
    'gaokao': [('清北', 3), ('985', 8), ('211', 20), ('特控', 60), ('本科', 95), ('未上线', 0)],
}


@bp.route('/exams/<int:exam_id>/bands')
@login_required
@perm_required('grades.settings')
def bands_page(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    data = st.ExamData(exam_id)
    directions = data.directions or ['物理', '历史']
    return render_template('grades/bands.html', exam=exam, directions=directions,
                           total_count=len(data.total_rows))


def _direction_scores(exam_id, direction):
    data = st.ExamData(exam_id)
    return [t['score'] for t in data.totals_of(direction=direction)]


def _ratio_to_score(exam_id, direction, ratio):
    """按方向内参考人数比例换算总分下界（名次 ≤ n*ratio%）档最低分；无参考返回 0"""
    scores = sorted(_direction_scores(exam_id, direction), reverse=True)
    if not scores:
        return 0.0
    if ratio <= 0:
        return 0.0
    if ratio >= 100:
        return scores[-1]
    n = len(scores)
    idx = max(0, int(math.ceil(n * ratio / 100.0)) - 1)
    return scores[min(idx, n - 1)]


def _band_counts(exam_id, direction, bands):
    """bands: [(name, lower)]（seq 升序，最高层在前）→ 每层人数（含占比）
    取首个 lower<=score 的层（高分落入高层）
    """
    data = st.ExamData(exam_id)
    totals = data.totals_of(direction=direction)
    counts = [0] * len(bands)
    for t in totals:
        idx = -1
        for i, (_name, lower) in enumerate(bands):
            if t['score'] >= lower:
                idx = i
                break
        if idx >= 0:
            counts[idx] += 1
        else:  # 低于最低层（层下界应=0，理论不出现）
            counts[-1] += 1
    return counts, len(totals)


def _bands_payload(exam_id, direction):
    """返回该方向 bands（含预览）JSON 列表"""
    rows = (ExamBand.query.filter_by(exam_id=exam_id)
            .filter(ExamBand.direction.in_([direction, '']))
            .order_by(ExamBand.seq.asc()).all())
    if not rows:
        return []
    # 以该方向实际 bands 为准（'' 通用组也展开到该方向显示）
    real = [r for r in rows if r.direction == direction]
    if not real:
        real = rows
    bands = [(r.name, r.lower_value) for r in real]
    counts, total = _band_counts(exam_id, direction, bands)
    return [{
        'id': r.id, 'seq': r.seq, 'name': r.name, 'lower_mode': r.lower_mode,
        'lower_value': r.lower_value,
        'preview_score': (_ratio_to_score(exam_id, direction, r.lower_value)
                          if r.lower_mode == 'ratio' else r.lower_value),
        'count': counts[i] if i < len(counts) else 0,
        'ratio': st.fmt_rate(counts[i], total) if i < len(counts) else None,
    } for i, r in enumerate(real)]


@bp.route('/api/bands')
@login_required
@perm_required('grades.settings')
def bands_get():
    exam_id = request.args.get('exam_id', type=int)
    direction = request.args.get('direction', '').strip()
    Exam.query.get_or_404(exam_id)
    return jsonify(success=True, data=_bands_payload(exam_id, direction))


def _save_bands(exam_id, direction, payload):
    """payload: [{seq,name,lower_mode,lower_value}]（seq 升序=最高层在前，下界分数依次降低）
    ratio 模式在保存时按当前参考人数换算为实际分数线落库（统一存 score）
    """
    items = []
    seen_seq = set()
    prev_lower = None
    for b in payload:
        seq = int(b['seq'])
        name = (b.get('name') or '').strip()
        mode = b.get('lower_mode') in ('score', 'ratio') and b['lower_mode'] or 'score'
        try:
            value = float(b.get('lower_value'))
        except (TypeError, ValueError):
            return None, f'第 {seq} 层下界值不是数字'
        if not name or seq < 1 or seq in seen_seq:
            return None, '层名不能为空且序号不能重复'
        if value < 0 or (mode == 'ratio' and value > 100):
            return None, f'第 {seq} 层（{name}）下界值需在有效范围（比例 0-100，分数 ≥0）'
        if mode == 'ratio':
            # 按方向内参考人数比例换算为分数线（例：前20% → 第 ceil(n*0.2) 名分数）
            value = _ratio_to_score(exam_id, direction, value)
        if prev_lower is not None and value >= prev_lower:
            return None, f'第 {seq} 层（{name}）下界需低于上一层（{prev_lower}）'
        prev_lower = value
        seen_seq.add(seq)
        items.append({'seq': seq, 'name': name, 'lower_mode': 'score',
                      'lower_value': value})
    # 覆盖保存
    ExamBand.query.filter_by(exam_id=exam_id, direction=direction).delete()
    for it in items:
        db.session.add(ExamBand(exam_id=exam_id, direction=direction,
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
    exam = Exam.query.get_or_404(exam_id)
    if direction not in ('物理', '历史'):
        return jsonify(success=False, message='方向无效'), 400
    items, err = _save_bands(exam_id, direction, data.get('bands') or [])
    if err:
        return jsonify(success=False, message=err), 400
    db.session.commit()
    delete_cache_prefix(f'grades_tab_{exam_id}_')
    log_operation(current_user, '划线', '考试', exam_id,
                  f'{exam.name} {direction}分层保存', module='grades')
    return jsonify(success=True, data=_bands_payload(exam_id, direction))


@bp.route('/api/bands/apply', methods=['POST'])
@login_required
@perm_required('grades.settings')
def bands_apply():
    data = request.get_json(silent=True) or {}
    exam_id = int(data.get('exam_id') or 0)
    tpl = data.get('template') or '4'
    both = bool(data.get('both'))
    exam = Exam.query.get_or_404(exam_id)
    if tpl not in TEMPLATES:
        return jsonify(success=False, message='模板不存在'), 400
    edata = st.ExamData(exam_id)
    directions = edata.directions or ['物理', '历史']
    targets = directions if both else ([data.get('direction')] if data.get('direction') in directions else [])
    if not targets:
        return jsonify(success=False, message='请先选择方向（或勾选应用到全部方向）'), 400
    for direction in targets:
        payload = []
        for seq, (name, ratio) in enumerate(TEMPLATES[tpl], start=1):
            # 最低层（待提升/未上线）下界=0 固定分数，其余按比例换算为分数
            if seq == len(TEMPLATES[tpl]):
                payload.append({'seq': seq, 'name': name, 'lower_mode': 'score',
                                'lower_value': 0})
            else:
                payload.append({'seq': seq, 'name': name, 'lower_mode': 'ratio',
                                'lower_value': float(ratio)})
        items, err = _save_bands(exam_id, direction, payload)
        if err:
            db.session.rollback()
            return jsonify(success=False, message=f'{direction}: {err}'), 400
    db.session.commit()
    delete_cache_prefix(f'grades_tab_{exam_id}_')
    log_operation(current_user, '划线', '考试', exam_id,
                  f'{exam.name} 应用模板 {tpl}（{"全部方向" if both else "、".join(targets)}）',
                  module='grades')
    return jsonify(success=True, message='分层模板已应用，层界分数按当前参考人数换算完成')
