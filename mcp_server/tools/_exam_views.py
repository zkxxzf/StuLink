# -*- coding: utf-8 -*-
"""普通考试的取数实现（**不含工具注册**）：考试 / 班级 / 学生三维度的查询。

注册与参数校验在 `tools/scores.py`；时限练（独立库 timed.db）的实现见
`tools/_timed_views.py`。两者都对同一批工具暴露，靠 `kind` 参数分流。

权限模型（三条规则取并集，详见 docs/mcp/MCP服务-成绩分析权限设计.md）：
  R1 任课教师：任教某年级某科 → 该年级全部班的该科成绩可见，另给每名学生
               的总分年级名次（名次可见、总分分数不可见）；
  R2 班主任：本班全部学科；R3 年级长及以上：本年级（校级/管理员=全校）。

取数复用网页端同一套服务（stats_service / ExamData 缓存），
保证"AI 看到的数字"与网页端一致。

2026-10-21：全部函数增加 `view`（brief 默认 / full）与必要时的 `top`，
按"默认少给"原则压体积——明细走列式（`respond.table_view`），
年级对照只在 full 下给全量各班，各科长 note 只在 full 下给。
"""
from sqlalchemy import func

from app.extensions import db
from app.models.grades import TOTAL_SUBJECT, Exam, ExamScore

from ..core import examdata, guards, respond, score_guard
from ._score_common import (NON_CLASS_NAMES, brief, exam_and_scope,
                            exam_fields, filter_subjects, note, slim_classes)


def _my_scope(user):
    return score_guard.resolve(user).describe()


def _list_exams(user, grade, limit, view):
    """列出账号可见范围内的考试（按考试日期倒序）。"""
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
    fields = exam_fields(view)
    return {'count': len(rows),
            'visible_grades': sorted(grades) if grades is not None else '全部年级',
            'exams': [respond.pick(e, fields) for e in rows]}


def _list_classes(user, grade, exam_id, view):
    """列出可下钻逐生明细的班级（班级口径＝考试当时的班级，非当前花名册）。

    exam_id 传 0 时汇总账号可见年级内所有考试出现过的班级（附出现场数）。
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
        item = {'grade': g, 'class_name': c, count_key: count}
        if not brief(view):
            item.update({
                'visible_subjects': sorted(subjects) if subjects is not None else '全部学科',
                'total_visible': scope.class_total_visible(g, c)})
        elif subjects is not None:
            item['subjects'] = sorted(subjects)
        out.append(item)
    return {'count': len(out), 'classes': respond.limit_rows(out),
            'note': note(view, '班级取自考试当时成绩；全年级各班汇总见 get_class_score_summary',
                         '本清单＝**可下钻逐生明细**的班级（班主任＝本班全部学科；'
                         '任课教师＝所教年级的全部班级，但只能看所教那一科）；'
                         '年级级各班汇总（公开口径，含全部班级）见 get_class_score_summary。'
                         '班级取自考试成绩（考试当时的班级），不是当前花名册；'
                         '"不分班 / 已转出 / 离校"不计入。')}


def _exam_overview(user, exam_id, view):
    """考试概览：口径塑形在 `app.modules.grades.services.score_overview`（与网页端 AI 同一份）。"""
    from app.modules.grades.services import score_overview

    exam, scope = exam_and_scope(user, exam_id)
    data = examdata.get_exam_data(exam.id)
    overview = {'exam': respond.pick(exam, exam_fields(view))}
    overview.update(score_overview.build(exam, scope, data))
    return overview


def _class_scores(user, exam_id, class_name, view):
    """年级各班成绩汇总（聚合口径，公开）+ 年级基准 + 班级位次。

    brief：每班一行核心指标（人数/均分/最高/最低/位次/能否下钻）；
    full：另含标准差、分层人数、各科均分（体积约为 brief 的三倍）。
    """
    from app.modules.grades.services import grade_context
    exam, scope = exam_and_scope(user, exam_id)
    data = examdata.get_exam_data(exam.id)
    out = grade_context.class_rows(scope, exam, data)
    rows = out['classes']
    if class_name:
        rows = [r for r in rows if r['class_name'] == class_name]
        if not rows:
            raise ValueError(f'该考试没有班级 {class_name} 的成绩（可用 list_classes 核对班级名）')
    return {'exam_id': exam.id, 'exam_name': exam.name, 'grade': exam.grade,
            'grade_baseline': out['grade'],
            'classes': respond.limit_rows(slim_classes(rows, view)),
            'note': note(view, 'detail_visible=false 的班不能下钻逐生成绩',
                         '各班汇总/各科均分/班级位次/年级基准属「年级成绩分析结果」，公开；'
                         '`detail_visible=false` 的班不能下钻逐生成绩')}


def _class_score_detail(user, exam_id, class_name, subject, view, top):
    """某场考试某班的逐生成绩明细（概况见 get_class_score_summary）。

    范围收敛：班级＝任课教师对该年级全部班可下钻；科目＝只返回可见学科；
    总分＝仅班主任及以上（任课教师只给总分年级名次 rank_dir）。

    brief：列式（cols+rows），只给学号/姓名/各科分/名次，年级对照只给本班位次
           与年级基准；full：对象式 + 全年级各班横向 + 方向/选科。
    """
    from app.modules.grades.services import grade_context
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
    rank_ok = scope.total_rank_visible(exam.grade, class_name)

    rows = []
    for snap in data.totals_of(class_name=class_name):
        item = {'no': snap['no'], 'name': snap['name']}
        for name in subjects:
            value = data.subj.get((snap['no'], name))
            if value is not None:
                item[name] = value
        if total_ok:
            item.update({'total': snap.get('score'), 'rank_c': snap.get('rank_class'),
                         'rank_g': snap.get('rank_dir')})
            if not brief(view):
                item.update({'direction': snap.get('direction'),
                             'selection': snap.get('selection')})
        elif rank_ok:
            item['rank_g'] = snap.get('rank_dir')      # 仅名次（总分年级名次）
        rows.append(item)
    if not rows:
        raise ValueError(f'该考试没有班级 {class_name} 的成绩（可用 list_classes 核对班级名）')
    if total_ok:      # 按总分降序；否则按学号
        rows.sort(key=lambda r: (-(r.get('total') or 0), r['no']))
    else:
        rows.sort(key=lambda r: r['no'])
    if top and top > 0:
        rows = rows[:top]

    vs = grade_context.class_vs_grade(scope, exam, data, class_name,
                                      with_peers=not brief(view))
    if brief(view):
        vs.pop('classes', None)
        vs.pop('class_count', None)
    cols = ['no', 'name'] + subjects + (['total', 'rank_c', 'rank_g'] if total_ok
                                        else ['rank_g'] if rank_ok else [])
    result = {'exam_id': exam.id, 'grade': exam.grade, 'class_name': class_name,
              'student_count': len(rows), 'subjects': subjects,
              'total_visible': total_ok, 'total_rank_visible': bool(rank_ok),
              'grade_context': vs,
              'students': respond.table_view(rows, cols) if brief(view)
                          else respond.limit_rows(rows)}
    if not total_ok:
        result['note'] = note(view, 'rank_g＝总分年级名次（不含总分分数）',
                              ('任课教师可见本年级所教科目的逐生成绩，rank_dir＝该生的总分年级名次'
                               '（仅名次，不含总分分数）' if rank_ok
                               else '任课教师仅可见所教学科成绩，总分与排名已隐藏'))
    return result


def _student_scores(user, student_no, exam_id, view):
    """单个学生在指定考试的成绩；exam_id 传 0 返回该生有成绩的考试清单。

    「不存在」与「不在范围」统一文案——持 grades.view 的账号不能据此
    枚举全校有效学号。
    """
    scope = score_guard.resolve(user)
    student = guards.find_student(student_no)
    if student is None:
        raise ValueError('无权查看该学生成绩（学号不存在或不在可见范围内）')
    try:
        score_guard.require_student(scope, student)
    except PermissionError:
        raise ValueError('无权查看该学生成绩（学号不存在或不在可见范围内）')
    total_ok = scope.class_total_visible(student.grade, student.class_name)
    rank_ok = scope.total_rank_visible(student.grade, student.class_name)

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
            elif rank_ok:
                item['rank_dir'] = r.rank_dir        # 仅名次：总分年级名次
            items.append(item)
        out = {'student_no': student_no, 'name': student.name, 'grade': student.grade,
               'class_name': student.class_name, 'total_visible': total_ok,
               'total_rank_visible': bool(rank_ok), 'count': len(items), 'exams': items}
        if not total_ok:
            out['note'] = note(view, 'rank_dir＝总分年级名次（不含总分分数）',
                               ('任课教师可见所教科目的成绩，rank_dir＝该生的总分年级名次'
                                '（仅名次，不含总分分数）' if rank_ok
                                else '任课教师仅可见所教学科成绩，总分与排名已隐藏'))
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
    rank_ok = scope.total_rank_visible(student.grade, student.class_name)
    student_out = dict(snap)
    if total_ok:
        pass
    elif rank_ok:
        # 2026-10-15：任课教师可见该生所教科 + **总分年级名次**（不给总分分数）
        for key in ('score', 'rank_class', 'move'):
            student_out.pop(key, None)
        student_out['total_hidden'] = True
        student_out['note'] = 'rank_dir＝该生的总分年级名次（仅名次，不含总分分数）'
    else:
        for key in ('score', 'rank_class', 'rank_dir', 'move'):
            student_out.pop(key, None)
        student_out['total_hidden'] = True
        student_out['note'] = '任课教师仅可见所教学科成绩，总分与排名已隐藏'
    if brief(view):
        student_out.pop('note', None)
    return {'exam': respond.pick(exam, exam_fields(view)),
            'visible_subjects': sorted(allowed) if allowed is not None else '全部学科',
            'total_rank_visible': bool(rank_ok),
            'student': student_out, 'subjects': subjects}


def _student_trend(user, student_no, subject, limit, view):
    """学生某学科（默认"总分"）的历次成绩走势。"""
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
    out = {'student_no': student_no, 'name': student.name,
           'grade': student.grade, 'class_name': student.class_name,
           'subject': subj, 'count': len(items)}
    if brief(view):
        out['trend'] = respond.table_view(
            items, ['exam_id', 'exam_name', 'exam_date', 'score', 'rank_dir'],
            labels={'exam_id': 'eid', 'exam_name': 'exam', 'exam_date': 'date',
                    'rank_dir': 'rank_g'})
    else:
        out['trend'] = items
    return out