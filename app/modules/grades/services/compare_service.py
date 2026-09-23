# StuLink v1.18.0.0 2026-09-23
# 班级对比分析：多班横向对比的数据组装（供 /api/analysis/compare 与 Excel 导出共用）
# 输出结构与 tab_service 四模块完全一致：{exam, tables, charts, meta}
# 口径沿用 tab_service：全部基于 stats_service.cached_exam_data 的内存视图，所选班一次遍历分组，
# 避免 for 班: totals_of(...) 的 O(班数 × n)。
# chartHint 约定：{'xAxis','series'}（或 'indicators'）齐全时视为完整图表规格覆盖；
# 否则仅用 type/topN 等字段纠正前端自动推断。导出只读 title/columns/rows，对其无感。
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import statistics

from app.models.grades import TOTAL_SUBJECT
from app.modules.grades.services import stats_service as st
from app.modules.grades.services import pivot_service as pv


def _empty(exam):
    return {'exam': {'id': exam.id, 'name': exam.name, 'grade': exam.grade,
                     'date': exam.exam_date.strftime('%Y-%m-%d'), 'status': exam.status},
            'tables': {}, 'charts': {}, 'meta': {'empty': True}}


def _pick_classes(data, class_names):
    """按传入顺序保留本场真实存在且有参考学生的班级（去重）"""
    known = set(data.classes)
    out = []
    for c in class_names or []:
        if c in known and c not in out:
            out.append(c)
    return out


def _subject_union(data, classes):
    """所选班应考科目并集（保持 SUBJECTS 顺序）；class_subjects 内部为 O(n)，每班只调一次"""
    subs, seen = [], set()
    for c in classes:
        for s in data.class_subjects(c):
            if s not in seen:
                seen.add(s)
                subs.append(s)
    return subs


def _stddev(values):
    return 0.0 if len(values) <= 1 else round(statistics.stdev(values), 1)


def _median(values):
    return round(statistics.median(values), 1) if values else None


def compare_tab(exam_id, class_names, direction=''):
    """多班横向对比。

    class_names: 班名列表（顺序=用户选择顺序）；不在本场/无参考学生的班级自动剔除。
    direction: 物理/历史，空串=全部方向。
    """
    data = st.cached_exam_data(exam_id)
    exam = data.exam
    classes = _pick_classes(data, class_names)
    if not data.total_rows or not classes:
        return _empty(exam)
    dr = direction or None

    # ---- 一次遍历按班分组（后续所有指标都基于该分组，避免重复扫描）----
    rows_all = data.totals_of(direction=dr)
    by_class = {c: [] for c in classes}
    for t in rows_all:
        bucket = by_class.get(t['class_name'])
        if bucket is not None:
            bucket.append(t)
    classes = [c for c in classes if by_class[c]]
    if len(classes) < 2:
        return _empty(exam)

    score_map = {c: [t['score'] for t in by_class[c]] for c in classes}
    count_map = {c: len(by_class[c]) for c in classes}
    grade_scores = [t['score'] for t in rows_all]
    grade_avg = st.mean(grade_scores)
    tables, charts = {}, {}

    # ---- T1 对比班概况（行=班，列=核心指标）----
    t1 = []
    for c in classes:
        vs = score_map[c]
        avg = st.mean(vs)
        t1.append({'class_name': c, 'count': count_map[c], 'avg': avg,
                   'median': _median(vs), 'std': _stddev(vs),
                   'max': max(vs), 'min': min(vs),
                   'range': round(max(vs) - min(vs), 1),
                   'diff': round(avg - grade_avg, 1) if avg is not None and grade_avg is not None else None})
    tables['cmp_overview'] = {
        'title': '对比班核心指标表',
        'columns': [{'key': 'class_name', 'label': '班级', 'type': 'text'},
                    {'key': 'count', 'label': '参考人数', 'type': 'int'},
                    {'key': 'avg', 'label': '总分均值', 'type': 'num'},
                    {'key': 'median', 'label': '中位数', 'type': 'num'},
                    {'key': 'std', 'label': '标准差', 'type': 'num'},
                    {'key': 'max', 'label': '最高分', 'type': 'num'},
                    {'key': 'min', 'label': '最低分', 'type': 'num'},
                    {'key': 'range', 'label': '极差', 'type': 'num'},
                    {'key': 'diff', 'label': '与年级均分差', 'type': 'num'}],
        'rows': t1,
        # 概况表各列量纲不同，自动 groupbar 无可比性 → 显式指定只画「总分均值」并带年级参考线
        'chartHint': {'type': 'bar', 'xAxis': classes,
                      'series': [{'name': '总分均值', 'data': [r['avg'] for r in t1]}],
                      'markLine': grade_avg},
    }

    # ---- T2/T3/T4 科目 × 班（均分 / 及格率% / 优秀率%）----
    subs_all = _subject_union(data, classes)
    per = {}
    for t in rows_all:
        c = t['class_name']
        if c not in by_class:
            continue
        for s in subs_all:
            v = data.subj.get((t['no'], s))
            if v is not None:
                per.setdefault((c, s), []).append(v)
    lines_map = {s: data.subject_lines(s) for s in subs_all}

    def _wide_rows(mode):
        """mode: 'avg' | 'pass' | 'good' → 行=科目，列=各班该口径值"""
        out = []
        for s in subs_all:
            cells = {}
            for i, c in enumerate(classes):
                vs = per.get((c, s)) or []
                if not vs:
                    cells[i] = None
                    continue
                if mode == 'avg':
                    cells[i] = st.mean(vs)
                else:
                    key = 'pass' if mode == 'pass' else 'excellent'
                    cells[i] = st.fmt_rate(sum(1 for v in vs if v >= lines_map[s][key]), len(vs))
            if all(v is None for v in cells.values()):
                continue    # 所选班均未考该科目
            out.append({'subject': s, **{'c%d' % i: cells[i] for i in range(len(classes))}})
        return out

    def _wide_columns(metric_cols, extra=None):
        cols = [{'key': 'subject', 'label': '科目', 'type': 'text'}]
        for i, c in enumerate(classes):
            for k, lbl, tp in metric_cols:
                cols.append({'key': '%s%d' % (k, i), 'label': '%s·%s' % (c, lbl), 'type': tp})
        cols += extra or []
        return cols

    def _wide_chart(rows, series_name_fmt):
        return {'type': 'groupbar', 'xAxis': [r['subject'] for r in rows],
                'series': [{'name': series_name_fmt % c,
                            'data': [r.get('c%d' % i) for r in rows]}
                           for i, c in enumerate(classes)]}

    t2 = _wide_rows('avg')
    tables['cmp_subject'] = {
        'title': '对比班各科平均分表',
        'columns': _wide_columns([('c', '均分', 'num')],
                                 [{'key': 'spread', 'label': '班极差', 'type': 'num'}]),
        'rows': [_spread_row(r, len(classes)) for r in t2],
        'chartHint': _wide_chart(t2, '%s'),
    }
    t3 = _wide_rows('pass')
    tables['cmp_pass'] = {
        'title': '对比班各科及格率%',
        'columns': _wide_columns([('c', '及格率%', 'num')]),
        'rows': t3,
        'chartHint': _wide_chart(t3, '%s及格率'),
    }
    t4 = _wide_rows('good')
    tables['cmp_good'] = {
        'title': '对比班各科优秀率%',
        'columns': _wide_columns([('c', '优秀率%', 'num')]),
        'rows': t4,
        'chartHint': _wide_chart(t4, '%s优秀率'),
    }

    # ---- T5/T6 分数段 / 名次段分布（所选班，堆叠）----
    def _seg_table(by_rank):
        segs = data.rank_segments() if by_rank else data.score_segments()
        mat = {c: [0] * len(segs) for c in classes}
        for t in rows_all:
            c = t['class_name']
            if c not in mat:
                continue
            idx = (data.rank_segment_index(t['rank_dir']) if by_rank
                   else data.segment_index(t['score']))
            if idx is None or not (0 <= idx < len(segs)):
                continue
            mat[c][idx] += 1
        rows = []
        for i, (label, _lo, _hi) in enumerate(segs):
            if not any(mat[c][i] for c in classes):
                continue    # 所选班在该段都无人 → 隐藏该段（与 _compact_segments 同口径）
            row = {'label': label}
            for j, c in enumerate(classes):
                row['c%d' % j] = mat[c][i]
            row['total'] = sum(mat[c][i] for c in classes)
            rows.append(row)
        return rows

    t5 = _seg_table(False)
    if t5:
        tables['cmp_score_seg'] = {
            'title': '对比班分数段人数分布表',
            'columns': [{'key': 'label', 'label': '分数段', 'type': 'text'}]
                       + [{'key': 'c%d' % i, 'label': c, 'type': 'int'}
                          for i, c in enumerate(classes)]
                       + [{'key': 'total', 'label': '所选班合计', 'type': 'int'}],
            'rows': t5,
            'chartHint': {'type': 'stack', 'stackKey': 'class_name',
                          'xAxis': [r['label'] for r in t5],
                          'series': [{'name': c, 'data': [r.get('c%d' % i) for r in t5]}
                                     for i, c in enumerate(classes)]},
        }
    t6 = _seg_table(True)
    if t6:
        tables['cmp_rank_seg'] = {
            'title': '对比班名次段人数分布表（方向内）',
            'columns': [{'key': 'label', 'label': '名次段', 'type': 'text'}]
                       + [{'key': 'c%d' % i, 'label': c, 'type': 'int'}
                          for i, c in enumerate(classes)]
                       + [{'key': 'total', 'label': '所选班合计', 'type': 'int'}],
            'rows': t6,
            'chartHint': {'type': 'stack', 'stackKey': 'class_name',
                          'xAxis': [r['label'] for r in t6],
                          'series': [{'name': c, 'data': [r.get('c%d' % i) for r in t6]}
                                     for i, c in enumerate(classes)]},
        }

    # ---- T7 分层上线（复用 A1 的累计口径，仅取所选班）----
    pass_stats = pv.layer_pass_stats(data, TOTAL_SUBJECT)
    if pass_stats and pass_stats.get('rows'):
        layers = pass_stats['layers']
        t7 = []
        for row in pass_stats['rows']:
            c = row['class_name']
            if c not in by_class:
                continue
            item = {'class_name': c, 'count': row['count']}
            for i, name in enumerate(layers):
                item['p_%d' % i] = row['pass'].get(name, 0)
                item['r_%d' % i] = row['rate'].get(name)
            t7.append(item)
        if t7:
            layer_cols = [{'key': 'class_name', 'label': '班级', 'type': 'text'},
                          {'key': 'count', 'label': '参考人数', 'type': 'int'}]
            for i, name in enumerate(layers):
                layer_cols += [{'key': 'p_%d' % i, 'label': f'{name}上线人数', 'type': 'int'},
                               {'key': 'r_%d' % i, 'label': f'{name}上线率%', 'type': 'num'}]
            tables['cmp_layer'] = {
                'title': '对比班分层累计上线表',
                'columns': layer_cols,
                'rows': t7,
                'chartHint': {'type': 'groupbar', 'xAxis': [r['class_name'] for r in t7],
                              'series': [{'name': name,
                                          'data': [r.get('r_%d' % i) for r in t7]}
                                         for i, name in enumerate(layers)]},
            }

    # ---- T8 总分五数概括（箱线）----
    t8 = []
    boxes = []
    for c in classes:
        five = st.box_five(score_map[c])
        if not five:
            continue
        # 偶数个元素取中位时会出现浮点尾数（如 541.1500000000001），统一保留 1 位
        five = [round(v, 1) for v in five]
        boxes.append(five)
        t8.append({'class_name': c, 'count': count_map[c],
                   'min': five[0], 'q1': five[1], 'median': five[2],
                   'q3': five[3], 'max': five[4]})
    if t8:
        tables['cmp_box'] = {
            'title': '对比班总分分布五数概括',
            'columns': [{'key': 'class_name', 'label': '班级', 'type': 'text'},
                        {'key': 'count', 'label': '参考人数', 'type': 'int'},
                        {'key': 'min', 'label': '最低分', 'type': 'num'},
                        {'key': 'q1', 'label': '下四分位', 'type': 'num'},
                        {'key': 'median', 'label': '中位数', 'type': 'num'},
                        {'key': 'q3', 'label': '上四分位', 'type': 'num'},
                        {'key': 'max', 'label': '最高分', 'type': 'num'}],
            'rows': t8,
            'chartHint': {'type': 'boxplot', 'xAxis': [r['class_name'] for r in t8],
                          'series': [{'name': '总分分布', 'data': boxes}]},
        }

    return {'exam': {'id': exam.id, 'name': exam.name, 'grade': exam.grade,
                     'date': exam.exam_date.strftime('%Y-%m-%d'), 'status': exam.status},
            'classes': classes,
            'tables': tables, 'charts': charts,
            'meta': {'classes': data.classes, 'selected': classes,
                     'directions': data.directions, 'direction': direction,
                     'partial': data.partial_import()}}


def _spread_row(row, n):
    """给「班 × 科目」宽表补一列：该科各班均分极差（最高班 - 最低班）"""
    vals = [row.get('c%d' % i) for i in range(n)]
    vals = [v for v in vals if v is not None]
    return dict(row, spread=round(max(vals) - min(vals), 1) if len(vals) >= 2 else None)
