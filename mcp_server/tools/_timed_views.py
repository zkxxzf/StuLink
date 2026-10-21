# -*- coding: utf-8 -*-
"""限时练的取数实现（**不含工具注册**）：场次 / 班级 / 学生三个维度。

数据源是独立库 `timed.db`（`app.models.timed`），与普通考试零耦合：
  · 场次 `TimedExam`、成绩 `TimedScore`（一行＝一学生一科目，**缺考不落库**）；
  · **没有总分行**：总分由各科现算，名次是**该科全年级名次** `rank_grade`；
  · 没有选科方向 / 组合，也没有分数线分层——只有「名次区间模板」。

**复用网页端服务**（`timed_service` / `timed_analysis`）而不是自己写聚合，
保证 AI 与网页分析页同口径（缺考不计入分母、去差名额、累积门槛…）。
注意：那些服务里带 `user=None` 的参数必须**显式传 user**——MCP 进程没有
`current_user`（`flask_login` 未登录)，取默认值会直接报错。

权限口径（2026-10-20 用户定案，与网页端一致）：
  · 查看＝`grades.view` + 该年级在可见年级范围内（`scope_service.visible_grades`）；
  · 本服务只读，不涉及改分 / 删除（那需要 `grades.edit`）。
"""
from ..core import respond, timeddata
from ._score_common import brief, note

TIMED_EXAM_FIELDS = ('id', 'name', 'grade', 'exam_date', 'term', 'status')


def visible_grades(user):
    """限时练可见年级（网页端同一口径：失败即空集，fail-closed）。"""
    from app.modules.grades.services import timed_service
    return timed_service.visible_grades_of(user)


def timed_exam(user, exam_id):
    """取限时练场次并校验年级可见性。

    「不存在」与「无权限」统一文案：否则持 grades.view 的账号能靠报错差异
    枚举出其它年级到底有哪些场次 id。
    """
    from app.extensions import db
    from app.models.timed import TimedExam
    exam = db.session.get(TimedExam, int(exam_id)) if exam_id else None
    if exam is None:
        raise ValueError(f'限时练场次不存在：exam_id={exam_id}'
                         '（用 list_exams(kind="timed") 查看可用场次）')
    grades = visible_grades(user)
    if getattr(user, 'role', '') != 'admin' and exam.grade not in grades:
        raise PermissionError(f'无权查看 {exam.grade} 的限时练成绩'
                              '（不在你的可见年级内）')
    timeddata.touch()
    return exam


def list_exams(user, grade, limit, view):
    """限时练场次清单（按日期倒序，附已导入科目与去重人数）。"""
    from app.models.timed import TimedExam
    from app.modules.grades.services import timed_service
    grades = visible_grades(user)
    if grade and getattr(user, 'role', '') != 'admin' and grade not in grades:
        raise PermissionError(f'无权查看 {grade} 的限时练成绩（不在你的可见年级内）')
    query = TimedExam.query
    if grade:
        query = query.filter(TimedExam.grade == grade)
    elif getattr(user, 'role', '') != 'admin':
        if not grades:
            return {'count': 0, 'exams': [],
                    'note': '当前账号没有任何可见年级'}
        query = query.filter(TimedExam.grade.in_(sorted(grades)))
    rows = (query.order_by(TimedExam.exam_date.desc(), TimedExam.id.desc())
            .limit(limit).all())
    exams = []
    for e in rows:
        item = respond.pick(e, TIMED_EXAM_FIELDS)
        item['status'] = e.status_label()
        if not brief(view):
            item['semester'] = e.natural_semester_label()
            item['subjects'] = timed_service.subjects_of(e.id)
            item['student_count'] = timed_service.student_total(e.id)
        exams.append(item)
    return {'count': len(exams),
            'visible_grades': sorted(grades) if grades else '全部年级',
            'exams': exams}


def list_classes(user, exam_id, view):
    """本场出现过的班级（含人数）；exam_id 传 0 时跨可见年级汇总。"""
    from sqlalchemy import func
    from app.extensions import db
    from app.models.timed import TimedExam, TimedScore
    query = db.session.query(TimedScore.grade, TimedScore.class_name,
                             func.count(func.distinct(TimedScore.student_no)))
    if exam_id:
        exam = timed_exam(user, exam_id)
        query = query.filter(TimedScore.exam_id == exam.id)
    else:
        grades = visible_grades(user)
        if getattr(user, 'role', '') != 'admin':
            if not grades:
                return {'count': 0, 'classes': []}
            ids = [e.id for e in TimedExam.query.filter(
                TimedExam.grade.in_(sorted(grades))).all()]
            # 可见年级内一场限时练都没有：用不可能命中的 id，避免 filter(False) 语义分歧
            query = query.filter(TimedScore.exam_id.in_(ids or [-1]))
    rows = (query.group_by(TimedScore.grade, TimedScore.class_name)
            .order_by(TimedScore.grade.desc(), TimedScore.class_name).all())
    out = [{'grade': g, 'class_name': c, 'student_count': n}
           for g, c, n in rows if c and c not in ('不分班', '未分班')]
    return {'count': len(out), 'classes': respond.limit_rows(out),
            'note': note(view, '班级取自本场成绩行（当时班级）',
                         '班级取自限时练成绩行里的班级快照；'
                         '"不分班 / 未分班"（无班级归属）不计入统计。'
                         '逐生明细用 get_class_score_detail(kind="timed")')}


def overview(user, exam_id, view):
    """场次概览：科目与人数、班级数、去重学生数、已用分层模板。"""
    from app.modules.grades.services import timed_analysis, timed_service
    exam = timed_exam(user, exam_id)
    counts = timed_service.student_counts(exam.id)
    classes = timed_analysis.classes_of(exam.id)
    out = {'exam': respond.pick(exam, TIMED_EXAM_FIELDS),
           'status': exam.status_label(),
           'semester': exam.natural_semester_label(),
           'grade': exam.grade, 'student_count': timed_service.student_total(exam.id),
           'subjects': sorted(counts), 'subject_counts': counts,
           'class_count': len(classes)}
    if not brief(view):
        out['classes'] = classes
        out['template'] = timed_service.current_template(exam).name
    return out


def class_summary(user, exam_id, class_name, view, top):
    """汇总表：一行一个学生 × 各科分数 + 现算总分 + 年级/班级名次。

    口径与网页端「成绩单」页同一份（`timed_analysis.summary_matrix`）：
    缺考不落库自然不计入分母、名次同级并列 1,1,3。
    """
    from app.modules.grades.services import timed_analysis
    exam = timed_exam(user, exam_id)
    matrix = timed_analysis.summary_matrix(exam, class_name, q='', page=None)
    rows = matrix['rows']
    subjects = matrix['subjects']
    if top and top > 0:
        rows = rows[:top]
    # 各科分数在 rows[i]['scores'] 子字典里，列式前摊平（省掉每层键名）
    flat = [{'no': r['student_no'], 'name': r['name'], 'cls': r['class_name'],
             'total': r['total'], 'rank_g': r['rank_grade'],
             'rank_c': r['rank_class'], **r['scores']} for r in rows]
    cols = ['no', 'name', 'cls'] + subjects + ['total', 'rank_g', 'rank_c']
    out = {'exam_id': exam.id, 'exam_name': exam.name, 'grade': exam.grade,
           'class_name': class_name or '全年级', 'subjects': subjects,
           'student_count': len(rows)}
    if brief(view):
        out['students'] = respond.table_view(flat, cols)
    else:
        out['students'] = respond.limit_rows(rows)
    out['note'] = note(view, 'rank_g=该科年级名次',
                       '总分＝各科现算（限时练不存总分行）；rank_g＝总分年级名次，'
                       'rank_c＝总分班名次，同分并列后延跳号（1,1,3）')
    return out


def subject_detail_rows(user, exam_id, class_name, subject, view, top):
    """某班某科逐生明细（分数 + 该科年级名次），按名次升序。"""
    from app.models.timed import TimedScore
    from app.modules.grades.services import timed_service
    exam = timed_exam(user, exam_id)
    subjects = timed_service.subjects_of(exam.id)
    if not subjects:
        raise ValueError(f'限时练 {exam.name} 还没有导入任何科目成绩')
    picked = (subject or '').strip()
    if picked and picked not in subjects:
        raise ValueError(f'该场次没有「{picked}」成绩（已导入科目：'
                         + '、'.join(subjects) + '）')
    query = TimedScore.query.filter_by(exam_id=exam.id)
    if class_name:
        query = query.filter(TimedScore.class_name == class_name)
    if picked:
        query = query.filter(TimedScore.subject == picked)
    rows = query.order_by(TimedScore.student_no.asc()).all()
    items = [{'no': r.student_no, 'name': r.student_name, 'class': r.class_name,
              'subject': r.subject, 'score': r.score, 'rank': r.rank_grade}
             for r in rows]
    items.sort(key=lambda x: ((x['rank'] or 99999), x['no']))
    if top and top > 0:
        items = items[:top]
    cols = ['no', 'name', 'subject', 'score', 'rank']
    if not class_name:
        cols.insert(2, 'class')
    return {'exam_id': exam.id, 'exam_name': exam.name, 'grade': exam.grade,
            'class_name': class_name or '全年级',
            'subject': picked or '全部科目', 'count': len(items),
            'students': respond.table_view(items, cols) if brief(view)
                        else respond.limit_rows(items)}


def student_scores(user, student_no, exam_id, view):
    """该生在某场限时练的各科成绩；exam_id 传 0 给该生参加过的场次清单。"""
    from app.models.timed import TimedExam, TimedScore
    grades = visible_grades(user)
    rows_all = (TimedScore.query.filter_by(student_no=student_no)
                .order_by(TimedScore.exam_id.desc()).all())
    if not rows_all:
        raise ValueError('无权查看该学生成绩（学号不存在或不在可见范围内）')
    if getattr(user, 'role', '') != 'admin':
        rows_all = [r for r in rows_all if r.grade in grades]
        if not rows_all:
            raise ValueError('无权查看该学生成绩（学号不存在或不在可见范围内）')

    if not exam_id:
        eids = sorted({r.exam_id for r in rows_all}, reverse=True)
        exams = {e.id: e for e in
                 TimedExam.query.filter(TimedExam.id.in_(eids)).all()}
        items = []
        for eid in eids:
            exam = exams.get(eid)
            if exam is None:
                continue
            subjects = sorted({r.subject for r in rows_all if r.exam_id == eid})
            items.append({'exam_id': eid, 'exam_name': exam.name,
                          'grade': exam.grade, 'subjects': subjects})
            if len(items) >= 30:
                break
        return {'student_no': student_no,
                'name': next((r.student_name for r in rows_all
                              if r.student_name), ''),
                'count': len(items), 'exams': items}

    exam = timed_exam(user, exam_id)
    rows = [r for r in rows_all if r.exam_id == exam.id]
    if not rows:
        raise ValueError(f'该学生在限时练 {exam.name} 中没有成绩记录')
    items = [{'subject': r.subject, 'score': r.score, 'rank_g': r.rank_grade}
             for r in sorted(rows, key=lambda r: r.subject)]
    return {'exam_id': exam.id, 'exam_name': exam.name, 'grade': exam.grade,
            'student_no': student_no,
            'name': next((r.student_name for r in rows if r.student_name), ''),
            'class_name': next((r.class_name for r in rows if r.class_name), ''),
            'subjects': respond.table_view(
                items, ['subject', 'score', 'rank_g']) if brief(view) else items}


def student_trend(user, student_no, subject, limit, view):
    """该生某科跨场次的走势（分数 + 该科年级名次 + T 分）。

    起点场次＝该生最近一次有成绩的限时练（用它确定年级），随后复用
    网页端 `timed_analysis.student_detail`（同年级全部场次 + T 分）。
    """
    from app.models.timed import TimedScore
    from app.modules.grades.services import timed_analysis
    latest = (TimedScore.query.filter_by(student_no=student_no)
              .order_by(TimedScore.exam_id.desc()).first())
    if latest is None:
        raise ValueError('无权查看该学生成绩（学号不存在或不在可见范围内）')
    exam = timed_exam(user, latest.exam_id)       # 顺带校验该年级可见
    detail = timed_analysis.student_detail(exam, student_no)
    chart = detail.get('chart') or {}
    picked = (subject or '').strip()
    if picked and picked not in chart:
        raise ValueError(f'该生没有「{picked}」的成绩（可用科目：'
                         + '、'.join(sorted(chart)) + '）')
    subjects = [picked] if picked else sorted(chart)
    out = {'student_no': student_no, 'name': detail.get('name'),
           'class_name': detail.get('cls'), 'grade': exam.grade,
           'subjects': subjects}
    series = []
    for subj in subjects:
        pts = chart.get(subj) or []
        if limit and limit > 0:
            pts = pts[-limit:]
        series.append({'subject': subj, 'count': len(pts), 'points': pts})
    out['series'] = series
    if not brief(view):
        out['stats'] = detail.get('stats')
        out['weak'] = [x['subject'] for x in (detail.get('weak') or [])]
        # T 分＝10Z+50（50 为年级平均），样本不足 30 人不给
        out['t_note'] = 'points=[考试日期, T分]，T=10×Z+50；样本不足 ' + \
                        str(timed_analysis.T_MIN_SAMPLE) + ' 人的场次不计算'
    return out
