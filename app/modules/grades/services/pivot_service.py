# StuLink v1.9.2 2026-09-16
# 分层上线统计（各班各层上线人数/上线率）+ 自由表（行/列/指标自由组合的交叉分析）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import statistics

from app.models.grades import SUBJECTS, TOTAL_SUBJECT
from app.modules.grades.services import report_service

# ============ 维度 ============
DIM_NONE = ''
DIM_CLASS = 'class'
DIM_LAYER = 'layer'
DIM_SUBJECT = 'subject'
DIM_DIR = 'direction'

DIM_LABELS = {
    DIM_CLASS: '班级', DIM_LAYER: '层（上线线）',
    DIM_SUBJECT: '科目', DIM_DIR: '方向',
}

# 指标定义：(key, 中文名, 是否依赖「层」维度, 是否依赖单科线)
MEASURE_DEFS = [
    ('count', '参考人数', False, False),
    ('avg', '平均分', False, False),
    ('max', '最高分', False, False),
    ('min', '最低分', False, False),
    ('std', '标准差', False, False),
    ('trim_avg', '去差均分', False, False),
    ('online_n', '上线人数', True, False),
    ('online_rate', '上线率%', True, False),
    ('dual_n', '双上线人数', True, False),
    ('dual_rate', '双上线率%', True, False),
    ('inlayer_n', '层内人数', True, False),
    ('inlayer_rate', '层内占比%', True, False),
    ('pass_n', '及格人数', False, True),
    ('pass_rate', '及格率%', False, True),
    ('good_n', '优秀人数', False, True),
    ('good_rate', '优秀率%', False, True),
]
MEASURE_LABELS = {k: n for k, n, _a, _b in MEASURE_DEFS}


def _stddev(values):
    return round(statistics.stdev(values), 1) if len(values) > 1 else 0.0


# ==================== 一、分层上线统计 ====================

def layer_pass_stats(data, subject=TOTAL_SUBJECT):
    """各班 × 各层「累计上线」人数与上线率（按方向各自的分层线分别判定后合并）。

    「上线」= 分数 ≥ 该层下界，是**累计口径**（与一本线/本科线用法一致：
    一本上线人数 = 总分达一本线及以上的全部人数，而不是只落在该档的人数）。
    分层线按「方向 × 学科」配置，物理类/历史类线不同，故按方向分别判定再合并。

    返回 None 表示该口径下没有任何分层配置（未划线）。
    """
    directions = data.directions or ['']
    bands_by_dir, layer_names = {}, []
    for d in directions:
        bl = data.band_list(d, subject)
        if not bl:
            continue
        bands_by_dir[d] = bl
        for name, _lower in bl:
            if name not in layer_names:
                layer_names.append(name)
    if not layer_names:
        return None

    acc = {}          # 班级 -> {'n': 参考人数, 'c': {层名: 累计上线人数}}
    for d, bl in bands_by_dir.items():
        for r in data.total_rows:
            if d and r.direction != d:          # d='' 表示未分科，不过滤方向
                continue
            sc = (r.score if subject == TOTAL_SUBJECT
                  else data.subj.get((r.student_no, subject)))
            if sc is None:
                continue
            cls = r.class_name or '—'
            a = acc.setdefault(cls, {'n': 0, 'c': {}})
            a['n'] += 1
            for name, lower in bl:
                if sc >= lower:
                    a['c'][name] = a['c'].get(name, 0) + 1

    order = {c: i for i, c in enumerate(data.classes)}
    rows = []
    for cls in sorted(acc.keys(), key=lambda c: order.get(c, 999)):
        a = acc[cls]
        rows.append({
            'class_name': cls, 'count': a['n'],
            'pass': {n: a['c'].get(n, 0) for n in layer_names},
            'rate': {n: (round(a['c'].get(n, 0) / a['n'] * 100, 1) if a['n'] else None)
                     for n in layer_names},
        })
    tn = sum(a['n'] for a in acc.values())
    total = {
        'class_name': '年级合计', 'count': tn,
        'pass': {n: sum(a['c'].get(n, 0) for a in acc.values()) for n in layer_names},
        'rate': {n: (round(sum(a['c'].get(n, 0) for a in acc.values()) / tn * 100, 1)
                     if tn else None) for n in layer_names},
    }
    return {'layers': layer_names, 'rows': rows, 'total': total, 'subject': subject}


def layer_pass_table(stats):
    """把 layer_pass_stats 结果整理成前端可直接渲染的表（行=班级，列=各层人数/上线率）"""
    if not stats:
        return None
    layers = stats['layers']
    cols = [{'key': 'class_name', 'label': '班级', 'type': 'text'},
            {'key': 'count', 'label': '参考人数', 'type': 'int'}]
    for n in layers:
        cols.append({'key': 'p_' + n, 'label': n + '上线人数', 'type': 'int'})
        cols.append({'key': 'r_' + n, 'label': n + '上线率%', 'type': 'num'})
    rows = []
    for src in list(stats['rows']) + [stats['total']]:
        r = {'class_name': src['class_name'], 'count': src['count']}
        for n in layers:
            r['p_' + n] = src['pass'].get(n, 0)
            r['r_' + n] = src['rate'].get(n)
        rows.append(r)
    subj = stats.get('subject', TOTAL_SUBJECT)
    return {
        'title': f'各班分层上线统计（{subj}·累计口径：达该层下界及以上）',
        'columns': cols, 'rows': rows,
    }


# ==================== 二、自由表（交叉分析） ====================

def _rec_key(rec, dim):
    if dim == DIM_CLASS:
        return rec['class']
    if dim == DIM_DIR:
        return rec['direction']
    if dim == DIM_SUBJECT:
        return rec['subject']
    return ''


def _ordered(keys, dim, data):
    if dim == DIM_CLASS:
        order = {c: i for i, c in enumerate(data.classes)}
        return sorted(keys, key=lambda k: order.get(k, 999))
    if dim == DIM_SUBJECT:
        order = {s: i for i, s in enumerate(SUBJECTS)}
        return sorted(keys, key=lambda k: order.get(k, 999))
    if dim == DIM_DIR:
        return sorted(keys)
    return list(keys)


def _build_records(data, need_subject, subject, direction):
    """构造参与统计的记录，并顺带返回该口径下出现过的层名（按 seq 顺序）。

    need_subject（科目作为维度之一）：记录 = 学生 × 其实际应考科目；
    否则记录 = 学生，分数取「统计口径」subject（默认总分）。

    每条记录带：班级/方向/科目/分数/各层达标情况/落入层/单科三线。
    """
    recs, layer_names = [], []
    bands_cache = {}
    # 双上线/去差均分所需：去差学生集合（按班班型剔总分末 N 人，口径与汇报区一致）、
    # 各方向总分层线（独立缓存，避免总分层名混进「科目×层」交叉的层取值）
    trimmed_nos = report_service.trimmed_nos(data)
    total_bands = {}

    def bands_of(d, sub):
        key = (d, sub)
        if key not in bands_cache:
            bl = data.band_list(d, sub)
            bands_cache[key] = bl
            for name, _lower in bl:
                if name not in layer_names:
                    layer_names.append(name)
        return bands_cache[key]

    def total_bands_of(d):
        if d not in total_bands:
            total_bands[d] = data.band_list(d, TOTAL_SUBJECT)
        return total_bands[d]

    for r in data.total_rows:
        if direction and r.direction != direction:
            continue
        # 该生总分在各总分层的达标情况（双上线＝单科过线且总分过同名层线）
        total_pass = {}
        for name, lower in total_bands_of(r.direction or ''):
            if r.score is not None and r.score >= lower:
                total_pass[name] = True
        is_trimmed = r.student_no in trimmed_nos
        if need_subject:
            pairs = [(s, data.subj.get((r.student_no, s))) for s in SUBJECTS]
            pairs = [(s, v) for s, v in pairs if v is not None]
        else:
            v = (r.score if subject == TOTAL_SUBJECT
                 else data.subj.get((r.student_no, subject)))
            pairs = [(subject, v)] if v is not None else []
        for sub, sc in pairs:
            pass_layers, in_layer = {}, None
            for name, lower in bands_of(r.direction or '', sub):
                if sc >= lower:
                    pass_layers[name] = True
                    if in_layer is None:
                        in_layer = name
            recs.append({
                'class': r.class_name or '—',
                'direction': r.direction or '—',
                'subject': sub, 'score': sc,
                'pass_layers': pass_layers, 'in_layer': in_layer,
                'lines': (data.exam.subject_lines(sub) if sub != TOTAL_SUBJECT else None),
                'total_pass_layers': total_pass, 'trimmed': is_trimmed,
            })
    return recs, layer_names


def _measure(group, layer, measures):
    """计算单个单元格的各项指标。

    layer 不为 None 时该单元格按「达到该层下界」的人群（累计上线口径）统计：
      上线人数/上线率 = 达标人数 / 组内参考人数；
      平均/最高/最低/标准差 = 对**达标人群**统计（如一本线上的平均分）；
      层内人数/占比 = 落入该层区间（互斥）的人数 / 组内参考人数。
    """
    n = len(group)
    pop = [x for x in group if x['pass_layers'].get(layer)] if layer else group
    scores = [x['score'] for x in pop]
    # 去差均分始终按「组内全体参考学生」去差（不随层筛选变口径），与汇报区图1/图3 一致；
    # 故在「科目×层」表里各层列下该值相同，即该组整体去差均分
    trim_scores = [x['score'] for x in group if not x['trimmed']]
    # 该单元格是否含单科记录（统计口径为总分本身时双上线无意义）
    has_subject_rec = any(x['subject'] != TOTAL_SUBJECT for x in group)
    out = {}
    for m in measures:
        if m == 'count':
            out[m] = n
        elif m == 'avg':
            out[m] = round(sum(scores) / len(scores), 1) if scores else None
        elif m == 'max':
            out[m] = max(scores) if scores else None
        elif m == 'min':
            out[m] = min(scores) if scores else None
        elif m == 'std':
            out[m] = _stddev(scores) if scores else None
        elif m == 'trim_avg':
            out[m] = round(sum(trim_scores) / len(trim_scores), 1) if trim_scores else None
        elif m == 'online_n':
            out[m] = len(pop) if layer else None
        elif m == 'online_rate':
            out[m] = round(len(pop) / n * 100, 1) if (layer and n) else None
        elif m in ('dual_n', 'dual_rate'):
            # 双上线仅对单科有意义：单科达该科层线 且 总分达同层线；
            # 统计口径为总分本身（组内无单科记录）时无意义，返回 None
            if not layer or not has_subject_rec:
                out[m] = None
            else:
                hit = sum(1 for x in pop
                          if x['subject'] != TOTAL_SUBJECT and x['total_pass_layers'].get(layer))
                out[m] = hit if m == 'dual_n' else (round(hit / n * 100, 1) if n else None)
        elif m == 'inlayer_n':
            out[m] = sum(1 for x in group if x['in_layer'] == layer) if layer else None
        elif m == 'inlayer_rate':
            out[m] = (round(sum(1 for x in group if x['in_layer'] == layer) / n * 100, 1)
                      if (layer and n) else None)
        elif m in ('pass_n', 'pass_rate', 'good_n', 'good_rate'):
            key = 'pass' if m.startswith('pass') else 'excellent'
            hit = [x for x in pop if x.get('lines') and x['score'] >= x['lines'][key]]
            out[m] = len(hit) if m.endswith('_n') else (round(len(hit) / n * 100, 1) if n else None)
        else:
            out[m] = None
    return out


def pivot_table(data, row_dim, col_dim, measures, subject=TOTAL_SUBJECT, direction=''):
    """自由表：按「行维度 × 列维度」交叉统计所选指标。

    row_dim/col_dim ∈ {class, layer, subject, direction, ''}；col_dim 可为 ''（不分列）。
    measures 为 MEASURE_DEFS 中的 key 列表。
    返回 {'row_dim','col_dim','row_keys','col_keys','measures','cells'}；
    cells 键为 '行值|列值'，值为 {指标key: 值}。无数据/无分层返回 {'error': ...}。
    """
    if row_dim == DIM_LAYER and col_dim == DIM_LAYER:
        return {'error': '行、列不能同时为「层」'}
    need_subject = DIM_SUBJECT in (row_dim, col_dim)
    measures = [m for m in (measures or []) if m in MEASURE_LABELS]
    if not measures:
        measures = ['count']
    need_layer = any(m in ('online_n', 'online_rate', 'dual_n', 'dual_rate',
                           'inlayer_n', 'inlayer_rate')
                     for m in measures)
    if need_layer and DIM_LAYER not in (row_dim, col_dim):
        return {'error': '「上线/层内」类指标需要把「层」放到行或列'}

    recs, layer_names = _build_records(data, need_subject, subject, direction)
    if not recs:
        return {'error': '该条件下没有可统计的成绩记录'}

    layer_pos = ('row' if row_dim == DIM_LAYER else
                 'col' if col_dim == DIM_LAYER else None)
    if layer_pos and not layer_names:
        return {'error': '该口径下没有分层配置，请先到「划线分层」设置分数线'}

    # ---- 行列取值 ----
    row_keys, col_keys = [], []

    def add(k, lst):
        if k not in lst:
            lst.append(k)

    if layer_pos == 'row':
        row_keys = list(layer_names)
        for x in recs:
            add(_rec_key(x, col_dim), col_keys)
        col_keys = _ordered(col_keys, col_dim, data)
    elif layer_pos == 'col':
        col_keys = list(layer_names)
        for x in recs:
            add(_rec_key(x, row_dim), row_keys)
        row_keys = _ordered(row_keys, row_dim, data)
    else:
        for x in recs:
            add(_rec_key(x, row_dim), row_keys)
            if col_dim:
                add(_rec_key(x, col_dim), col_keys)
        row_keys = _ordered(row_keys, row_dim, data)
        col_keys = _ordered(col_keys, col_dim, data) if col_dim else ['—']

    # ---- 分组（层维度单独展开，不参与分组键） ----
    groups = {}
    if layer_pos == 'row':
        for x in recs:
            groups.setdefault(_rec_key(x, col_dim), []).append(x)
    elif layer_pos == 'col':
        for x in recs:
            groups.setdefault(_rec_key(x, row_dim), []).append(x)
    else:
        for x in recs:
            key = (_rec_key(x, row_dim), _rec_key(x, col_dim) if col_dim else '—')
            groups.setdefault(key, []).append(x)

    cells = {}
    for rk in row_keys:
        for ck in col_keys:
            if layer_pos == 'row':
                group, layer = groups.get(ck, []), rk
            elif layer_pos == 'col':
                group, layer = groups.get(rk, []), ck
            else:
                group, layer = groups.get((rk, ck), []), None
            cells[rk + '|' + ck] = _measure(group, layer, measures)

    return {
        'row_dim': row_dim, 'col_dim': col_dim,
        'row_label': DIM_LABELS.get(row_dim, '—'),
        'col_label': DIM_LABELS.get(col_dim, '—') if col_dim else '',
        'row_keys': row_keys, 'col_keys': col_keys,
        'measures': [{'key': m, 'label': MEASURE_LABELS[m]} for m in measures],
        'cells': cells,
    }
