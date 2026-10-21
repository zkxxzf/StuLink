# -*- coding: utf-8 -*-
"""限时练的单科分析（**不含工具注册**）：各班去差均分 + 名次区间人数。

口径全部来自网页端 `timed_analysis.subject_analysis`（同一份实现，不重写）：
  · **去差均分**＝按班型名额剔除该科末尾 N 人后重算（与全局对比同一套
    `TRIM_RULES`，卓越班 2 人、其它班型不去差）；
  · **名次区间**＝限时练专属「名次分层」模板的**累积门槛**（层「前N」＝
    全年级该科名次 ≤ N 的人数），且按实考人数自适应裁剪（<100 人按 10 人一组）；
  · 「不分班 / 未分班」这类无班级归属的成绩不参与班级统计。

限时练**没有分数线、没有分数段**——网页端分析页也没有，所以
`get_exam_score_segments` / `get_layer_online_stats` 对限时练直接提示改用本模块。
"""
from ..core import respond, score_guard
from ._score_common import brief, note
from ._timed_views import timed_exam


def _visible_subjects(scope, grade):
    """该账号在该年级「至少有一处可见」的科目集合；None ＝ 全部学科。

    年级长及以上 / 校级 / 管理员 → 全部；班主任（有本班）→ 本班全科；
    纯任课教师 → 所教的年级学科。
    """
    if scope.full_school or grade in scope.grades:
        return None
    if any(g == grade for g, _c in scope.classes):
        return None
    return {s for g, s in scope.teach if g == grade}


def _pick_subject(exam, subject, user=None):
    """确定分析科目：未指定时取本场第一科（按 SUBJECTS 顺序）。

    2026-10-21：可选科目先按成绩可见范围收窄 —— 任课教师只能分析自己所教科。
    """
    from app.models.grades import SUBJECTS
    from app.modules.grades.services import timed_service
    subjects = timed_service.subjects_of(exam.id)
    if not subjects:
        raise ValueError(f'限时练 {exam.name} 还没有导入任何科目成绩')
    if user is not None:
        allowed = _visible_subjects(score_guard.resolve(user), exam.grade)
        if allowed is not None:
            subjects = [s for s in subjects if s in allowed]
            if not subjects:
                raise PermissionError(
                    f'无权查看 {exam.grade} 的限时练学科成绩'
                    '（任课教师仅能查看自己所教学科）')
    picked = (subject or '').strip()
    if picked and picked not in subjects:
        raise ValueError(f'该场次没有「{picked}」成绩（已导入科目：'
                         + '、'.join(subjects) + '）')
    if not picked:
        picked = next((s for s in SUBJECTS if s in subjects), subjects[0])
    return picked


def _bands_of(row):
    """[{name,label,count}] → {层名: 人数}（累积门槛口径）。"""
    return {b['name']: b['count'] for b in (row.get('bands') or [])}


def _load(exam, subject, user=None):
    """取本场某科的完整分析结果（网页端同一份，按可见范围收口）。"""
    from app.modules.grades.services import timed_analysis as ta
    from app.modules.grades.services import timed_service
    picked = _pick_subject(exam, subject, user)
    return picked, ta.subject_analysis(
        exam, picked, timed_service.current_template(exam), user=user)


def subject_analysis(user, exam_id, subject, class_name, view):
    """单科分析：各班去差均分 + 各名次区间人数 + 全年级合计行。"""
    exam = timed_exam(user, exam_id)
    scope = score_guard.resolve(user)
    if class_name:
        score_guard.require_class(scope, exam.grade, class_name)
    picked, result = _load(exam, subject, user)
    rows = result['rows']
    if class_name:
        rows = [r for r in rows if r['class_name'] == class_name]
        if not rows:
            raise ValueError(f'该场次没有班级 {class_name} 的「{picked}」成绩'
                             '（可用 list_classes(kind="timed") 核对班级名）')
    layers = [x['name'] for x in result['layers']]
    out = {'exam_id': exam.id, 'exam_name': exam.name, 'grade': exam.grade,
           'subject': picked, 'layers': layers,
           'student_count': result['student_count'], 'class_count': len(rows)}
    if brief(view):
        flat = [{'class': r['class_name'], 'n': r['count'],
                 'trim_avg': r['trim_avg'], **_bands_of(r)} for r in rows]
        out['classes'] = respond.table_view(
            flat, ['class', 'n', 'trim_avg'] + layers)
    else:
        # 复制后再删字段：缓存里的是**共享对象**，直接 pop 会污染网页分析页的图表数据
        full_rows = []
        for r in rows:
            item = dict(r)
            item.pop('kept', None)
            item.pop('stack', None)
            full_rows.append(item)
        out['classes'] = full_rows
        out['layer_avgs'] = result['layer_avgs']
    total = result['total']
    if brief(view):
        out['grade_total'] = {'n': total['count'], 'trim_avg': total['trim_avg']}
    else:
        out['grade_total'] = {k: v for k, v in total.items() if k != 'kept'}
    out['note'] = note(view, '层＝累积门槛（前N＝名次≤N）；trim_avg＝去差均分',
                       '各班 trim_avg＝去差均分（按班型名额剔除该科末尾 N 人，'
                       '与全局对比同规则）；各层人数＝累积门槛（层「前N」统计'
                       '全年级该科名次 ≤ N 的人数，一人可同时计入多层）。'
                       '"不分班/未分班"不参与统计。')
    return out


def compare(user, exam_id, class_names, subject, view):
    """班级对比：按某科比较各班去差均分与名次区间人数。"""
    names = [n.strip() for n in (class_names or '').replace('，', ',').split(',')
             if n.strip()]
    if len(names) < 2:
        raise ValueError('请提供至少 2 个班级名（英文逗号分隔，如 "01班,02班"）')
    exam = timed_exam(user, exam_id)
    scope = score_guard.resolve(user)
    for name in names:
        score_guard.require_class(scope, exam.grade, name)
    picked, result = _load(exam, subject, user)
    have = {r['class_name'] for r in result['rows']}
    order = {n: i for i, n in enumerate(names)}
    rows = sorted((r for r in result['rows'] if r['class_name'] in order),
                  key=lambda r: order[r['class_name']])
    if len(rows) < 2:
        raise ValueError(f'参与对比的班级不足 2 个（本场没有：'
                         + '、'.join(n for n in names if n not in have) + '）')
    if brief(view):
        flat = [{'class': r['class_name'], 'n': r['count'],
                 'trim_avg': r['trim_avg'], **_bands_of(r)} for r in rows]
        body = respond.table_view(
            flat, ['class', 'n', 'trim_avg'] + [x['name'] for x in result['layers']])
    else:
        body = []
        for r in rows:
            item = dict(r)
            item.pop('kept', None)
            item.pop('stack', None)
            body.append(item)
    return {'exam_id': exam.id, 'exam_name': exam.name, 'subject': picked,
            'classes': body,
            'unknown_classes': [n for n in names if n not in have]}
