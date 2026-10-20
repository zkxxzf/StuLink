# -*- coding: utf-8 -*-
"""成绩工具的共享件：常量 + 视图裁剪 helper（**不含任何工具注册**）。

被 `tools/scores.py`（成绩查询）与 `tools/score_analysis.py`（成绩分析）
共用，避免同一份范围过滤 / 裁剪逻辑写两遍。

2026-10-21 新增 `view` 参数口径（省额度）：
  · `brief`（默认）——只给"够回答这个问题"的字段，明细走列式（cols+rows）；
  · `full`        ——给全字段（含各科均分、分层计数、年级横向明细等）；
工具数量本就不多（12 个），不靠拆工具省 token，靠**默认少给**省 token：
模型需要更多时显式加 view=full 即可，不必每次都为用不到的字段付费。
"""
from ..core import guards, score_guard

EXAM_FIELDS = ('id', 'name', 'grade', 'exam_kind', 'exam_type', 'exam_date',
               'term', 'score_mode', 'status')

# 精简视图下的考试清单字段（原 9 个字段里一半只在排查时才用得到）
BRIEF_EXAM_FIELDS = ('id', 'name', 'grade', 'exam_date', 'exam_type')

# 非行政班：班级字典里的特殊项，不是真实班级，不参与成绩统计
NON_CLASS_NAMES = ('不分班', '已转出', '离校')

# 场次类型：普通考试 / 限时练（限时练是独立库 timed.db，口径见 tools/_timed_views.py）
KIND_EXAM = 'exam'
KIND_TIMED = 'timed'
KIND_HINT = 'kind：exam=普通考试（默认）；timed=限时练'

# 班级汇总行：brief 保留的字段（去掉 std / layers / direction / 各科均分）
BRIEF_CLASS_KEYS = ('class_name', 'count', 'avg', 'max', 'min',
                    'class_rank', 'detail_visible')


def is_timed(kind):
    """是否查限时练（kind 传 timed / 限时练 都认）。"""
    return str(kind or '').strip().lower() in ('timed', '限时练', 'xl')


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


def brief(value):
    """是否精简视图（view 参数：brief / full，其它值按 brief 处理）。"""
    return str(value or 'brief').strip().lower() != 'full'


def exam_fields(view):
    """按视图挑考试的字段清单（brief 只留定位考试必需的四五个字段）。"""
    return BRIEF_EXAM_FIELDS if brief(view) else EXAM_FIELDS


def slim_classes(rows, view):
    """班级汇总行瘦身：brief 只留核心指标（去掉 std/layers/direction/各科均分）。"""
    if not brief(view):
        return rows
    if isinstance(rows, dict):          # limit_rows 截断后的形态，原样返回
        return rows
    return [{k: r[k] for k in BRIEF_CLASS_KEYS if k in r} for r in rows or []]


def note(view, short, long):
    """按视图给提示文案：brief 只留一句短的，full 才给完整口径说明。

    长文案一次几百字，逐次返回都要付一遍，是纯噪声——除非模型真需要按它办事。
    """
    return short if brief(view) else long


# 说明（2026-10-15）：原 `subject_avgs` / `apply_total_visibility` 两个 helper 已删除——
# 前者移入 `app/modules/grades/services/grade_context.py`（与 Web 端 AI 共用同一份）；
# 后者是"总分聚合不对任课教师开放"的旧口径，用户已明确"年级成绩分析结果可公开，
# 只有学生个人成绩不公开"，班级级总分聚合不再隐藏，隐藏逻辑只保留在逐生工具里。
