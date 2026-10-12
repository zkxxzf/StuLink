# StuLink v1.19.43.2 2026-10-10
# 全局对比指标引擎：班级 × 学科「去差均分 + 各分数线」宽表
#
# 口径与 report_service / report_pivot_service 完全一致（复用其公共函数）：
#  - 去差均分：按班型剔除各班总分末尾 N 人（卓越班去 2 人）后再均分；
#  - 各分数线 = 该方向总分各层的**同名单科线**（高→低，逐方向独立划线）；
#    默认展示 211线/特控线/本科线，其余层（清北/985…）由页面复选框控制显隐；
#  - 上线人数按「该班方向」的层线判定；小计/总计行并入学生重算（人数加权），
#    不是各班均分的简单平均；
#  - 班型/方向/班级一律读成绩行快照（考试当时），缺快照回落当前 ClassProfile。
#
# 形态自适应：
#  - 全科（高一未分科）：所有班方向为空 → 单组，只出「全年级」总计行；
#  - 分科（物理/历史方向并存）：按方向分组的班级段 + 各方向「○○全年级」小计行 +
#    末尾「全年级」总计行（跨方向逐生取各自方向层线判定）。
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


def _cells(data, rows, direction, subjects, layer_names, trimmed):
    """一组学生（rows = 总分行）的 总分 + 各科 指标。

    layer_names：本场要统计的层名（高→低）。每科输出 {count, trim_avg, lines}，
    其中 lines = {层名: {'n': 上线人数, 'rate': 上线率}}；该组在该层无对应线
    （如某科未划此层）时为 None（前端显示「—」，与「0 人上线」区分）。

    direction 语义（关键）：
      - 传具体方向（'物理'/'历史'/'')：整组按该方向层线判定（班级行、方向小计行）；
      - 传 None：每个学生按其**自身方向**取线（跨方向的全年级总计行）。
    """
    line_cache = {}

    def _line(d, sub, name):
        key = (d, sub, name)
        if key not in line_cache:
            line_cache[key] = rs.pick_layer(data, d, sub, name)
        return line_cache[key]

    out = {}
    for sub in [TOTAL_SUBJECT] + list(subjects):
        n = 0
        vals = []
        hits = {name: 0 for name in layer_names}
        has = {name: False for name in layer_names}
        for r in rows:
            sc = r.score if sub == TOTAL_SUBJECT else data.subj.get((r.student_no, sub))
            if sc is None:
                continue
            n += 1
            if r.student_no not in trimmed:
                vals.append(sc)
            d = direction if direction is not None else (r.direction or '')
            for name in layer_names:
                ln = _line(d, sub, name)
                if ln is None:
                    continue
                has[name] = True
                if sc >= ln[1]:
                    hits[name] += 1
        out[sub] = {
            'count': n,
            'trim_avg': rs._avg(vals),
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
        return {'error': '总分尚未划线，请先在「划线分层」设置特优/本科线'}
    layer_names = layers
    default_lines = default_line_names(layers)

    # 科目列：本场实际有成绩的科目按系统顺序（全科=9 科；分科=各方向并集，缺科显示 —）
    subjects = list(data.imported_subjects)
    trimmed = rs.trimmed_nos(data)
    # v1.19.43.2 班主任取**本场考试当时**的快照（与任课教师同一机制）：
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
    # v1.19.43.2 方向下拉的**完整**可选集：先按全部班级算出各方向，再按 direction
    # 过滤生成展示分组。否则选定某方向后返回值里只剩该方向，前端据此重建下拉
    # 就会丢掉另一个方向（用户反馈的 bug）。
    all_dirs = sorted({_row_dir(data, cls) for cls in classes},
                      key=lambda x: (_DIR_ORDER.get(x, 3), x))
    by_dir = {}
    for cls in classes:
        d = _row_dir(data, cls)
        if direction and d != direction:
            continue
        by_dir.setdefault(d, []).append(cls)
    if not by_dir:
        return {'error': f'该考试没有{direction or ""}方向的班级成绩'}

    def class_row(cls):
        rows = [r for r in data.total_rows if (r.class_name or '—') == cls]
        d = _row_dir(data, cls)
        cells = _cells(data, rows, d, subjects, layer_names, trimmed)
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
            'cells': cells,
        }

    def subtotal_row(d):
        rows = [r for r in data.total_rows if (r.direction or '') == d]
        return {
            'grade': data.exam.grade, 'class_name': (d + '全年级') if d else '全年级',
            'count': len(rows), 'direction': d,
            'class_type': '全年级', 'headteacher': '',
            'is_subtotal': True,
            'cells': _cells(data, rows, d, subjects, layer_names, trimmed),
        }

    # mode=split：存在方向（物理/历史）即按方向分组出小计；全科只有一组时不出小计
    dir_keys = [d for d in sorted(by_dir, key=lambda x: (_DIR_ORDER.get(x, 3), x)) if d]
    mode = 'split' if dir_keys else 'all'
    groups = []
    for d in sorted(by_dir, key=lambda x: (_DIR_ORDER.get(x, 3), x)):
        groups.append({
            'key': d or 'all',
            'label': (d + '方向') if d else '全科',
            'rows': [class_row(c) for c in by_dir[d]],
            'subtotal': subtotal_row(d) if (mode == 'split' and d) else None,
        })

    # 全年级总计：分科时逐生取各自方向层线；指定单方向时该组的「○○全年级」已是全体，不再出
    grand = None
    if not direction:
        grand = {
            'grade': data.exam.grade, 'class_name': '全年级',
            'count': len(data.total_rows), 'direction': '',
            'class_type': '全年级', 'headteacher': '',
            'is_grand': True,
            'cells': _cells(data, list(data.total_rows),
                            None if mode == 'split' else '',
                            subjects, layer_names, trimmed),
        }

    return {
        'exam': {'id': data.exam.id, 'name': data.exam.name,
                 'date': data.exam.exam_date.strftime('%Y-%m-%d') if data.exam.exam_date else '',
                 'grade': data.exam.grade,
                 'grade_label': grade_level_label(data.exam.grade, data.exam.term)},
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
