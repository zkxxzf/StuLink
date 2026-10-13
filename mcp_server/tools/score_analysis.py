# -*- coding: utf-8 -*-
"""成绩分析工具（**全部只读**）：学科分析 / 分数段 / 分层上线 / 班级对比。

全部基于 `stats_service.cached_exam_data(exam_id)` 的全场缓存（经 `core/examdata` 控制
新鲜度），不额外引入慢查询；调用前统一做考试与班级的可见范围校验。
考试 / 班级 / 学生维度的查询工具见 `scores.py`；共享常量与 helper 见 `_score_common.py`。
"""
from app.models.grades import TOTAL_SUBJECT

from ..core import examdata, respond, score_guard, toolkit
from ._score_common import (EXAM_FIELDS, apply_total_visibility, exam_and_scope,
                            subject_avgs)


def _subject_analysis(user, exam_id, direction):
    from app.modules.grades.services import stats_service
    exam, scope = exam_and_scope(user, exam_id)
    allowed = scope.grade_subjects(exam.grade)
    if allowed is not None and not allowed:
        raise PermissionError(
            '当前账号在该年级没有可见学科：年级级学科分析仅对年级长及以上、'
            '以及该年级的任课教师开放；班主任请用 get_class_score_summary 查看本班成绩')
    rows = stats_service.subject_table(examdata.get_exam_data(exam.id), direction or None)
    if allowed is not None:
        rows = [r for r in rows if r['subject'] in allowed]
    return {'exam_id': exam.id, 'exam_name': exam.name, 'direction': direction or '全部',
            'visible_subjects': sorted(allowed) if allowed is not None else '全部学科',
            'subjects': respond.limit_rows(rows)}


def _score_segments(user, exam_id, class_name, by_rank):
    from app.modules.grades.services import stats_service
    exam, scope = exam_and_scope(user, exam_id)
    if class_name:
        score_guard.require_class(scope, exam.grade, class_name)
    elif not (scope.full_school or exam.grade in scope.grades):
        raise PermissionError(
            '年级级分数段为全年级口径，仅对年级长及以上开放；'
            '请用 class_name 指定您任教或所辖的班级')
    data = examdata.get_exam_data(exam.id)
    segs, rows = stats_service.segment_table(data, class_name or None, None, bool(by_rank))
    return {'exam_id': exam.id, 'exam_name': exam.name,
            'class_name': class_name or '全年级',
            'mode': '按名次段' if by_rank else '按分数段',
            'segments': [{'label': s[0], 'low': s[1], 'high': s[2]} for s in (segs or [])],
            'distribution': respond.limit_rows(rows)}


def _layer_online(user, exam_id, subject):
    from app.modules.grades.services import pivot_service
    exam, scope = exam_and_scope(user, exam_id)
    if not (scope.full_school or exam.grade in scope.grades):
        raise PermissionError('分层上线统计为全年级口径，仅对年级长及以上开放')
    subj = (subject or TOTAL_SUBJECT).strip() or TOTAL_SUBJECT
    allowed = scope.grade_subjects(exam.grade)
    if allowed is not None and subj != TOTAL_SUBJECT and subj not in allowed:
        raise PermissionError(f'无权查看该年级「{subj}」的成绩（任课教师仅能查看所教学科）')
    stats = pivot_service.layer_pass_stats(examdata.get_exam_data(exam.id), subj)
    if not stats:
        raise ValueError('该考试未配置分层线（或所选科目无分层配置），'
                         '可先调用 get_exam_overview 查看科目，或在系统内配置分层')
    return {'exam_id': exam.id, 'exam_name': exam.name, 'subject': stats.get('subject', subj),
            'layers': stats.get('layers'),
            'rows': respond.limit_rows(stats.get('rows')), 'total': stats.get('total')}


def _compare(user, exam_id, class_names, direction):
    from app.modules.grades.services import stats_service
    exam, scope = exam_and_scope(user, exam_id)
    names, seen = [], set()
    for name in (class_names or '').replace('，', ',').split(','):
        name = name.strip()
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    if not names:
        raise ValueError('请提供至少 2 个班级名（英文逗号分隔，如 "01班,02班"）')
    allowed_names = [n for n in names if scope.can_view_class(exam.grade, n)]
    denied = [n for n in names if n not in allowed_names]
    if len(allowed_names) < 2:
        raise ValueError(f'可见范围内参与对比的班级不足 2 个（不可见：{denied or "无"}）；'
                         '请用 list_classes 核对班级名与您的可见范围')

    data = examdata.get_exam_data(exam.id)
    rows = [r for r in stats_service.class_summary_table(data, direction or None)
            if r['class_name'] in allowed_names]
    for row in rows:
        allowed = scope.class_subjects(exam.grade, row['class_name'])
        row['subject_avg'] = subject_avgs(data, row['class_name'], allowed)
        apply_total_visibility(row, scope, exam.grade, row['class_name'])
    return {'exam': respond.pick(exam, EXAM_FIELDS), 'direction': direction or '全部',
            'classes': respond.limit_rows(rows),
            'denied_classes': denied}


# ---------------- 工具注册 ----------------

def register(server):
    """把本模块的只读成绩分析工具注册到 MCP 服务。"""

    @server.tool(name='get_subject_analysis',
                 description='查询某场考试的年级学科分析（各科人数、满分、均分、最高/最低、'
                             '标准差、及格率、优秀率）；direction 可选 "物理"/"历史"')
    async def get_subject_analysis(exam_id: int, direction: str = '') -> str:
        return await toolkit.db_call(lambda u: _subject_analysis(u, exam_id, direction),
                                     tool='get_subject_analysis',
                                     params={'exam_id': exam_id, 'direction': direction},
                                     perm='grades.view')

    @server.tool(name='get_exam_score_segments',
                 description='查询分数段（或名次段）分布：各段人数与合计；'
                             'class_name 指定班级（班主任/任课教师必填），by_rank=true 切换名次段')
    async def get_exam_score_segments(exam_id: int, class_name: str = '',
                                      by_rank: bool = False) -> str:
        params = {'exam_id': exam_id, 'class_name': class_name, 'by_rank': by_rank}
        return await toolkit.db_call(
            lambda u: _score_segments(u, exam_id, class_name, by_rank),
            tool='get_exam_score_segments', params=params, perm='grades.view')

    @server.tool(name='get_layer_online_stats',
                 description='查询分层上线统计（各层上线人数与上线率，全年级口径，'
                             '仅年级长及以上可用）；subject 默认 "总分"')
    async def get_layer_online_stats(exam_id: int, subject: str = '总分') -> str:
        return await toolkit.db_call(lambda u: _layer_online(u, exam_id, subject),
                                     tool='get_layer_online_stats',
                                     params={'exam_id': exam_id, 'subject': subject},
                                     perm='grades.view')

    @server.tool(name='compare_classes',
                 description='班级对比：传多个班级名（英文逗号分隔，如 "01班,02班"），'
                             '返回各班人数、总分均分与各科均分；不可见的班级会被拒绝并列出')
    async def compare_classes(exam_id: int, class_names: str = '',
                              direction: str = '') -> str:
        params = {'exam_id': exam_id, 'class_names': class_names, 'direction': direction}
        return await toolkit.db_call(lambda u: _compare(u, exam_id, class_names, direction),
                                     tool='compare_classes', params=params,
                                     perm='grades.view')
