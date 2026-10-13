# -*- coding: utf-8 -*-
"""成绩查询工具（**全部只读**）：考试 / 班级 / 学生三个维度的取数。

权限模型（三条规则，取并集；详见 docs/MCP服务-成绩分析权限设计.md）：
  R1 任课教师：仅当同时任教该班所在年级与该班时可看该班成绩，且只看自己所教学科；
  R2 班主任（班型设置）：可看本班全部学科成绩；
  R3 年级长及以上：可看所在年级成绩（校级/管理员 = 全校）。
成绩分析类工具（学科分析 / 分数段 / 分层上线 / 班级对比）见 `score_analysis.py`；
共享常量与 helper 见 `_score_common.py`。本工具集**不含任何写工具**。

取数复用网页端同一套服务（stats_service / ExamData 缓存），
保证"AI 看到的数字"与网页端一致。
"""
from sqlalchemy import func

from app.extensions import db
from app.models.grades import TOTAL_SUBJECT, Exam, ExamScore

from ..core import examdata, guards, respond, score_guard, toolkit
from ._score_common import (EXAM_FIELDS, NON_CLASS_NAMES, apply_total_visibility,
                            exam_and_scope, filter_subjects, subject_avgs)


def _my_scope(user):
    return score_guard.resolve(user).describe()


def _list_exams(user, grade, limit):
    scope = score_guard.resolve(user)
    grades = scope.visible_grades()
    if grades is not None and not grades:
        return {'count': 0, 'exams': [],
                'note': '当前账号没有任何可见年级（既非任课教师、也不是班主任/年级长及以上）'}
    query = Exam.query
    if grades is not None:
        query = query.filter(Exam.grade.in_(sorted(grades)))
    if grade:
        query = query.filter(Exam.grade == grade)
    rows = query.order_by(Exam.exam_date.desc(), Exam.id.desc()).limit(limit).all()
    return {'count': len(rows),
            'visible_grades': sorted(grades) if grades is not None else '全部年级',
            'exams': [respond.pick(e, EXAM_FIELDS) for e in rows]}


def _list_classes(user, grade, exam_id):
    """列出账号可见范围内的班级。

    班级口径＝**考试内的班级**（`exam_score` 的"考试当时班级"），**不是学生花名册**：
    花名册的班级随时会变（转班 / 转出 / 不分班），而成绩分析跟的是考试当时的班级。
    另外"不分班 / 已转出 / 离校"属非行政班（班级字典中的特殊项），不参与成绩统计，一律剔除。

    exam_id 传 0 时，汇总账号可见年级内所有考试出现过的班级（附出现场数）。
    """
    if exam_id:
        exam, scope = exam_and_scope(user, exam_id)
        query = db.session.query(ExamScore.grade, ExamScore.class_name,
                                 func.count(func.distinct(ExamScore.student_no)))
        query = query.filter(ExamScore.exam_id == exam.id)
        count_key = 'student_count'
    else:
        scope = score_guard.resolve(user)
        grades = scope.visible_grades()
        if grades is not None and not grades:
            return {'count': 0, 'classes': [],
                    'note': '当前账号没有任何可见年级（既非任课教师、也不是班主任/年级长及以上）'}
        query = db.session.query(ExamScore.grade, ExamScore.class_name,
                                 func.count(func.distinct(ExamScore.exam_id)))
        if grades is not None:
            query = query.filter(ExamScore.grade.in_(sorted(grades)))
        count_key = 'exam_count'

    if grade:
        query = query.filter(ExamScore.grade == grade)
    rows = (query.filter(ExamScore.subject == TOTAL_SUBJECT)
            .group_by(ExamScore.grade, ExamScore.class_name)
            .order_by(ExamScore.grade.desc(), ExamScore.class_name).all())
    out = []
    for g, c, count in rows:
        if c in NON_CLASS_NAMES:            # 非行政班：不统计
            continue
        if not scope.can_view_class(g, c):
            continue
        subjects = scope.class_subjects(g, c)
        out.append({
            'grade': g, 'class_name': c, count_key: count,
            'visible_subjects': sorted(subjects) if subjects is not None else '全部学科',
            'total_visible': scope.class_total_visible(g, c),
        })
    return {'count': len(out), 'classes': respond.limit_rows(out),
            'note': '班级清单取自考试成绩（考试当时的班级），不是当前花名册；'
                    '"不分班 / 已转出 / 离校"不计入。'}


def _exam_overview(user, exam_id):
    exam, scope = exam_and_scope(user, exam_id)
    data = examdata.get_exam_data(exam.id)
    visible = scope.visible_classes(exam.grade)
    snaps = [s for s in data.snap.values()
             if visible is None or s['class_name'] in visible]
    allowed = scope.grade_subjects(exam.grade)
    imported = filter_subjects(data.imported_subjects, allowed)
    expected = filter_subjects(data.expected_subjects, allowed)
    missing = [s for s in expected if s not in imported]
    class_names = {s['class_name'] for s in snaps}
    total_ok = bool(class_names) and all(
        scope.class_total_visible(exam.grade, c) for c in class_names)
    totals = [s['score'] for s in snaps if isinstance(s.get('score'), (int, float))]
    overview = {
        'exam': respond.pick(exam, EXAM_FIELDS),
        'visible_classes': sorted(visible) if visible is not None else '全部班级',
        'visible_subjects': sorted(allowed) if allowed is not None else '全部学科',
        'student_count': len(snaps),
        'score_count': len(totals) if total_ok else None,
        'avg_total': round(sum(totals) / len(totals), 1) if (totals and total_ok) else None,
        'max_total': max(totals) if (totals and total_ok) else None,
        'min_total': min(totals) if (totals and total_ok) else None,
        'class_count': len(class_names),
        'directions': sorted({s['direction'] for s in snaps if s['direction']}),
        'imported_subjects': imported,
        'expected_subjects': expected,
        'partial_import': ({'imported': imported, 'expected': expected,
                            'missing': missing} if missing else None),
    }
    if not total_ok:
        overview['total_hidden'] = True
        overview['note'] = '任课教师仅可见所教学科成绩，总分口径已隐藏'
    return overview


def _class_scores(user, exam_id, class_name):
    from app.modules.grades.services import stats_service
    exam, scope = exam_and_scope(user, exam_id)
    if class_name:
        score_guard.require_class(scope, exam.grade, class_name)
    data = examdata.get_exam_data(exam.id)
    rows = stats_service.class_summary_table(data)
    visible = scope.visible_classes(exam.grade)
    if visible is not None:
        rows = [r for r in rows if r['class_name'] in visible]
    if class_name:
        rows = [r for r in rows if r['class_name'] == class_name]
        if not rows:
            raise ValueError(f'该考试没有班级 {class_name} 的成绩（可用 list_classes 核对班级名）')
    for row in rows:
        allowed = scope.class_subjects(exam.grade, row['class_name'])
        row['subject_avg'] = subject_avgs(data, row['class_name'], allowed)
        apply_total_visibility(row, scope, exam.grade, row['class_name'])
    return {'exam_id': exam.id, 'exam_name': exam.name, 'grade': exam.grade,
            'classes': respond.limit_rows(rows)}


def _class_score_detail(user, exam_id, class_name, subject):
    """某场考试某班的**逐生成绩明细**（班级层面的概况见 get_class_score_summary）。

    范围收敛：
      · 科目只返回账号在该班的可见学科（任课教师＝所教学科）；
      · 总分与排名仅对班主任及以上开放（任课教师只被授予单科）。
    subject 留空返回全部可见学科；传单科（如 "数学"）则只返回该科。
    """
    exam, scope = exam_and_scope(user, exam_id)
    if not class_name:
        raise ValueError('请指定班级名 class_name（如 "01班"；可用 list_classes 核对）')
    score_guard.require_class(scope, exam.grade, class_name)
    data = examdata.get_exam_data(exam.id)
    allowed = scope.class_subjects(exam.grade, class_name)
    subjects = filter_subjects(data.imported_subjects, allowed)
    picked = (subject or '').strip()
    if picked:
        if picked not in subjects:
            raise PermissionError(
                f'无权查看 {exam.grade} {class_name} 的「{picked}」成绩'
                '（任课教师仅能查看自己所教学科）')
        subjects = [picked]
    if not subjects:
        raise ValueError(f'该考试没有 {exam.grade} {class_name} 的可见学科成绩'
                         '（可用 get_exam_overview 查看该场已导入科目）')
    total_ok = scope.class_total_visible(exam.grade, class_name)

    rows = []
    for snap in data.totals_of(class_name=class_name):
        item = {'student_no': snap['no'], 'name': snap['name'],
                'direction': snap.get('direction'), 'selection': snap.get('selection')}
        item['scores'] = {name: data.subj.get((snap['no'], name))
                          for name in subjects
                          if data.subj.get((snap['no'], name)) is not None}
        if total_ok:
            item['total_score'] = snap.get('score')
            item['rank_class'] = snap.get('rank_class')
            item['rank_dir'] = snap.get('rank_dir')
        rows.append(item)
    if not rows:
        raise ValueError(f'该考试没有班级 {class_name} 的成绩（可用 list_classes 核对班级名）')
    if total_ok:      # 按总分降序；否则按学号
        rows.sort(key=lambda r: (-(r.get('total_score') or 0), r['student_no']))
    else:
        rows.sort(key=lambda r: r['student_no'])

    result = {'exam': respond.pick(exam, EXAM_FIELDS),
              'grade': exam.grade, 'class_name': class_name,
              'student_count': len(rows), 'visible_subjects': subjects,
              'total_visible': total_ok,
              'students': respond.limit_rows(rows)}
    if not total_ok:
        result['note'] = '任课教师仅可见所教学科成绩，总分与排名已隐藏'
    return result


def _student_scores(user, student_no, exam_id):
    """单个学生在指定考试的成绩；exam_id 传 0 返回该生有成绩的考试清单。"""
    student = guards.find_student(student_no)
    scope = score_guard.resolve(user)
    score_guard.require_student(scope, student)
    total_ok = scope.class_total_visible(student.grade, student.class_name)

    if not exam_id:
        rows = (ExamScore.query
                .filter_by(student_no=student_no, subject=TOTAL_SUBJECT)
                .order_by(ExamScore.exam_id.desc()).limit(50).all())
        exam_ids = [r.exam_id for r in rows]
        exams = {e.id: e for e in Exam.query.filter(Exam.id.in_(exam_ids)).all()} if exam_ids else {}
        grades = scope.visible_grades()
        items = []
        for r in rows:
            exam = exams.get(r.exam_id)
            if exam is None or (grades is not None and exam.grade not in grades):
                continue
            item = {'exam_id': r.exam_id, 'exam_name': exam.name, 'grade': exam.grade,
                    'exam_date': exam.exam_date.isoformat() if exam.exam_date else None}
            if total_ok:
                item.update({'total_score': r.score, 'rank_class': r.rank_class,
                             'rank_dir': r.rank_dir, 'move_rank': r.move_rank})
            items.append(item)
        out = {'student_no': student_no, 'name': student.name, 'grade': student.grade,
               'class_name': student.class_name, 'total_visible': total_ok,
               'count': len(items), 'exams': items}
        if not total_ok:
            out['note'] = '任课教师仅可见所教学科成绩，总分与排名已隐藏'
        return out

    exam, scope = exam_and_scope(user, exam_id)
    data = examdata.get_exam_data(exam.id)
    snap = data.snap.get(student_no)
    if snap is None:
        raise ValueError(f'该学生在考试 {exam.name} 中没有成绩记录')
    allowed = scope.class_subjects(student.grade, student.class_name)
    subjects = []
    for subject in filter_subjects(data.imported_subjects, allowed):
        value = data.subj.get((student_no, subject))
        if value is not None:
            subjects.append({'subject': subject, 'score': value})
    student_out = dict(snap)
    if not total_ok:
        for key in ('score', 'rank_class', 'rank_dir', 'move'):
            student_out.pop(key, None)
        student_out['total_hidden'] = True
        student_out['note'] = '任课教师仅可见所教学科成绩，总分与排名已隐藏'
    return {'exam': respond.pick(exam, EXAM_FIELDS),
            'visible_subjects': sorted(allowed) if allowed is not None else '全部学科',
            'student': student_out, 'subjects': subjects}


def _student_trend(user, student_no, subject, limit):
    student = guards.find_student(student_no)
    scope = score_guard.resolve(user)
    score_guard.require_student(scope, student)
    subj = (subject or TOTAL_SUBJECT).strip() or TOTAL_SUBJECT
    allowed = scope.class_subjects(student.grade, student.class_name)
    if allowed is not None and subj not in allowed:
        raise PermissionError(
            f'无权查看 {student.grade} {student.class_name} 的「{subj}」成绩'
            '（任课教师仅能查看自己所教学科）')
    if subj == TOTAL_SUBJECT and not scope.class_total_visible(student.grade,
                                                               student.class_name):
        raise PermissionError('总分不对任课教师开放（任课教师仅可见所教学科）')

    rows = (ExamScore.query.filter_by(student_no=student_no, subject=subj)
            .order_by(ExamScore.exam_id.desc()).limit(limit).all())
    exam_ids = [r.exam_id for r in rows]
    exams = {e.id: e for e in Exam.query.filter(Exam.id.in_(exam_ids)).all()} if exam_ids else {}
    grades = scope.visible_grades()
    items = []
    for r in rows:
        exam = exams.get(r.exam_id)
        if exam is None or (grades is not None and exam.grade not in grades):
            continue
        items.append({'exam_id': r.exam_id, 'exam_name': exam.name, 'grade': exam.grade,
                      'exam_date': exam.exam_date.isoformat() if exam.exam_date else None,
                      'score': r.score, 'rank_class': r.rank_class, 'rank_dir': r.rank_dir})
    items.reverse()      # 时间升序，便于看走势
    return {'student_no': student_no, 'name': student.name,
            'grade': student.grade, 'class_name': student.class_name,
            'subject': subj, 'count': len(items), 'trend': items}


# ---------------- 工具注册 ----------------

def register(server):
    """把本模块的只读成绩查询工具注册到 MCP 服务。"""

    @server.tool(name='list_my_score_scope',
                 description='查看当前账号在成绩分析中的可见范围（可见年级/班级/学科与授权原因），'
                             '只读；建议先调用它确认能查什么')
    async def list_my_score_scope() -> str:
        return await toolkit.db_call(_my_scope, tool='list_my_score_scope',
                                     perm='grades.view')

    @server.tool(name='list_exams',
                 description='列出账号可见范围内的考试（按考试日期倒序）；'
                             '可选 grade 如 "2025级"，limit 默认 20 最大 100')
    async def list_exams(grade: str = '', limit: int = 20) -> str:
        limit = max(1, min(int(limit or 20), 100))
        return await toolkit.db_call(lambda u: _list_exams(u, grade, limit),
                                     tool='list_exams',
                                     params={'grade': grade, 'limit': limit},
                                     perm='grades.view')

    @server.tool(name='list_classes',
                 description='列出账号可见范围内的班级及可看学科、是否可见总分。'
                             '班级口径是"考试当时的班级"（不是当前花名册），'
                             '"不分班/已转出/离校"不计入；'
                             '可选 exam_id 只看某场考试，或 grade 如 "2025级"')
    async def list_classes(grade: str = '', exam_id: int = 0) -> str:
        params = {'grade': grade, 'exam_id': exam_id}
        return await toolkit.db_call(lambda u: _list_classes(u, grade, exam_id),
                                     tool='list_classes', params=params,
                                     perm='grades.view')

    @server.tool(name='get_exam_overview',
                 description='查询某场考试在账号可见范围内的概览：参考人数、总分均分/最高/最低、'
                             '班级数、可见学科与导入情况')
    async def get_exam_overview(exam_id: int) -> str:
        return await toolkit.db_call(lambda u: _exam_overview(u, exam_id),
                                     tool='get_exam_overview',
                                     params={'exam_id': exam_id}, perm='grades.view')

    @server.tool(name='get_class_score_summary',
                 description='查询班级成绩概况（人数、总分均分/标准差/最高/最低、分层人数、各科均分）；'
                             'class_name 留空返回可见范围内的全部班级。任课教师只会看到自己所教学科')
    async def get_class_score_summary(exam_id: int, class_name: str = '') -> str:
        return await toolkit.db_call(lambda u: _class_scores(u, exam_id, class_name),
                                     tool='get_class_score_summary',
                                     params={'exam_id': exam_id, 'class_name': class_name},
                                     perm='grades.view')

    @server.tool(name='get_class_score_detail',
                 description='查询某场考试某班的**逐生成绩明细**（每个学生的各科分数；'
                             '班主任及以上另含总分与排名）；'
                             'subject 留空返回全部可见学科，传单科（如 "数学"）只看该科')
    async def get_class_score_detail(exam_id: int, class_name: str,
                                     subject: str = '') -> str:
        params = {'exam_id': exam_id, 'class_name': class_name, 'subject': subject}
        return await toolkit.db_call(
            lambda u: _class_score_detail(u, exam_id, class_name, subject),
            tool='get_class_score_detail', params=params, perm='grades.view')

    @server.tool(name='get_student_scores',
                 description='查询学生成绩：传 exam_id 返回该场各科分数与排名；'
                             'exam_id 传 0 则返回该生有成绩的历次考试清单')
    async def get_student_scores(student_no: str, exam_id: int = 0) -> str:
        return await toolkit.db_call(lambda u: _student_scores(u, student_no, exam_id),
                                     tool='get_student_scores',
                                     params={'student_no': student_no, 'exam_id': exam_id},
                                     perm='grades.view')

    @server.tool(name='get_student_score_trend',
                 description='查询学生某学科（默认"总分"）的历次成绩走势；limit 默认 8 最大 30')
    async def get_student_score_trend(student_no: str, subject: str = '总分',
                                      limit: int = 8) -> str:
        limit = max(1, min(int(limit or 8), 30))
        params = {'student_no': student_no, 'subject': subject, 'limit': limit}
        return await toolkit.db_call(lambda u: _student_trend(u, student_no, subject, limit),
                                     tool='get_student_score_trend', params=params,
                                     perm='grades.view')
