# StuLink v1.19.49.44 2026-10-10
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


def _short_layer(name):
    """层名精简：去掉尾部「线」（本科线→本科、特控线→特控），其余原样。"""
    if not name:
        return ''
    return name[:-1] if name.endswith('线') else name


def _short_exam_name(name, grade):
    """考试名精简：去年级前缀与末尾「（学期）」，如 2025级20260318
    限时练（高一下）→ 20260318限时练（表格/横轴都更短）。"""
    s = name or ''
    if grade and s.startswith(grade):
        s = s[len(grade):]
    if s.endswith('）') and '（' in s:
        s = s[:s.rfind('（')]
    return s or (name or '')


def _rate_columns(pass_name, good_name):
    """原「及格率%/优秀率%」两列的列定义：列名=精简层名+率%（如 特控率%/本科率%）；
    两层同名（单层模板）时只出一列；未划线返回空列表（不出这两列）。"""
    cols = []
    if pass_name:
        cols.append({'key': 'pass_rate',
                     'label': f'{_short_layer(pass_name)}率%', 'type': 'num'})
    if good_name and good_name != pass_name:
        cols.append({'key': 'good_rate',
                     'label': f'{_short_layer(good_name)}率%', 'type': 'num'})
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

    # A1-T1 年级各班汇总表（2026-10-19 用户口径：把「各班分层上线统计」并进这张表，
    # 只出一张 —— 班级/人数/总分均值/标准差/最高/最低 + 各层上线人数与上线率）
    summary = st.class_summary_table(data, dr)
    shown_classes = [r['class_name'] for r in summary]
    # 分层上线（累计口径：达该层下界及以上）先算好：列名、各班数据、年级总计都从它出
    pass_stats = pv.layer_pass_stats(data, TOTAL_SUBJECT)
    layer_names = list((pass_stats or {}).get('layers') or [])
    if not layer_names:            # 未划线：保留原「每层人数」列，表结构不变
        for row in summary:
            for name in row['layers']:
                if name not in layer_names:
                    layer_names.append(name)
    pass_by_cls = {r['class_name']: r
                   for r in (pass_stats or {}).get('rows', [])}

    cols = [{'key': 'class_name', 'label': '班级', 'type': 'text'},
            {'key': 'count', 'label': n_label, 'type': 'int'},
            {'key': 'avg', 'label': '总分均值', 'type': 'num'},
            {'key': 'std', 'label': '标准差', 'type': 'num'},
            {'key': 'max', 'label': '最高分', 'type': 'num'},
            {'key': 'min', 'label': '最低分', 'type': 'num'}]
    if pass_stats:
        for _n in layer_names:
            cols.append({'key': 'p_' + _n, 'label': f'{_n}上线人数', 'type': 'int'})
            cols.append({'key': 'r_' + _n, 'label': f'{_n}上线率%', 'type': 'num'})
    else:
        cols += [{'key': f'l_{i}', 'label': f'{name}数', 'type': 'text'}
                 for i, name in enumerate(layer_names)]

    rows_out = []
    for row in summary:
        out = dict(row)
        if pass_stats:
            pr = pass_by_cls.get(row['class_name']) or {}
            for _n in layer_names:
                out['p_' + _n] = (pr.get('pass') or {}).get(_n, 0)
                out['r_' + _n] = (pr.get('rate') or {}).get(_n)
        else:
            for i, name in enumerate(layer_names):
                out['l_%d' % i] = _cell_layer(row['layers'].get(name))
        rows_out.append(out)
    if pass_stats:                 # 年级总计行（原分层表的合计行，统计列一并补齐）
        _tot = pass_stats.get('total') or {}
        _scores = st.ref_scores(data, direction=dr)
        _n_all = st.base_count(data, direction=dr)
        grand = {'class_name': '年级总计', 'is_total': True,
                 'count': _tot.get('count') or _n_all,
                 'avg': st.avg_by_denom(_scores, _n_all),
                 'std': round(st._stddev(_scores), 1) if _scores else None,
                 'max': max(_scores) if _scores else None,
                 'min': min(_scores) if _scores else None}
        for _n in layer_names:
            grand['p_' + _n] = (_tot.get('pass') or {}).get(_n, 0)
            grand['r_' + _n] = (_tot.get('rate') or {}).get(_n)
        rows_out.append(grand)

    tables['class_summary'] = {
        'title': '年级各班汇总表（含分层上线）',
        'columns': cols, 'rows': rows_out,
    }
    # 2026-10-19 用户口径：图表只画「各层上线人数（柱）+ 上线率（折线）」，
    # 参考人数/均分/标准差/最高/最低不进图；人数走左轴、率% 走右轴，同一张图。
    _chart_rows = [r for r in rows_out if not r.get('is_total')]
    if pass_stats and _chart_rows:
        _xs = [r['class_name'] for r in _chart_rows]
        _series = []
        for _n in layer_names:
            _series.append({'name': f'{_n}上线人数', 'type': 'bar', 'axis': 0,
                            'data': [r.get('p_' + _n) for r in _chart_rows]})
            _series.append({'name': f'{_n}上线率%', 'type': 'line', 'axis': 1,
                            'data': [r.get('r_' + _n) for r in _chart_rows]})
        tables['class_summary']['chartHint'] = {
            'type': 'combo', 'xAxis': _xs, 'series': _series,
            'yAxisNames': ['上线人数', '上线率%'],
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
    # 2026-10-19：原 A1-T6「各班分层上线统计」已并入上面的 class_summary 单表，
    # 不再单独出表（pass_stats 在上方已算好，G3 上线率图继续用它）。
    # A1-T3 分数段 / A1-T4 名次段
    _segment_tables(data, tables, direction=dr, classes=shown_classes)
    # 2026-10-19 用户口径：「历次考试总览下面的东西全部删了」——
    # 历次考试总览表 + 其后的全部图表（各班均分柱 / 箱线 / 分层上线率 /
    # 分数段堆叠 / 历次考试折线）一并下线；年级分析页只保留上面四张表。
    # charts 仍返回空字典（前端按 dictionary 渲染，没有 key 就不出图表区）。
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
    """只出 A1-T4 名次段分布表（2026-10-19 用户口径：分数段分布表直接删除）。"""
    cls = classes if classes is not None else data.classes
    total_n = len(data.totals_of(direction=direction))

    segs2, rows2 = st.segment_table(data, direction=direction, by_rank=True)
    segs2, rows2 = _compact_segments(segs2, rows2)
    rows_out = _segment_rows(rows2, cls, total_n)
    tables['rank_segment'] = {
        'title': '年级名次段分布表（方向内）',
        'columns': [{'key': 'label', 'label': '名次段', 'type': 'text'}]
                   + [{'key': 'c_' + c, 'label': c, 'type': 'int'} for c in cls]
                   + [{'key': 'total', 'label': '年级合计', 'type': 'int'},
                      {'key': 'ratio', 'label': '合计占比%', 'type': 'num'}],
        'rows': rows_out,
        # 2026-10-19 用户口径：图表横轴＝各班、堆叠＝名次段。
        # 不给 hint 时前端自动推断成「横轴名次段 × 堆叠班级」，看不出各班构成。
        'chartHint': _rank_seg_chart(rows_out, cls),
    }
    return (segs2, rows2) if ret else None


def _rank_seg_chart(rows, classes):
    """名次段分布图规格：横轴＝班级，每个名次段一条堆叠系列。

    参数：
        rows: _segment_rows 的输出（每行一个名次段，含 c_<班> 人数）
        classes: 班级名列表（表的列顺序）
    返回：
        前端 autoChartSpec 用的完整 chartHint（type=stack）
    """
    return {
        'type': 'stack',
        'xAxis': list(classes),
        'series': [{'name': r['label'],
                    'data': [r.get('c_' + c, 0) for c in classes]}
                   for r in rows],
    }


def _segment_rows(rows, classes, total):
    out = []
    for row in rows:
        out.append({'label': row['label'],
                    **{'c_' + c: row['counts'].get(c, 0) for c in classes},
                    'total': row['total'],
                    'ratio': st.fmt_rate(row['total'], total)})
    return out


# ==================== A2 班级分析 ====================

def _drop_empty_score_segs(segs, rows, class_name):
    """分数段裁剪：班内 0 人的段不展示（2026-10-19 用户口径）。

    得分率偏低（< 总分 30%）的段通常本来就没人，按本规则整段隐藏，
    横轴/表头不再被空档占满；但只要该段**真有该班学生**就保留，
    不会因为分数低被砍掉（不能把低分学生从分布里藏起来）。
    """
    keep = [(s, r) for s, r in zip(segs, rows)
            if r['counts'].get(class_name, 0) > 0]
    if not keep:
        return [], []
    return [s for s, _ in keep], [r for _, r in keep]


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
    segs, rows = _drop_empty_score_segs(segs, rows, class_name)
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
    # A2-T3b 班级学生各科成绩表（每生 × 应考科目分数，按总分排名序）
    # 2026-10-19 用户口径：原「班级学生历次成绩明细表」整表下线（含进退步列与
    # 点行跳个人成绩），本表只保留表格、不画图（noChart）。
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
        'noChart': True,
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
            for i, name in enumerate(layer_names):
                od = online.get(name, {})
                sn = od.get('single_n', 0)
                dn = od.get('dual_n', 0)
                row['lo_%d_n' % i] = sn
                row['lo_%d_dn' % i] = dn
        t1.append(row)
    t1.sort(key=lambda r: -(r['avg'] or 0))
    # 2026-10-19 修复：原第一列「科目」恒为当前学科（整列重复），已删除；
    # 班级直接作为首列，图表横轴也随之取班级（原横轴 12 个标签全是「语文」）。
    cmp_cols = ([{'key': 'class_name', 'label': '班级', 'type': 'text'},
                 {'key': 'count', 'label': n_label.replace('参考', ''),
                  'type': 'int'},
                 {'key': 'avg', 'label': '均分', 'type': 'num'}]
                + _rate_columns(rate_pass, rate_good)
                + [{'key': 'max', 'label': '最高', 'type': 'num'},
                   {'key': 'low_count', 'label': '低分', 'type': 'int'},
                   {'key': 'diff', 'label': '均分差', 'type': 'num'}])
    if layer_names:
        # 2026-10-19 用户口径：删「X线人数」（落层人数，互斥）——
        # 最高层的落层人数与「X线单上」完全相等，低层又易被误读为上线人数。
        # 只留累计口径的「上线 / 双上」两列；双上率=双上÷人数，不再单列。
        for i, name in enumerate(layer_names):
            _sn = _short_layer(name)
            cmp_cols += [
                {'key': 'lo_%d_n' % i, 'label': f'{_sn}上线', 'type': 'int'},
                {'key': 'lo_%d_dn' % i, 'label': f'{_sn}双上', 'type': 'int'},
            ]
    _chart_keys = ['avg'] + [c['key'] for c in cmp_cols
                             if c['key'] in ('pass_rate', 'good_rate')]
    tables['class_compare'] = {
        'title': f'{subject}学科各班对比表',
        'columns': cmp_cols,
        'rows': t1,
        'chartHint': {'type': 'groupbar', 'xKey': 'class_name',
                      'seriesKeys': _chart_keys},
    }
    # A3-T2 学科历次（聚合查询，避免逐场构造 ExamData；率按**各场自己**的划线算，
    # 未划线的场次显示 —；列名用本场层名）
    t2 = []
    for _r in st.exam_trend_series(exam.grade, subject, direction=dr, need_rates=True,
                                   pass_name=rate_pass, good_name=rate_good):
        t2.append({'exam': _short_exam_name(_r['name'], exam.grade),
                   'date': _r['date'], 'count': _r['count'],
                   'avg': _r['avg'], 'pass_rate': _r['pass_rate'],
                   'good_rate': _r['good_rate']})
    _his_cols = [{'key': 'exam', 'label': '考试', 'type': 'text'},
                 {'key': 'date', 'label': '日期', 'type': 'text'},
                 {'key': 'count', 'label': '人数', 'type': 'int'},
                 {'key': 'avg', 'label': '均分', 'type': 'num'}]
    if any(r['pass_rate'] is not None for r in t2):
        _his_cols.append({'key': 'pass_rate',
                          'label': f"{_short_layer(rate_pass) or '最低层'}率%",
                          'type': 'num'})
    if (not (rate_pass and rate_good == rate_pass)
            and any(r['good_rate'] is not None for r in t2)):
        _his_cols.append({'key': 'good_rate',
                          'label': f"{_short_layer(rate_good) or '最高层'}率%",
                          'type': 'num'})
    _his_keys = ['avg'] + [c['key'] for c in _his_cols
                           if c['key'] in ('pass_rate', 'good_rate')]
    tables['history'] = {
        'title': f'{subject}学科历次考试统计表',
        'columns': _his_cols,
        'rows': t2,
        # 2026-10-19：历次表也只画 均分 + 上线率（参考人数单独成列，不入图）
        'chartHint': {'type': 'groupbar', 'xKey': 'exam',
                      'seriesKeys': _his_keys},
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
        # v1.19.49.44 优先读本场考试的任课快照（考试当时）；无则惰性生成，
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
        t1.append(row)
    # 列定义（2026-10-19 用户口径：表内只做「同学科教师」对比，
    # 年级恒为本场年级、科目由上方筛选固定 → 两列都不再重复占位）
    teacher_cols = [
        {'key': 'teacher', 'label': '教师', 'type': 'text'},
        {'key': 'class_name', 'label': '班级', 'type': 'text'},
        {'key': 'count', 'label': n_label.replace('参考', ''), 'type': 'int'},
        {'key': 'avg', 'label': '班均分', 'type': 'num'},
        # 年级均分是同科所有班共用的常数，逐行重复没有信息量；
        # 保留「分差」（= 班均分 − 年级均分）即可对比。
        {'key': 'diff', 'label': '分差', 'type': 'num'},
    ] + _rate_columns(rate_pass, rate_good)
    if subject_layer_names:
        for i, name in enumerate(subject_layer_names):
            _sn = _short_layer(name)
            teacher_cols += [
                {'key': 't_lo_%d_n' % i, 'label': f'{_sn}上线', 'type': 'int'},
                {'key': 't_lo_%d_dn' % i, 'label': f'{_sn}双上', 'type': 'int'},
            ]
    tables['teacher_data'] = {
        'title': '教师教学数据表',
        'columns': teacher_cols,
        'rows': t1,
        # 表卡不再自动画「12 个指标混轴」图；同科教师对比由下方
        # 专用图 teacher_bar（教师·班级 × 班均分）承担。
        'noChart': True,
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
            t2.append({'exam': _short_exam_name(_r['name'], exam.grade),
                       'date': _r['date'], 'count': _r['count'],
                       'avg': _r['avg'],
                       'grd_avg': _grd_track.get(_r['id'], {}).get('avg'),
                       'pass_rate': _r['pass_rate']})
    _track_cols = [{'key': 'exam', 'label': '考试', 'type': 'text'},
                   {'key': 'date', 'label': '日期', 'type': 'text'},
                   {'key': 'count', 'label': '人数', 'type': 'int'},
                   {'key': 'avg', 'label': '班均分', 'type': 'num'},
                   {'key': 'grd_avg', 'label': '年级均分', 'type': 'num'}]
    if any(r['pass_rate'] is not None for r in t2):
        _track_cols.append({'key': 'pass_rate',
                            'label': f"{_short_layer(_track_pass) or '最低层'}率%",
                            'type': 'num'})
    _track_keys = ['avg', 'grd_avg'] + [c['key'] for c in _track_cols
                                        if c['key'] == 'pass_rate']
    tables['teacher_track'] = {
        'title': '教师历次考试跟踪表',
        'columns': _track_cols,
        'rows': t2,
        'chartHint': {'type': 'groupbar', 'xKey': 'exam',
                      'seriesKeys': _track_keys},
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
