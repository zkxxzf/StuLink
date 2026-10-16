# StuLink v1.19.47.4 2026-10-10
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
from app.modules.grades.services.exam_guard import assert_exam_visible
from app.modules.grades.utils import delete_cache_prefix, invalidate_exam_cache
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation

# 2026-10-10 架构审计修复：本文件原有静默 `except Exception: pass`，失败不可观测。
# 统一加 logger 记录 warning（仅留痕，不改变任何降级行为与返回值）。

logger = logging.getLogger(__name__)

# 模板定义：[(层名, ratio%)]
TEMPLATES = {
    '4': [('优秀', 20), ('良好', 60), ('及格', 95), ('待提升', 0)],
    'gaokao': [('清北', 3), ('985', 8), ('211', 20), ('特控', 60), ('本科', 95), ('未上线', 0)],
}

# 按「选科组合」划线的科目（2026-10-15 用户口径）：再选四科各组合一条线；
# 语文/数学/外语 + 首选科（物理/历史）仍按方向划。划线脚本与页面共用这一处定义。
COMBO_SUBJECTS = ('化学', '生物', '政治', '地理')


def _log():
    """划线专用日志：写 StuLink/logs/bands.log，同时输出到 Flask 默认日志（stderr）

    排查保存失败时看它即可（实时跟踪）：
        Get-Content StuLink\\logs\\bands.log -Tail 50 -Wait
    """
    lg = logging.getLogger('stulink.bands')
    # L-2：日志落盘前统一脱敏（身份证/手机号/邮箱）
    from app.utils.log_mask import MaskingFilter
    if not any(isinstance(f, MaskingFilter) for f in lg.filters):
        lg.addFilter(MaskingFilter())
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
        except Exception as _e:  # noqa: BLE001  日志不可写不能影响业务
            logger.warning('bands.py::_log 日志处理器初始化失败: %s', _e)
    return lg


def _is_subject(value):
    return value == TOTAL_SUBJECT or value in SUBJECTS


def _scope_scores(data, direction, subject, selection=''):
    """（方向 × [组合] × 学科）参考人群的分数列表

    selection 非空 = 该选科组合（如 物化生）的人群（组合总分线用，direction 必填）；
    空 = 按方向/全体（原口径）。
    """
    if selection:
        rows = [t for t in data.totals_of(direction=direction)
                if t.get('selection') == selection]
        if subject == TOTAL_SUBJECT:
            return [t['score'] for t in rows if t['score'] is not None]
        return [v for v in (data.subj.get((t['no'], subject)) for t in rows)
                if v is not None]
    if subject == TOTAL_SUBJECT:
        return [t['score'] for t in data.totals_of(direction=direction)]
    return list(data.scores_of_subject(subject, direction=direction))


def sel_meta(data):
    """本场各选科组合 → {组合: {'direction': 方向, 'subjects': [该组合实际开考科目]}}

    2026-10-15 用户口径（三次定案）：**只有总分按组合**，其余各科按方向；页面用
    **列级「按组合」开关**（`bands.html` 的 `q-combo-chk`）逐列决定该科是否按组合填，
    不再有整表「划线维度」下拉（`matrix?selection=` 仅保留给脚本/测试）。
    `band_subjects` = 该组合按组合级划线的科目（再选四科；语数外+首选科按方向，不在内），
    也是写入口的科目白名单（审计 A3）。
    """
    rows = data.totals_of()          # dict 行（total_rows 是 ORM 对象，无 .get）
    out = {}
    for t in rows:
        sel = (t.get('selection') or '').strip()
        if not sel:
            continue
        rec = out.setdefault(sel, {'direction': t.get('direction') or '',
                                   'subjects': set(), 'count': 0})
        rec['count'] += 1        # 该组合人数（组合维度下算「比例 → 约 N 人」用）
        if not rec['direction'] and t.get('direction'):
            rec['direction'] = t['direction']
    if not out:
        return {}
    nos = {t['no']: (t.get('selection') or '').strip() for t in rows}
    for (no, sub), v in data.subj.items():
        sel = nos.get(no)
        # 总分不是单科（前端会单独补一列），这里只收单科
        if v is not None and sel in out and sub != TOTAL_SUBJECT:
            out[sel]['subjects'].add(sub)
    # 固定顺序（下拉选项顺序稳定）：物理类三组合 → 史地政；未知组合排最后
    order = ['物化生', '物化地', '物化政', '史地政']
    return {k: {'direction': v['direction'], 'subjects': sorted(v['subjects']),
                # 该组合按「组合级」划线的科目（再选四科；语数外+首选科按方向，不在内）
                'band_subjects': [s for s in sorted(v['subjects']) if s in COMBO_SUBJECTS],
                'count': v['count']}
            for k, v in sorted(out.items(),
                               key=lambda kv: (order.index(kv[0])
                                               if kv[0] in order else 99, kv[0]))}


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


def _ratio_to_score(data, direction, subject, ratio, selection=''):
    """按参考人数比例换算下界；无参考返回 0"""
    return _ratio_from_sorted(sorted(_scope_scores(data, direction, subject, selection),
                                     reverse=True),
                              ratio)


def _rank_to_score(data, direction, subject, rank, selection=''):
    """按名次换算下界：第 rank 名（1 起）的分数；参考人数不足时取最后一名。

    例：填 50 → 取该（方向×[组合]×学科）第 50 名的分数作为该层下界，
    即「前 50 名」这一层。落库与比例模式一样换算成实际分数线（统一存 score）。
    """
    desc = sorted(_scope_scores(data, direction, subject, selection), reverse=True)
    if not desc:
        return 0.0
    try:
        r = int(rank)
    except (TypeError, ValueError):
        return 0.0
    return desc[min(max(r - 1, 0), len(desc) - 1)]


def _band_counts(data, direction, subject, bands, selection=''):
    """bands: [(name, lower)]（seq 升序，最高层在前）→ 每层人数（含占比）
    取首个 lower<=score 的层（高分落入高层）
    """
    counts = [0] * len(bands)
    total = 0
    for s in _scope_scores(data, direction, subject, selection):
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


# ==================== 读（所有能看成绩的账号）====================
# 2026-10-15 用户口径：「划线分层应该可以向所有老师展示，但是他们不能修改，
# 因为这个是教务员/管理员、年级长整的。」——页面与两个读取接口放到 grades.view，
# 页面按 can_edit（grades.settings）渲染只读视图；
# 三个写入接口（save / batch / apply）**保持 grades.settings**（管理员/教务员/
# 年级长/考试管理员的「教学管理」写入档），只读账号即使伪造请求也会被 403。
@bp.route('/exams/<int:exam_id>/bands')
@login_required
@perm_required('grades.view')
def bands_page(exam_id):
    exam = assert_exam_visible(exam_id)   # H-4：分档线页同样校验考试年级范围
    data = st.cached_exam_data(exam_id)
    # 选科后为 ['物理','历史']；选科前无方向 → ['']（空串=全体，不分方向）
    directions = data.directions or ['']
    # 各组实际开考学科（矩阵列）：四选二导致同方向学生选科不同，取并集
    dir_subjects = {d: [s for s in SUBJECTS
                        if data.scores_of_subject(s, direction=d or None)]
                    for d in directions}
    return render_template('grades/bands.html', exam=exam, directions=directions,
                           dir_subjects=dir_subjects,
                           sel_meta=sel_meta(data),
                           # 组合级科目白名单（单一来源＝COMBO_SUBJECTS，审计 S2-1：
                           # 前端不再自己复制一份，兜底路径也用这份）
                           combo_subjects=list(COMBO_SUBJECTS),
                           can_edit=current_user.has_perm('grades.settings'),
                           total_count=len(data.total_rows))


def _bands_payload(exam_id, direction, subject, data=None, selection=''):
    """返回该（方向 × [组合] × 学科）bands（含预览）JSON 列表

    data 可传入已构造好的 ExamData 以复用（批量接口用，避免逐列重复加载明细行）。
    """
    rows = (ExamBand.query.filter_by(exam_id=exam_id, subject=subject,
                                     selection=selection)
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
    counts, total = _band_counts(data, direction, subject, bands, selection)
    # 比例模式预览：排序一次复用，避免每层都重排全量分数
    sorted_desc = None
    out = []
    for i, r in enumerate(real):
        if r.lower_mode == 'ratio':
            if sorted_desc is None:
                sorted_desc = sorted(_scope_scores(data, direction, subject, selection),
                                     reverse=True)
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
@perm_required('grades.view')      # 只读接口（2026-10-15 口径，见 bands_page 注释）
def bands_get():
    exam_id = request.args.get('exam_id', type=int)
    direction = request.args.get('direction', '').strip()
    subject = request.args.get('subject', '').strip() or TOTAL_SUBJECT
    selection = request.args.get('selection', '').strip()
    assert_exam_visible(exam_id)          # H-4
    if not _is_subject(subject):
        return jsonify(success=False, message='学科无效'), 400
    return jsonify(success=True, data=_bands_payload(exam_id, direction, subject,
                                                     selection=selection))


@bp.route('/api/bands/matrix')
@login_required
@perm_required('grades.view')      # 只读接口（2026-10-15 口径，见 bands_page 注释）
def bands_matrix():
    """一次返回全部（方向 × 学科）划线，供划线页整表回填

    原实现：前端对每一列发一次 /api/bands，后端每列都要重建 ExamData
    （一场考试约 1 万行明细），2 个方向 × 多科就是十几次全量加载，是划线页慢的主因。
    改为单次请求、全程共用一份 ExamData。
    """
    exam_id = request.args.get('exam_id', type=int)
    selection = (request.args.get('selection') or '').strip()
    assert_exam_visible(exam_id)          # H-4
    data = st.cached_exam_data(exam_id)
    directions = data.directions or ['']
    meta = sel_meta(data)
    out = {}
    for d in directions:
        subs = [s for s in SUBJECTS if data.scores_of_subject(s, direction=d or None)]
        if selection:
            # 「按组合」维度（2026-10-15）：只回该组合自己的线（总分＋该组合开考科目），
            # 且**不带 selection 字段** —— 前端直接填进编辑格，保存即写回该组合
            m = meta.get(selection) or {}
            if d != (m.get('direction') or d):
                continue
            # 只回「总分 + 该组合的组合级科目（band_subjects：再选四科）」——
            # 语数外/首选科按方向划，不从组合维度回值（审计 A3：否则整表切组合时
            # 会把方向线当组合线编辑，写入口径漂移）
            allowed = set(m.get('band_subjects') or []) | {TOTAL_SUBJECT}
            for sub in [TOTAL_SUBJECT] + subs:
                if sub not in allowed:
                    continue
                payload = _bands_payload(exam_id, d, sub, data=data, selection=selection)
                out[f'{selection}|{sub}'] = [
                    {k: v for k, v in b.items() if k != 'selection'} for b in payload]
            continue
        for sub in [TOTAL_SUBJECT] + subs:
            payload = _bands_payload(exam_id, d, sub, data=data)
            # 「按方向」维度：把该 (方向×学科) 的**组合线**一并返回 —— 渲染成只读小字
            # 备注格（不进编辑格、批量保存不带它；要改组合线请把上方维度切到对应组合）
            sel_rows = (ExamBand.query
                        .filter(ExamBand.exam_id == exam_id,
                                ExamBand.subject == sub,
                                ExamBand.direction == d,
                                ExamBand.selection != '')
                        .order_by(ExamBand.selection, ExamBand.seq).all())
            if sel_rows:
                payload = list(payload) + [
                    {'seq': r.seq, 'name': r.name, 'lower_value': r.lower_value,
                     'selection': r.selection} for r in sel_rows]
            out[f'{d}|{sub}'] = payload
    return jsonify(success=True, data=out)


def _save_bands(exam_id, direction, subject, payload, selection='', data=None):
    """payload: [{seq,name,lower_mode,lower_value}]（seq 升序=最高层在前，下界分数依次降低）
    ratio 模式在保存时按当前参考人数换算为实际分数线落库（统一存 score）
    selection 非空 = 写「该选科组合」的线（组合总分线，direction 必填），
    覆盖写入只删同 (方向,组合,学科) 的旧行，不影响方向线。
    data 可传入已构造的 ExamData 复用（审计 W8-a：脚本逐科调用时避免每次重建全量明细）。
    """
    data = data if data is not None else st.ExamData(exam_id)
    # 2026-10-12 数据可信度审计（§3.6-7）：没成绩就划线时，_ratio_to_score /
    # _rank_to_score 对"无参考"都返回 0.0 → 会划出「所有层下界都是 0」的线
    # 并**保存成功**，之后所有分层统计/上线人数全部失真，且很难被发现。
    # 这里在该（方向×[组合]×学科）没有任何参考分数时直接拒绝。
    if not _scope_scores(data, direction, subject, selection):
        label = f'{direction or "全体"}·{selection}·{subject}' if selection \
            else f'{direction or "全体"}·{subject}'
        return None, f'{label} 暂无成绩，请先导入成绩再划线'
    # 2026-10-13：科目名白名单校验 —— AI 导入的解析器对别名表外的科目名会原样透传
    # （如「英语」），落库后 matrix 只认 SUBJECTS+总分，这些行永远查不到也不报错。
    from app.models.grades import SUBJECTS, TOTAL_SUBJECT
    if subject not in SUBJECTS and subject != TOTAL_SUBJECT:
        return None, f'科目名无效：{subject}（AI 导入解析到无法识别的科目，请检查表头）'
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
            # 按（方向×[组合]×学科）参考人数比例换算为分数线
            value = _ratio_to_score(data, direction, subject, value, selection)
        elif mode == 'rank':
            # 按名次换算为分数线（「前 N 名」）
            value = _rank_to_score(data, direction, subject, value, selection)
        # 修复：允许相邻两层下界相等——不同比例换算落在同一同分段时必然相等
        #（同分学生无法用分数线切分，等界=高层优先收录）；仅拦截逆序（高于上一层）
        if prev_lower is not None and value > prev_lower:
            return None, f'第 {seq} 层（{name}）下界需不高于上一层（{prev_lower}）'
        prev_lower = value
        seen_seq.add(seq)
        items.append({'seq': seq, 'name': name, 'lower_mode': 'score',
                      'lower_value': value})
    # 覆盖保存（仅覆盖该 方向×[组合]×学科 组合）
    ExamBand.query.filter_by(exam_id=exam_id, direction=direction, subject=subject,
                             selection=selection).delete()
    for it in items:
        db.session.add(ExamBand(exam_id=exam_id, direction=direction, subject=subject,
                                selection=selection,
                                seq=it['seq'], name=it['name'],
                                lower_mode=it['lower_mode'],
                                lower_value=it['lower_value']))
    return items, None


@bp.route('/api/bands/save', methods=['POST'])
@login_required
@perm_required('grades.settings')      # 写入：教务员/管理员/年级长/考试管理员
def bands_save():
    data = request.get_json(silent=True) or {}
    exam_id = int(data.get('exam_id') or 0)
    direction = (data.get('direction') or '').strip()
    subject = (data.get('subject') or '').strip() or TOTAL_SUBJECT
    selection = (data.get('selection') or '').strip()
    exam = assert_exam_visible(exam_id)   # H-4
    if direction not in ('物理', '历史'):
        return jsonify(success=False, message='方向无效'), 400
    if not _is_subject(subject):
        return jsonify(success=False, message='学科无效'), 400
    items, err = _save_bands(exam_id, direction, subject, data.get('bands') or [],
                             selection)
    if err:
        _log().warning('单个保存失败 exam_id=%s %s·%s：%s', exam_id, direction, subject, err)
        return jsonify(success=False, message=err), 400
    db.session.commit()
    _log().info('单个保存成功 exam_id=%s %s·%s：%d 层 %s', exam_id, direction, subject,
                len(items), [(i['name'], i['lower_value']) for i in items])
    invalidate_exam_cache(exam_id)
    log_operation(current_user, '划线', '考试', exam_id,
                  f'{exam.name} {direction}·{subject}分层保存', module='grades')
    return jsonify(success=True, data=_bands_payload(exam_id, direction, subject,
                                                     selection=selection))


@bp.route('/api/bands/batch', methods=['POST'])
@login_required
@perm_required('grades.settings')      # 写入：教务员/管理员/年级长/考试管理员
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
    exam = assert_exam_visible(exam_id)   # H-4
    mode = data.get('mode') if data.get('mode') in ('score', 'ratio', 'rank') else 'score'
    scope = 'all' if data.get('scope') == 'all' else 'per_subject'
    include_total = bool(data.get('include_total'))
    layers = data.get('layers') or []
    if not layers:
        return jsonify(success=False, message='没有可保存的层'), 400

    selection = (data.get('selection') or '').strip()   # 非空=「按组合」维度
    _log().info('=== 批量划线请求 exam_id=%s 用户=%s mode=%s scope=%s 含总分=%s 组合=%s 层数=%s',
                exam_id, current_user.username, mode, scope, include_total,
                selection or '（按方向）', len(layers))
    edata = st.ExamData(exam_id)
    meta = sel_meta(edata)
    if selection:
        # 「按组合」维度（2026-10-15）：方向由组合推出，科目限该组合实际开考的（＋总分）
        m = meta.get(selection)
        if not m:
            return jsonify(success=False,
                           message=f'选科组合无效或本场无该组合学生：{selection}'), 400
        targets = [(m.get('direction') or '', selection,
                    list(m.get('subjects') or []))]
    else:
        # 原「按方向」维度：选科前 directions=['']（空串=全体）
        targets = [(d, '',
                    [s for s in SUBJECTS
                     if edata.scores_of_subject(s, direction=d or None)])
                   for d in (edata.directions or [''])]
    applied, skipped = [], []
    for direction, sel, subs in targets:
        if include_total:
            subs = [TOTAL_SUBJECT] + subs
        # 前端列键：按组合='物化生|化学'（组合名当列头），按方向='物理|化学'
        key_d = sel or direction
        for sub in subs:
            label = f'{key_d or "全体"}·{sub}'
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
                    value = (lay.get('values') or {}).get(f'{key_d}|{sub}')
                if value is None or value == '':
                    continue        # 该格未填 → 该组合不划线
                payload.append({'seq': seq, 'name': name, 'lower_mode': mode,
                                'lower_value': value})
            if not payload:
                _log().info('  跳过 %s：未填任何下界值', label)
                skipped.append(label)      # 整列未填
                continue
            if not _scope_scores(edata, direction, sub, sel):
                _log().info('  跳过 %s：无参考学生', label)
                skipped.append(label)      # 该组合无参考学生
                continue
            # 第七轮审计 P-H1：复用入口 ExamData（原实现每列重建全场明细 ≈21 次/次批量）
            items, err = _save_bands(exam_id, direction, sub, payload, sel, data=edata)
            if err:
                db.session.rollback()
                _log().warning('  校验失败 %s：%s（提交值 %s）', label, err,
                               [(p['name'], p['lower_value']) for p in payload])
                return jsonify(success=False, message=f'{label}：{err}'), 400
            _log().info('  写入 %s：%s', label,
                        [(i['name'], i['lower_value']) for i in items])
            applied.append(label)

    # 2026-10-15：「按方向」视图里**格内逐组合**填的值 —— layers[i].sel_values =
    #   {'物理|总分': {'物化生': 496.5, '物化地': 427.5, ...}, ...}
    # 直接写各组合自己的线（selection=组合），与方向线互不影响
    extra = {}
    for lay in layers:
        try:
            _seq = int(lay.get('seq'))
        except (TypeError, ValueError):
            continue
        _name = (lay.get('name') or '').strip()
        for _key, _by_sel in (lay.get('sel_values') or {}).items():
            if '|' not in _key or not isinstance(_by_sel, dict):
                # 结构必须是 {列键: {组合: 值}}；写错一层直接 400，不要 500
                #（第六轮审计自测发现：传 {'物化生|化学': 57} 会 AttributeError）
                return jsonify(success=False,
                               message='sel_values 结构应为 {"方向|科目": {"组合": 值}}'), 400
            _kd, _sub = _key.split('|', 1)
            if _sub not in SUBJECTS and _sub != TOTAL_SUBJECT:
                return jsonify(success=False, message=f'科目名无效：{_sub}'), 400
            for _sel, _val in (_by_sel or {}).items():
                if _val is None or _val == '':
                    continue
                _m = meta.get(_sel)
                if not _m:
                    return jsonify(success=False,
                                   message=f'选科组合无效或本场无该组合学生：{_sel}'), 400
                # 组合级线只允许「总分 + 该组合的组合级科目」——语数外/首选科按方向划
                #（审计 A3：否则页面/脚本能把已按方向划的科目又写成组合线，两端口径漂移）
                if _sub != TOTAL_SUBJECT and _sub not in (_m.get('band_subjects') or []):
                    return jsonify(success=False,
                                   message=f'{_sel} 的「{_sub}」不按组合划线（该科按方向）'), 400
                extra.setdefault((_m.get('direction') or '', _sel, _sub), []).append(
                    {'seq': _seq, 'name': _name, 'lower_mode': mode, 'lower_value': _val})
    for (direction, sel, sub), payload in sorted(extra.items()):
        label = f'{sel}·{sub}'
        if not _scope_scores(edata, direction, sub, sel):
            _log().info('  跳过 %s：无参考学生', label)
            skipped.append(label)
            continue
        items, err = _save_bands(exam_id, direction, sub, payload, sel, data=edata)
        if err:
            db.session.rollback()
            _log().warning('  校验失败 %s：%s（提交值 %s）', label, err,
                           [(p['name'], p['lower_value']) for p in payload])
            return jsonify(success=False, message=f'{label}：{err}'), 400
        _log().info('  写入（格内组合） %s：%s', label,
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
@perm_required('grades.settings')      # 写入：教务员/管理员/年级长/考试管理员
def bands_apply():
    data = request.get_json(silent=True) or {}
    exam_id = int(data.get('exam_id') or 0)
    tpl = data.get('template') or '4'
    both = bool(data.get('both'))
    all_subjects = bool(data.get('all_subjects'))
    exam = assert_exam_visible(exam_id)   # H-4
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
            items, err = _save_bands(exam_id, direction, sub, payload, data=edata)
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
