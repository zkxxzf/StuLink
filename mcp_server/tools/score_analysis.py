# -*- coding: utf-8 -*-
"""成绩分析工具注册（**全部只读**）：学科分析 / 分数段 / 分层上线 / 班级对比。

本文件只做 **参数校验 → kind 分流**，聚合逻辑在别处：
  · 普通考试 —— 复用网页端 `app.modules.grades.services.stats_service` /
    `pivot_service` / `grade_context`（同口径，避免因两份实现导致数字漂移）；
  · 限时练   —— `tools/_timed_analysis.py`（同样调网页端 `timed_analysis`）。

**口径（2026-10-15 用户裁决）**：本模块全部是**聚合层**属"年级成绩分析结果"
→ 公开（能看该考试的账号都能看全年级）；不公开的只有**学生个人成绩**，
逐生明细仍按班级/学科收敛（见 `tools/scores.py` 的 get_class_score_detail）。
"""
from app.models.grades import TOTAL_SUBJECT

from ..core import examdata, respond, toolkit
from . import _timed_analysis
from ._score_common import (KIND_HINT, brief, exam_and_scope, is_timed,
                            slim_classes)

_ARGS = 'view: brief(默认,精简)/full；' + KIND_HINT

_NOT_TIMED = ('限时练没有分数段/名次段统计（网页端分析页也没有）'
              '——请改用 get_subject_analysis(kind="timed", subject="某科")：'
              '它给各班去差均分与各名次区间人数')

_NOT_LINES = ('限时练没有分数线（只有名次区间），且也不对外提供分层上线率'
              '——请改用 get_subject_analysis(kind="timed", subject="某科")')


def _subject_analysis(user, exam_id, direction, view):
    """年级学科分析（各科人数/满分/均分/最高最低/标准差/及格率/优秀率）。

    聚合口径，公开：任课教师也能看到年级各科总表（不再按所教学科收窄）。
    """
    from app.modules.grades.services import stats_service
    exam, scope = exam_and_scope(user, exam_id)
    rows = stats_service.subject_table(examdata.get_exam_data(exam.id),
                                       direction or None)
    out = {'exam_id': exam.id, 'exam_name': exam.name,
           'direction': direction or '全部'}
    if brief(view):
        out['subjects'] = respond.table_view(
            rows, ['subject', 'count', 'avg', 'max', 'min', 'pass_rate',
                   'good_rate'])
    else:
        out['subject_scope'] = '全部学科（年级学科分析为聚合口径，公开）'
        out['subjects'] = rows
    return out


def _score_segments(user, exam_id, class_name, by_rank, view):
    """分数段 / 名次段分布（年级级或某班）；聚合口径，公开。"""
    from app.modules.grades.services import stats_service
    exam, scope = exam_and_scope(user, exam_id)
    data = examdata.get_exam_data(exam.id)
    if class_name and class_name not in set(data.classes):
        raise ValueError(f'该考试没有班级 {class_name} 的成绩（可用 get_class_score_summary 核对）')
    segs, rows = stats_service.segment_table(data, class_name or None, None,
                                             bool(by_rank))
    out = {'exam_id': exam.id, 'exam_name': exam.name,
           'class_name': class_name or '全年级',
           'mode': '按名次段' if by_rank else '按分数段'}
    if brief(view):
        # 列式：一列一个班（含"合计"），行＝各段
        peers = sorted({c for r in rows for c in r['counts']})
        flat = [{'seg': r['label'], **{c: r['counts'].get(c) for c in peers},
                 'total': r['total']} for r in rows]
        out['distribution'] = respond.table_view(
            flat, ['seg'] + peers + ['total'])
    else:
        out['segments'] = [{'label': s[0], 'low': s[1], 'high': s[2]}
                           for s in (segs or [])]
        out['distribution'] = respond.limit_rows(rows)
    return out


def _layer_online(user, exam_id, subject, view):
    """分层上线统计（各层分数线 + 上线人数与上线率）。

    公开口径（聚合层）；逐生名单仍按班/科收敛。带各层分数线本身——网页端
    「划线分层」对所有老师只读展示，AI 端同口径，避免出现"网页能看线、
    问 AI 却说拿不到"。
    """
    from app.modules.grades.services import pivot_service, stats_service
    exam, scope = exam_and_scope(user, exam_id)
    subj = (subject or TOTAL_SUBJECT).strip() or TOTAL_SUBJECT
    stats = pivot_service.layer_pass_stats(examdata.get_exam_data(exam.id), subj)
    if not stats:
        raise ValueError('该考试未配置分层线（或所选科目无分层配置），'
                         '可先调用 get_exam_overview 查看科目，或在系统内配置分层')
    lines = stats_service.band_lines(exam.id, subj)
    out = {'exam_id': exam.id, 'exam_name': exam.name,
           'subject': stats.get('subject', subj)}
    if brief(view):
        # 线可能有十几条（方向×组合×层），brief 只留定位必需的四列
        out['lines'] = respond.table_view(
            lines, ['scope', 'layer', 'lower'])
    else:
        out['lines'] = lines
        out['note'] = '组合线优先、方向线兜底'
    out['rows'] = respond.limit_rows(stats.get('rows'))
    out['total'] = stats.get('total')
    return out


def _compare(user, exam_id, class_names, direction, view):
    """班级对比（各班人数、总分均分、各科均分、班级位次）＋ 年级基准。

    班级级聚合属年级成绩分析结果 → 公开，参与对比的班级不限于"自己班"。
    """
    from app.modules.grades.services import grade_context
    exam, scope = exam_and_scope(user, exam_id)
    names, seen = [], set()
    for name in (class_names or '').replace('，', ',').split(','):
        name = name.strip()
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    if not names:
        raise ValueError('请提供至少 2 个班级名（英文逗号分隔，如 "01班,02班"）')

    data = examdata.get_exam_data(exam.id)
    exists = set(data.classes)
    unknown = [n for n in names if n not in exists]
    names = [n for n in names if n in exists]
    if len(names) < 2:
        raise ValueError(f'参与对比的班级不足 2 个（本场考试没有：{unknown or "无"}）；'
                         '可用 get_class_score_summary 查看本场全部班级')

    out = grade_context.class_rows(scope, exam, data, direction=direction or None)
    order = {n: i for i, n in enumerate(names)}
    rows = sorted((r for r in out['classes'] if r['class_name'] in names),
                  key=lambda r: order.get(r['class_name'], 99))
    return {'exam_id': exam.id, 'direction': direction or '全部',
            'grade_baseline': out['grade'],
            'classes': respond.limit_rows(slim_classes(rows, view)),
            'unknown_classes': unknown}


def register(server):
    """把本模块的只读成绩分析工具注册到 MCP 服务。"""

    @server.tool(name='get_subject_analysis',
                 description='某场考试的学科分析（人数、满分、均分、最高/最低、'
                             '及格率、优秀率）。普通考试＝年级各科总表；'
                             '限时练＝**某科的班级分析**（各班去差均分 + 名次区间人数，'
                             '需用 subject 指定科目）。' + _ARGS)
    async def get_subject_analysis(exam_id: int, direction: str = '',
                                   subject: str = '', kind: str = 'exam',
                                   view: str = 'brief') -> str:
        params = {'exam_id': exam_id, 'direction': direction,
                  'subject': subject, 'kind': kind, 'view': view}
        if is_timed(kind):
            fn = lambda u: _timed_analysis.subject_analysis(            # noqa: E731
                u, exam_id, subject, '', view)
        else:
            fn = lambda u: _subject_analysis(u, exam_id, direction, view)  # noqa: E731
        return await toolkit.db_call(fn, tool='get_subject_analysis',
                                     params=params, perm='grades.view')

    @server.tool(name='get_exam_score_segments',
                 description='分数段（by_rank=true 切名次段）分布：各段人数。'
                             'class_name 留空为全年级。**仅普通考试**，'
                             '限时练请用 get_subject_analysis。' + _ARGS)
    async def get_exam_score_segments(exam_id: int, class_name: str = '',
                                      by_rank: bool = False,
                                      kind: str = 'exam',
                                      view: str = 'brief') -> str:
        params = {'exam_id': exam_id, 'class_name': class_name,
                  'by_rank': by_rank, 'kind': kind, 'view': view}
        if is_timed(kind):
            return toolkit.fail(_NOT_TIMED)
        return await toolkit.db_call(
            lambda u: _score_segments(u, exam_id, class_name, by_rank, view),
            tool='get_exam_score_segments', params=params, perm='grades.view')

    @server.tool(name='get_layer_online_stats',
                 description='分层上线统计：各层分数线 + 上线人数与上线率（全年级）。'
                             '**仅普通考试**，限时练请用 get_subject_analysis。' + _ARGS)
    async def get_layer_online_stats(exam_id: int, subject: str = '总分',
                                     kind: str = 'exam',
                                     view: str = 'brief') -> str:
        params = {'exam_id': exam_id, 'subject': subject, 'kind': kind,
                  'view': view}
        if is_timed(kind):
            return toolkit.fail(_NOT_LINES)
        return await toolkit.db_call(
            lambda u: _layer_online(u, exam_id, subject, view),
            tool='get_layer_online_stats', params=params, perm='grades.view')

    @server.tool(name='compare_classes',
                 description='班级对比：传多个班级名（英文逗号分隔），给各班人数/'
                             '总分均分/各科均分/班级位次 + 年级基准。'
                             '限时练需 subject 指定科目（按该科比去差均分与名次区间）。'
                             + _ARGS)
    async def compare_classes(exam_id: int, class_names: str = '',
                              direction: str = '', subject: str = '',
                              kind: str = 'exam', view: str = 'brief') -> str:
        params = {'exam_id': exam_id, 'class_names': class_names,
                  'direction': direction, 'subject': subject, 'kind': kind,
                  'view': view}
        if is_timed(kind):
            fn = lambda u: _timed_analysis.compare(                     # noqa: E731
                u, exam_id, class_names, subject, view)
        else:
            fn = lambda u: _compare(u, exam_id, class_names, direction, view)  # noqa: E731
        return await toolkit.db_call(fn, tool='compare_classes',
                                     params=params, perm='grades.view')
