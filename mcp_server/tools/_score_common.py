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


# 说明（2026-10-15）：原 `subject_avgs` / `apply_total_visibility` 两个 helper 已删除——
# 前者移入 `app/modules/grades/services/grade_context.py`（与 Web 端 AI 共用同一份）；
# 后者是"总分聚合不对任课教师开放"的旧口径，用户已明确"年级成绩分析结果可公开，
# 只有学生个人成绩不公开"，班级级总分聚合不再隐藏，隐藏逻辑只保留在逐生工具里。
