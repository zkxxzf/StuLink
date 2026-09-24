# StuLink v1.18.1.0 2026-09-24
# 成绩汇报区指标引擎：对标年级汇报 PPT 口径
#  - 单上线：单科（或总分）≥ 对应层下界
#  - 双上线：单科过该科层线 且 总分过同层线（如一本双上线）
#  - 去差均分：按班型剔除各班总分末尾 N 人后再均分（卓越班去 2 人，其余不去）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from app.models.grades import (Exam, SUBJECTS, TOTAL_SUBJECT, subject_display,
                               TeacherSubjectLink)
from app.models import ClassProfile, UserClassLink, User
from app.modules.grades.services import stats_service as st

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

def layers_desc(data, direction, subject=TOTAL_SUBJECT):
    """某方向×学科的层线，按下界从高到低返回 [(层名, 下界)]；无划线返回 []。
    band_list 会同时命中该方向线与历史遗留的通用线(direction='')，同名时只保留一条，
    避免汇报统计把同一层算两遍（上线率超 100%）。"""
    seen, out = set(), []
    for name, lower in sorted(data.band_list(direction or '', subject),
                              key=lambda x: -(x[1] or 0)):
        if name in seen:
            continue
        seen.add(name)
        out.append((name, lower))
    return out


def pick_layer(data, direction, subject=TOTAL_SUBJECT, layer_name=''):
    """取指定层 (名, 下界)。
    - 显式传 layer_name：必须精确匹配，找不到返回 None（绝不回退到别的层，防止张冠李戴）；
    - 不传：按 PREFER_LAYERS 顺序精确匹配，都没有时取最高层。
    """
    bl = layers_desc(data, direction, subject)
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
    """去差学生学号集合：各班按班型规则剔除总分末尾 N 人（班型取自 ClassProfile）"""
    profiles = ClassProfile.query.filter_by(grade=data.exam.grade).all()
    trim_n = {p.class_name: TRIM_RULES.get(p.class_type or '', DEFAULT_TRIM)
              for p in profiles}
    by_class = {}
    for r in data.total_rows:
        by_class.setdefault(r.class_name or '—', []).append(r)
    out = set()
    for cls, rows in by_class.items():
        n = trim_n.get(cls, DEFAULT_TRIM)
        if n and n > 0:
            # 末 N 名同分并列时按成绩记录 id 确定性截断（固定剔除 N 人，不随统计顺序波动）
            lows = sorted(rows, key=lambda r: (r.score if r.score is not None else -1,
                                               r.id))[:n]
            out.update(r.student_no for r in lows)
    return out


def subject_teacher_map(grade):
    """{科目展示名: '教师A、教师B'}：该年级各班任课教师按科目去重合并。
    板块一「任课教师」列数据源——不依赖划线，教师未配置/停用则科目对应空串。"""
    links = TeacherSubjectLink.query.filter_by(grade=grade, active=True).all()
    uids = {lk.user_id for lk in links}
    users = User.query.filter(User.id.in_(uids), User.is_active.is_(True)).all() if uids else []
    names = {u.id: u.real_name for u in users}
    out, seen = {}, {}
    for lk in links:
        nm = names.get(lk.user_id)
        if not nm:
            continue
        disp = subject_display(lk.subject)
        bag = seen.setdefault(disp, set())
        if lk.user_id not in bag:
            bag.add(lk.user_id)
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
    """单科目本次口径：线分/参考数/单上线数率/双上线数率/去差均分"""
    rows = _dir_rows(data, direction)
    total_line = pick_layer(data, direction, TOTAL_SUBJECT, layer_name)
    total_lower = total_line[1] if total_line else None
    scores, single_n, dual_n = [], 0, 0
    for r in rows:
        sc = data.subj.get((r.student_no, sub))
        if sc is None:
            continue
        scores.append(sc)
        if sc >= layer_lower:
            single_n += 1
            if total_lower is not None and r.score is not None and r.score >= total_lower:
                dual_n += 1
    trim_vals = [data.subj.get((r.student_no, sub)) for r in rows
                 if r.student_no not in trimmed]
    trim_vals = [v for v in trim_vals if v is not None]
    n = len(scores)
    return {'subject': subject_display(sub), 'line': layer_lower, 'count': n,
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
            'teachers': subject_teacher_map(data.exam.grade),
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
        for name, lower in layers_desc(data, r.direction or ''):
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
