# StuLink v1.19.49.36 2026-10-10
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
        sel = t.get('selection') or ''
        # 总分线按学生自身组合取线（组合总分线优先，回落方向线）；单科线同口径回落
        subj_bands = data.band_list(dir_key, subject, sel)
        total_bands = data.band_list(dir_key, TOTAL_SUBJECT, sel)
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
        sel = t.get('selection') or ''
        # 总分线按学生自身组合取线（组合总分线优先，回落方向线）；单科线同口径回落
        subj_bands = data.band_list(dir_key, subject, sel)
        total_bands = data.band_list(dir_key, TOTAL_SUBJECT, sel)
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


# 原「及格率/优秀率」两列在本场划线里对应的层名（统一实现见 stats_service.rate_names_for）
_rate_names_for = st.rate_names_for


def _rate_columns(pass_name, good_name):
    """原「及格率%/优秀率%」两列的列定义：列名=实际层名（如 特控线率%/本科线率%）；
    两层同名（单层模板）时只出一列；未划线返回空列表（不出这两列）。"""
    cols = []
    if pass_name:
        cols.append({'key': 'pass_rate', 'label': f'{pass_name}率%', 'type': 'num'})
    if good_name and good_name != pass_name:
        cols.append({'key': 'good_rate', 'label': f'{good_name}率%', 'type': 'num'})
    return cols


def _uniq_band_lines(band_lines):
    """把 {(方向, 组合): [(层名, 下界)]} 转成图表参考线，同一条线只保留一条。

    2026-10-15 组合级单科线：语数外各组合共享方向线、再选四科各组合各一条 →
    去重后语数外只显示一次、化学显示三条（组合名标注）。
    """
    seen, out = set(), []
    for (d, sel), bl in band_lines.items():
        for name, lower in bl:
            key = (name, lower)
            if key in seen:
                continue
            seen.add(key)
            out.append({'name': f'{sel or d}·{name}', 'value': lower})
    return out


def _rate_of(online, name, denom):
    """单科上线率：online 为 _layer_online_counts* 输出 {层名: {'single_n': n}}。

    该层缺失（该科/该方向没划过这条线）或未划线时返回 None（前端显示 —，
    不再回落到满分 60%/90% 固定线）。
    """
    if not name or not denom:
        return None
    od = (online or {}).get(name)
    if not od:
        return None
    return _fmt_pct(od.get('single_n', 0), denom)


# ==================== A1 年级分析 ====================

def grade_tab(exam_id, direction='', selection='', denom='ref'):
    """A1 年级分析；direction=物理/历史 时仅统计该方向，selection=选科组合时仅统计该组合

    denom（2026-10-17）：ref=参考数（成绩>0 才算参考）/ enrolled=班级实际在籍人数，
    影响所有「人数/率/均分」口径；全程随 population 视图下传，不逐个改函数签名。
    """
    # v1.13.2 性能：改用进程级共享缓存，避免每次打开分析页重复全量拉取明细行
    data = st.cached_exam_data(exam_id)
    exam = data.exam
    if not data.total_rows:
        return _empty(exam)
    tables, charts = {}, {}
    dr = direction or None   # None=全部
    sel = selection or None  # None=全部组合（2026-10-16 组合维度）
    # 人群视图：之后本函数所有 totals_of/scores_of_subject 都按 (方向×组合×口径) 收窄
    data = data.population(dr, sel, denom)
    n_label = '在籍人数' if data.denom == st.DENOM_ENROLLED else '参考人数'

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
                     {'key': 'count', 'label': n_label, 'type': 'int'},
                     {'key': 'avg', 'label': '总分均值', 'type': 'num'},
                     {'key': 'std', 'label': '标准差', 'type': 'num'},
                     {'key': 'max', 'label': '最高分', 'type': 'num'},
                     {'key': 'min', 'label': '最低分', 'type': 'num'}] + col_layer),
        'rows': [dict(row, **{'l_%d' % i: _cell_layer(row['layers'].get(name))
                              for i, name in enumerate(layer_names)})
                 for row in summary],
    }
    # A1-T2 科目总表（2026-10-15 用户口径：原「及格率/优秀率」按满分 60%/90% 固定线算，
    # 与本场划线无关；改为**按本场划线**——及格率位=最低层（四层模板=及格层）、
    # 优秀率位=最高层（=优秀层），逐生按自身方向/组合取单科线，值为单科上线率；
    # 未划线不出这两列，某科技未划线时该格显示 —）
    subj_rows = st.subject_table(data, dr)
    subj_rate_names = (None, None)
    subj_online = {}
    for _r in subj_rows:
        _sub = _r['subject']
        _pname, _gname = _rate_names_for(data, _sub)
        if not (_pname or _gname):
            _r['pass_rate'] = _r['good_rate'] = None
            continue
        if subj_rate_names == (None, None):
            subj_rate_names = (_pname, _gname)
        _online = subj_online.get(_sub)
        if _online is None:
            _online = subj_online[_sub] = _layer_online_counts(data, _sub, direction=dr)
        _r['pass_rate'] = _rate_of(_online, _pname, _r['count'])
        _r['good_rate'] = _rate_of(_online, _gname, _r['count'])
    tables['subject_total'] = {
        'title': '年级科目总表',
        'columns': [
            {'key': 'subject', 'label': '科目', 'type': 'text'},
            {'key': 'count', 'label': n_label, 'type': 'int'},
            {'key': 'full', 'label': '满分', 'type': 'int'},
            {'key': 'avg', 'label': '年级平均分', 'type': 'num'},
            {'key': 'max', 'label': '最高分', 'type': 'num'},
            {'key': 'min', 'label': '最低分', 'type': 'num'},
            {'key': 'std', 'label': '标准差', 'type': 'num'},
        ] + _rate_columns(*subj_rate_names),
        'rows': subj_rows,
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
    # A1-T5 历次考试总览 + G4 折线：共用一次聚合查询（避免对全部历史考试重复扫描）。
    # v1.17.x 性能：原先 trend_overview 只查总分、_trend_lines 又查 10 个科目，等于把
    # 全年级 25 场约 22 万行扫描两遍；此处一次查回 (总分+9 科) 均值，两处复用。
    _full_means = st.exam_score_means(exam.grade, [TOTAL_SUBJECT] + list(SUBJECTS),
                                       direction=dr)
    rows = []
    for _eid, _info in _full_means.items():
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
    # 年级均分参考线用**人数加权**（2026-10-17 修）：原为各班均分的简单平均，
    # 各班人数不等时与"全体参考学生均分"不同（手册「参考数口径」要求基于参考学生）。
    _w_num = sum((r['avg'] or 0) * (r['count'] or 0)
                 for r in summary if r['avg'] is not None)
    _w_den = sum((r['count'] or 0) for r in summary if r['avg'] is not None)
    charts['avg_bar'] = {
        'type': 'bar', 'title': '各班总分均值对比',
        'xAxis': shown_classes,
        'series': [{'name': '总分均值', 'data': [r['avg'] for r in summary]}],
        'markLine': (_w_num / _w_den) if _w_den else 0,
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
    # G4 历次多折线（复用上面已算好的 _full_means，不再重复聚合查询）
    trends = _trend_lines(exam.grade, means=_full_means, direction=dr)
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
                     'denom': data.denom,
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


def _trend_lines(grade, means=None, direction=None):
    """历次考试折线数据；direction 过滤时只统计该方向（聚合查询，避免逐场构造 ExamData）。

    means: 预计算的 exam_score_means(grade, [总分]+SUBJECTS, direction) 结果；
    传入则直接复用，避免 grade_tab 已算过一遍又重复扫描历史考试。"""
    x, series_map = [], {}
    _means = means if means is not None else st.exam_score_means(
        grade, [TOTAL_SUBJECT] + list(SUBJECTS), direction=direction)
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

def class_tab(exam_id, class_name, direction='', selection='', denom='ref'):
    data = st.cached_exam_data(exam_id)
    exam = data.exam
    if not data.total_rows or class_name not in data.classes:
        return _empty(exam)
    # 2026-10-16 组合维度：班里也按 (方向×组合) 收窄（行政班内混组合时有用）
    data = data.population(direction or None, selection or None, denom)
    n_label = '在籍人数' if data.denom == st.DENOM_ENROLLED else '参考人数'
    tables, charts = {}, {}
    totals = data.totals_of(class_name=class_name)
    # 2026-10-17 口径：人数/均分随 denom（enrolled 时缺考按 0 分计入均分）
    cls_n = st.base_count(data, class_name=class_name)
    grd_n = st.base_count(data)
    grade_avg = st.avg_by_denom(st.ref_scores(data), grd_n)
    cls_avg = st.avg_by_denom(st.ref_scores(data, class_name=class_name), cls_n)
    fm = data.full_marks()

    # A2-T1 班级概况
    segs, rows = st.segment_table(data, class_name=class_name)
    _ov_row = {'k': '各分数段人数', **{'s%d' % i: r['counts'].get(class_name, 0)
                                   for i, r in enumerate(rows)}}
    tables['class_overview'] = {
        'title': '班级各分数段人数',
        'columns': [{'key': 'k', 'label': '项目', 'type': 'text'}] +
                   [{'key': 's%d' % i, 'label': s[0], 'type': 'int'} for i, s in enumerate(segs)],
        'rows': [_ov_row],
        # 单行 × 多分数段：自动推断会画成 1 根柱，显式转置为「分数段 → 人数」
        'chartHint': {'type': 'bar', 'xAxis': [s[0] for s in segs],
                      'series': [{'name': '人数', 'data': [_ov_row.get('s%d' % i)
                                                          for i in range(len(segs))]}]},
    }
    tables['class_meta'] = {
        'title': '班级基本指标',
        'columns': [{'key': 'k', 'label': '指标', 'type': 'text'},
                    {'key': 'v', 'label': '数值', 'type': 'text'}],
        'rows': [
            {'k': n_label, 'v': cls_n},
            {'k': f'年级{n_label}', 'v': grd_n},
            {'k': '总分均值', 'v': cls_avg},
            {'k': '年级均值', 'v': grade_avg},
            {'k': '与年级均值差', 'v': (round(cls_avg - grade_avg, 1)
                                     if cls_avg is not None and grade_avg is not None else '—')},
        ],
        # 键值表无法被前端自动推断（第二列是混合量纲的文本）→ 显式给图：仅画可比的 4 个数值项
        'chartHint': {'type': 'bar',
                      'xAxis': ['总分均值', '年级均值', n_label, f'年级{n_label}'],
                      'series': [{'name': '本班', 'data': [cls_avg, grade_avg,
                                                          cls_n, grd_n]}]},
    }
    # A2-T2 班级-年级科目对标（2026-10-15 用户口径：原「及格率」按满分 60% 固定线算 →
    # 改为按本场划线的「及格率位」层（最低层；四层模板=及格层）单科上线率；
    # 逐生按自身方向/组合取线；未划线不出现这两列）
    subs = data.class_subjects(class_name)
    t2 = []
    rate_pass = None
    for sub in subs:
        # 2026-10-17 口径：单科分子只算实考（>0），分母按所选口径（enrolled 含缺考）
        cls_s = st.ref_scores(data, sub, class_name=class_name)
        grd_s = st.ref_scores(data, sub)
        if not cls_s:
            continue
        cls_sub_n = st.base_count(data, sub, class_name=class_name)
        grd_sub_n = st.base_count(data, sub)
        pname, _gname = _rate_names_for(data, sub)
        cls_pass = grd_pass = None
        if pname:
            rate_pass = rate_pass or pname
            by_cls = _layer_online_counts_by_class(data, sub)
            od = (by_cls.get(class_name) or {}).get(pname)
            cls_pass = _fmt_pct(od.get('single_n', 0), cls_sub_n) if od else None
            grd_hit = sum(((v.get(pname) or {}).get('single_n') or 0)
                          for v in by_cls.values())
            grd_pass = _fmt_pct(grd_hit, grd_sub_n) if grd_s else None
        cls_m = st.avg_by_denom(cls_s, cls_sub_n)
        grd_m = st.avg_by_denom(grd_s, grd_sub_n)
        t2.append({'subject': sub, 'cls_count': cls_sub_n, 'cls_avg': cls_m,
                   'grd_avg': grd_m,
                   'diff': round(cls_m - grd_m, 1) if cls_m is not None and grd_m is not None else None,
                   'cls_pass': cls_pass, 'grd_pass': grd_pass})
    t2_cols = [{'key': 'subject', 'label': '科目', 'type': 'text'},
               {'key': 'cls_count', 'label': f'本班{n_label}', 'type': 'int'},
               {'key': 'cls_avg', 'label': '本班平均分', 'type': 'num'},
               {'key': 'grd_avg', 'label': '年级平均分', 'type': 'num'},
               {'key': 'diff', 'label': '分差', 'type': 'num'}]
    if rate_pass:
        t2_cols += [{'key': 'cls_pass', 'label': f'本班{rate_pass}率%', 'type': 'num'},
                    {'key': 'grd_pass', 'label': f'年级{rate_pass}率%', 'type': 'num'}]
    tables['subject_compare'] = {
        'title': '班级-年级科目对标表',
        'columns': t2_cols,
        'rows': t2,
    }
    # A2-T3 学生明细（每生 × 应考科目分数 + 本次/上次总分排名；前端点行跳个人成绩查询）
    t3 = []
    for t in sorted(totals, key=lambda x: x['rank_dir'] or 99999):
        prev = data.prev_totals.get(t['no'])
        cur_score = t['score']
        prev_score = prev[0] if prev else None
        prev_rank = prev[1] if prev else None
        row = {'no': t['no'], 'name': t['name'], 'class_name': t['class_name']}
        for sub in subs:
            row[sub] = data.subj.get((t['no'], sub))
        row.update({'total': cur_score, 'rank': t['rank_dir'],
                    'prev_total': prev_score, 'prev_rank': prev_rank,
                    'score_move': round(cur_score - prev_score, 1) if prev_score is not None else None,
                    'rank_move': (t['rank_dir'] - prev_rank) if prev_rank else None})
        t3.append(row)
    tables['student_detail'] = {
        'title': '班级学生历次成绩明细表',
        'columns': [{'key': 'no', 'label': '学号', 'type': 'text'},
                    {'key': 'name', 'label': '姓名', 'type': 'text'}]
                   + [{'key': s, 'label': s, 'type': 'num'} for s in subs]
                   + [{'key': 'total', 'label': '本次总分', 'type': 'num'},
                      {'key': 'rank', 'label': '本次排名', 'type': 'int'},
                      {'key': 'prev_total', 'label': '上次总分', 'type': 'num'},
                      {'key': 'prev_rank', 'label': '上次排名', 'type': 'int'},
                      {'key': 'score_move', 'label': '分数变动', 'type': 'num'},
                      {'key': 'rank_move', 'label': '排名变动', 'type': 'int'}],
        'rows': t3,
        # 明细表行=学生：图表取排名靠前的若干人画「各科分数」，避免几十上百根柱挤在一起
        'chartHint': {'type': 'groupbar', 'xKey': 'name', 'topN': 25,
                      'seriesKeys': list(subs)},
        'link': 'student-query',
    }
    # A2-T3b 班级学生各科成绩表（每生 × 应考科目分数，排名同明细表序）
    t3b = []
    for t in sorted(totals, key=lambda x: x['rank_dir'] or 99999):
        row = {'no': t['no'], 'name': t['name']}
        for sub in subs:
            row[sub] = data.subj.get((t['no'], sub))
        row['total'] = t['score']
        t3b.append(row)
    tables['student_scores'] = {
        'title': '班级学生各科成绩表',
        'columns': [{'key': 'no', 'label': '学号', 'type': 'text'},
                    {'key': 'name', 'label': '姓名', 'type': 'text'}]
                   + [{'key': s, 'label': s, 'type': 'num'} for s in subs]
                   + [{'key': 'total', 'label': '总分', 'type': 'num'}],
        'rows': t3b,
    }
    # A2-T4 分层统计（按本班学生自身方向判定；总分线按学生组合取线）
    t4 = []
    layers_map = {}
    layer_idx = {}       # (方向, 层名) -> 层序号（band_list 按 seq 升序，0=最高层）
    for t in totals:
        sel = t.get('selection') or ''
        idx = data.band_of_score(t['direction'], t['score'], selection=sel)
        if idx is None:
            continue
        bands = data.band_list(t['direction'], selection=sel)
        if not (0 <= idx < len(bands)):
            continue
        _key = (t['direction'], bands[idx][0])
        layers_map.setdefault(_key, []).append(t)
        layer_idx[_key] = idx
    for (direction, lname), members in layers_map.items():
        row = {'layer': lname, 'direction': direction, 'count': len(members),
               'ratio': st.fmt_rate(len(members), cls_n),
               'avg': st.mean([m['score'] for m in members])}
        for sub in subs:
            vals = []
            for m in members:
                s = data.subj.get((m['no'], sub))
                if s is not None:
                    vals.append(s)
            row[sub] = st.mean(vals)
        t4.append(row)
    # ⚠️ 必须按**层序号**排，不能按层名字符串排：
    # 层名可能是"前10 / 前20 / 前50 / 前80 / 前100 / 前300"（限时练按名次分层），
    # 字符串序会排成 前10 < 前100 < 前20 < 前300 < 前50 < 前80
    # —— 2026-10-16 用户报「限时练分层这个有点子问题哈」就是这个。
    # 层序号来自 band_of_score（band_list 按 seq 升序，0=最高层/最严格层）。
    # 方向固定物理在前（中文排序会把"历史"排到前面）。
    _dir_order = {'物理': 0, '历史': 1}
    t4.sort(key=lambda r: (_dir_order.get(r['direction'], 9),
                           layer_idx.get((r['direction'], r['layer']), 99)))
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
            'meta': {'classes': data.classes, 'denom': data.denom,
                     'partial': data.partial_import()}}


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

def subject_tab(exam_id, subject, direction='', selection='', denom='ref'):
    """A3 学科分析；direction=物理/历史 时仅统计该方向，selection=选科组合时仅统计该组合"""
    data = st.cached_exam_data(exam_id)
    exam = data.exam
    if not data.total_rows:
        return _empty(exam)
    dr = direction or None   # None=全部
    sel = selection or None  # None=全部组合（2026-10-16 组合维度）
    # 人群视图：本 tab 内所有取数都按 (方向×组合×口径) 收窄
    data = data.population(dr, sel, denom)
    n_label = '在籍人数' if data.denom == st.DENOM_ENROLLED else '参考人数'
    tables, charts = {}, {}
    scores_map = {}   # class -> scores
    grd_scores = st.ref_scores(data, subject)
    grd_n = st.base_count(data, subject)
    grd_avg = st.avg_by_denom(grd_scores, grd_n)
    lines = data.subject_lines(subject)
    # 该科「方向 × 组合」单科线（未划线时为空，表格不出现分层列）。
    # 2026-10-15 二次口径：再选四科（化/生/政/地）**按组合**划（化学=物化生/物化地/物化政
    # 各一条），语数外+首选科按方向划 → 一律按**学生自身组合**取线（组合优先，回落方向线）
    sel_dirs = data.directions if not dr else [direction]
    band_keys = []          # [(方向, 组合)]：本场该科出现的组合（判层/取线用）
    for t in data.totals_of(direction=dr):
        k = (t['direction'] or '', t.get('selection') or '')
        if k not in band_keys:
            band_keys.append(k)
    band_lines = {k: data.band_list(k[0], subject, k[1]) for k in band_keys}
    layer_names = []
    for k in band_keys:
        for name, _lower in band_lines[k]:
            if name not in layer_names:
                layer_names.append(name)
    # 原「及格率/优秀率」两列 → 本场划线口径（2026-10-15 用户口径）：
    # 及格率位=最低层（四层模板=及格层）、优秀率位=最高层（=优秀层）；未划线不出这两列
    rate_pass = rate_good = None
    for k in band_keys:
        _p, _g = st.rate_layer_pair(band_lines.get(k) or [])
        if _p or _g:
            rate_pass = _p[0] if _p else None
            rate_good = _g[0] if _g else None
            break

    def _layer_counts(cls):
        """该班学生在本学科各层的人数（按学生自身「方向×组合」的单科线判定，落入层/互斥）"""
        counts = {name: 0 for name in layer_names}
        for t in data.totals_of(class_name=cls, direction=dr):
            s = data.subj.get((t['no'], subject))
            if s is None:
                continue
            k = (t['direction'] or '', t.get('selection') or '')
            bl = band_lines.get(k) or []
            idx = data.band_of_score(k[0], s, subject, k[1])
            if idx is None or idx < 0 or idx >= len(bl):
                continue
            counts[bl[idx][0]] = counts.get(bl[idx][0], 0) + 1
        return counts

    # v1.13.2 双上线：预算一次按班级分组，班级循环里直接查（O(n) 而非 O(班级数 × n)）
    online_by_cls = (_layer_online_counts_by_class(data, subject, direction=dr)
                     if layer_names else {})
    t1 = []
    for cls in data.classes:
        cls_s = st.ref_scores(data, subject, class_name=cls, direction=dr)
        if not cls_s:
            continue
        scores_map[cls] = cls_s
        # 2026-10-17 口径：人数/率/均分全按所选分母（enrolled 含缺考，缺考按 0 分计入均分）
        cls_n = st.base_count(data, subject, class_name=cls, direction=dr)
        online = online_by_cls.get(cls, {})
        cls_avg = st.avg_by_denom(cls_s, cls_n)
        row = {
            'subject': subject, 'class_name': cls, 'count': cls_n,
            'avg': cls_avg,
            'pass_rate': _rate_of(online, rate_pass, cls_n),
            'good_rate': _rate_of(online, rate_good, cls_n),
            'max': max(cls_s),
            'low_count': sum(1 for s in cls_s if s < lines['low']),
            'diff': round(cls_avg - grd_avg, 1) if grd_avg is not None else None,
        }
        if layer_names:
            lc = _layer_counts(cls)
            for i, name in enumerate(layer_names):
                row['l_%d' % i] = lc.get(name, 0)
                od = online.get(name, {})
                sn = od.get('single_n', 0)
                dn = od.get('dual_n', 0)
                row['lo_%d_n' % i] = sn
                row['lo_%d_dn' % i] = dn
                row['lo_%d_dr' % i] = _fmt_pct(dn, cls_n)
        t1.append(row)
    t1.sort(key=lambda r: -(r['avg'] or 0))
    cmp_cols = ([{'key': 'subject', 'label': '科目', 'type': 'text'},
                 {'key': 'class_name', 'label': '班级', 'type': 'text'},
                 {'key': 'count', 'label': n_label, 'type': 'int'},
                 {'key': 'avg', 'label': '平均分', 'type': 'num'}]
                + _rate_columns(rate_pass, rate_good)
                + [{'key': 'max', 'label': '最高分', 'type': 'num'},
                   {'key': 'low_count', 'label': '低分人数', 'type': 'int'},
                   {'key': 'diff', 'label': '与年级均分差', 'type': 'num'}])
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
    # A3-T2 学科历次（聚合查询，避免逐场构造 ExamData；率按**各场自己**的划线算，
    # 未划线的场次显示 —；列名用本场层名）
    t2 = []
    for _r in st.exam_trend_series(exam.grade, subject, direction=dr, need_rates=True,
                                   pass_name=rate_pass, good_name=rate_good):
        t2.append({'exam': _r['name'], 'date': _r['date'], 'count': _r['count'],
                   'avg': _r['avg'], 'pass_rate': _r['pass_rate'], 'good_rate': _r['good_rate']})
    _his_cols = [{'key': 'exam', 'label': '考试', 'type': 'text'},
                 {'key': 'date', 'label': '日期', 'type': 'text'},
                 {'key': 'count', 'label': '参考人数', 'type': 'int'},
                 {'key': 'avg', 'label': '平均分', 'type': 'num'}]
    if any(r['pass_rate'] is not None for r in t2):
        _his_cols.append({'key': 'pass_rate',
                          'label': f"{rate_pass or '最低层'}率%", 'type': 'num'})
    if (not (rate_pass and rate_good == rate_pass)
            and any(r['good_rate'] is not None for r in t2)):
        _his_cols.append({'key': 'good_rate',
                          'label': f"{rate_good or '最高层'}率%", 'type': 'num'})
    tables['history'] = {
        'title': f'{subject}学科历次考试统计表',
        'columns': _his_cols,
        'rows': t2,
    }
    # A3-T3 分层内得分（指定方向时仅该方向分层；总分线按学生组合取线）
    t3 = []
    for direction in sel_dirs:
        for lname, _lower in data.band_list(direction):
            members = []
            for t in data.totals_of(direction=direction):
                sel = t.get('selection') or ''
                idx = data.band_of_score(t['direction'], t['score'], selection=sel)
                if idx is None:
                    continue
                bands = data.band_list(t['direction'], selection=sel)
                if 0 <= idx < len(bands) and bands[idx][0] == lname:
                    members.append(t)
            # 原「该科及格率%」按满分固定线 → 按「及格率位」层线（最低层/及格层）；
            # 再选四科按**学生自身组合**取线（化学=物化生/物化地/物化政各一条）
            vals, hit = [], 0
            for m in members:
                sv = data.subj.get((m['no'], subject))
                if sv is None:
                    continue
                vals.append(sv)
                _p_pair, _g_pair = st.rate_layer_pair(
                    band_lines.get((m['direction'] or '', m.get('selection') or '')) or [])
                if _p_pair and sv >= _p_pair[1]:
                    hit += 1
            if not members or not vals:
                continue
            t3.append({'layer': lname, 'direction': direction,
                       'count': len(members),
                       'avg': st.mean(vals),
                       'pass_rate': st.fmt_rate(hit, len(vals)) if rate_pass else None})
    _ls_cols = [{'key': 'direction', 'label': '方向', 'type': 'text'},
                {'key': 'layer', 'label': '层', 'type': 'text'},
                {'key': 'count', 'label': '该层人数', 'type': 'int'},
                {'key': 'avg', 'label': '该科均分', 'type': 'num'}]
    if rate_pass:
        _ls_cols.append({'key': 'pass_rate', 'label': f'该科{rate_pass}率%', 'type': 'num'})
    tables['layer_score'] = {
        'title': f'{subject}总分分层内得分统计表',
        'columns': _ls_cols,
        'rows': t3,
    }
    # A3-T4 该科单科线分层统计（按该科自身分数线划分，而非总分分层）
    # 2026-10-15：单科线可能按组合各一条（页面手工勾列时）→ 线内容相同的组合合并成一行；
    # 该方向全部组合共用一条线（单科按方向划的常态）时，标注「全方向」而不是罗列组合名
    dir_sels = {}       # 方向 -> 该方向出现过的组合集合
    for k in band_keys:
        if k[1]:
            dir_sels.setdefault(k[0], set()).add(k[1])
    t4_groups = []      # [{'d': 方向, 'sels': [组合…], 'bl': 线}]
    for k in band_keys:
        bl = band_lines.get(k) or []
        if not bl:
            continue
        for g in t4_groups:
            if g['d'] == k[0] and g['bl'] == bl:
                g['sels'].append(k[1])
                break
        else:
            t4_groups.append({'d': k[0], 'sels': [k[1]], 'bl': bl})
    t4 = []
    for g in t4_groups:
        d, bl, sels = g['d'], g['bl'], g['sels']
        members = {name: [] for name, _lower in bl}
        for t in data.totals_of(direction=d):
            sel = t.get('selection') or ''
            if sel not in sels:
                continue
            s = data.subj.get((t['no'], subject))
            if s is None:
                continue
            idx = data.band_of_score(d, s, subject, sel)
            if idx is not None and 0 <= idx < len(bl):
                members[bl[idx][0]].append(s)
        n_total = sum(len(v) for v in members.values())
        # 该方向全部组合共用同一条线 → 标「全方向」（单科按方向划时就是这种）
        if sels and set(sels) == dir_sels.get(d):
            label = '全方向'
        else:
            label = '、'.join(x or '全方向' for x in sels)
        for name, lower in bl:
            vals = members.get(name) or []
            if not vals:
                continue
            t4.append({'direction': d, 'selection': label, 'layer': name, 'lower': lower,
                       'count': len(vals),
                       'ratio': st.fmt_rate(len(vals), n_total) if n_total else None,
                       'avg': st.mean(vals)})
    tables['subject_layer'] = {
        'title': f'{subject}单科划线分层统计表',
        'columns': [{'key': 'direction', 'label': '方向', 'type': 'text'},
                    {'key': 'selection', 'label': '适用组合', 'type': 'text'},
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
        # 该科各层分数线（参考线，按去重后的方向/组合标注）；未划线时为空数组
        'bandLines': _uniq_band_lines(band_lines),
    }
    _rate_label = f"{rate_pass or '及格'}率"
    charts['trend'] = {
        'type': 'dual', 'title': f'{subject}历次全年级平均分/{_rate_label}',
        'xAxis': [r['exam'] for r in t2],
        'series': [
            {'name': '平均分', 'yAxis': 0, 'data': [r['avg'] for r in t2]},
            {'name': _rate_label + '%', 'yAxis': 1, 'data': [r['pass_rate'] for r in t2]},
        ],
    }
    return {'exam': {'id': exam.id, 'name': exam.name, 'grade': exam.grade,
                     'date': exam.exam_date.strftime('%Y-%m-%d'), 'status': exam.status},
            'subject': subject, 'tables': tables, 'charts': charts,
            'meta': {'classes': data.classes, 'denom': data.denom}}


def _heatmap(data, direction=None):
    """科目 × 班级 平均分热力矩阵（direction 过滤：只显示该方向班级与该方向实际开考科目）"""
    shown_classes = [c for c in data.classes
                     if data.totals_of(class_name=c, direction=direction)]
    values = []
    y_labels = []
    for sub in SUBJECTS:
        col = []
        for cls in shown_classes:
            s = st.ref_scores(data, sub, class_name=cls, direction=direction)
            n = st.base_count(data, sub, class_name=cls, direction=direction)
            col.append(st.avg_by_denom(s, n) if s else None)
        if not any(col):
            continue  # 该方向未开考科目不显示（避免全空行）
        y_labels.append(sub)
        values.append(col)
    return {'type': 'heatmap', 'title': '各班级各科目平均分热力矩阵',
            'xAxis': shown_classes, 'yAxis': y_labels, 'data': values}


# ==================== A4 任课教师分析 ====================

def teacher_tab(exam_id, subject=None, links=None, grade=None, denom='ref'):
    """links: 允许展示的 (grade,class_name,subject,user_id) 列表（None=全部 active）

    denom（2026-10-17）：ref=参考数 / enrolled=班级实际人数，影响人数/率/均分口径。
    """
    from app.models.grades import TeacherSubjectLink
    from app.models import User
    data = st.cached_exam_data(exam_id)
    exam = data.exam
    if not data.total_rows:
        return _empty(exam)
    data = data.population(None, None, denom)
    n_label = '在籍人数' if data.denom == st.DENOM_ENROLLED else '参考人数'
    if links is None:
        # v1.19.49.36 优先读本场考试的任课快照（考试当时）；无则惰性生成，
        # 生成失败（旧库无表）再从当前映射回落
        from app.modules.grades.services import teacher_snapshot_service as _tss
        _snap = _tss.links_of(exam_id, grade=grade)
        if _snap:
            links = _snap
        else:
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
    # 原「及格率/优秀率」两列 → 本场划线口径（2026-10-15 用户口径）：
    # 及格率位=最低层（四层模板=及格层）、优秀率位=最高层（=优秀层）；未划线不出这两列
    rate_pass, rate_good = _rate_names_for(data, _first_subj)
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
        cls_s = st.ref_scores(data, link.subject, class_name=link.class_name)
        if not cls_s:
            continue
        grd_s = st.ref_scores(data, link.subject)
        # 2026-10-17 口径：人数/率/均分随 denom
        cls_n = st.base_count(data, link.subject, class_name=link.class_name)
        grd_n = st.base_count(data, link.subject)
        grd_avg = st.avg_by_denom(grd_s, grd_n)
        cls_avg = st.avg_by_denom(cls_s, cls_n)
        online = online_cache.get((link.class_name, link.subject), {})
        row = {
            'teacher': users.get(link.user_id, f'#{link.user_id}'),
            'class_name': link.class_name, 'subject': link.subject,
            'grade': link.grade,
            'count': cls_n,
            'avg': cls_avg,
            'grd_avg': grd_avg,
            'diff': round(cls_avg - grd_avg, 1) if grd_avg is not None else None,
            'pass_rate': _rate_of(online, rate_pass, cls_n),
            'good_rate': _rate_of(online, rate_good, cls_n),
        }
        if subject_layer_names:
            for i, name in enumerate(subject_layer_names):
                od = online.get(name, {})
                sn = od.get('single_n', 0)
                dn = od.get('dual_n', 0)
                row['t_lo_%d_n' % i] = sn
                row['t_lo_%d_dn' % i] = dn
                row['t_lo_%d_dr' % i] = _fmt_pct(dn, cls_n)
        t1.append(row)
    # 列定义
    teacher_cols = [
        {'key': 'teacher', 'label': '教师姓名', 'type': 'text'},
        {'key': 'grade', 'label': '年级', 'type': 'text'},
        {'key': 'class_name', 'label': '班级', 'type': 'text'},
        {'key': 'subject', 'label': '科目', 'type': 'text'},
        {'key': 'count', 'label': n_label, 'type': 'int'},
        {'key': 'avg', 'label': '科目平均分', 'type': 'num'},
        {'key': 'grd_avg', 'label': '年级该科平均分', 'type': 'num'},
        {'key': 'diff', 'label': '分差', 'type': 'num'},
    ] + _rate_columns(rate_pass, rate_good)
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
    # 教师历次跟踪（按第一个有数据的组合或传入 combos[0]；率按各场自己的划线算，
    # 未划线的场次显示 —，列名用本场层名）
    combo = combos[0] if combos else None
    t2 = []
    _track_pass = _track_good = None
    if combo:
        _track_pass, _track_good = _rate_names_for(data, combo['subject'])
        _cls_track = st.exam_trend_series(exam.grade, combo['subject'],
                                          class_name=combo['class_name'], need_rates=True,
                                          pass_name=_track_pass, good_name=_track_good)
        _grd_track = {_r['id']: _r for _r in
                      st.exam_trend_series(exam.grade, combo['subject'])}
        for _r in _cls_track:
            t2.append({'exam': _r['name'], 'date': _r['date'], 'count': _r['count'],
                       'avg': _r['avg'],
                       'grd_avg': _grd_track.get(_r['id'], {}).get('avg'),
                       'pass_rate': _r['pass_rate']})
    _track_cols = [{'key': 'exam', 'label': '考试', 'type': 'text'},
                   {'key': 'date', 'label': '日期', 'type': 'text'},
                   {'key': 'count', 'label': '参考人数', 'type': 'int'},
                   {'key': 'avg', 'label': '该班该科均分', 'type': 'num'},
                   {'key': 'grd_avg', 'label': '年级该科均分', 'type': 'num'}]
    if any(r['pass_rate'] is not None for r in t2):
        _track_cols.append({'key': 'pass_rate',
                            'label': f"{_track_pass or '最低层'}率%", 'type': 'num'})
    tables['teacher_track'] = {
        'title': '教师历次考试跟踪表',
        'columns': _track_cols,
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
