# -*- coding: utf-8 -*-
"""成绩查询工具注册（**全部只读**）：考试 / 班级 / 学生三个维度的取数。

本文件只做三件事：**参数校验 → 按 kind 分流 → 交给 `_exam_views` /
`_timed_views` 取数**。业务逻辑一律不写在这里：
  · 普通考试 —— `tools/_exam_views.py`（主库 grades.db 的 exams / exam_scores）；
  · 限时练   —— `tools/_timed_views.py`（独立库 timed.db，**无总分行、
                单科追加、名次＝该科全年级名次**）。

2026-10-21「省额度」：统一两个控制参数，工具数量刻意**不增加**（工具越多，
每轮对话都要付一遍 tools/list 的 schema 钱）：
  · `view`：brief（默认）/ full —— 默认只给够用的字段，明细走列式；
  · `kind`：exam（默认）/ timed —— 同一批工具查两种考试，不新开一套工具。
"""
from ..core import toolkit
from . import _exam_views, _timed_views
from ._score_common import KIND_HINT, is_timed

# 每个工具描述尾部统一带的参数说明（schema 常驻，越短越好）
_ARGS = 'view: brief(默认,精简)/full；' + KIND_HINT


def register(server):
    """把本模块的只读成绩查询工具注册到 MCP 服务。"""

    @server.tool(name='list_my_score_scope',
                 description='查看当前账号在成绩分析中的可见范围（年级/班级/学科与授权原因）；'
                             '建议先调用它确认能查什么')
    async def list_my_score_scope() -> str:
        def _run(user):
            scope = _exam_views._my_scope(user)
            scope['timed_visible_grades'] = _timed_views.visible_grades(user)
            return scope
        return await toolkit.db_call(_run, tool='list_my_score_scope',
                                     perm='grades.view')

    @server.tool(name='list_exams',
                 description='列出账号可见范围内的考试（日期倒序）。'
                             'grade 如 "2025级"；limit 默认 20 最大 100。' + _ARGS)
    async def list_exams(grade: str = '', limit: int = 20, kind: str = 'exam',
                         view: str = 'brief') -> str:
        limit = max(1, min(int(limit or 20), 100))
        params = {'grade': grade, 'limit': limit, 'kind': kind, 'view': view}
        fn = (_timed_views.list_exams if is_timed(kind)
              else _exam_views._list_exams)
        return await toolkit.db_call(
            lambda u: fn(u, grade, limit, view), tool='list_exams',
            params=params, perm='grades.view')

    @server.tool(name='list_classes',
                 description='列出账号可见范围内的班级（口径＝考试当时的班级，'
                             '"不分班/已转出/离校"不计入）。'
                             'exam_id 只看某场，或 grade 如 "2025级"。' + _ARGS)
    async def list_classes(grade: str = '', exam_id: int = 0, kind: str = 'exam',
                           view: str = 'brief') -> str:
        params = {'grade': grade, 'exam_id': exam_id, 'kind': kind,
                  'view': view}
        if is_timed(kind):
            fn = lambda u: _timed_views.list_classes(u, exam_id, view)  # noqa: E731
        else:
            fn = lambda u: _exam_views._list_classes(u, grade, exam_id, view)  # noqa: E731
        return await toolkit.db_call(fn, tool='list_classes', params=params,
                                     perm='grades.view')

    @server.tool(name='get_exam_overview',
                 description='某场考试的概览：参考人数、总分均分/最高/最低、班级数、'
                             '已导入科目与可见范围。' + _ARGS)
    async def get_exam_overview(exam_id: int, kind: str = 'exam',
                                view: str = 'brief') -> str:
        params = {'exam_id': exam_id, 'kind': kind, 'view': view}
        if is_timed(kind):
            fn = lambda u: _timed_views.overview(u, exam_id, view)      # noqa: E731
        else:
            fn = lambda u: _exam_views._exam_overview(u, exam_id, view)  # noqa: E731
        return await toolkit.db_call(fn, tool='get_exam_overview',
                                     params=params, perm='grades.view')

    @server.tool(name='get_class_score_summary',
                 description='年级各班成绩汇总（人数、总分均分/最高/最低、班级位次）'
                             '＋年级基准；"年级成绩分析结果"属公开口径，返回本场全部班级。'
                             '每行 detail_visible 指出能否下钻逐生。'
                             'class_name 留空＝全年级。' + _ARGS)
    async def get_class_score_summary(exam_id: int, class_name: str = '',
                                      kind: str = 'exam',
                                      view: str = 'brief') -> str:
        params = {'exam_id': exam_id, 'class_name': class_name, 'kind': kind,
                  'view': view}
        if is_timed(kind):
            fn = lambda u: _timed_views.class_summary(                  # noqa: E731
                u, exam_id, class_name, view, 0)
        else:
            fn = lambda u: _exam_views._class_scores(                   # noqa: E731
                u, exam_id, class_name, view)
        return await toolkit.db_call(fn, tool='get_class_score_summary',
                                     params=params, perm='grades.view')

    @server.tool(name='get_class_score_detail',
                 description='某场考试某班的逐生成绩明细。班主任及以上含总分与排名；'
                             '任课教师只见所教学科，另给每人的总分年级名次 rank_g。'
                             'subject 留空＝全部可见学科。'
                             'top：只看前 N 名（按总分/名次，0=全部）。' + _ARGS)
    async def get_class_score_detail(exam_id: int, class_name: str,
                                     subject: str = '', kind: str = 'exam',
                                     view: str = 'brief',
                                     top: int = 0) -> str:
        params = {'exam_id': exam_id, 'class_name': class_name,
                  'subject': subject, 'kind': kind, 'view': view,
                  'top': top}
        if is_timed(kind):
            fn = lambda u: _timed_views.subject_detail_rows(            # noqa: E731
                u, exam_id, class_name, subject, view, top)
        else:
            fn = lambda u: _exam_views._class_score_detail(             # noqa: E731
                u, exam_id, class_name, subject, view, top)
        return await toolkit.db_call(fn, tool='get_class_score_detail',
                                     params=params, perm='grades.view')

    @server.tool(name='get_student_scores',
                 description='查询单个学生的成绩：传 exam_id 给该场各科分数与排名；'
                             'exam_id=0 给该生有成绩的场次清单。' + _ARGS)
    async def get_student_scores(student_no: str, exam_id: int = 0,
                                 kind: str = 'exam',
                                 view: str = 'brief') -> str:
        params = {'student_no': student_no, 'exam_id': exam_id, 'kind': kind,
                  'view': view}
        fn = (_timed_views.student_scores if is_timed(kind)
              else _exam_views._student_scores)
        return await toolkit.db_call(
            lambda u: fn(u, student_no, exam_id, view),
            tool='get_student_scores', params=params, perm='grades.view')

    @server.tool(name='get_student_score_trend',
                 description='查询学生某科的历次成绩走势（默认"总分"）；'
                             'limit 默认 8 最大 30。'
                             '限时练返回 [考试日期, T分] 序列（T=10Z+50）。' + _ARGS)
    async def get_student_score_trend(student_no: str, subject: str = '总分',
                                      limit: int = 8, kind: str = 'exam',
                                      view: str = 'brief') -> str:
        limit = max(1, min(int(limit or 8), 30))
        params = {'student_no': student_no, 'subject': subject,
                  'limit': limit, 'kind': kind, 'view': view}
        fn = (_timed_views.student_trend if is_timed(kind)
              else _exam_views._student_trend)
        return await toolkit.db_call(
            lambda u: fn(u, student_no, subject, limit, view),
            tool='get_student_score_trend', params=params, perm='grades.view')
