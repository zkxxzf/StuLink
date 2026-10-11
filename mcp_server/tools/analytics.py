# -*- coding: utf-8 -*-
"""统计分析工具：学科分析、分数段分布、分层上线统计、班级对比。

全部基于 stats_service.cached_exam_data(exam_id) 的全场缓存，
不额外引入慢查询；调用前统一做考试可见范围校验。
"""
from ..core import examdata, guards, respond, toolkit


def _subject_analysis(user, exam_id, direction):
    from app.modules.grades.services import stats_service
    exam = guards.exam_or_raise(exam_id)
    guards.exam_scope(user, exam)
    guards.tab_scope(user, 'subject')
    data = examdata.get_exam_data(exam.id)
    rows = stats_service.subject_table(data, direction or None)
    return {'exam_id': exam.id, 'exam_name': exam.name,
            'direction': direction or '全部',
            'subjects': respond.limit_rows(rows)}


def _score_segments(user, exam_id, class_name, by_rank):
    from app.modules.grades.services import stats_service
    exam = guards.exam_or_raise(exam_id)
    guards.exam_scope(user, exam)
    if class_name:
        # 指定班级 = 班级维度：受限账号只能查本人所辖班级
        guards.tab_scope(user, 'class')
        allowed = guards.visible_class_names(user, exam.grade)
        if allowed is not None and class_name not in allowed:
            raise PermissionError(f'无权查看班级 {class_name} 的分数段（仅限本人所辖班级）')
    else:
        guards.tab_scope(user, 'grade')
    data = examdata.get_exam_data(exam.id)
    segs, rows = stats_service.segment_table(data, class_name or None,
                                             None, bool(by_rank))
    return {
        'exam_id': exam.id, 'exam_name': exam.name,
        'mode': '按名次段' if by_rank else '按分数段',
        'segments': [{'label': s[0], 'low': s[1], 'high': s[2]} for s in (segs or [])],
        'distribution': respond.limit_rows(rows),
    }


def _layer_online(user, exam_id, subject):
    from app.modules.grades.services import pivot_service
    exam = guards.exam_or_raise(exam_id)
    guards.exam_scope(user, exam)
    guards.tab_scope(user, 'grade')
    data = examdata.get_exam_data(exam.id)
    stats = pivot_service.layer_pass_stats(data, subject or '总分')
    if not stats:
        raise ValueError('该考试未配置分层线（或所选科目无分层配置），'
                         '可先调用 get_exam_overview 查看科目，或在系统内配置分层')
    return {'exam_id': exam.id, 'exam_name': exam.name,
            'subject': stats.get('subject', subject),
            'layers': stats.get('layers'),
            'rows': respond.limit_rows(stats.get('rows')),
            'total': stats.get('total')}


def _compare(user, exam_id, class_names, direction):
    from app.modules.grades.services import compare_service
    exam = guards.exam_or_raise(exam_id)
    guards.exam_scope(user, exam)
    guards.tab_scope(user, 'compare')
    names = [c.strip() for c in (class_names or '').split(',') if c.strip()]
    # 班级可见性过滤：与网页端 api_compare_tab 同一口径（受限账号剔除未授权班级）
    allowed = guards.visible_class_names(user, exam.grade)
    if allowed is not None:
        names = [c for c in names if c in allowed]
    if len(names) < 2:
        raise ValueError('参与对比的班级不足 2 个（请用 list_classes 核对班级名与您的可见范围）')
    examdata.get_exam_data(exam.id)     # 先做缓存新鲜度检查（compare_tab 复用同一份缓存）
    result = compare_service.compare_tab(exam.id, names, direction or '')
    meta = result.get('meta') or {}
    if meta.get('empty'):
        raise ValueError(f'参与对比的班级不足 2 个（实际匹配到 {meta.get("selected") or 0} 个）；'
                         '请用 list_classes 核对班级名后重试')
    return {'exam': result.get('exam'), 'classes': result.get('classes'),
            'meta': meta,
            'tables': respond.limit_rows(list((result.get('tables') or {}).keys())),
            'detail': result.get('tables')}


def register(server):
    """把本模块的工具注册到 MCP 服务。"""

    @server.tool(name='get_subject_analysis',
                 description='查询某场考试的学科分析（各科参考人数、满分、均分、最高/最低、'
                             '标准差、及格率、优秀率）；direction 可选 "物理"/"历史"，留空为全部')
    async def get_subject_analysis(exam_id: int, direction: str = '') -> str:
        return await toolkit.db_call(lambda u: _subject_analysis(u, exam_id, direction),
                                     tool='get_subject_analysis',
                                     params={'exam_id': exam_id, 'direction': direction},
                                     perm='grades.view')

    @server.tool(name='get_exam_score_segments',
                 description='查询某场考试的分数段（或名次段）分布：各段人数与总人数；'
                             'class_name 可只看某班，by_rank=true 切换为名次段')
    async def get_exam_score_segments(exam_id: int, class_name: str = '',
                                      by_rank: bool = False) -> str:
        params = {'exam_id': exam_id, 'class_name': class_name, 'by_rank': by_rank}
        return await toolkit.db_call(
            lambda u: _score_segments(u, exam_id, class_name, by_rank),
            tool='get_exam_score_segments', params=params, perm='grades.view')

    @server.tool(name='get_layer_online_stats',
                 description='查询某场考试的分层上线统计：各班级在特控/本科等各层的上线人数与上线率；'
                             'subject 默认 "总分"，也可传单科名（如 "数学"）')
    async def get_layer_online_stats(exam_id: int, subject: str = '总分') -> str:
        return await toolkit.db_call(lambda u: _layer_online(u, exam_id, subject),
                                     tool='get_layer_online_stats',
                                     params={'exam_id': exam_id, 'subject': subject},
                                     perm='grades.view')

    @server.tool(name='compare_classes',
                 description='班级对比：传多个班级名（英文逗号分隔，如 "01班,02班,03班"），'
                             '返回概览/学科/上线率/优秀率/分数段/名次段/分层等多张对比表')
    async def compare_classes(exam_id: int, class_names: str = '',
                              direction: str = '') -> str:
        params = {'exam_id': exam_id, 'class_names': class_names, 'direction': direction}
        return await toolkit.db_call(
            lambda u: _compare(u, exam_id, class_names, direction),
            tool='compare_classes', params=params, perm='grades.view')