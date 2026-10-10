# StuLink v1.19.0 2026-10-10
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""成绩模块「考试上下文」导航解析

背景（用户口径）：成绩模块改为**考试上下文驱动** ——
  进入模块先看「历次考试列表」；点进某场考试后，左侧栏切换为该场考试的流程：
  考务安排 → 成绩导入 → 成绩分析。

本模块只做一件事：**判断当前请求属于哪场考试的哪一段**，供 base.html 渲染悬浮流程栏。
只读、不写库；同一请求内用 `g` 缓存，避免重复查询。
"""
from __future__ import annotations

from flask import g, request

# 段位键：overview 概览 / affair 考务 / import 导入 / analysis 分析
SECTION_LABELS = {
    'overview': '考试概览',
    'affair': '考务安排',
    'import': '成绩导入',
    'analysis': '成绩分析',
}

# endpoint → 段位（前缀匹配，先长后短）
_EP_SECTION = (
    ('grades.exam_ai_import', 'import'),
    ('grades.exam_import', 'import'),
    ('grades.bands_page', 'import'),          # 划线分层属“导入”环节
    ('grades.exam_affair_go', 'affair'),
    ('grades.affair', 'affair'),
    ('grades.affairs', 'affair'),
    ('grades.exam_detail', 'overview'),
    ('grades.exam_scores_page', 'overview'),
    ('grades.score_update', 'overview'),
    ('grades.score_delete', 'overview'),
    ('grades.exam_recalc', 'overview'),
    ('grades.exam_rename', 'overview'),
    ('grades.exam_delete', 'overview'),
    ('grades.index', 'analysis'),
    ('grades.pivot_page', 'analysis'),
    ('grades.report_page', 'analysis'),
    ('grades.global_compare_page', 'analysis'),
    ('grades.student_query_page', 'analysis'),
    ('grades.history', 'analysis'),
)


def _section_of(endpoint):
    for prefix, sec in _EP_SECTION:
        if endpoint == prefix or endpoint.startswith(prefix):
            return sec
    return ''


def exam_nav():
    """当前请求的考试上下文；不在考试上下文时返回 None

    返回 dict：
      exam / exam_id / section / affair_id / steps / next_step
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


def _resolve():
    from app.models.grades import Exam, ExamAffair, ExamBand
    ep = request.endpoint or ''
    sec = _section_of(ep)
    va = request.view_args or {}
    exam_id = va.get('exam_id')
    affair_id = va.get('aid')
    affair = None

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
    if affair is None and sec == 'affair':
        affair = ExamAffair.query.filter_by(exam_id=exam.id).first()

    # 四步进度（与考试详情页同口径：建考试 / 考务 / 导入 / 划线）
    imported = (exam.status == 'imported')
    banded = ExamBand.query.filter_by(exam_id=exam.id).count() > 0
    steps = [
        {'label': '建立考试', 'done': True, 'key': 'exam'},
        {'label': '考务安排', 'done': bool(affair), 'key': 'affair'},
        {'label': '成绩导入', 'done': imported, 'key': 'import'},
        {'label': '成绩分析', 'done': banded, 'key': 'analysis'},
    ]
    nxt = next((s for s in steps if not s['done']), None)
    return {'exam': exam, 'exam_id': exam.id, 'section': sec or 'overview',
            'affair_id': (affair.id if affair else None),
            'affair': affair, 'steps': steps, 'next_step': nxt}
