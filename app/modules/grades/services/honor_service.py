# -*- coding: utf-8 -*-
"""表彰名单服务（2026-10-18）：年级总分榜 / 卓越班榜 / 单科榜。

为什么单独一份：表彰会要的是「人 + 具体分数」的榜单，与分析页的聚合口径不同。
取数与排名规则见 `honor_rank`（并列一并保留），本模块只负责**分组与对外结构**。

分组口径（用户 2026-10-18 定案）：
- 有多个选科组合 → 每个组合前 20；
- 否则有多个方向 → 物理前 30 / 历史前 20；
- 否则 → 全年级前 30。
卓越班：每个卓越班总分前 10 + 每班各科前 10；年级单科各前 10。
班型优先取考试快照（exam_scores.class_type），老考试快照为空时回落班级档案。
"""
from app.modules.grades.services import honor_rank as hr
from app.modules.grades.services import stats_service as st

TOP_COMBO = 20          # 选科组合口径：每组前 20
TOP_WHOLE = 30          # 全年级 / 无组合口径：前 30
TOP_DIR = {'物理': 30, '历史': 20}   # 方向口径（用户 2026-10-18）
TOP_ELITE_TOTAL = 10    # 每个卓越班总分前 10
TOP_SUBJECT = 10        # 年级单科 / 卓越班单科前 10
DIR_ORDER = ('物理', '历史')


def _pick_mode(rows, want):
    """分组维度：显式指定优先，否则自动取最细一级。"""
    if want in ('combo', 'direction', 'whole'):
        return want
    combos = {r['selection'] for r in rows if r['selection']}
    dirs = {r['direction'] for r in rows if r['direction']}
    if len(combos) > 1:
        return 'combo'
    if len(dirs) > 1:
        return 'direction'
    return 'whole'


def _mode_options(rows):
    """页面可选的分组维度（不支持的维度不出现，避免选到空榜）。"""
    combos = {r['selection'] for r in rows if r['selection']}
    dirs = {r['direction'] for r in rows if r['direction']}
    opts = []
    if len(combos) > 1:
        opts.append({'key': 'combo', 'label': '按选科组合',
                     'desc': f'各组合前 {TOP_COMBO} 名'})
    if len(dirs) > 1:
        opts.append({'key': 'direction', 'label': '按方向',
                     'desc': f'物理前 {TOP_DIR["物理"]} / 历史前 {TOP_DIR["历史"]}'})
    opts.append({'key': 'whole', 'label': '全年级',
                 'desc': f'全年级前 {TOP_WHOLE} 名'})
    return opts


def _bucket_groups(rows, mode):
    """按维度分桶 → 每组一张榜。"""
    if mode == 'combo':
        buckets = {}
        for r in rows:
            buckets.setdefault(r['selection'] or '未分组合', []).append(r)
        order = sorted(buckets, key=lambda k: (-len(buckets[k]), k))
        groups = []
        for key in order:
            items = buckets[key]
            dirs = {r['direction'] for r in items if r['direction']}
            note = ('、'.join(d + '类' for d in DIR_ORDER if d in dirs)
                    + ' · ' if dirs else '') + f'{len(items)} 人参考'
            groups.append({'key': key, 'label': key, 'note': note,
                           'top_n': TOP_COMBO, 'size': len(items),
                           'rows': hr.rank_top(items, 'total', TOP_COMBO)})
        return groups
    if mode == 'direction':
        buckets = {}
        for r in rows:
            buckets.setdefault(r['direction'] or '未分方向', []).append(r)
        order = [d for d in DIR_ORDER if d in buckets]
        order += [d for d in buckets if d not in DIR_ORDER]
        groups = []
        for key in order:
            items = buckets[key]
            n = TOP_DIR.get(key, TOP_WHOLE)
            groups.append({'key': key, 'label': key + '类', 'top_n': n,
                           'size': len(items), 'note': f'{len(items)} 人参考',
                           'rows': hr.rank_top(items, 'total', n)})
        return groups
    items = list(rows)
    return [{'key': '', 'label': '全年级', 'top_n': TOP_WHOLE,
             'size': len(items), 'note': f'{len(items)} 人参考',
             'rows': hr.rank_top(items, 'total', TOP_WHOLE)}]


def build(exam_id, mode=''):
    """构建一场考试的完整表彰名单（数据来自共享 ExamData 缓存）。"""
    data = st.cached_exam_data(exam_id)
    exam = data.exam
    rows = hr.rows_of(data)
    gap = hr.fill_class_type(rows, exam.grade)
    subjects = hr.subjects_of(data)
    mode = _pick_mode(rows, mode)
    elite_classes = hr.elite_board(rows, subjects, TOP_ELITE_TOTAL, TOP_SUBJECT)
    return {
        'exam': {
            'id': exam.id, 'name': exam.name, 'grade': exam.grade,
            'date': exam.exam_date.strftime('%Y-%m-%d') if exam.exam_date else '',
            'stage': exam.stage_label(), 'kind': exam.kind_label(),
            'status': exam.status,
        },
        'mode': mode,
        'mode_options': _mode_options(rows),
        'groups': _bucket_groups(rows, mode),
        'elite': {'top_n': TOP_ELITE_TOTAL, 'total_limit': TOP_ELITE_TOTAL,
                  'subject_limit': TOP_SUBJECT, 'classes': elite_classes},
        'subjects': subjects,
        'subject_board': hr.subject_board(rows, subjects, TOP_SUBJECT),
        'class_names': sorted({r['class_name'] for r in rows if r['class_name']},
                              key=hr.class_no),
        'stats': {
            'students': len(rows),
            'classes': len({r['class_name'] for r in rows if r['class_name']}),
            'elite_classes': len(elite_classes),
            'subjects': len(subjects),
        },
        'gap': gap,
    }
