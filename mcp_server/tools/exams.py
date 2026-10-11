# -*- coding: utf-8 -*-
"""考试与成绩工具：考试清单、考试概览、学生成绩、班级成绩概况。

数据来源分两种：
  · stats_service.cached_exam_data(exam_id)：进程级缓存的全场分析数据（快、结构化好）；
  · ExamScore 明细：按学生维度直查（有覆盖索引，快）。
两者都先做数据范围校验（visible_grades / check_exam_visible），越界一律拒绝。
"""
from app.models.grades import TOTAL_SUBJECT, Exam, ExamScore

from ..core import examdata, guards, respond, toolkit


def _visible_exam_query(user):
    """按账号可见年级收窄考试查询（与网页端考试下拉同口径）。"""
    from app.modules.grades.services import scope
    query = Exam.query
    grades = scope.visible_grades(user)
    if grades is not None:
        query = query.filter(Exam.grade.in_(grades))
    return query


def _list_exams(user, grade, limit):
    query = _visible_exam_query(user)
    if grade:
        query = query.filter(Exam.grade == grade)
    rows = query.order_by(Exam.exam_date.desc()).limit(limit).all()
    fields = ('id', 'name', 'grade', 'exam_kind', 'exam_type', 'exam_date',
              'term', 'score_mode', 'status')
    return {'count': len(rows), 'exams': [respond.pick(e, fields) for e in rows]}


def _exam_overview(user, exam_id):
    exam = guards.exam_or_raise(exam_id)
    guards.exam_scope(user, exam)
    guards.tab_scope(user, 'grade')
    data = examdata.get_exam_data(exam.id)

    totals = [s.get('score') for s in data.snap.values()
              if isinstance(s.get('score'), (int, float))]
    overview = {
        'exam': respond.pick(exam, ('id', 'name', 'grade', 'exam_kind', 'exam_type',
                                    'exam_date', 'term', 'score_mode', 'status')),
        'student_count': len(data.snap),
        'score_count': len(totals),
        'avg_total': round(sum(totals) / len(totals), 2) if totals else None,
        'max_total': max(totals) if totals else None,
        'min_total': min(totals) if totals else None,
        'directions': data.directions,
        'class_count': len(data.classes or []),
        'imported_subjects': data.imported_subjects,
        'expected_subjects': data.expected_subjects,
        'partial_import': data.partial_import(),
    }
    return overview


def _class_scores(user, exam_id, class_name):
    from app.modules.grades.services import stats_service
    exam = guards.exam_or_raise(exam_id)
    guards.exam_scope(user, exam)
    guards.tab_scope(user, 'class')
    # 班级可见性过滤：受限账号（如班主任）只能看到本人所辖班级
    allowed = guards.visible_class_names(user, exam.grade)
    if allowed is not None and class_name and class_name not in allowed:
        raise PermissionError(f'无权查看班级 {class_name} 的成绩（仅限本人所辖班级）')
    data = examdata.get_exam_data(exam.id)
    summary = stats_service.class_summary_table(data)
    if allowed is not None:
        summary = [row for row in summary if row.get('class_name') in allowed]
    if class_name:
        summary = [row for row in summary if row.get('class_name') == class_name]
        if not summary:
            raise ValueError(f'该考试没有班级 {class_name} 的数据（可用 list_classes 核对班级名）')
    for row in summary:
        row['subject_avg'] = _subject_avgs(data, row.get('class_name'))
    return {'exam_id': exam.id, 'exam_name': exam.name,
            'classes': respond.limit_rows(summary)}


def _subject_avgs(data, class_name):
    """班级各科均分（基于全场缓存数据现算，避免额外查询）。"""
    numbers = [no for no, snap in data.snap.items()
               if snap.get('class_name') == class_name]
    result = []
    for subject in (data.imported_subjects or []):
        values = [data.subj.get((no, subject)) for no in numbers]
        values = [v for v in values if isinstance(v, (int, float))]
        result.append({'subject': subject, 'count': len(values),
                       'avg': round(sum(values) / len(values), 2) if values else None})
    return result


def _student_scores(user, student_no, exam_id):
    """单个学生在指定考试的成绩；exam_id 传 0 时返回该生有成绩的考试清单。"""
    guards.find_student(student_no)
    guards.student_scope(user, student_no)

    if not exam_id:
        rows = (ExamScore.query
                .filter_by(student_no=student_no, subject=TOTAL_SUBJECT)
                .order_by(ExamScore.exam_id.desc()).limit(50).all())
        exam_ids = [r.exam_id for r in rows]
        # 只返回账号可见年级的考试（越界场次静默跳过，不泄露考试存在性）
        exams = {e.id: e for e in _visible_exam_query(user).filter(
            Exam.id.in_(exam_ids)).all()} if exam_ids else {}
        items = []
        for r in rows:
            exam = exams.get(r.exam_id)
            if exam is None:
                continue
            items.append({'exam_id': r.exam_id,
                          'exam_name': exam.name,
                          'exam_date': exam.exam_date.isoformat() if exam.exam_date else None,
                          'total_score': r.score, 'rank_class': r.rank_class,
                          'rank_dir': r.rank_dir, 'move_rank': r.move_rank})
        return {'student_no': student_no, 'count': len(items), 'exams': items}

    exam = guards.exam_or_raise(exam_id)
    guards.exam_scope(user, exam)
    data = examdata.get_exam_data(exam.id)
    snap = data.snap.get(student_no)
    if snap is None:
        raise ValueError(f'该学生在考试 {exam.name} 中没有成绩记录')
    subjects = []
    for subject in (data.imported_subjects or []):
        score = data.subj.get((student_no, subject))
        if score is not None:
            subjects.append({'subject': subject, 'score': score})
    return {'exam': respond.pick(exam, ('id', 'name', 'grade', 'exam_date', 'score_mode')),
            'student': snap, 'subjects': subjects}


def register(server):
    """把本模块的工具注册到 MCP 服务。"""

    @server.tool(name='list_exams',
                 description='列出考试清单（按考试日期倒序）；可选传 grade 如 "2025级"，'
                             'limit 默认 20 最大 100')
    async def list_exams(grade: str = '', limit: int = 20) -> str:
        limit = max(1, min(int(limit or 20), 100))
        return await toolkit.db_call(lambda u: _list_exams(u, grade, limit),
                                     tool='list_exams',
                                     params={'grade': grade, 'limit': limit},
                                     perm='grades.view')

    @server.tool(name='get_exam_overview',
                 description='查询某场考试的概览：参考人数、总分均分/最高/最低、'
                             '方向与班级数、已导入/应导入科目')
    async def get_exam_overview(exam_id: int) -> str:
        return await toolkit.db_call(lambda u: _exam_overview(u, exam_id),
                                     tool='get_exam_overview',
                                     params={'exam_id': exam_id},
                                     perm='grades.view')

    @server.tool(name='get_class_score_summary',
                 description='查询某场考试的班级成绩概况（各班级人数、均分、标准差、'
                             '最高/最低、分层人数、各科均分）；class_name 留空返回全部班级')
    async def get_class_score_summary(exam_id: int, class_name: str = '') -> str:
        return await toolkit.db_call(lambda u: _class_scores(u, exam_id, class_name),
                                     tool='get_class_score_summary',
                                     params={'exam_id': exam_id, 'class_name': class_name},
                                     perm='grades.view')

    @server.tool(name='get_student_scores',
                 description='查询学生成绩：传 exam_id 返回该场各科分数与排名；'
                             'exam_id 传 0 则返回该生有成绩的历次考试清单')
    async def get_student_scores(student_no: str, exam_id: int = 0) -> str:
        return await toolkit.db_call(lambda u: _student_scores(u, student_no, exam_id),
                                     tool='get_student_scores',
                                     params={'student_no': student_no, 'exam_id': exam_id},
                                     perm='grades.view')
