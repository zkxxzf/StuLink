# StuLink v1.19.0 2026-10-10
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""成绩模块「考试上下文」导航解析

背景（用户口径）：成绩模块改为**考试上下文驱动** ——
  进入模块先看「历次考试列表」；点进某场考试后，左侧栏切换为该场考试的流程：
  考务安排 → 数据导入 → 成绩分析。
（2026-10-19：「成绩导入」升级为「数据导入」——成绩 / 分数线（划线分层）/
  考生名单 / 考场 / 场次 都在同一页导入。）

本模块只做一件事：**判断当前请求属于哪场考试的哪一段**，供 base.html 渲染悬浮流程栏。
只读、不写库；同一请求内用 `g` 缓存，避免重复查询。
2026-10-21：侧栏不再显示分步骤进度，上下文只保留考试 / 段位 / 成绩单入口判定。
"""
from __future__ import annotations

from flask import g, request

# 段位键：overview 概览 / affair 考务 / import 导入 / analysis 分析
SECTION_LABELS = {
    'overview': '考试概览',
    'affair': '考务安排',
    'import': '数据导入',
    'analysis': '成绩分析',
}

# endpoint → 段位（前缀匹配，先长后短）
_EP_SECTION = (
    ('grades.exam_ai_import', 'import'),
    ('grades.exam_import', 'import'),
    # 2026-10-19 用户口径：侧栏去掉「划线分层」独立项，划线编辑页归到「数据导入」段
    # （进入划线页时高亮数据导入；编辑页入口在导入页下方的划线状态条 / 考试详情）。
    ('grades.bands_page', 'import'),
    ('grades.exam_affair_go', 'affair'),
    ('grades.affair', 'affair'),
    ('grades.affairs', 'affair'),
    ('grades.exam_detail', 'overview'),
    ('grades.exam_scores_page', 'overview'),
    ('grades.score_update', 'overview'),
    ('grades.score_delete', 'overview'),
    ('grades.exam_recalc', 'overview'),
    ('grades.exam_update', 'overview'),
    ('grades.exam_delete', 'overview'),
    ('grades.index', 'analysis'),
    ('grades.honor', 'analysis'),
    ('grades.pivot_page', 'analysis'),
    ('grades.report_page', 'analysis'),
    ('grades.global_compare_page', 'analysis'),
    ('grades.student_query_page', 'analysis'),
)


def _section_of(endpoint):
    for prefix, sec in _EP_SECTION:
        if endpoint == prefix or endpoint.startswith(prefix):
            return sec
    return ''


def exam_nav():
    """当前请求的考试上下文；不在考试上下文时返回 None

    返回 dict：
      exam / exam_id / section / affair_id / affair / steps
    """
    if request.blueprint != 'grades':
        return None
    if getattr(g, '_exam_nav_done', False):
        return getattr(g, '_exam_nav_val', None)
    g._exam_nav_done = True
    g._exam_nav_val = None
    try:
        val = _resolve()
    except Exception:                      # 导航解析失败绝不能影响页面渲染
        val = None
    g._exam_nav_val = val
    return val


def _can_open_scoresheet(exam):
    """侧栏「考试概览·成绩单」入口是否该显示。

    与 `app/modules/grades/routes/exams.py::_scoresheet_scope` 同一份判断
    （`services/scoresheet.py`），避免"看得见点不进"的 403 死链。
    2026-10-15 用户口径：成绩单是学生成绩，任课教师/班主任也能看，但只看本班
    （仅任课映射的班不含总分口径，不计入）。任何异常一律按"不显示"处理。
    """
    try:
        from flask_login import current_user
        if not getattr(current_user, 'is_authenticated', False):
            return False
        if current_user.has_perm('grades.edit'):        # 有编辑权的人本来就能看全量
            return True
        from app.modules.grades.services import scoresheet
        _allowed, classes = scoresheet.visible(current_user, exam)
        return bool(classes)
    except Exception:                                   # noqa: BLE001  导航判定绝不影响渲染
        return False


def _resolve():
    from app.models.grades import Exam, ExamAffair
    ep = request.endpoint or ''
    sec = _section_of(ep)
    va = request.view_args or {}
    exam_id = va.get('exam_id')
    affair_id = va.get('aid')

    if affair_id:                          # 考务批次页：反查它绑定的考试
        affair = ExamAffair.query.get(affair_id)
        if affair is not None and affair.exam_id:
            exam_id = affair.exam_id
    if not exam_id:                        # 分析类页面：靠 ?exam=<id> 进入考试态
        raw = (request.args.get('exam') or '').strip()
        exam_id = int(raw) if raw.isdigit() else None
    if not exam_id:
        return None

    exam = Exam.query.get(int(exam_id))
    if exam is None:
        return None

    # 2026-10-21 用户口径：侧栏不再显示分步骤进度（4 步圆点 / 考务 7 环节圆点），
    # 只保留「当前页面高亮」，因此不再查考务批次、也不再计算 steps / affair_steps。
    return {'exam': exam, 'exam_id': exam.id, 'section': sec or 'overview',
            'can_scoresheet': _can_open_scoresheet(exam)}
