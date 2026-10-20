# StuLink v1.19.49.44 2026-10-10
# 成绩汇报区指标引擎：对标年级汇报 PPT 口径
#  - 单上线：单科（或总分）≥ 对应层下界
#  - 双上线：单科过该科层线 且 总分过同层线（如一本双上线）
#  - 去差均分：按班型剔除各班总分末尾 N 人后再均分（卓越班去 2 人，其余不去）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from app.models.grades import (Exam, SUBJECTS, TOTAL_SUBJECT, subject_display,
                               TeacherSubjectLink)
from app.models import ClassProfile, UserClassLink, User
from app.modules.grades.services import stats_service as st

# 2026-10-10 架构审计修复：本文件原有静默 `except Exception: pass`，失败不可观测。
# 统一加 logger 记录 warning（仅留痕，不改变任何降级行为与返回值）。
import logging

logger = logging.getLogger(__name__)

# ============ 可调口径配置（修改规则只改这里） ============
# 班型 → 去差人数：计算「去差均分」时该班总分排名末尾剔除人数；未列出的班型为 0
TRIM_RULES = {'卓越班': 2}
DEFAULT_TRIM = 0
# 汇报表默认展示的层（按顺序精确匹配，兼容「一本」叫法）；都不在时取最高层
PREFER_LAYERS = ('特控', '一本')
# 压线提醒默认范围（分）：线上 N 分以内、线下 M 分以内的临界生；NEAR_LINE_MAX 为输入上限
NEAR_LINE_ABOVE = 10
NEAR_LINE_BELOW = 20
NEAR_LINE_MAX = 100
# 学科强弱红绿标注阈值（板块四）：特控上线率差≥N个百分点、去差均分差≥N分才标色
STRONG_RATE_PP = 5
STRONG_AVG_DIFF = 5


# ============ 公共口径 ============

def layers_desc(data, direction, subject=TOTAL_SUBJECT, selection=''):
    """某方向×[组合]×学科的层线，按下界从高到低返回 [(层名, 下界)]；无划线返回 []。
    band_list 会同时命中该方向线与历史遗留的通用线(direction='')，同名时只保留一条，
    避免汇报统计把同一层算两遍（上线率超 100%）。
    selection 非空 = 该选科组合的线（组合总分线，band_list 内部回落方向线）。"""
    seen, out = set(), []
    for name, lower in sorted(data.band_list(direction or '', subject, selection),
                              key=lambda x: -(x[1] or 0)):
        if name in seen:
            continue
        seen.add(name)
        out.append((name, lower))
    return out


def layer_lowers(data, direction, subject, layer_name, rows, cache=None):
    """把某层线按「学生自身组合」展开为下界，供逐生判层（2026-10-15 组合级划线）。

    返回 (base_lower, {组合: 下界})：
    - base_lower：方向级下界（无组合维度时的兜底；也可能为 None）；
    - {组合: 下界}：只收录该组合确实有这条线的（组合线或 band_list 回落的方向线）。
    rows：该群体的学生行（含 subject_selection）；cache 可选，避免同层多科重复取线。
    """
    key = (direction, subject, layer_name)
    if cache is not None and key in cache:
        return cache[key]
    base = pick_layer(data, direction, subject, layer_name)
    base_lower = base[1] if base else None
    out = {}
    for r in rows:
        sel = (getattr(r, 'subject_selection', '') or '').strip()
        if not sel or sel in out:
            continue
        line = pick_layer(data, direction, subject, layer_name, selection=sel)
        if line:
            out[sel] = line[1]
    res = (base_lower, out)
    if cache is not None:
        cache[key] = res
    return res


def pick_layer(data, direction, subject=TOTAL_SUBJECT, layer_name='', selection=''):
    """取指定层 (名, 下界)。
    - 显式传 layer_name：必须精确匹配，找不到返回 None（绝不回退到别的层，防止张冠李戴）；
    - 不传：按 PREFER_LAYERS 顺序精确匹配，都没有时取最高层。
    - selection 非空 = 按该选科组合的线取（组合总分线；band_list 内部回落方向线）。
    """
    bl = layers_desc(data, direction, subject, selection)
    if not bl:
        return None
    if layer_name:
        for name, lower in bl:
            if name == layer_name:
                return name, lower
        return None
    for prefer in PREFER_LAYERS:
        for name, lower in bl:
            if name == prefer:
                return name, lower
    return bl[0]


def trimmed_nos(data):
    """去差学生学号集合：各班按班型规则剔除总分末尾 N 人

    v1.19.49.44 班型取数改为**快照优先**：
      ① 优先用成绩行上的 class_type（该场考试导入时的「当时班型」）；
      ② 快照为空（迁移前导入的历史考试）→ 回落读 ClassProfile（过渡兼容）。
    这样班型一旦调整（强基↔卓越），历史考试的去差口径不会跟着变。
    """
    # ① 快照班型：同班内一致，取首个非空值
    snap_ct = {}
    for r in data.total_rows:
        cls = r.class_name or '—'
        if cls not in snap_ct and getattr(r, 'class_type', None):
            snap_ct[cls] = r.class_type
    # ② 回落：当前班型设置
    live_ct = {}
    try:
        for p in ClassProfile.query.filter_by(grade=data.exam.grade).all():
            live_ct[p.class_name] = p.class_type or ''
    except Exception as _e:  # noqa: BLE001
        logger.warning('report_service.py::trimmed_nos 异常（已降级处理）: %s', _e)

    by_class = {}
    for r in data.total_rows:
        by_class.setdefault(r.class_name or '—', []).append(r)

    def _trim_of(cls):
        ct = snap_ct.get(cls) or live_ct.get(cls, '')
        return TRIM_RULES.get(ct, DEFAULT_TRIM)

    out = set()
    for cls, rows in by_class.items():
        n = _trim_of(cls)
        if n and n > 0:
            # 末 N 名同分并列时按成绩记录 id 确定性截断（固定剔除 N 人，不随统计顺序波动）
            lows = sorted(rows, key=lambda r: (r.score if r.score is not None else -1,
                                               r.id))[:n]
            out.update(r.student_no for r in lows)
    return out


def subject_teacher_map(grade, exam_id=None):
    """{科目展示名: '教师A、教师B'}：该年级各班任课教师按科目去重合并。

    板块一「任课教师」列数据源——不依赖划线，教师未配置/停用则科目对应空串。
    v1.19.49.44：传入 exam_id 时优先读**本场考试的任课快照**（考试当时），
    教师调动/重新分班后历史考试的展示不会跟着变。
    """
    pairs = []          # [(subject, 姓名)]
    if exam_id:
        from app.modules.grades.services import teacher_snapshot_service as _tss
        nm_map = _tss.name_map_of(exam_id, grade=grade)
        pairs = [(subj, nm) for (_cls, subj), nm in nm_map.items() if nm]
    if not pairs:
        # 无快照（或教师姓名全部缺失）→ 回落当前映射
        links = TeacherSubjectLink.query.filter_by(grade=grade, active=True).all()
        uids = {lk.user_id for lk in links}
        users = User.query.filter(User.id.in_(uids), User.is_active.is_(True)).all() if uids else []
        names = {u.id: u.real_name for u in users}
        pairs = [(lk.subject, names.get(lk.user_id)) for lk in links]
    out, seen = {}, {}
    for subject, nm in pairs:
        if not nm:
            continue
        disp = subject_display(subject)
        bag = seen.setdefault(disp, set())
        if nm not in bag:
            bag.add(nm)
            out[disp] = (out[disp] + '、' + nm) if disp in out else nm
    return out


def headteacher_map(grade):
    """{(年级, 班级): '班主任A、班主任B'}（顿号连接；无配置返回 ''）"""
    links = UserClassLink.query.filter_by(grade=grade).all()
    uids = {lk.user_id for lk in links}
    users = User.query.filter(User.id.in_(uids), User.is_active.is_(True)).all() if uids else []
    names = {u.id: u.real_name for u in users}
    out, seen = {}, {}
    for lk in links:
        key = (lk.grade, lk.class_name)
        nm = names.get(lk.user_id)
        # 同班多班主任顿号连接；按 user_id 去重，防止重复关联出现两遍
        if nm and lk.user_id not in seen.setdefault(key, set()):
            seen[key].add(lk.user_id)
            out[key] = (out[key] + '、' + nm) if key in out else nm
    return out


def _pct(n, d):
    """百分比取整（PPT 口径：45%），四舍五入避免 Python 银行家舍入偶发偏差"""
    return int(n / d * 100 + 0.5) if d else None


def _avg(values):
    return round(sum(values) / len(values), 1) if values else None


def _dir_rows(data, direction):
    """该方向（或全部）的总分行快照"""
    if not direction:
        return list(data.total_rows)
    return [r for r in data.total_rows if r.direction == direction]


# 方向自动落地的优先顺序（各方向独立划线，无「通用线」）
_DIR_PREFER = {'物理': 0, '历史': 1}


def resolve_direction(data, direction=''):
    """未指定方向时，返回第一个有总分划线的方向（物理优先）；指定时原样返回。
    全部无划线时返回 ''。各汇报板块统一调用，避免自动落地口径不一致。"""
    if direction:
        return direction
    for d in sorted(data.directions or [], key=lambda x: (_DIR_PREFER.get(x, 2), x)):
        if layers_desc(data, d):
            return d
    return ''


# ============ 板块一：年级各科「层上线」对比表（本次 vs 上次） ============

def _subject_layer_row(data, direction, sub, layer_name, layer_lower, trimmed):
    """单科目本次口径：线分/参考数/单上线数率/双上线数率/去差均分

    2026-10-15 组合级划线：**单科线**与双上线的**总分线**都按学生自身组合取线
    （再选四科＝各组合各一条线，语数外+首选科＝方向线；band_list 内部回落方向线）。"""
    rows = _dir_rows(data, direction)
    total_lower, total_sels = layer_lowers(data, direction, TOTAL_SUBJECT, layer_name, rows)
    sub_base, sub_sels = layer_lowers(data, direction, sub, layer_name, rows)
    if sub_base is None:
        sub_base = layer_lower      # 该科该层无组合维度线时沿用调用方给的方向线
    scores, single_n, dual_n = [], 0, 0
    for r in rows:
        sc = data.subj.get((r.student_no, sub))
        if sc is None:
            continue
        scores.append(sc)
        sel = r.subject_selection or ''
        sl = sub_sels.get(sel, sub_base)
        if sl is not None and sc >= sl:
            single_n += 1
            tl = total_sels.get(sel, total_lower)
            if tl is not None and r.score is not None and r.score >= tl:
                dual_n += 1
    trim_vals = [data.subj.get((r.student_no, sub)) for r in rows
                 if r.student_no not in trimmed]
    trim_vals = [v for v in trim_vals if v is not None]
    n = len(scores)
    # 线分展示：该科只有一条线时显示分数；按组合多线时显示「按组合」（上线数已逐生按各自组合算）
    _lows = set(sub_sels.values()) | ({sub_base} if sub_base is not None else set())
    line_show = layer_lower if len(_lows) <= 1 else '按组合'
    return {'subject': subject_display(sub), 'line': line_show, 'lines': sub_sels, 'count': n,
            'online_n': single_n, 'online_rate': _pct(single_n, n),
            'dual_n': dual_n, 'dual_rate': _pct(dual_n, n),
            'trim_avg': _avg(trim_vals)}


def subject_layer_report(exam_id, direction, layer_name='', compare_exam_id=None, no_compare=False):
    """图1：某方向各学科层上线表，含对比考试同口径（默认上一场，可指定 compare_exam_id）。

    no_compare=True 时不做任何对比（不取 compare_exam_id 也不回退到上一场）。

    返回 rows 顺序 = 该方向实际有成绩的科目（按系统科目序）。
    teachers：{科目展示名: '姓名、姓名'}，任课教师列（不依赖划线，修复未划线考试显示「—」）。
    对比考试未划线/无该科时对应字段为 None，前端显示「—」。
    """
    data = st.cached_exam_data(exam_id)
    if not data.total_rows:
        return {'error': '该考试暂无成绩'}
    # 各方向分别划线，不存在「通用线」：未指定方向时落到第一个有总分划线的方向（物理优先）
    direction = resolve_direction(data, direction)
    layer = pick_layer(data, direction, TOTAL_SUBJECT, layer_name)
    if not layer:
        # 区分「该方向完全没划线」与「有划线但没有所选层」，避免误提示
        if layer_name and layers_desc(data, direction):
            return {'error': f'{direction or ""}方向总分划线中没有「{layer_name}」层，请切换层或检查划线'}
        return {'error': '总分尚未划线，请先在「划线分层」设置特控/本科线'}
    layer_name, _ = layer
    trimmed = trimmed_nos(data)
    subj_rows, layer_names = [], [n for n, _ in layers_desc(data, direction)]
    for sub in SUBJECTS:
        sub_layer = pick_layer(data, direction, sub, layer_name)
        if not sub_layer:
            continue
        cur = _subject_layer_row(data, direction, sub, sub_layer[0], sub_layer[1], trimmed)
        subj_rows.append(cur)

    # 对比考试：no_compare 时不对比；否则显式指定优先（须同年级），再否则默认最近一场同年级考试
    cmp_exam = None
    if not no_compare:
        if compare_exam_id:
            cmp_exam = Exam.query.get(compare_exam_id)
            if cmp_exam and cmp_exam.grade != data.exam.grade:
                cmp_exam = None
        elif data.prev_exam:
            cmp_exam = data.prev_exam
    prev_block = None
    if cmp_exam:
        prev = st.cached_exam_data(cmp_exam.id)
        p_trimmed = trimmed_nos(prev)
        prev_block = []
        # 科目名回填映射：展示名→系统名
        display_to_sys = {subject_display(s): s for s in SUBJECTS}
        for cur in subj_rows:
            s = display_to_sys[cur['subject']]
            p_layer = pick_layer(prev, direction, s, layer_name)
            if not p_layer:
                prev_block.append({'subject': cur['subject'], 'online_n': None,
                                   'online_rate': None, 'trim_avg': None})
                continue
            pr = _subject_layer_row(prev, direction, s, p_layer[0], p_layer[1], p_trimmed)
            prev_block.append({'subject': cur['subject'], 'online_n': pr['online_n'],
                               'online_rate': pr['online_rate'], 'trim_avg': pr['trim_avg']})
    return {'exam': {'id': data.exam.id, 'name': data.exam.name,
                     'date': data.exam.exam_date.strftime('%Y-%m-%d')},
            'direction': direction or '全部', 'layer': layer_name,
            'layers': layer_names, 'rows': subj_rows, 'prev': prev_block,
            'teachers': subject_teacher_map(data.exam.grade, exam_id=data.exam.id),
            'prev_exam': (cmp_exam.name if cmp_exam else None),
            'prev_exam_id': (cmp_exam.id if cmp_exam else None)}


# ============ 板块二：班级概况（班主任/特控/本科/去差均分） ============

def class_overview_report(exam_id, direction=''):
    """图2：各班 参考数/各总分层上线数率/去差均分（总分）；各方向分别判定后合并"""
    data = st.cached_exam_data(exam_id)
    if not data.total_rows:
        return {'error': '该考试暂无成绩'}
    # 层列只取被统计方向：指定方向时不并入其他方向的层名（否则会出现整列 0 的空列）
    dirs = [direction] if direction else (data.directions or [''])
    layer_names = []
    for d in dirs:
        for n, _ in layers_desc(data, d):
            if n not in layer_names:
                layer_names.append(n)
    if not layer_names:
        return {'error': '总分尚未划线，请先在「划线分层」设置特控/本科线'}
    trimmed = trimmed_nos(data)
    ht = headteacher_map(data.exam.grade)

    acc = {}
    for r in data.total_rows:
        if direction and r.direction != direction:
            continue
        cls = r.class_name or '—'
        a = acc.setdefault(cls, {'n': 0, 'pass': {}, 'trim_vals': []})
        a['n'] += 1
        if r.student_no not in trimmed and r.score is not None:
            a['trim_vals'].append(r.score)
        # 总分线按学生自身组合取线（组合总分线优先，回落方向线）
        for name, lower in layers_desc(data, r.direction or '',
                                       selection=r.subject_selection or ''):
            if r.score is not None and r.score >= lower:
                a['pass'][name] = a['pass'].get(name, 0) + 1

    order = {c: i for i, c in enumerate(data.classes)}
    rows = []
    for cls in sorted(acc, key=lambda c: order.get(c, 999)):
        a = acc[cls]
        row = {'class_name': cls,
               'headteacher': ht.get((data.exam.grade, cls), ''),
               'count': a['n'], 'trim_avg': _avg(a['trim_vals'])}
        for n in layer_names:
            cnt = a['pass'].get(n, 0)
            row[n + '_n'] = cnt
            row[n + '_rate'] = _pct(cnt, a['n'])
        rows.append(row)
    return {'exam': {'id': data.exam.id, 'name': data.exam.name},
            'direction': direction or '全部', 'layers': layer_names, 'rows': rows}
