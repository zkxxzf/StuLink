# StuLink v1.9.2 2026-09-16
# 四大分析模块数据组装（供 /api/analysis/* 与 Excel 导出共用）
# 表/图编号与设计文档 2.3 对应：A1(年级) A2(班级) A3(学科) A4(任课教师)
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from app.models.grades import SUBJECTS, TOTAL_SUBJECT
from app.modules.grades.services import stats_service as st
from app.modules.grades.services import pivot_service as pv
from app.modules.grades.utils import delete_cache_prefix


def _empty(exam):
    return {'exam': {'id': exam.id, 'name': exam.name, 'grade': exam.grade,
                     'date': exam.exam_date.strftime('%Y-%m-%d'), 'status': exam.status},
            'tables': {}, 'charts': {}, 'meta': {'empty': True}}


def _layer_names(data, direction):
    return [name for name, _lower in data.band_list(direction)]


def _layer_online_counts(data, subject, class_name=None, direction=None):
    """按累计口径统计某班/某方向 每科各层的「单上线 / 双上线」人数。

    口径（与汇报区/自由表一致）：
    - 单上线：单科 ≥ 该层单科线
    - 双上线：单科 ≥ 该层单科线 AND 学生总分 ≥ 同名层总分线

    返回 {层名: {'single_n': int, 'dual_n': int}}；
    未划线/该层无单科线或无同名总分线的科目不返回该层。

    注：不传 class_name 时返回全年级合计；A3/A4 热路径请在外层按班级分组后一次性遍历
    total_rows 避免 O(班级数 × n) 重复扫描。"""
    out = {}
    for t in data.totals_of(class_name=class_name, direction=direction):
        subj_score = data.subj.get((t['no'], subject))
        if subj_score is None:
            continue
        total_score = t['score']
        dir_key = t['direction'] or ''
        subj_bands = data.band_list(dir_key, subject)
        total_bands = data.band_list(dir_key, TOTAL_SUBJECT)
        total_map = {n: lo for n, lo in total_bands}
        for name, lower in subj_bands:
            d = out.setdefault(name, {'single_n': 0, 'dual_n': 0})
            if subj_score >= lower:
                d['single_n'] += 1
                tlo = total_map.get(name)
                if tlo is not None and total_score is not None and total_score >= tlo:
                    d['dual_n'] += 1
    return out


def _layer_online_counts_by_class(data, subject, direction=None):
    """遍历 total_rows 一次，按班级分组返回 {class_name: _layer_online_counts(...)}。

    替代在班级循环里反复调 _layer_online_counts → totals_of（每次都 O(n) 过滤），
    把 O(班级数 × n) 降到 O(n)。"""
    by_cls = {}
    for t in data.totals_of(direction=direction):
        cls = t['class_name'] or '—'
        subj_score = data.subj.get((t['no'], subject))
        if subj_score is None:
            continue
        total_score = t['score']
        dir_key = t['direction'] or ''
        subj_bands = data.band_list(dir_key, subject)
        total_bands = data.band_list(dir_key, TOTAL_SUBJECT)
        total_map = {n: lo for n, lo in total_bands}
        out = by_cls.setdefault(cls, {})
        for name, lower in subj_bands:
            d = out.setdefault(name, {'single_n': 0, 'dual_n': 0})
            if subj_score >= lower:
                d['single_n'] += 1
                tlo = total_map.get(name)
                if tlo is not None and total_score is not None and total_score >= tlo:
                    d['dual_n'] += 1
    return by_cls


def _fmt_pct(n, d):
    """分母为 0 时返回 None（前端渲染成 ---）"""
    return st.fmt_rate(n, d) if d else None


# ==================== A1 年级分析 ====================

def grade_tab(exam_id, direction=''):
    """A1 年级分析；direction=物理/历史 时仅统计该方向（空串=全部）"""
    # v1.13.2 性能：改用进程级共享缓存，避免每次打开分析页重复全量拉取明细行
    data = st.cached_exam_data(exam_id)
    exam = data.exam
    if not data.total_rows:
        return _empty(exam)
    tables, charts = {}, {}
    dr = direction or None   # None=全部

    # A1-T1 各班汇总表
    summary = st.class_summary_table(data, dr)
    shown_classes = [r['class_name'] for r in summary]
    layer_names = []
    for row in summary:
        for name in row['layers']:
            if name not in layer_names:
                layer_names.append(name)
    col_layer = [{'key': f'l_{i}', 'label': f'{name}数',
                  'type': 'text'} for i, name in enumerate(layer_names)]
    tables['class_summary'] = {
        'title': '年级各班汇总表',
        'columns': ([{'key': 'class_name', 'label': '班级', 'type': 'text'},
                     {'key': 'count', 'label': '参考人数', 'type': 'int'},
                     {'key': 'avg', 'label': '总分均值', 'type': 'num'},
                     {'key': 'std', 'label': '标准差', 'type': 'num'},
                     {'key': 'max', 'label': '最高分', 'type': 'num'},
                     {'key': 'min', 'label': '最低分', 'type': 'num'}] + col_layer),
        'rows': [dict(row, **{'l_%d' % i: _cell_layer(row['layers'].get(name))
                              for i, name in enumerate(layer_names)})
                 for row in summary],
    }
    # A1-T2 科目总表
    tables['subject_total'] = {
        'title': '年级科目总表',
        'columns': [
            {'key': 'subject', 'label': '科目', 'type': 'text'},
            {'key': 'count', 'label': '参考人数', 'type': 'int'},
            {'key': 'full', 'label': '满分', 'type': 'int'},
            {'key': 'avg', 'label': '年级平均分', 'type': 'num'},
            {'key': 'max', 'label': '最高分', 'type': 'num'},
            {'key': 'min', 'label': '最低分', 'type': 'num'},
            {'key': 'std', 'label': '标准差', 'type': 'num'},
            {'key': 'pass_rate', 'label': '及格率%', 'type': 'num'},
            {'key': 'good_rate', 'label': '优秀率%', 'type': 'num'},
        ],
        'rows': st.subject_table(data, dr),
    }
    # A1-T6 各班分层上线统计（累计口径：达该层下界及以上，按方向各自划线判定）
    pass_stats = pv.layer_pass_stats(data, TOTAL_SUBJECT)
    if pass_stats:
        _t = pv.layer_pass_table(pass_stats)
        if _t:
            tables['layer_pass'] = _t
    # A1-T3 分数段 / A1-T4 名次段（一次算出，分段结果同时供 G2 堆叠图复用）
    segs, seg_rows = _segment_tables(data, tables, direction=dr, classes=shown_classes,
                                     ret=True)
    # A1-T5 历次考试总览（聚合查询，避免逐场构造 ExamData）
    rows = []
    _tm = st.exam_score_means(exam.grade, [TOTAL_SUBJECT], direction=dr)
    for _eid, _info in _tm.items():
        _m = _info.get('means', {}).get(TOTAL_SUBJECT)
        _c = _info.get('counts', {}).get(TOTAL_SUBJECT)
        if _m is None:
            continue
        rows.append({'exam': _info['name'], 'date': _info['date'], 'count': _c, 'avg': _m})
    tables['trend_overview'] = {
        'title': '历次考试总览',
        'columns': [{'key': 'exam', 'label': '考试', 'type': 'text'},
                    {'key': 'date', 'label': '日期', 'type': 'text'},
                    {'key': 'count', 'label': '参考人数', 'type': 'int'},
                    {'key': 'avg', 'label': '总分均值', 'type': 'num'}],
        'rows': rows,
    }

    # ---- 图表 ----
    # G1 各班总分均值柱状
    charts['avg_bar'] = {
        'type': 'bar', 'title': '各班总分均值对比',
        'xAxis': shown_classes,
        'series': [{'name': '总分均值', 'data': [r['avg'] for r in summary]}],
        'markLine': st.mean([r['avg'] for r in summary if r['avg'] is not None]) or 0,
    }
    # G2 分数段堆叠柱（复用上面已算好的 segs/seg_rows，避免再统计一遍全年级）
    charts['seg_stack'] = {
        'type': 'stack', 'title': '年级分数段人数分布', 'stackKey': 'class_name',
        'xAxis': [s[0] for s in segs],
        'series': [_stack_series(seg_rows, cls) for cls in shown_classes],
    }
    # G3 箱线
    charts['box'] = {
        'type': 'boxplot', 'title': '各班总分分布（箱线）',
        'xAxis': shown_classes,
        'series': [{'data': [st.box_five([t['score'] for t in data.totals_of(class_name=c,
                                                                            direction=dr)])
                             for c in shown_classes]}],
    }
    # G4 历次多折线
    trends = _trend_lines(exam.grade, direction=dr)
    charts['trend_line'] = {
        'type': 'line', 'title': '年级历次考试平均分变化趋势',
        'xAxis': trends['x'],
        'series': trends['series'],
        'selected': {'总分': True, '语文': True, '数学': True, '外语': True},
    }
    # G5 各班分层累计上线率（需已划线；未划线时 pass_stats 为 None，不出图）
    if pass_stats:
        charts['layer_rate'] = {
            'type': 'groupbar', 'title': '各班分层累计上线率（%）',
            'xAxis': [r['class_name'] for r in pass_stats['rows']],
            'series': [{'name': n,
                        'data': [r['rate'].get(n) for r in pass_stats['rows']]}
                       for n in pass_stats['layers']],
        }
    return {'exam': {'id': exam.id, 'name': exam.name, 'grade': exam.grade,
                     'date': exam.exam_date.strftime('%Y-%m-%d'), 'status': exam.status},
            'tables': tables, 'charts': charts,
            'meta': {'classes': shown_classes, 'directions': data.directions,
                     'layers': layer_names, 'direction': direction,
                     'partial': data.partial_import()}}


def _cell_layer(v):
    return f'{v}' if v else '—'


def _compact_segments(segs, rows):
    """隐藏全空段（表格与图表共用同一分段）"""
    keep = [(s, r) for s, r in zip(segs, rows) if r['total'] > 0]
    if not keep:
        return [], []
    return [s for s, _ in keep], [r for _, r in keep]


def _segment_tables(data, tables, direction=None, classes=None, ret=False):
    cls = classes if classes is not None else data.classes
    total_n = len(data.totals_of(direction=direction))

    segs, rows = st.segment_table(data, direction=direction)
    segs, rows = _compact_segments(segs, rows)
    tables['score_segment'] = {
        'title': '年级分数段分布表',
        'columns': [{'key': 'label', 'label': '分数段', 'type': 'text'}]
                   + [{'key': 'c_' + c, 'label': c, 'type': 'int'} for c in cls]
                   + [{'key': 'total', 'label': '年级合计', 'type': 'int'},
                      {'key': 'ratio', 'label': '合计占比%', 'type': 'num'}],
        'rows': _segment_rows(rows, cls, total_n),
    }
    segs2, rows2 = st.segment_table(data, direction=direction, by_rank=True)
    segs2, rows2 = _compact_segments(segs2, rows2)
    tables['rank_segment'] = {
        'title': '年级名次段分布表（方向内）',
        'columns': [{'key': 'label', 'label': '名次段', 'type': 'text'}]
                   + [{'key': 'c_' + c, 'label': c, 'type': 'int'} for c in cls]
                   + [{'key': 'total', 'label': '年级合计', 'type': 'int'},
                      {'key': 'ratio', 'label': '合计占比%', 'type': 'num'}],
        'rows': _segment_rows(rows2, cls, total_n),
    }
    return (segs, rows) if ret else None


def _segment_rows(rows, classes, total):
    out = []
    for row in rows:
        out.append({'label': row['label'],
                    **{'c_' + c: row['counts'].get(c, 0) for c in classes},
                    'total': row['total'],
                    'ratio': st.fmt_rate(row['total'], total)})
    return out


def _stack_series(rows, cls):
    return {'name': cls, 'data': [r['counts'].get(cls, 0) for r in rows]}


def _trend_lines(grade, direction=None):
    """历次考试折线数据；direction 过滤时只统计该方向（聚合查询，避免逐场构造 ExamData）"""
    x, series_map = [], {}
    _means = st.exam_score_means(grade, [TOTAL_SUBJECT] + list(SUBJECTS), direction=direction)
    for _eid, _info in _means.items():
        _m = _info.get('means', {})
        if _m.get(TOTAL_SUBJECT) is None:
            continue
        x.append(_info['date'][5:])
        series_map.setdefault(TOTAL_SUBJECT, []).append(_m.get(TOTAL_SUBJECT))
        for sub in SUBJECTS:
            series_map.setdefault(sub, []).append(_m.get(sub))
    series = [{'name': k, 'data': v} for k, v in series_map.items()]
    return {'x': x, 'series': series}


# ==================== A2 班级分析 ====================

def class_tab(exam_id, class_name):
    data = st.cached_exam_data(exam_id)
    exam = data.exam
    if not data.total_rows or class_name not in data.classes:
        return _empty(exam)
    tables, charts = {}, {}
    totals = data.totals_of(class_name=class_name)
    grade_avg = st.mean([r.score for r in data.total_rows])
    cls_avg = st.mean([t['score'] for t in totals])
    fm = data.full_marks()

    # A2-T1 班级概况
    segs, rows = st.segment_table(data, class_name=class_name)
    tables['class_overview'] = {
        'title': '班级各分数段人数',
        'columns': [{'key': 'k', 'label': '项目', 'type': 'text'}] +
                   [{'key': 's%d' % i, 'label': s[0], 'type': 'int'} for i, s in enumerate(segs)],
        'rows': [
            {'k': '各分数段人数', **{'s%d' % i: r['counts'].get(class_name, 0)
                                  for i, r in enumerate(rows)}},
        ],
    }
    tables['class_meta'] = {
        'title': '班级基本指标',
        'columns': [{'key': 'k', 'label': '指标', 'type': 'text'},
                    {'key': 'v', 'label': '数值', 'type': 'text'}],
        'rows': [
            {'k': '参考人数', 'v': len(totals)},
            {'k': '年级参考人数', 'v': len(data.total_rows)},
            {'k': '总分均值', 'v': cls_avg},
            {'k': '年级均值', 'v': grade_avg},
            {'k': '与年级均值差', 'v': (round(cls_avg - grade_avg, 1)
                                     if cls_avg is not None and grade_avg is not None else '—')},
        ],
    }
    # A2-T2 班级-年级科目对标
    subs = data.class_subjects(class_name)
    t2 = []
    for sub in subs:
        cls_s = data.scores_of_subject(sub, class_name=class_name)
        grd_s = data.scores_of_subject(sub)
        if not cls_s:
            continue
        lines = data.subject_lines(sub)
        cls_pass = st.fmt_rate(sum(1 for s in cls_s if s >= lines['pass']), len(cls_s))
        grd_pass = st.fmt_rate(sum(1 for s in grd_s if s >= lines['pass']), len(grd_s))
        cls_m, grd_m = st.mean(cls_s), st.mean(grd_s)
        t2.append({'subject': sub, 'cls_count': len(cls_s), 'cls_avg': cls_m,
                   'grd_avg': grd_m,
                   'diff': round(cls_m - grd_m, 1) if cls_m is not None and grd_m is not None else None,
                   'cls_pass': cls_pass, 'grd_pass': grd_pass})
    tables['subject_compare'] = {
        'title': '班级-年级科目对标表',
        'columns': [{'key': 'subject', 'label': '科目', 'type': 'text'},
                    {'key': 'cls_count', 'label': '本班参考人数', 'type': 'int'},
                    {'key': 'cls_avg', 'label': '本班平均分', 'type': 'num'},
                    {'key': 'grd_avg', 'label': '年级平均分', 'type': 'num'},
                    {'key': 'diff', 'label': '分差', 'type': 'num'},
                    {'key': 'cls_pass', 'label': '本班及格率%', 'type': 'num'},
                    {'key': 'grd_pass', 'label': '年级及格率%', 'type': 'num'}],
        'rows': t2,
    }
    # A2-T3 学生明细（本次/上次）
    t3 = []
    for t in sorted(totals, key=lambda x: x['rank_dir'] or 99999):
        prev = data.prev_totals.get(t['no'])
        cur_score = t['score']
        prev_score = prev[0] if prev else None
        prev_rank = prev[1] if prev else None
        t3.append({'no': t['no'], 'name': t['name'], 'class_name': t['class_name'],
                   'total': cur_score, 'rank': t['rank_dir'],
                   'prev_total': prev_score, 'prev_rank': prev_rank,
                   'score_move': round(cur_score - prev_score, 1) if prev_score is not None else None,
                   'rank_move': (t['rank_dir'] - prev_rank) if prev_rank else None})
    tables['student_detail'] = {
        'title': '班级学生历次成绩明细表',
        'columns': [{'key': 'no', 'label': '学号', 'type': 'text'},
                    {'key': 'name', 'label': '姓名', 'type': 'text'},
                    {'key': 'total', 'label': '本次总分', 'type': 'num'},
                    {'key': 'rank', 'label': '本次排名', 'type': 'int'},
                    {'key': 'prev_total', 'label': '上次总分', 'type': 'num'},
                    {'key': 'prev_rank', 'label': '上次排名', 'type': 'int'},
                    {'key': 'score_move', 'label': '分数变动', 'type': 'num'},
                    {'key': 'rank_move', 'label': '排名变动', 'type': 'int'}],
        'rows': t3,
    }
    # A2-T4 分层统计（按本班学生自身方向判定）
    t4 = []
    layers_map = {}
    for t in totals:
        idx = data.band_of_score(t['direction'], t['score'])
        if idx is None:
            continue
        bands = data.band_list(t['direction'])
        if not (0 <= idx < len(bands)):
            continue
        layers_map.setdefault((t['direction'], bands[idx][0]), []).append(t)
    for (direction, lname), members in layers_map.items():
        row = {'layer': lname, 'direction': direction, 'count': len(members),
               'ratio': st.fmt_rate(len(members), len(totals)),
               'avg': st.mean([m['score'] for m in members])}
        for sub in subs:
            vals = []
            for m in members:
                s = data.subj.get((m['no'], sub))
                if s is not None:
                    vals.append(s)
            row[sub] = st.mean(vals)
        t4.append(row)
    t4.sort(key=lambda r: r['layer'])
    tables['layer_stat'] = {
        'title': '班级分层统计表',
        'columns': [{'key': 'layer', 'label': '层', 'type': 'text'},
                    {'key': 'direction', 'label': '方向', 'type': 'text'},
                    {'key': 'count', 'label': '人数', 'type': 'int'},
                    {'key': 'ratio', 'label': '占比%', 'type': 'num'},
                    {'key': 'avg', 'label': '总分均值', 'type': 'num'}] +
                   [{'key': s, 'label': s, 'type': 'num'} for s in subs],
        'rows': t4,
    }
    # 图
    charts['subject_bar'] = {
        'type': 'groupbar', 'title': '本班各科 vs 年级各科平均分',
        'xAxis': [r['subject'] for r in t2],
        'series': [
            {'name': '本班', 'data': [r['cls_avg'] for r in t2]},
            {'name': '年级', 'data': [r['grd_avg'] for r in t2]},
        ],
    }
    hist = _histogram(data, class_name)
    charts['histogram'] = {
        'type': 'bar', 'title': '本班总分分布直方图',
        'xAxis': hist['labels'], 'series': [{'name': '人数', 'data': hist['counts']}],
    }
    charts['trend'] = _class_trend(exam.grade, class_name)
    charts['radar'] = {
        'type': 'radar', 'title': '班级各科能力雷达图（得分率%）',
        'indicators': [{'name': r['subject'], 'max': 100} for r in t2],
        'series': [
            {'name': '本班', 'data': [_score_rate(t, fm, 'cls_avg') for t in t2]},
            {'name': '年级', 'data': [_score_rate(t, fm, 'grd_avg') for t in t2]},
        ],
    }
    return {'exam': {'id': exam.id, 'name': exam.name, 'grade': exam.grade,
                     'date': exam.exam_date.strftime('%Y-%m-%d'), 'status': exam.status},
            'class_name': class_name,
            'tables': tables, 'charts': charts,
            'meta': {'classes': data.classes, 'partial': data.partial_import()}}


def _score_rate(t, fm, key):
    v = t.get(key)
    if v is None:
        return None
    full = fm.get(t['subject'], 100) or 100
    return round(v / full * 100, 1)


def _histogram(data, class_name):
    segs = data.score_segments()
    counts = [0] * len(segs)
    for t in data.totals_of(class_name=class_name):
        counts[data.segment_index(t['score'])] += 1
    return {'labels': [s[0] for s in segs], 'counts': counts}


def _class_trend(grade, class_name):
    x, cls_line, grd_line = [], [], []
    _cls = st.exam_score_means(grade, [TOTAL_SUBJECT], class_name=class_name)
    _grd = st.exam_score_means(grade, [TOTAL_SUBJECT])
    for _eid, _info in _cls.items():
        _cm = _info.get('means', {}).get(TOTAL_SUBJECT)
        if _cm is None:
            continue
        x.append(_info['date'][5:])
        cls_line.append(_cm)
        grd_line.append(_grd.get(_eid, {}).get('means', {}).get(TOTAL_SUBJECT))
    return {'type': 'line', 'title': '班级历次考试平均分走势',
            'xAxis': x,
            'series': [{'name': f'{class_name}总分均分', 'data': cls_line},
                       {'name': '年级总分均分', 'data': grd_line}]}


# ==================== A3 学科分析 ====================

def subject_tab(exam_id, subject, direction=''):
    """A3 学科分析；direction=物理/历史 时仅统计该方向（空串=全部）"""
    data = st.cached_exam_data(exam_id)
    exam = data.exam
    if not data.total_rows:
        return _empty(exam)
    tables, charts = {}, {}
    dr = direction or None
    scores_map = {}   # class -> scores
    grd_scores = data.scores_of_subject(subject, direction=dr)
    grd_avg = st.mean(grd_scores)
    lines = data.subject_lines(subject)
    # 该科「方向 × 学科」单科线（未划线时为空，表格不出现分层列）
    sel_dirs = data.directions if not dr else [direction]
    band_lines = {d: data.band_list(d, subject) for d in sel_dirs}
    layer_names = []
    for d in sel_dirs:
        for name, _lower in band_lines[d]:
            if name not in layer_names:
                layer_names.append(name)

    def _layer_counts(cls):
        """该班学生在本学科各层的人数（按学生自身方向的单科线判定，落入层/互斥）"""
        counts = {name: 0 for name in layer_names}
        for t in data.totals_of(class_name=cls, direction=dr):
            s = data.subj.get((t['no'], subject))
            if s is None:
                continue
            idx = data.band_of_score(t['direction'], s, subject)
            if idx is None:
                continue
            bl = band_lines.get(t['direction']) or []
            if 0 <= idx < len(bl):
                counts[bl[idx][0]] = counts.get(bl[idx][0], 0) + 1
        return counts

    # v1.13.2 双上线：预算一次按班级分组，班级循环里直接查（O(n) 而非 O(班级数 × n)）
    online_by_cls = (_layer_online_counts_by_class(data, subject, direction=dr)
                     if layer_names else {})
    t1 = []
    for cls in data.classes:
        cls_s = data.scores_of_subject(subject, class_name=cls, direction=dr)
        if not cls_s:
            continue
        scores_map[cls] = cls_s
        row = {
            'subject': subject, 'class_name': cls, 'count': len(cls_s),
            'avg': st.mean(cls_s),
            'pass_rate': st.fmt_rate(sum(1 for s in cls_s if s >= lines['pass']), len(cls_s)),
            'good_rate': st.fmt_rate(sum(1 for s in cls_s if s >= lines['excellent']), len(cls_s)),
            'max': max(cls_s),
            'low_count': sum(1 for s in cls_s if s < lines['low']),
            'diff': round(st.mean(cls_s) - grd_avg, 1) if grd_avg is not None else None,
        }
        if layer_names:
            lc = _layer_counts(cls)
            online = online_by_cls.get(cls, {})
            for i, name in enumerate(layer_names):
                row['l_%d' % i] = lc.get(name, 0)
                od = online.get(name, {})
                sn = od.get('single_n', 0)
                dn = od.get('dual_n', 0)
                row['lo_%d_n' % i] = sn
                row['lo_%d_dn' % i] = dn
                row['lo_%d_dr' % i] = _fmt_pct(dn, len(cls_s))
        t1.append(row)
    t1.sort(key=lambda r: -(r['avg'] or 0))
    cmp_cols = [{'key': 'subject', 'label': '科目', 'type': 'text'},
                {'key': 'class_name', 'label': '班级', 'type': 'text'},
                {'key': 'count', 'label': '参考人数', 'type': 'int'},
                {'key': 'avg', 'label': '平均分', 'type': 'num'},
                {'key': 'pass_rate', 'label': '及格率%', 'type': 'num'},
                {'key': 'good_rate', 'label': '优秀率%', 'type': 'num'},
                {'key': 'max', 'label': '最高分', 'type': 'num'},
                {'key': 'low_count', 'label': '低分人数', 'type': 'int'},
                {'key': 'diff', 'label': '与年级均分差', 'type': 'num'}]
    if layer_names:
        # v1.13.2 双上线：每层 4 列（落层人数 / 单上线人数 / 双上线人数 / 双上线率%）
        # 落层人数：该科实际分数落入该层的学生数（互斥，每人只属一层）
        # 单/双上线：累计口径（达该层下界及以上），双上线=单科+总分双过同层线
        for i, name in enumerate(layer_names):
            cmp_cols += [
                {'key': 'l_%d' % i, 'label': f'{name}人数', 'type': 'int'},
                {'key': 'lo_%d_n' % i, 'label': f'{name}单上', 'type': 'int'},
                {'key': 'lo_%d_dn' % i, 'label': f'{name}双上', 'type': 'int'},
                {'key': 'lo_%d_dr' % i, 'label': f'{name}双上率%', 'type': 'num'},
            ]
    tables['class_compare'] = {
        'title': f'{subject}学科各班对比表',
        'columns': cmp_cols,
        'rows': t1,
    }
    # A3-T2 学科历次（聚合查询，避免逐场构造 ExamData）
    t2 = []
    for _r in st.exam_trend_series(exam.grade, subject, direction=dr, need_rates=True):
        t2.append({'exam': _r['name'], 'date': _r['date'], 'count': _r['count'],
                   'avg': _r['avg'], 'pass_rate': _r['pass_rate'], 'good_rate': _r['good_rate']})
    tables['history'] = {
        'title': f'{subject}学科历次考试统计表',
        'columns': [{'key': 'exam', 'label': '考试', 'type': 'text'},
                    {'key': 'date', 'label': '日期', 'type': 'text'},
                    {'key': 'count', 'label': '参考人数', 'type': 'int'},
                    {'key': 'avg', 'label': '平均分', 'type': 'num'},
                    {'key': 'pass_rate', 'label': '及格率%', 'type': 'num'},
                    {'key': 'good_rate', 'label': '优秀率%', 'type': 'num'}],
        'rows': t2,
    }
    # A3-T3 分层内得分（指定方向时仅该方向分层）
    t3 = []
    for direction in sel_dirs:
        for lname, _lower in data.band_list(direction):
            members = []
            for t in data.totals_of(direction=direction):
                idx = data.band_of_score(t['direction'], t['score'])
                if idx is None:
                    continue
                bands = data.band_list(t['direction'])
                if 0 <= idx < len(bands) and bands[idx][0] == lname:
                    members.append(t)
            vals = [data.subj.get((m['no'], subject)) for m in members]
            vals = [v for v in vals if v is not None]
            if not members:
                continue
            l2 = data.subject_lines(subject)
            t3.append({'layer': lname, 'direction': direction,
                       'count': len(members),
                       'avg': st.mean(vals) if vals else None,
                       'pass_rate': st.fmt_rate(sum(1 for s in vals if s >= l2['pass']),
                                                len(vals)) if vals else None})
    tables['layer_score'] = {
        'title': f'{subject}总分分层内得分统计表',
        'columns': [{'key': 'direction', 'label': '方向', 'type': 'text'},
                    {'key': 'layer', 'label': '层', 'type': 'text'},
                    {'key': 'count', 'label': '该层人数', 'type': 'int'},
                    {'key': 'avg', 'label': '该科均分', 'type': 'num'},
                    {'key': 'pass_rate', 'label': '该科及格率%', 'type': 'num'}],
        'rows': t3,
    }
    # A3-T4 该科单科线分层统计（按该科自身分数线划分，而非总分分层）
    t4 = []
    for d in sel_dirs:
        bl = band_lines.get(d) or []
        if not bl:
            continue
        members = {name: [] for name, _lower in bl}
        for t in data.totals_of(direction=d):
            s = data.subj.get((t['no'], subject))
            if s is None:
                continue
            idx = data.band_of_score(d, s, subject)
            if idx is not None and 0 <= idx < len(bl):
                members[bl[idx][0]].append(s)
        n_total = sum(len(v) for v in members.values())
        for name, lower in bl:
            vals = members.get(name) or []
            if not vals:
                continue
            t4.append({'direction': d, 'layer': name, 'lower': lower,
                       'count': len(vals),
                       'ratio': st.fmt_rate(len(vals), n_total) if n_total else None,
                       'avg': st.mean(vals)})
    tables['subject_layer'] = {
        'title': f'{subject}单科划线分层统计表',
        'columns': [{'key': 'direction', 'label': '方向', 'type': 'text'},
                    {'key': 'layer', 'label': '层', 'type': 'text'},
                    {'key': 'lower', 'label': '分数线', 'type': 'num'},
                    {'key': 'count', 'label': '人数', 'type': 'int'},
                    {'key': 'ratio', 'label': '占比%', 'type': 'num'},
                    {'key': 'avg', 'label': '该科均分', 'type': 'num'}],
        'rows': t4,
    }
    # 图
    charts['heatmap'] = _heatmap(data, dr)
    charts['class_bar'] = {
        'type': 'bar', 'title': f'{subject}各班得分对比',
        'xAxis': [r['class_name'] for r in t1],
        'series': [{'name': subject, 'data': [r['avg'] for r in t1]}],
        'markLine': grd_avg,
        # 该科各层分数线（参考线）；未划线时为空数组
        'bandLines': [{'name': f'{d}·{name}', 'value': lower}
                      for d in sel_dirs for name, lower in (band_lines.get(d) or [])],
    }
    charts['trend'] = {
        'type': 'dual', 'title': f'{subject}历次全年级平均分/及格率',
        'xAxis': [r['exam'] for r in t2],
        'series': [
            {'name': '平均分', 'yAxis': 0, 'data': [r['avg'] for r in t2]},
            {'name': '及格率%', 'yAxis': 1, 'data': [r['pass_rate'] for r in t2]},
        ],
    }
    return {'exam': {'id': exam.id, 'name': exam.name, 'grade': exam.grade,
                     'date': exam.exam_date.strftime('%Y-%m-%d'), 'status': exam.status},
            'subject': subject, 'tables': tables, 'charts': charts,
            'meta': {'classes': data.classes}}


def _heatmap(data, direction=None):
    """科目 × 班级 平均分热力矩阵（direction 过滤：只显示该方向班级与该方向实际开考科目）"""
    shown_classes = [c for c in data.classes
                     if data.totals_of(class_name=c, direction=direction)]
    values = []
    y_labels = []
    for sub in SUBJECTS:
        col = []
        for cls in shown_classes:
            s = data.scores_of_subject(sub, class_name=cls, direction=direction)
            col.append(st.mean(s) if s else None)
        if not any(col):
            continue  # 该方向未开考科目不显示（避免全空行）
        y_labels.append(sub)
        values.append(col)
    return {'type': 'heatmap', 'title': '各班级各科目平均分热力矩阵',
            'xAxis': shown_classes, 'yAxis': y_labels, 'data': values}


# ==================== A4 任课教师分析 ====================

def teacher_tab(exam_id, subject=None, links=None, grade=None):
    """links: 允许展示的 (grade,class_name,subject,user_id) 列表（None=全部 active）"""
    from app.models.grades import TeacherSubjectLink
    from app.models import User
    data = st.cached_exam_data(exam_id)
    exam = data.exam
    if not data.total_rows:
        return _empty(exam)
    if links is None:
        q = TeacherSubjectLink.query.filter_by(active=True)
        if grade:
            q = q.filter_by(grade=grade)
        links = q.all()
    tables, charts = {}, {}
    fm = data.full_marks()
    users = {u.id: u.real_name for u in User.query.filter(
        User.id.in_([l.user_id for l in links] or [0])).all()}

    t1 = []
    # v1.13.2 双上线：预算一次按 (class_name, subject) 分组，避免每个 link 都 O(n) 过滤
    # subject_layer_names 仅用于决定是否显示分层列；真正的线上统计按 link.subject 各自的划线
    # 列层名取自「默认首个方向 + 首个有数据的科目」——仅用于表头展示，不影响计算
    _first_subj = None
    for l in links:
        if not subject or l.subject == subject:
            _first_subj = l.subject
            break
    if not _first_subj:
        _first_subj = subject or SUBJECTS[0]
    _first_dir = (data.directions or [''])[0]
    subject_layer_names = [n for n, _ in data.band_list(_first_dir, _first_subj)]
    all_subjects = sorted({l.subject for l in links if not subject or l.subject == subject})
    online_cache = {}  # (class_name, subject) -> online_counts
    if subject_layer_names:
        for s in all_subjects:
            by_cls = _layer_online_counts_by_class(data, s)
            for cls, od in by_cls.items():
                online_cache[(cls, s)] = od
    for link in links:
        if subject and link.subject != subject:
            continue
        cls_s = data.scores_of_subject(link.subject, class_name=link.class_name)
        if not cls_s:
            continue
        grd_s = data.scores_of_subject(link.subject)
        grd_avg = st.mean(grd_s)
        l2 = data.subject_lines(link.subject)
        row = {
            'teacher': users.get(link.user_id, f'#{link.user_id}'),
            'class_name': link.class_name, 'subject': link.subject,
            'grade': link.grade,
            'count': len(cls_s),
            'avg': st.mean(cls_s),
            'grd_avg': grd_avg,
            'diff': round(st.mean(cls_s) - grd_avg, 1) if grd_avg is not None else None,
            'pass_rate': st.fmt_rate(sum(1 for s in cls_s if s >= l2['pass']), len(cls_s)),
            'good_rate': st.fmt_rate(sum(1 for s in cls_s if s >= l2['excellent']), len(cls_s)),
        }
        if subject_layer_names:
            online = online_cache.get((link.class_name, link.subject), {})
            for i, name in enumerate(subject_layer_names):
                od = online.get(name, {})
                sn = od.get('single_n', 0)
                dn = od.get('dual_n', 0)
                row['t_lo_%d_n' % i] = sn
                row['t_lo_%d_dn' % i] = dn
                row['t_lo_%d_dr' % i] = _fmt_pct(dn, len(cls_s))
        t1.append(row)
    # 列定义
    teacher_cols = [
        {'key': 'teacher', 'label': '教师姓名', 'type': 'text'},
        {'key': 'grade', 'label': '年级', 'type': 'text'},
        {'key': 'class_name', 'label': '班级', 'type': 'text'},
        {'key': 'subject', 'label': '科目', 'type': 'text'},
        {'key': 'count', 'label': '参考人数', 'type': 'int'},
        {'key': 'avg', 'label': '科目平均分', 'type': 'num'},
        {'key': 'grd_avg', 'label': '年级该科平均分', 'type': 'num'},
        {'key': 'diff', 'label': '分差', 'type': 'num'},
        {'key': 'pass_rate', 'label': '及格率%', 'type': 'num'},
        {'key': 'good_rate', 'label': '优秀率%', 'type': 'num'},
    ]
    if subject_layer_names:
        for i, name in enumerate(subject_layer_names):
            teacher_cols += [
                {'key': 't_lo_%d_n' % i, 'label': f'{name}单上', 'type': 'int'},
                {'key': 't_lo_%d_dn' % i, 'label': f'{name}双上', 'type': 'int'},
                {'key': 't_lo_%d_dr' % i, 'label': f'{name}双上率%', 'type': 'num'},
            ]
    tables['teacher_data'] = {
        'title': '教师教学数据表',
        'columns': teacher_cols,
        'rows': t1,
    }
    # 任课组合（供前端选择器）
    combos = []
    for link in links:
        if subject and link.subject != subject:
            continue
        combos.append({'grade': link.grade, 'class_name': link.class_name,
                       'subject': link.subject, 'user_id': link.user_id,
                       'teacher': users.get(link.user_id, '')})
    # 教师历次跟踪（按第一个有数据的组合或传入 combos[0]）
    combo = combos[0] if combos else None
    t2 = []
    if combo:
        _cls_track = st.exam_trend_series(exam.grade, combo['subject'],
                                          class_name=combo['class_name'], need_rates=True)
        _grd_track = {_r['id']: _r for _r in
                      st.exam_trend_series(exam.grade, combo['subject'])}
        for _r in _cls_track:
            t2.append({'exam': _r['name'], 'date': _r['date'], 'count': _r['count'],
                       'avg': _r['avg'],
                       'grd_avg': _grd_track.get(_r['id'], {}).get('avg'),
                       'pass_rate': _r['pass_rate']})
    tables['teacher_track'] = {
        'title': '教师历次考试跟踪表',
        'columns': [{'key': 'exam', 'label': '考试', 'type': 'text'},
                    {'key': 'date', 'label': '日期', 'type': 'text'},
                    {'key': 'count', 'label': '参考人数', 'type': 'int'},
                    {'key': 'avg', 'label': '该班该科均分', 'type': 'num'},
                    {'key': 'grd_avg', 'label': '年级该科均分', 'type': 'num'},
                    {'key': 'pass_rate', 'label': '及格率%', 'type': 'num'}],
        'rows': t2,
    }
    # 图：G12 同科目不同教师柱状对比；G13 组合走势
    charts['teacher_bar'] = {
        'type': 'bar', 'title': f"{subject or '所选科目'}不同任课教师所带班级平均分对比",
        'xAxis': [f"{r['teacher']}·{r['class_name']}" for r in t1],
        'series': [{'name': '班级均分', 'data': [r['avg'] for r in t1]}],
        'markLine': (st.mean([r['grd_avg'] for r in t1]) if t1 else None),
    }
    charts['teacher_trend'] = {
        'type': 'line', 'title': '该组合所带科目历次成绩走势',
        'xAxis': [r['exam'] for r in t2],
        'series': [{'name': '该班该科均分', 'data': [r['avg'] for r in t2]},
                   {'name': '年级该科均分', 'data': [r['grd_avg'] for r in t2]}],
    }
    return {'exam': {'id': exam.id, 'name': exam.name, 'grade': exam.grade,
                     'date': exam.exam_date.strftime('%Y-%m-%d'), 'status': exam.status},
            'tables': tables, 'charts': charts,
            'meta': {'combos': combos}}


def clear_exam_cache(exam_id):
    # v1.13.0 同时失效汇报区缓存（单/双上线、去差均分等依赖划线与成绩）
    from app.modules.grades.utils import invalidate_exam_cache
    invalidate_exam_cache(exam_id)
