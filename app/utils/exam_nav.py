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

# 段位键：overview 概览 / affair 考务 / import 导入 / bands 划线 / analysis 分析
SECTION_LABELS = {
    'overview': '考试概览',
    'affair': '考务安排',
    'import': '成绩导入',
    'bands': '划线分层',
    'analysis': '成绩分析',
}

# v1.19.49.35 考务向导 7 个环节（/grades/affairs/<aid> 的页签 #t1~#t7）。
# 侧栏悬浮栏原先只画 4 个圆点（建考试/考务/导入/分析），与考务页真实的 7 个环节对不上；
# 这里给出 7 环节的标签与完成判定，供模板渲染「当前所处环节」高亮 —— 只亮当前这一步，
# 不是把前面的全部点亮（用户口径 2026-10-12）。
AFFAIR_STEPS = (
    {'key': 't1', 'label': '学生名单'},
    {'key': 't2', 'label': '考场设置'},
    {'key': 't3', 'label': '编排与考号生成'},
    {'key': 't4', 'label': '核对任课教师'},
    {'key': 't5', 'label': '考试时间表'},
    {'key': 't6', 'label': '编排监考表'},
    {'key': 't7', 'label': '导出'},
)

# endpoint → 段位（前缀匹配，先长后短）
_EP_SECTION = (
    ('grades.exam_ai_import', 'import'),
    ('grades.exam_import', 'import'),
    # v1.19.49.35 划线分层单独一段：原先归到 'import' 导致侧栏「成绩导入」「划线分层」同时高亮
    ('grades.bands_page', 'bands'),
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
    if affair is None:
        # v1.19.49.35：**任何**进入考试态的页面都要取本场考务批次。
        # 此前只在 sec == 'affair' 时查，导致成绩导入/划线分层/分数修改记录等页面里
        # affair 恒为 None → 侧栏「考务安排」永远挂「待做」、进度显示 3/4 步，
        # 而本场考务其实早已编排完成（有考场·座号·考号）。
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

    # v1.19.49.35 考务向导 7 环节：完成状态 + 当前所处环节（由 URL hash / 查询参数决定）。
    # 仅考务页（affair 段）需要，其他页为 []（侧栏仍按 4 步渲染）。
    affair_steps = []
    if sec == 'affair' and affair is not None:
        from app.models.grades import (AffairStudent, AffairRoom, AffairTeacher,
                                       AffairSchedule, AffairInvigilation)
        n_stu = AffairStudent.query.filter_by(affair_id=affair.id).count()
        n_room = AffairRoom.query.filter_by(affair_id=affair.id).count()
        n_arr = AffairStudent.query.filter_by(affair_id=affair.id).filter(
            AffairStudent.room_no.isnot(None)).count()
        n_num = AffairStudent.query.filter_by(affair_id=affair.id).filter(
            AffairStudent.exam_number.isnot(None)).count()
        done_map = {
            't1': n_stu > 0,
            't2': n_room > 0,
            't3': n_arr > 0 or n_num > 0,
            't4': AffairTeacher.query.filter_by(affair_id=affair.id).count() > 0,
            't5': AffairSchedule.query.filter_by(affair_id=affair.id).count() > 0,
            't6': AffairInvigilation.query.filter_by(affair_id=affair.id).count() > 0,
            't7': True,
        }
        # 当前环节：URL hash（#tN）优先，其次 ?step=tN；缺省第 1 步
        cur = (request.args.get('step') or '').strip().lower()
        url = request.url or ''
        for key in ('t1', 't2', 't3', 't4', 't5', 't6', 't7'):
            if ('#' + key) in url:
                cur = key
                break
        if cur not in done_map:
            cur = 't1'
        for s in AFFAIR_STEPS:
            affair_steps.append({'key': s['key'], 'label': s['label'],
                                 'done': done_map[s['key']],
                                 'current': s['key'] == cur})

    return {'exam': exam, 'exam_id': exam.id, 'section': sec or 'overview',
            'affair_id': (affair.id if affair else None),
            'affair': affair, 'steps': steps,
            'affair_steps': affair_steps,
            'can_scoresheet': _can_open_scoresheet(exam)}
