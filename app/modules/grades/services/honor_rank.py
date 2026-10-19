# -*- coding: utf-8 -*-
"""表彰名单的取数与排名（`honor_service` 的内部实现，单向依赖 service → 本模块）。

核心规则：**并列不能漏** —— 同分同名次、后延跳号（1,1,3），取前 N 名时按
「组首位置」判定：组首进了前 N，该组同分者全部保留（第 20 名 3 人同分 → 3 人都在榜）。
"""
from app.models import ClassProfile
from app.models.grades import SUBJECTS

ELITE_TYPE = '卓越班'


def class_no(class_name):
    """班级排序键：01班 → 1（非数字班级排最后）。"""
    digits = ''.join(ch for ch in str(class_name or '') if ch.isdigit())
    return (0, int(digits)) if digits else (1, str(class_name or ''))


def rank_top(items, key, limit):
    """降序排名并取前 limit 名；同分同名次、后延跳号（1,1,3）。

    判定用**组首位置**（idx）而不是名次：组首进了前 limit，整组都进 ——
    这是「别漏了并列」的直接实现。
    """
    ordered = sorted(items, key=lambda r: (-(r.get(key) or 0),
                                           r.get('class_name') or '',
                                           str(r.get('no') or '')))
    out, idx, n = [], 0, len(ordered)
    while idx < n:
        score = ordered[idx].get(key)
        end = idx
        while end < n and ordered[end].get(key) == score:
            end += 1
        if idx >= limit:          # 组首已在前 limit 之外，后面分数更低
            break
        for j in range(idx, end):
            row = dict(ordered[j])
            row['rank'] = idx + 1     # 按位置跳号：2 人并列第 1 → 下一个是第 3 名
            out.append(row)
        idx = end
    return out


def rows_of(data):
    """ExamData → 榜单基础行（含各科成绩与班型快照）。"""
    rows = []
    for r in data.total_rows:
        if r.score is None:
            continue
        rows.append({
            'no': r.student_no,
            'name': r.student_name or '',
            'class_name': r.class_name or '',
            'class_type': r.class_type or '',
            'direction': r.direction or '',
            'selection': r.subject_selection or '',
            'total': r.score,
            'scores': {s: data.subj.get((r.student_no, s)) for s in SUBJECTS},
        })
    return rows


def fill_class_type(rows, grade):
    """班型优先用考试快照；快照为空的老考试回落班级档案（当前班型）。

    返回提示文案（无回退时为空串），页面据此说明「为什么老考试也有卓越班」。
    """
    if rows and all(r['class_type'] for r in rows):
        return ''
    try:
        prof = {p.class_name: (p.class_type or '') for p in
                ClassProfile.query.filter_by(grade=grade).all()}
    except Exception:                       # 班型只用于分组，取不到不影响榜单
        prof = {}
    filled = 0
    for r in rows:
        if not r['class_type']:
            r['class_type'] = prof.get(r['class_name'], '')
            if r['class_type']:
                filled += 1
    return ('本场成绩未存班型快照，卓越班按班级档案（当前班型）判定'
            if filled else '')


def subjects_of(data):
    """本场实际有成绩的科目（保持 SUBJECTS 顺序）。"""
    have = {s for (_, s) in data.subj if s in SUBJECTS}
    return [s for s in SUBJECTS if s in have]


def _subject_items(rows, subject):
    """某科的有效行（该科无成绩的行不参与排名）。"""
    items = []
    for r in rows:
        score = r['scores'].get(subject)
        if score is None:
            continue
        item = dict(r)
        item['score'] = score
        items.append(item)
    return items


def subject_board(rows, subjects, limit):
    """逐科榜（年级或班内）：每科前 limit 名，并列全列。"""
    out = []
    for subject in subjects:
        items = _subject_items(rows, subject)
        if not items:
            continue
        ranked = rank_top(items, 'score', limit)
        out.append({'subject': subject,
                    'top': max(r['score'] for r in items),
                    'count': len(items),
                    'tied': sum(1 for r in ranked if r['rank'] == 1),
                    'top_n': limit, 'rows': ranked})
    return out


def elite_board(rows, subjects, total_limit, subject_limit):
    """每个卓越班：总分前 total_limit + 各科前 subject_limit（班内排名）。"""
    buckets = {}
    for r in rows:
        if r['class_type'] == ELITE_TYPE and r['class_name']:
            buckets.setdefault(r['class_name'], []).append(r)
    out = []
    for class_name in sorted(buckets, key=class_no):
        items = buckets[class_name]
        out.append({'class_name': class_name,
                    'students': len(items),
                    'total': rank_top(items, 'total', total_limit),
                    'subjects': subject_board(items, subjects, subject_limit)})
    return out
