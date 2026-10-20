# StuLink v1.19.49.48 2026-10-20
# 限时练单科分析：班均分 + 名次区间人数（不做多科汇总）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""限时练单科分析（2026-10-20 用户口径）。

只做两件事，不加载任何其它分析子页面：
1. **各班该科平均分**（缺考不落库，天然不计入分母）；
2. **各班落在指定名次区间内的人数**（区间来自限时练专属分层模板，
   按本场该科的**全年级名次** ``rank_grade`` 落区间）。
"""
import time

from app.extensions import db
from app.models.timed import TimedScore


# ==================== 进程内缓存（2026-10-20 性能优化） ====================
# 为什么：一场 500 人 × 9 科 = 4500 行，翻页 / 换搜索词 / 看分析页都会重复
# 算一遍名次与 T 分（纯 Python 遍历 + 每科 500 次均值方差）。
# 这些结果只随"成绩变动"变化，所以按场次缓存 2 分钟；导入 / 删除时主动失效。
# 单进程 dict 足够：数据量小（几 MB），多进程部署最多多算几次，不影响正确性。
_CACHE = {}
_CACHE_TTL = 120


def _cached(key, builder):
    """命中返回缓存值，否则用 builder() 生成并缓存"""
    now = time.time()
    hit = _CACHE.get(key)
    if hit and hit[0] > now:
        return hit[1]
    val = builder()
    if len(_CACHE) > 128:                      # 顺手清过期，防止无限增长
        for k in [k for k, v in _CACHE.items() if v[0] <= now]:
            _CACHE.pop(k, None)
    _CACHE[key] = (now + _CACHE_TTL, val)
    return val


def invalidate(exam_id=None):
    """成绩变动后清缓存（导入 / 删除 / 覆盖时调用）。

    直接整表清：趋势 / 学生详情是**跨场次**的派生数据，按单个场次清不干净，
    而重建成本只有一次几百毫秒，宁可多算也别给旧数据。
    """
    _CACHE.clear()


def save_subject(exam, subject, rows):
    """整科覆盖写入 + 重算名次（不 commit，事务交给路由层）"""
    for row in rows:
        db.session.add(TimedScore(
            exam_id=exam.id, student_no=row['student_no'],
            student_name=row.get('student_name', ''),
            zhixue_no=row.get('zhixue_no', ''),
            grade=exam.grade, class_name=row.get('class_name', ''),
            subject=subject, score=row['score']))
    db.session.flush()
    from app.modules.grades.services import timed_service
    timed_service.recalc_subject_ranks(exam.id, subject)
    invalidate(exam.id)              # 成绩变了，派生的名次 / T 分缓存作废


def classes_of(exam_id, subject=None):
    """本场出现过的班级（按班级名排序，供成绩单 / 分析页筛选）"""
    q = db.session.query(TimedScore.class_name).filter(
        TimedScore.exam_id == int(exam_id))
    if subject:
        q = q.filter(TimedScore.subject == subject)
    names = {r[0] for r in q.distinct().all() if r[0]}
    return sorted(names)


def _summary_base(exam):
    """汇总表全量（按场次缓存）：科目 + 每生各科分 + 名次 + T 分"""
    from app.models.grades import SUBJECTS

    rows = TimedScore.query.filter_by(exam_id=exam.id).all()
    subs = {r.subject for r in rows}
    subjects = [s for s in SUBJECTS if s in subs]
    by = {}
    by_subject = {}
    for r in rows:
        raw_cls = (r.class_name or '').strip()
        it = by.setdefault(r.student_no, {
            'student_no': r.student_no, 'name': r.student_name or '',
            # 无班级归属的（不分班/未分班）显示为「—」，页面与导出里不再出现「不分班」
            'class_name': '—' if _no_class(raw_cls) else raw_cls,
            'scores': {}})
        it['scores'][r.subject] = r.score
        by_subject.setdefault(r.subject, {})[r.student_no] = r.score

    # 每科 T 分（同科内比），汇总表在分数下方用小字显示
    tmaps = {s: _t_map_of(m) for s, m in by_subject.items()}

    out = []
    for it in by.values():
        it['total'] = round(sum(it['scores'].values()), 1)
        it['subject_count'] = len(it['scores'])
        it['ts'] = {s: tmaps.get(s, {}).get(it['student_no'])
                    for s in subjects}
        out.append(it)

    # 年级名次 → 班级名次（都在**过滤班级之前**算，保证名次口径是全年级）
    out.sort(key=lambda x: (-x['total'], x['student_no']))
    _assign_ranks(out, 'rank_grade')
    classes = [c for c in sorted({x['class_name'] for x in out}) if c != '—']
    for cls in classes:
        _assign_ranks([x for x in out if x['class_name'] == cls], 'rank_class')

    # 总分 T 分（跨场次比总水平用）
    tmap = _t_map_of({x['student_no']: x['total'] for x in out})
    for x in out:
        x['t'] = tmap.get(x['student_no'])

    return {'subjects': subjects, 'out': out, 'classes': classes,
            't_ready': bool(tmap)}


def summary_matrix(exam, class_name='', q='', page=None, per_page=50):
    """汇总表：一行一个学生 × 各科分数 + 总分 + 年级名次 + 班级名次 + 总分 T 分。

    2026-10-20 用户口径：限时练没有「总分」行，总分由各科现算；
    名次按总分降序（同分并列 1,1,3），班级名次在**同班**内部再排一次。
    全量结果按场次缓存，翻页 / 搜索 / 换班级都只做内存过滤。
    """
    base = _cached(('summary', exam.id), lambda: _summary_base(exam))
    subjects = base['subjects']
    out = base['out']
    classes = list(base['classes'])
    t_ready = base['t_ready']

    shown = [x for x in out if not class_name or x['class_name'] == class_name]
    # 搜索：学号 / 姓名包含即可（不区分大小写）
    kw = (q or '').strip().lower()
    if kw:
        shown = [x for x in shown
                 if kw in (x['student_no'] or '').lower()
                 or kw in (x['name'] or '').lower()]
    total = len(shown)
    # page=None → 全量（导出用）；否则分页（页面默认 50 人/页）
    if page is None:
        rows, pages, page_no = shown, 1, 1
    else:
        pages = max(1, (total + per_page - 1) // per_page)
        try:
            page_no = int(page or 1)
        except (TypeError, ValueError):
            page_no = 1
        page_no = min(max(1, page_no), pages)
        rows = shown[(page_no - 1) * per_page: page_no * per_page]
    return {'subjects': subjects, 'rows': rows, 'classes': classes,
            't_ready': t_ready, 't_min': T_MIN_SAMPLE,
            'count': total, 'page': page_no, 'pages': pages,
            'per_page': per_page, 'q': q}


def _assign_ranks(items, key):
    """按当前顺序写入名次（同分并列，如 1,1,3）"""
    prev = None
    rank = 0
    for i, it in enumerate(items, 1):
        if it['total'] != prev:
            rank = i
            prev = it['total']
        it[key] = rank


def subject_blocks(exam, tpl):
    """分析页数据：一个学科一块，每块两组「图 + 表」——

    上：**人数** 左堆叠图（各班各名次段人数） + 右人数表；
    下：**平均分** 左柱状图（各班均分） + 右平均分表（含各段均分）。
    """
    from app.models.grades import SUBJECTS
    have = {r[0] for r in db.session.query(TimedScore.subject)
            .filter_by(exam_id=exam.id).distinct().all()}
    out = []
    for s in SUBJECTS:
        if s not in have:
            continue
        a = _cached(('analysis', exam.id, s, getattr(tpl, 'id', 0)),
                    lambda s=s: subject_analysis(exam, s, tpl))
        rows = a['rows']
        classes = [r['class_name'] for r in rows]
        # 堆积图：互斥段，各班段内人数
        seg_names = [seg['name'] for seg in a['total']['stack']]
        count_chart = {
            'classes': classes,
            'series': [
                {'name': name,
                 'data': [next((seg['count'] for seg in r['stack']
                                if seg['name'] == name), 0)
                          for r in rows]}
                for name in seg_names],
        }
        out.append({
            'subject': s,
            'analysis': a,
            'count_chart': count_chart,
            'avg_chart': {'classes': classes,
                          'avgs': [r['avg'] for r in rows]},
        })
    return out


def _layer_label(layer):
    """层说明（悬停用）：前10 → 「全年级名次 ≤ 10」"""
    high = layer.get('to_rank')
    if high in (None, ''):
        return '全年级'
    return f'全年级名次 ≤ {int(high)}'


def effective_layers(layers, count):
    """按实考人数裁剪 / 自适应分层（保底机制，2026-10-20 用户口径）。

    - **人数 < 100**：按 10 人一组分层（前10、前20…刚好覆盖全体），
      不够 100 人时 12 层模板的前50/前100 没有区分度；
    - **人数 ≥ 100**：沿用模板层，但**丢掉门槛 > 人数的层**
      （497 人时「前500 / 前550」就等于全体，不再显示）。
    """
    layers = list(layers or [])
    if count <= 0:
        return []
    # 收口层：无上界 = 全年级都算。
    # 为什么不用「前{count}」：同分同名次后延跳号，497 人里最大名次可能到 499，
    # 用名次门槛会漏掉尾巴上的人；「全员」段保证一个人都不少。
    closing = {'name': '全员', 'from_rank': 1, 'to_rank': None}
    if count < 100:
        out = []
        rank = 10
        while rank < count:
            out.append({'name': f'前{rank}', 'from_rank': 1, 'to_rank': rank})
            rank += 10
        out.append(closing)
        return out
    keep = [x for x in layers
            if x.get('to_rank') is None or int(x['to_rank']) <= count]
    keep = [x for x in keep if x.get('to_rank') is not None] or [
        dict(x) for x in layers[:3] if x.get('to_rank') is not None]
    keep.append(closing)
    return keep


def _empty_row(name):
    """一行统计骨架（班级 / 合计）"""
    return {'class_name': name, 'teacher': '', 'count': 0, 'sum': 0.0,
            'max': None, 'min': None, 'bands': [], 'band_scores': {}}


def _teacher_map(grade, subject):
    """{班级: 任课教师姓名}（取当前生效的任课映射，未设置返回空）"""
    from app.models import User
    from app.models.grades import TeacherSubjectLink

    links = (TeacherSubjectLink.query
             .filter_by(grade=grade, subject=subject, active=True).all())
    if not links:
        return {}
    users = {u.id: u for u in User.query.filter(
        User.id.in_({x.user_id for x in links})).all()}
    out = {}
    for link in links:
        u = users.get(link.user_id)
        if u:
            out[link.class_name] = u.real_name or u.username
    return out


def _hit(layer, rank):
    """累积门槛：名次 ≤ 层门槛即命中（to_rank 空 = 全年级都算）"""
    if not rank:
        return False
    high = layer.get('to_rank')
    return True if high in (None, '') else rank <= int(high)


def _accumulate(row, score, rank):
    """把一名学生累加进统计行（rank 只用于区间判定，见 subject_analysis）"""
    row['count'] += 1
    row['sum'] += float(score or 0)
    row['max'] = score if row['max'] is None else max(row['max'], score)
    row['min'] = score if row['min'] is None else min(row['min'], score)


def _finish(row):
    """结算均分（1 位小数）"""
    row['avg'] = round(row['sum'] / row['count'], 1) if row['count'] else None
    row.pop('sum', None)
    return row


# 无班级归属的占位班级名（这类成绩不进班级统计）
NO_CLASS_NAMES = ('不分班', '未分班')


def _no_class(name):
    """是否为「无班级」占位值（空 / 不分班 / 未分班）"""
    return not (name or '').strip() or (name or '').strip() in NO_CLASS_NAMES


def subject_analysis(exam, subject, template):
    """单科分析表：各班均分 + 各名次区间人数 + 合计行（纯内存，一次查询）

    2026-10-20 用户口径：「不分班」这类无班级归属的成绩不参与班级统计，
    表格里不再出现「不分班」这一行（合计与人数口径同步排除）。
    """
    teachers = _teacher_map(exam.grade, subject)
    rows = (TimedScore.query
            .filter_by(exam_id=exam.id, subject=subject)
            .order_by(TimedScore.class_name.asc()).all())
    # 无班级归属的不参与统计（先剔除，人数口径才准）
    rows = [r for r in rows if not _no_class((r.class_name or '').strip())]
    # 分层按实考人数自适应：<100 人按 10 人一组；≥100 人丢掉超过人数的层
    layers = effective_layers(
        template.get_layers() if template else [], len(rows))
    by_class = {}
    total = _empty_row('合计')
    layer_scores = {}
    for r in rows:
        cls = (r.class_name or '').strip()
        row = by_class.setdefault(cls, _empty_row(cls))
        _accumulate(row, r.score, r.rank_grade)
        _accumulate(total, r.score, r.rank_grade)
        for layer in layers:
            # 累积门槛：一名学生可以同时计入「前10 / 前20 / 前50 …」
            if _hit(layer, r.rank_grade):
                row['bands'].append(layer['name'])
                total['bands'].append(layer['name'])
                layer_scores.setdefault(layer['name'], []).append(r.score)
                row['band_scores'].setdefault(layer['name'], []).append(r.score)

    def _band_counts(item):
        """把命中的层名列表转成各层人数（按模板顺序）"""
        return [{'name': lay['name'], 'label': _layer_label(lay),
                 'count': item['bands'].count(lay['name'])} for lay in layers]

    def _stack_of(item):
        """堆积条用的**互斥区间**人数：本层累计 − 上一层累计。

        表里的层是累积门槛（前10 ⊂ 前20），直接堆叠会重复计数；
        堆积条要的是"本段多少人"，所以取相邻两层的差，末层之后单列一段。
        """
        counts = [b['count'] for b in item['bands']]
        segs = []
        prev = 0
        for i, band in enumerate(item['bands']):
            segs.append({'name': band['name'], 'count': counts[i] - prev})
            prev = counts[i]
        rest = item['count'] - prev
        if rest > 0:
            segs.append({'name': '未入层', 'count': rest})
        return segs

    def _row_layer_avgs(item):
        """本班各累积层的学生均分（平均分表用）"""
        out = []
        for lay in layers:
            scores = item['band_scores'].get(lay['name'], [])
            out.append({'name': lay['name'], 'label': _layer_label(lay),
                        'avg': round(sum(scores) / len(scores), 1)
                        if scores else None})
        item.pop('band_scores', None)
        return out

    out_rows = []
    for cls in sorted(by_class):
        item = by_class[cls]
        item['bands'] = _band_counts(item)
        item['teacher'] = teachers.get(cls, '')
        item['stack'] = _stack_of(item)
        item['layer_avgs'] = _row_layer_avgs(item)
        out_rows.append(_finish(item))
    total['bands'] = _band_counts(total)
    total['stack'] = _stack_of(total)
    total['layer_avgs'] = _row_layer_avgs(total)
    total = _finish(total)

    # 每个名次层的学生平均分（累积口径：前10 的均分、前20 的均分…）
    layer_avgs = []
    for lay in layers:
        scores = layer_scores.get(lay['name'], [])
        layer_avgs.append({
            'name': lay['name'], 'label': _layer_label(lay),
            'count': len(scores),
            'avg': round(sum(scores) / len(scores), 1) if scores else None,
        })

    return {
        'subject': subject,
        'template_name': template.name if template else '',
        'layers': [{'name': lay['name'], 'label': _layer_label(lay)}
                   for lay in layers],
        'rows': out_rows,
        'total': total,
        'layer_avgs': layer_avgs,
        'classes': [r['class_name'] for r in out_rows],
        'student_count': int(len(rows)),
        'class_count': len(out_rows),
    }





# ==================== T 分数（标准分） ====================
# T = 10 × Z + 50，Z = (个人分 - 群体均分) / 群体标准差。
# 作用：把"原始分"换成"在这批考生里的相对位置"，跨试卷 / 跨科目可比。
# 边界：样本少于 30 人不算（小样本的均分/标准差抖动大，T 分会误导）。
T_MIN_SAMPLE = 30


def _mean_sd(values):
    """(均值, 标准差)；样本不足 2 个返回 (None, None)"""
    n = len(values)
    if n < 2:
        return None, None
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n
    return mean, var ** 0.5


def _t_map_of(score_map):
    """{键: T 分}：T = 10×(x−均分)÷标准差 + 50；样本不足 / 标准差≈0 返回空"""
    if len(score_map) < T_MIN_SAMPLE:
        return {}
    mean, sd = _mean_sd(list(score_map.values()))
    if not sd or sd < 1e-9:
        return {}
    return {k: round(10 * (v - mean) / sd + 50, 1)
            for k, v in score_map.items()}


def subject_t_map(exam_id, subject):
    """{学号: T 分}（单科口径，分析页给单科 T 用）"""
    rows = (TimedScore.query
            .filter_by(exam_id=int(exam_id), subject=subject)
            .with_entities(TimedScore.student_no, TimedScore.score).all())
    return _t_map_of({x[0]: x[1] for x in rows})


def _student_detail_base(exam, student_no):
    """单个学生的**全部场次**明细（学生分析详情页）。

    行 = 本年级每一场限时练，列 = 该生参加过的科目；
    单元格 = 分数 + 该科 T 分 + 年级名次；另给各科 T 分折线数据。
    """
    from app.models.grades import SUBJECTS
    from app.models.timed import TimedExam

    exams = (TimedExam.query.filter_by(grade=exam.grade)
             .order_by(TimedExam.exam_date.asc(), TimedExam.id.asc()).all())
    eids = [e.id for e in exams]
    rows = (TimedScore.query
            .filter(TimedScore.exam_id.in_(eids),
                    TimedScore.student_no == student_no).all())
    if not rows:
        return {'student_no': student_no, 'name': '', 'cls': '',
                'subjects': [], 'lines': [], 'chart': {}, 't_min': T_MIN_SAMPLE}

    # T 分要按「每场每科全体」算，所以仍取一次全量成绩（内存分组）
    full = (TimedScore.query.filter(TimedScore.exam_id.in_(eids))
            .with_entities(TimedScore.exam_id, TimedScore.subject,
                           TimedScore.student_no, TimedScore.score).all())
    groups = {}
    for eid, subj, no, score in full:
        groups.setdefault((eid, subj), {})[no] = score
    tmaps = {k: _t_map_of(m) for k, m in groups.items()}

    name = next((r.student_name for r in rows if r.student_name), '')
    cls = next((r.class_name for r in rows if r.class_name), '')
    subjects = [s for s in SUBJECTS if any(r.subject == s for r in rows)]

    by_exam = {}
    for r in rows:
        item = by_exam.setdefault(r.exam_id, {'cells': {}})
        item['cells'][r.subject] = {
            'score': r.score, 'rank': r.rank_grade,
            't': tmaps.get((r.exam_id, r.subject), {}).get(student_no),
        }
    emap = {e.id: e for e in exams}
    lines = [{'exam': emap[eid], **data} for eid, data in by_exam.items()]
    lines.sort(key=lambda x: (x['exam'].exam_date, x['exam'].id))

    chart = {}
    for it in lines:
        stamp = it['exam'].exam_date.strftime('%m-%d')
        for subj, cell in it['cells'].items():
            if cell['t'] is not None:
                chart.setdefault(subj, []).append([stamp, cell['t']])

    # 每科最近一次 T（卡片上直接看）
    latest = {}
    for s in subjects:
        pts = chart.get(s) or []
        latest[s] = pts[-1][1] if pts else None

    return {'student_no': student_no, 'name': name, 'cls': cls,
            'subjects': subjects, 'lines': lines, 'chart': chart,
            'latest': latest, 't_min': T_MIN_SAMPLE}


def student_detail(exam, student_no):
    """学生详情（按年级 + 学号缓存，重复点同一人不再扫全表）"""
    return _cached(('detail', exam.grade, student_no),
                   lambda: _student_detail_base(exam, student_no))


TREND_MAX_POINTS = 8      # 迷你折线最多回溯几场


def _spark_path(points, w=72, h=22):
    """把 [(日期, T)] 折成 SVG polyline 坐标串（宽 w 高 h，纵向归一化）"""
    if len(points) < 2:
        return ''
    ts = [p[1] for p in points]
    lo, hi = min(ts), max(ts)
    span = (hi - lo) or 1.0
    step = w / (len(ts) - 1)
    return ' '.join(
        f'{round(i * step, 1)},{round(h - 2 - (t - lo) / span * (h - 4), 1)}'
        for i, t in enumerate(ts))


def _trends_base(exam):
    """全年级学生 × 各科 **T 分轨迹**（按年级缓存，班级筛选与分页在调用方做）。

    性能取舍：
    - 一次把本年级**全部**限时练成绩取出来（6 场 ≈ 1.4 万行），在内存里按
      (场次, 科目) 分组算 T 分，避免 N 场 × M 科 的循环查询；
    - 结果缓存（见 ``_cached``），翻页 / 换班级不再重算。
    """
    from app.models.grades import SUBJECTS
    from app.models.timed import TimedExam

    exams = (TimedExam.query.filter_by(grade=exam.grade)
             .order_by(TimedExam.exam_date.asc(), TimedExam.id.asc()).all())
    eids = [e.id for e in exams]
    dates = {e.id: e.exam_date for e in exams}
    rows = (TimedScore.query.filter(TimedScore.exam_id.in_(eids))
            .with_entities(TimedScore.exam_id, TimedScore.subject,
                           TimedScore.student_no, TimedScore.student_name,
                           TimedScore.class_name, TimedScore.score).all())

    groups = {}
    for eid, subj, no, _name, _cls, score in rows:
        groups.setdefault((eid, subj), {})[no] = score
    tmaps = {k: _t_map_of(m) for k, m in groups.items()}

    cur = [r for r in rows if r[0] == exam.id]
    subjects = [s for s in SUBJECTS if any(r[1] == s for r in cur)]

    info = {}
    for eid, subj, no, name, cls, _score in cur:
        it = info.setdefault(no, {'no': no, 'name': name or '',
                                  'cls': cls or '', 'now': {}})
        it['now'][subj] = tmaps.get((eid, subj), {}).get(no)

    series = {}
    for (eid, subj), m in tmaps.items():
        if subj not in subjects:
            continue
        for no, t in m.items():
            if no in info:
                series.setdefault((no, subj), []).append((dates[eid], t))

    out = []
    for no, it in info.items():
        pts = {}
        for s in subjects:
            pts[s] = sorted(series.get((no, s), []))[-TREND_MAX_POINTS:]
        out.append({
            **it, 'pts': pts,
            # 行内迷你折线：后端直接给 SVG 坐标串，前端不画图
            'paths': {s: _spark_path(p) for s, p in pts.items()},
        })

    # 本场总分 T 高的在前，方便老师先看头部学生
    def _sort_key(x):
        vals = [v for v in x['now'].values() if v is not None]
        return (-(sum(vals) / len(vals)) if vals else 0, x['cls'], x['no'])

    out.sort(key=_sort_key)
    return {'subjects': subjects, 'rows': out}


def student_trends(exam, class_name='', page=1, per_page=20):
    """学生分析页：班级筛选 + 分页（全量结果按年级缓存，翻页不再重算）"""
    base = _cached(('trends', exam.grade), lambda: _trends_base(exam))
    subjects = base['subjects']
    out = base['rows']
    if class_name:
        out = [x for x in out if x['cls'] == class_name]

    total = len(out)
    pages = max(1, (total + per_page - 1) // per_page)
    try:
        page = int(page or 1)
    except (TypeError, ValueError):
        page = 1
    page = min(max(1, page), pages)
    start = (page - 1) * per_page
    return {'subjects': subjects, 'rows': out[start:start + per_page],
            'total': total, 'page': page, 'pages': pages,
            'per_page': per_page, 'class_name': class_name,
            't_min': T_MIN_SAMPLE}
