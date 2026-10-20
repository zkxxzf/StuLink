# StuLink v1.19.49.43 2026-10-10
# 全局对比指标引擎：班级 × 学科「去差均分 + 各分数线」宽表
#
# 口径与 report_service / report_pivot_service 完全一致（复用其公共函数）：
#  - 去差均分：按班型剔除各班总分末尾 N 人（卓越班去 2 人）后再均分；同时给全体均分；
#  - 各分数线 = 该方向总分各层的**同名单科线**（高→低，逐方向独立划线）；
#    默认展示 211线/特控线/本科线，其余层（清北/985…）由页面复选框控制显隐；
#  - 上线人数**逐生按学生自己的方向与选科组合**取线判定（不是按班方向）；
#    小计/总计行并入学生重算（人数加权），不是各班均分的简单平均；
#  - 班型/方向/班级一律读成绩行快照（考试当时），缺快照回落当前 ClassProfile。
#
# 形态自适应：
#  - 全科（高一未分科）：所有班方向为空 → 单组，只出「年级」总计行；
#  - 分科（物理/历史方向并存）：按方向分组的班级段 + 各方向「年级」小计行（其下按
#    选科组合拆明细行）+ 末尾「年级」总计行（跨组合 → 不给均分，只给人数与上线率）；
#  - 行按「班级 × 选科组合」拆（2026-10-16）：混组合班 = 班级合计行 + 各组合明细行。
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from app.models import ClassProfile
from app.models.grades import SUBJECTS, TOTAL_SUBJECT, grade_level_label
from app.modules.grades.services import report_service as rs
from app.modules.grades.services import stats_service as st
from app.modules.grades.utils import numeric_classes

# 2026-10-10 架构审计修复：本文件原有静默 `except Exception: pass`，失败不可观测。
# 统一加 logger 记录 warning（仅留痕，不改变任何降级行为与返回值）。
import logging

logger = logging.getLogger(__name__)

# 方向展示顺序（物理在前，与汇报区 _DIR_PREFER 口径一致；无方向（全科）排最后）
_DIR_ORDER = {'物理': 0, '历史': 1, '': 2}

# 默认展示的分数线（用户口径 2026-10-11）：211线 / 特控线 / 本科线。
# 其余线（清北线 / 985线 …）在页面上作为可选项，勾选才显示，不勾选不显示。
DEFAULT_LINE_NAMES = ('211', '特控', '本科')


def line_label(layer_name):
    """层名 → 列头文案：'本科' → '本科线'；已带「线」不重复追加；无层名返回空串"""
    if not layer_name:
        return ''
    return layer_name if layer_name.endswith('线') else layer_name + '线'


def _norm_line(name):
    """层名归一：去掉结尾「线」，使 '211线' 与 '211' 视为同一层。"""
    n = (name or '').strip()
    return n[:-1] if n.endswith('线') else n


def default_line_names(layers):
    """从可用层名里挑默认勾选的层（211/特控/本科，保持高→低顺序）。

    都不匹配（学校用了别的层名）时回落取前 3 层，保证页面总有内容可看。
    """
    picked = [n for n in layers if _norm_line(n) in DEFAULT_LINE_NAMES]
    return picked if picked else list(layers[:3])


def _row_dir(data, cls):
    """班级主方向：人数最多的方向；平票时物理优先（与 class_subject_report 同口径）
    全科班（方向为空）返回 ''。"""
    dirs = {}
    for r in data.total_rows:
        if (r.class_name or '—') != cls:
            continue
        d = r.direction or ''
        dirs[d] = dirs.get(d, 0) + 1
    if not dirs:
        return ''
    return sorted(dirs, key=lambda d: (-dirs[d], _DIR_ORDER.get(d, 3), d))[0]


def _selection_stats(rows):
    """一组总分行（rows）的选科组合分布：[{'name': '物化地', 'count': 35}, …]

    2026-10-15 新增「组合」列：读成绩行的选科快照（考试当时），人数降序、同人数按名称，
    与逐生取线用同一个 subject_selection 口径；旧写法先归一（史政地→史地政），
    避免同一组合因写法不同被拆成两行。'全科'/空 不计入（未分科 → 空列表，页面显示「—」）。
    """
    from app.utils.helpers import normalize_subject
    cnt = {}
    for r in rows:
        sel = normalize_subject((r.subject_selection or '').strip())
        if sel and sel != '全科':
            cnt[sel] = cnt.get(sel, 0) + 1
    return [{'name': n, 'count': c}
            for n, c in sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))]


def _cells(data, rows, line_cache, subjects, layer_names, trimmed):
    """一组学生（rows = 总分行）的 总分 + 各科 指标。

    layer_names：本场要统计的层名（高→低）。每科输出 {count, trim_avg, lines}，
    其中 lines = {层名: {'n': 上线人数, 'rate': 上线率}}；该组在该层无对应线
    （如某科未划此层）时为 None（前端显示「—」，与「0 人上线」区分）。

    direction 语义（关键）：
      - 传具体方向（'物理'/'历史'/'')：整组按该方向层线判定（班级行、方向小计行）；
      - 传 None：每个学生按其**自身方向**取线（跨方向的全年级总计行）。
    """
    # line_cache 由 global_compare_report 传入（一次请求内共享，PERF-P04 修复）
    from app.utils.helpers import normalize_subject

    def _line(d, sub, name, sel):
        # 2026-10-15 组合级划线：带上学生自身的选科组合取线（再选四科各组合一条，
        # 语数外+首选科=方向线；band_list 内部回落方向线）
        key = (d, sub, name, sel)
        if key not in line_cache:
            line_cache[key] = rs.pick_layer(data, d, sub, name, selection=sel)
        return line_cache[key]

    # 与科目无关的量先按学生算一遍（2026-10-16 审计：原先放在科目循环里，
    # 10 科就把同一批学生重复扫 10 遍）
    marks = [(r, r.student_no not in trimmed, r.direction or '',
              normalize_subject(r.subject_selection or '')) for r in rows]

    out = {}
    for sub in [TOTAL_SUBJECT] + list(subjects):
        n = 0
        vals = []          # 去差后留用的分数（剔掉各班末尾 N 人）
        # 2026-10-16 用户要「不带去差均分的那种」：全体均分（含被剔学生）——
        # 人数列是全体人数，均分也应给一个对得上的全体口径
        all_vals = []
        hits = {name: 0 for name in layer_names}
        has = {name: False for name in layer_names}
        for r, keep, d, sel in marks:
            sc = r.score if sub == TOTAL_SUBJECT else data.subj.get((r.student_no, sub))
            if sc is None:
                continue
            n += 1
            all_vals.append(sc)
            if keep:
                vals.append(sc)
            for name in layer_names:
                ln = _line(d, sub, name, sel)
                if ln is None:
                    continue
                has[name] = True
                if sc >= ln[1]:
                    hits[name] += 1
        out[sub] = {
            'count': n,
            'trim_avg': rs._avg(vals),
            'avg': rs._avg(all_vals),
            'lines': {name: ({'n': hits[name], 'rate': rs._pct(hits[name], n)}
                             if has[name] else None) for name in layer_names},
        }
    return out


def global_compare_report(exam_id, direction=''):
    """全局对比主入口。

    返回 {exam, mode, subjects, layers, default_lines, groups, grand, partial}；
    layers = 本场可选的层名（高→低，取该方向总分划线）；
    default_lines = 页面默认勾选的层（211/特控/本科，保持高→低）。
    未划线/无成绩返回 {'error': '...'}（前端按提示展示）。
    """
    data = st.cached_exam_data(exam_id)
    if not data.total_rows:
        return {'error': '该考试暂无成绩'}

    # 层线：取「第一个有总划线方向」的总分层名（逐方向独立划线，通常同名）
    base_dir = rs.resolve_direction(data, direction)
    layers = [n for n, _ in rs.layers_desc(data, base_dir)]
    if not layers:
        # 2026-10-12 质量审计 P2：文案与其他 6 处统一为「特控」（原写「特优」，
        # 同一句错误提示在模块内两种说法，用户会以为缺的是两套线）
        return {'error': '总分尚未划线，请先在「划线分层」设置特控/本科线'}
    layer_names = layers
    default_lines = default_line_names(layers)

    # 科目列：本场实际有成绩的科目按系统顺序（全科=9 科；分科=各方向并集，缺科显示 —）
    subjects = list(data.imported_subjects)
    trimmed = rs.trimmed_nos(data)
    # v1.19.49.43 班主任取**本场考试当时**的快照（与任课教师同一机制）：
    # 历史考试不能再显示现在的班主任；快照缺失时自动回落当前关联
    from app.modules.grades.services import teacher_snapshot_service as _tss
    ht = _tss.headteacher_map_of(data.exam.id, grade=data.exam.grade)
    # 各科任课教师（v1.18.9.0）：同样读本场考试快照（缺则惰性回填），
    # 供每班每科「成绩下方」展示「这科当时谁教的」——与班主任同一套快照机制
    teachers = _tss.name_map_of(data.exam.id, grade=data.exam.grade)

    # 班型快照优先、当前设置为回落（与 trimmed_nos 同口径）
    snap_ct, live_ct = {}, {}
    for r in data.total_rows:
        cls = r.class_name or '—'
        if cls not in snap_ct and getattr(r, 'class_type', None):
            snap_ct[cls] = r.class_type
    try:
        for p in ClassProfile.query.filter_by(grade=data.exam.grade).all():
            live_ct[p.class_name] = p.class_type or ''
    except Exception as _e:  # noqa: BLE001  模型/库缺失时属性列留空，不阻断报表
        logger.warning('global_compare_service.py::global_compare_report 异常（已降级处理）: %s', _e)

    # 行集合：数字班（过滤 不分班/离校 等非教学班），保持班号顺序
    classes = numeric_classes(data.classes)
    # 2026-10-16 第九轮审计（PERF-P01）：班级→方向原先被 all_dirs / by_dir / class_rows
    # 三处各全表扫一遍（O(班数×总行数)，40 班×2100 人 ≈ 25 万次迭代），改为一次预算
    dir_of = {cls: _row_dir(data, cls) for cls in classes}
    # v1.19.49.43 方向下拉的**完整**可选集：先按全部班级算出各方向，再按 direction
    # 过滤生成展示分组。否则选定某方向后返回值里只剩该方向，前端据此重建下拉
    # 就会丢掉另一个方向（用户反馈的 bug）。
    all_dirs = sorted(set(dir_of.values()),
                      key=lambda x: (_DIR_ORDER.get(x, 3), x))
    by_dir = {}
    for cls in classes:
        d = dir_of[cls]
        if direction and d != direction:
            continue
        by_dir.setdefault(d, []).append(cls)
    if not by_dir:
        return {'error': f'该考试没有{direction or ""}方向的班级成绩'}

    # 取线缓存提到请求作用域（PERF-P04）：原先每个 _cells 调用各建一个空 cache，
    # 31 个展示行 → pick_layer 被重复算 ~1700 次（同键同值）
    line_cache = {}

    def _one_row(cls, d, rows):
        """一个展示行（全班 或 班内某组合）"""
        cells = _cells(data, rows, line_cache, subjects, layer_names, trimmed)
        # 每科成绩下方显示该班该科任课教师（v1.18.9.0）。
        # 总分列不放教师：班主任已在总分格下方展示（前端按 sub==总分 取 headteacher）。
        for sub in subjects:
            nm = teachers.get((cls, sub))
            if nm:
                cells[sub]['teacher'] = nm
        return {
            'grade': data.exam.grade, 'class_name': cls,
            'count': len(rows), 'direction': d,
            'class_type': snap_ct.get(cls) or live_ct.get(cls, ''),
            'headteacher': ht.get((data.exam.grade, cls), ''),
            'selections': _selection_stats(rows),
            'cells': cells,
        }

    def class_rows_by_dir(cls):
        """一个班的展示行：混组合班 = **班级合计行 + 各组合明细行**；纯组合班一行。

        2026-10-16 用户要求「那你算均分不也得按组合整嘛」——
        分数线本来就是按组合划的（物化生 496.5 / 物化地 427.5 / 物化政 467.5），
        上线人数已逐生按组合判定；但「去差均分」若把不同组合混在一起算，
        班级之间就没法比了：实测 exam 23 的 03班（物化地 35 + 物化政 13）混算 416.4，
        拆开是 408.7 与 438.4（差 30 分）；而 05班 是纯物化生（461.1）。

        同日追加（用户原话「如果班里面有不同的组合的，应该有个上线率的合计的那种」）：
        混组合班最前面再给一行**班级合计**——人数与上线率有效（逐生按各自组合取线后
        汇总），跨组合的均分没有可比性 → 与方向小计/全年级同款，均分位显示「—」。
        """
        rows = [r for r in data.total_rows if (r.class_name or '—') == cls]
        if not rows:
            return []
        # ⚠️ 2026-10-17 修：按**学生自己的方向**拆，不能用班级众数方向（dir_of[cls]）。
        # 实测 exam 33 的 01班 = 历史 46 + 物理 1：用班级方向会把那名物理生塞进历史块、
        # 按历史线判上线 → 本科合计比打印版/分层表少 1 人（跨模块对账查出来的）。
        # 现在「方向 × 组合」两维一起拆：方向决定归到哪个块、组合决定取哪条线。
        groups = {}
        for r in rows:
            groups.setdefault((r.direction or '', r.subject_selection or ''), []).append(r)
        out = []
        for d in sorted({k[0] for k in groups}, key=lambda x: _DIR_ORDER.get(x, 3)):
            combos = [(k[1], groups[k]) for k in groups if k[0] == d]
            combos.sort(key=lambda x: (-len(x[1]), x[0]))
            all_rows = [r for _s, v in combos for r in v]
            if len(combos) <= 1:
                out.append(_one_row(cls, d, all_rows))     # 纯组合班 / 未分科：一行
                continue
            total = _one_row(cls, d, all_rows)
            total['hide_avg'] = True
            out.append(total)
            for _sel, rs_rows in combos:
                row = _one_row(cls, d, rs_rows)
                row['is_combo'] = True
                row['headteacher'] = ''   # 班主任只写在班级合计行，组合明细行不重复
                out.append(row)
        return out

    def _combo_split(rows):
        """按选科组合把一批学生分组（人数降序、组合名归一）→ [(组合, rows), …]

        只含真实组合：'全科'/空（未分科）不计（与组合列 _selection_stats 同口径；
        先归一避免「史政地 / 史地政」同组合被拆成两行）。
        """
        from app.utils.helpers import normalize_subject
        by = {}
        for r in rows:
            s = normalize_subject((r.subject_selection or '').strip())
            by.setdefault(s, []).append(r)
        return [(s, rs) for s, rs in sorted(by.items(), key=lambda kv: (-len(kv[1]), kv[0]))
                if s and s != '全科']

    def _row_base(label, rows, direction='', **extra):
        """小计/总计类行的骨架：固定列 + 指标（取线口径见 `_cells`：逐生自身）。"""
        r = {
            'grade': data.exam.grade, 'class_name': label,
            'count': len(rows), 'direction': direction,
            'class_type': '全年级', 'headteacher': '',
            'selections': _selection_stats(rows),
            'cells': _cells(data, rows, line_cache, subjects, layer_names, trimmed),
        }
        r.update(extra)
        return r

    def _combo_rows(label, rows, direction='', **extra):
        """方向小计行按选科组合拆出的明细行（2026-10-16 用户要求）。

        用户原话「总分人数有分组合的显示，去差均分怎么没有呢」——组合列早就按组合
        列出人数，但去差均分/上线还是整个方向混算的：组合不同、各自的分数线不同，
        混算的均分没法跟各班同组合比（exam 23 物理方向 = 物化生 266 / 物化地 102 /
        物化政 43，三种组合的线差 30~70 分）。故合计行之后逐个组合出行，
        口径与班级行完全一致（同一 _cells，只是 rows 换成该组合的学生）。
        真实组合 ≤1 个（单组合方向 / 未分科）时不拆，避免把同一份数据重复一遍。
        **只服务方向小计**：全年级总计的 combo_rows 恒为 []（与各方向小计重复，见下）。
        """
        combos = _combo_split(rows)
        if len(combos) <= 1:
            return []
        return [_row_base(label, rs, direction=direction,
                          is_combo=True, **extra) for _, rs in combos]

    def subtotal_row(d):
        rows = [r for r in data.total_rows if (r.direction or '') == d]
        # 2026-10-16 用户口径：「物理全年级改成年级、历史全年级改成年级，全年级也改成年级」
        # —— 汇总行的「班级」列统一只写「年级」，方向由「方向」列体现（页面是彩色方向
        # 徽标、打印版是「物理方向 / 历史方向」分组行），不必在班级名里重复方向。
        label = '年级'
        row = _row_base(label, rows, direction=d, is_subtotal=True)
        row['combo_rows'] = _combo_rows(label, rows, direction=d, is_subtotal=True)
        # 2026-10-16 用户口径：「总共的组合都不同，我们按组合给的，你搞这个全年级的
        # 去差均分还有啥意思，搞个上线率还差不多」——跨组合（下面拆出了 ≥2 个组合明细行）
        # 的合计行**不给均分**：不同组合分数线不同，混算的均分没有可比性；
        # 合计行的价值在**按组合逐生判定的上线率**（右侧各线列）。
        # 单组合方向（如历史=史地政）合计行本身就是该组合口径，保留均分。
        row['hide_avg'] = bool(row['combo_rows'])
        return row

    # 先算好所有展示行，再**按行自己的方向**归组 —— 混方向班的少数派学生会回到自己方向，
    # 而不是被塞进班级主方向的块里（2026-10-17 修，见 class_rows_by_dir 注释）。
    _rows_all = [r for c in classes for r in class_rows_by_dir(c)]
    _rows_by_dir = {}
    for _r in _rows_all:
        _rows_by_dir.setdefault(_r['direction'], []).append(_r)

    # mode=split：存在方向（物理/历史）即按方向分组出小计；全科只有一组时不出小计
    dir_keys = [d for d in sorted(by_dir, key=lambda x: (_DIR_ORDER.get(x, 3), x)) if d]
    mode = 'split' if dir_keys else 'all'
    groups = []
    for d in sorted(by_dir, key=lambda x: (_DIR_ORDER.get(x, 3), x)):
        groups.append({
            'key': d or 'all',
            'label': (d + '方向') if d else '全科',
            'rows': _rows_by_dir.get(d, []),
            'subtotal': subtotal_row(d) if (mode == 'split' and d) else None,
        })

    # 全年级总计：逐生取各自方向层线；指定单方向时该组的「年级」小计已是全体，不再出
    grand = None
    if not direction:
        _all = list(data.total_rows)
        grand = _row_base('年级', _all, is_grand=True)
        # 2026-10-16 用户口径「数据有些重复了」：总计行的组合明细与各方向小计的**完全重复**
        # （每个组合只属于一个方向，同一批人同一套数字）→ 总计行不再重复拆组合，
        # 组合明细看各方向小计即可；总计行只给「人数 + 上线率」。
        grand['combo_rows'] = []
        # 跨组合总计不给混算均分（组合线不同）；未分科（全科）时只有一个口径，保留均分
        grand['hide_avg'] = len(_combo_split(_all)) > 1

    # 2026-10-17 用户口径：高一未让学生选科时，本场没有方向/组合 → 页面与打印版
    # 直接不显示这两列（原来固定出「方向 / 组合」两列，整列全是「—」白占宽度）。
    has_direction = any((r.direction or '').strip() for r in data.total_rows)
    has_selection = any((r.subject_selection or '').strip() not in ('', '全科')
                        for r in data.total_rows)
    return {
        'exam': {'id': data.exam.id, 'name': data.exam.name,
                 'date': data.exam.exam_date.strftime('%Y-%m-%d') if data.exam.exam_date else '',
                 'grade': data.exam.grade,
                 'grade_label': grade_level_label(data.exam.grade, data.exam.term)},
        # 本场是否有方向 / 选科组合（决定「方向」「组合」两列显隐）
        'has_direction': has_direction,
        'has_selection': has_selection,
        'mode': mode,
        # 列顺序：总分在科目之前（全局对比按「先看总分，再看各科」阅读）
        'subjects': [TOTAL_SUBJECT] + subjects,
        'subject_count': len(subjects),
        # 可选层名（高→低）+ 默认勾选（211/特控/本科）；页面用复选框控制显示列
        'layers': layers,
        'default_lines': default_lines,
        # 方向下拉用**完整**可选集（不随当前筛选变化，避免选完一个就丢另一个）
        'directions': [d for d in all_dirs if d],
        'groups': groups,
        'grand': grand,
        # 分批导入缺科提示（总分＝已导入科目合计，口径不完整）
        'partial': data.partial_import(),
    }
