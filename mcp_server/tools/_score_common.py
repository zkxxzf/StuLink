# -*- coding: utf-8 -*-
"""成绩工具的共享件：常量 + 响应塑形 helper（**不含任何工具注册**）。

被 `tools/scores.py`（成绩查询）与 `tools/score_analysis.py`（成绩分析）共用，
避免同一份范围过滤逻辑写两遍。
"""
from ..core import guards, score_guard

EXAM_FIELDS = ('id', 'name', 'grade', 'exam_kind', 'exam_type', 'exam_date',
               'term', 'score_mode', 'status')

# 非行政班：班级字典里的特殊项，不是真实班级，不参与成绩统计
NON_CLASS_NAMES = ('不分班', '已转出', '离校')


def exam_and_scope(user, exam_id):
    """取考试并做年级级权限校验，返回 (exam, scope)。"""
    exam = guards.exam_or_raise(exam_id)
    scope = score_guard.require_exam(user, exam)
    return exam, scope


def filter_subjects(subjects, allowed):
    """allowed 为 None 时不过滤（全部学科），否则只保留可见学科。"""
    if allowed is None:
        return list(subjects or [])
    return [s for s in (subjects or []) if s in allowed]


def subject_avgs(data, class_name, allowed):
    """班级各科均分（只算可见学科）。"""
    out = []
    for subject in filter_subjects(data.imported_subjects, allowed):
        values = data.scores_of_subject(subject, class_name=class_name)
        out.append({'subject': subject, 'count': len(values),
                    'avg': round(sum(values) / len(values), 1) if values else None})
    return out


def apply_total_visibility(row, scope, grade, class_name):
    """任课教师只被授予所教学科：总分口径（均分/最高/最低/分层）不对其开放。"""
    if scope.class_total_visible(grade, class_name):
        return row
    row['total_hidden'] = True
    row['avg'] = row['std'] = row['max'] = row['min'] = None
    row['layers'] = {}
    row['note'] = '任课教师仅可见所教学科成绩，该班总分口径已隐藏'
    return row
