# -*- coding: utf-8 -*-
"""成绩分析工具（**全部只读**）：学科分析 / 分数段 / 分层上线 / 班级对比。

全部基于 `stats_service.cached_exam_data(exam_id)` 的全场缓存（经 `core/examdata` 控制
新鲜度），不额外引入慢查询；调用前统一做考试与班级的可见范围校验。

**口径（2026-10-15 用户裁决）**：本模块全部是**聚合层**（年级/班级/学科统计、分布、上线率），
属"年级成绩分析结果"→ **公开**（能看该考试的账号都能看全年级）；**不公开的只有学生个人
成绩**，逐生明细仍按班级/学科收敛，见 `scores.py::_class_score_detail` 与
`app/modules/grades/services/grade_context.py`（年级维度塑形唯一实现）。

考试 / 班级 / 学生维度的查询工具见 `scores.py`；共享常量与 helper 见 `_score_common.py`。
"""
from app.models.grades import TOTAL_SUBJECT

from ..core import examdata, respond, toolkit
from ._score_common import EXAM_FIELDS, exam_and_scope


def _subject_analysis(user, exam_id, direction):
    """年级学科分析（各科人数/满分/均分/最高最低/标准差/及格率/优秀率）。

    聚合口径，**公开**（2026-10-15 用户口径：年级成绩分析结果可公开，不公开的只有
    学生个人成绩）：不再按所教学科收窄 —— 与此前不同，任课教师也能看到年级各科总表。
    """
    from app.modules.grades.services import stats_service
    exam, scope = exam_and_scope(user, exam_id)
    rows = stats_service.subject_table(examdata.get_exam_data(exam.id), direction or None)
    return {'exam_id': exam.id, 'exam_name': exam.name, 'direction': direction or '全部',
            'subject_scope': '全部学科（年级学科分析为聚合口径，公开）',
            'subjects': respond.limit_rows(rows)}


def _score_segments(user, exam_id, class_name, by_rank):
    """分数段 / 名次段分布。

    2026-10-15 用户口径：年级级分布（各段人数）属聚合结果，**公开**——不再仅限年级长及以上；
    传 class_name 时按该班统计（班级存在于本场考试即可，聚合口径公开）。
    """
    from app.modules.grades.services import stats_service
    exam, scope = exam_and_scope(user, exam_id)
    data = examdata.get_exam_data(exam.id)
    if class_name and class_name not in set(data.classes):
        raise ValueError(f'该考试没有班级 {class_name} 的成绩（可用 get_class_score_summary 核对）')
    segs, rows = stats_service.segment_table(data, class_name or None, None, bool(by_rank))
    return {'exam_id': exam.id, 'exam_name': exam.name,
            'class_name': class_name or '全年级',
            'mode': '按名次段' if by_rank else '按分数段',
            'segments': [{'label': s[0], 'low': s[1], 'high': s[2]} for s in (segs or [])],
            'distribution': respond.limit_rows(rows)}


def _layer_online(user, exam_id, subject):
    """分层上线统计（各层上线人数与上线率）。

    2026-10-15 用户口径：属年级成绩分析结果 → **公开**（不再仅限年级长及以上，
    也不再按所教学科收窄）；逐生名单仍按班/科收敛。
    """
    from app.modules.grades.services import pivot_service
    exam, scope = exam_and_scope(user, exam_id)
    subj = (subject or TOTAL_SUBJECT).strip() or TOTAL_SUBJECT
    stats = pivot_service.layer_pass_stats(examdata.get_exam_data(exam.id), subj)
    if not stats:
        raise ValueError('该考试未配置分层线（或所选科目无分层配置），'
                         '可先调用 get_exam_overview 查看科目，或在系统内配置分层')
    return {'exam_id': exam.id, 'exam_name': exam.name, 'subject': stats.get('subject', subj),
            'layers': stats.get('layers'),
            'rows': respond.limit_rows(stats.get('rows')), 'total': stats.get('total')}


def _compare(user, exam_id, class_names, direction):
    """班级对比（各班人数、总分均分、各科均分、班级位次）＋ 年级基准。

    2026-10-15 用户口径：班级级聚合属年级成绩分析结果 → **公开**，
    参与对比的班级不再限于"自己班"（只校验班级在本场考试内存在）。
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
    rows = [r for r in out['classes'] if r['class_name'] in names]
    order = {n: i for i, n in enumerate(names)}
    rows.sort(key=lambda r: order.get(r['class_name'], 99))
    return {'exam': respond.pick(exam, EXAM_FIELDS), 'direction': direction or '全部',
            'grade_baseline': out['grade'],
            'classes': respond.limit_rows(rows),
            'unknown_classes': unknown}


# ---------------- 工具注册 ----------------

def register(server):
    """把本模块的只读成绩分析工具注册到 MCP 服务。"""

    @server.tool(name='get_subject_analysis',
                 description='查询某场考试的**年级学科分析**（各科人数、满分、均分、最高/最低、'
                             '标准差、及格率、优秀率）——聚合口径，公开；'
                             'direction 可选 "物理"/"历史"')
    async def get_subject_analysis(exam_id: int, direction: str = '') -> str:
        return await toolkit.db_call(lambda u: _subject_analysis(u, exam_id, direction),
                                     tool='get_subject_analysis',
                                     params={'exam_id': exam_id, 'direction': direction},
                                     perm='grades.view')

    @server.tool(name='get_exam_score_segments',
                 description='查询分数段（或名次段）分布：各段人数与合计 —— 聚合口径，公开；'
                             'class_name 留空为全年级，传班级名则看该班（by_rank=true 切名次段）')
    async def get_exam_score_segments(exam_id: int, class_name: str = '',
                                      by_rank: bool = False) -> str:
        params = {'exam_id': exam_id, 'class_name': class_name, 'by_rank': by_rank}
        return await toolkit.db_call(
            lambda u: _score_segments(u, exam_id, class_name, by_rank),
            tool='get_exam_score_segments', params=params, perm='grades.view')

    @server.tool(name='get_layer_online_stats',
                 description='查询分层上线统计（各层上线人数与上线率，全年级口径）——'
                             '聚合口径，公开；subject 默认 "总分"')
    async def get_layer_online_stats(exam_id: int, subject: str = '总分') -> str:
        return await toolkit.db_call(lambda u: _layer_online(u, exam_id, subject),
                                     tool='get_layer_online_stats',
                                     params={'exam_id': exam_id, 'subject': subject},
                                     perm='grades.view')

    @server.tool(name='compare_classes',
                 description='班级对比：传多个班级名（英文逗号分隔，如 "01班,02班"），'
                             '返回各班人数、总分均分、各科均分与班级位次＋年级基准 —— '
                             '班级级聚合公开，任何班（只要在本场考试内）都可对比')
    async def compare_classes(exam_id: int, class_names: str = '',
                              direction: str = '') -> str:
        params = {'exam_id': exam_id, 'class_names': class_names, 'direction': direction}
        return await toolkit.db_call(lambda u: _compare(u, exam_id, class_names, direction),
                                     tool='compare_classes', params=params,
                                     perm='grades.view')
