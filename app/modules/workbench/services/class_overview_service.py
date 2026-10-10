# StuLink v1.18.9.2 2026-10-10
# 班级概览服务：聚合班主任管辖班级数据（跨库查询）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from datetime import date, datetime
from sqlalchemy import func, case

from app.extensions import db
from app.models.user_class_link import UserClassLink
from app.models.student import Student
from app.models.points import PointRecord
from app.models.grades import Exam, ExamScore, TeacherSubjectLink
from app.models.academic import AttendanceRecord


def _month_range(month_str=None):
    """解析 YYYY-MM 格式，返回 (start_date, end_date)；默认本月"""
    if month_str:
        try:
            parts = month_str.split('-')
            y, m = int(parts[0]), int(parts[1])
            start = date(y, m, 1)
            if m == 12:
                end = date(y + 1, 1, 1)
            else:
                end = date(y, m + 1, 1)
            return start, end
        except (ValueError, IndexError):
            pass
    today = date.today()
    start = today.replace(day=1)
    if today.month == 12:
        end = date(today.year + 1, 1, 1)
    else:
        end = date(today.year, today.month + 1, 1)
    return start, end


def get_managed_classes(user_id):
    """获取用户管辖的班级列表 [(grade, class_name), ...]"""
    links = UserClassLink.query.filter_by(user_id=user_id).all()
    return [(lk.grade, lk.class_name) for lk in links]


def get_class_overview(user_id, month_str=None):
    """聚合管辖班级概览数据

    返回: [{class_name, grade, student_count, points_plus, points_minus,
            latest_exam_name, latest_exam_avg, attendance_rate}, ...]
    """
    links = UserClassLink.query.filter_by(user_id=user_id).all()
    if not links:
        return []

    month_start, month_end = _month_range(month_str)

    # v1.16.0 性能改造：旧版每班 6 条查询（学生数/积分/考试/均分/考勤总数/考勤出勤），
    # 班主任带多个班时 SQL 随班数线性膨胀。现改为跨班一次性 GROUP BY 聚合 +
    # 考勤条件聚合，总查询数与班数无关。跨库不能 JOIN，故按库分别聚合后内存拼接，口径一致。
    grades = list({lk.grade for lk in links})
    class_names = list({lk.class_name for lk in links})

    # 1. 学生数（system.db）：一次 GROUP BY
    stu_map = {}
    for g, c, n in db.session.query(
            Student.grade, Student.class_name, func.count(Student.id)
    ).filter(
            Student.grade.in_(grades), Student.class_name.in_(class_names)
    ).group_by(Student.grade, Student.class_name).all():
        stu_map[(g, c)] = n

    # 2. 本月积分加减分（points.db）：一次 GROUP BY 条件聚合
    pts_map = {}
    for g, c, plus, minus in db.session.query(
            PointRecord.grade, PointRecord.class_name,
            func.sum(case((PointRecord.points > 0, PointRecord.points), else_=0)),
            func.sum(case((PointRecord.points < 0, PointRecord.points), else_=0)),
    ).filter(
            PointRecord.grade.in_(grades), PointRecord.class_name.in_(class_names),
            PointRecord.recorded_at >= month_start, PointRecord.recorded_at < month_end,
    ).group_by(PointRecord.grade, PointRecord.class_name).all():
        pts_map[(g, c)] = (abs(int(plus or 0)), abs(int(minus or 0)))

    # 3. 各年级最近一场考试（grades.db）：recent_exam 只依赖年级
    latest_exam_by_grade = {}
    for ex in db.session.query(Exam).filter(
            Exam.grade.in_(grades)).order_by(Exam.exam_date.desc()).all():
        if ex.grade not in latest_exam_by_grade:
            latest_exam_by_grade[ex.grade] = ex
    # 各班总分均分：按 (exam_id, class_name) 一次 GROUP BY
    avg_map = {}
    exam_ids = list({ex.id for ex in latest_exam_by_grade.values()})
    if exam_ids:
        for eid, cn, a in db.session.query(
                ExamScore.exam_id, ExamScore.class_name, func.avg(ExamScore.score)
        ).filter(
                ExamScore.exam_id.in_(exam_ids), ExamScore.subject == '总分'
        ).group_by(ExamScore.exam_id, ExamScore.class_name).all():
            avg_map[(eid, cn)] = round(float(a), 1) if a is not None else None

    # 4. 本月出勤率（academic.db）：一次 GROUP BY 条件聚合
    att_map = {}
    for g, c, tot, pres in db.session.query(
            AttendanceRecord.grade, AttendanceRecord.class_name,
            func.count(AttendanceRecord.id),
            func.sum(case((AttendanceRecord.status == 'present', 1), else_=0)),
    ).filter(
            AttendanceRecord.grade.in_(grades), AttendanceRecord.class_name.in_(class_names),
            AttendanceRecord.attend_date >= month_start, AttendanceRecord.attend_date < month_end,
    ).group_by(AttendanceRecord.grade, AttendanceRecord.class_name).all():
        att_map[(g, c)] = round(pres / tot * 100, 1) if tot and tot > 0 else None

    results = []
    for lk in links:
        grade, class_name = lk.grade, lk.class_name
        full_class = f'{grade}{class_name}'
        student_count = stu_map.get((grade, class_name), 0)
        points_plus, points_minus = pts_map.get((grade, class_name), (0, 0))
        recent_exam = latest_exam_by_grade.get(grade)
        latest_exam_name = recent_exam.name if recent_exam else ''
        latest_exam_avg = avg_map.get((recent_exam.id, class_name)) if recent_exam else None
        attendance_rate = att_map.get((grade, class_name))

        results.append({
            'grade': grade,
            'class_name': class_name,
            'full_class': full_class,
            'student_count': student_count,
            'points_plus': points_plus,
            'points_minus': points_minus,
            'latest_exam_name': latest_exam_name,
            'latest_exam_avg': latest_exam_avg,
            'attendance_rate': attendance_rate,
        })

    return results


def get_students_by_class(grade, class_name, search=None, page=1, per_page=30):
    """获取班级学生列表（分页）

    返回: {students: [...], total: int, page: int, per_page: int, pages: int}
    """
    q = Student.query.filter_by(grade=grade, class_name=class_name)
    if search:
        search = search.strip()
        q = q.filter(
            db.or_(
                Student.name.ilike(f'%{search}%'),
                Student.student_number.ilike(f'%{search}%'),
            )
        )
    q = q.order_by(Student.student_number)
    pagination = q.paginate(page=page, per_page=per_page, error_out=False)
    return {
        'students': pagination.items,
        'total': pagination.total,
        'page': pagination.page,
        'per_page': pagination.per_page,
        'pages': pagination.pages,
    }


def get_teaching_links(user_id):
    """本人任教的 (年级, 班级, 学科) 清单：成绩页的筛选选项、徽章与前端归属校验共用。

    返回: [{grade, class_name, class_name_full, subject}]，按年级/班级/学科排序。
    """
    links = (TeacherSubjectLink.query
             .filter_by(user_id=user_id, active=True)
             .order_by(TeacherSubjectLink.grade, TeacherSubjectLink.class_name,
                       TeacherSubjectLink.subject).all())
    return [{'grade': lk.grade,
             'class_name': lk.class_name,
             'class_name_full': f'{lk.grade}{lk.class_name}',
             'subject': lk.subject} for lk in links]


def get_grade_summary(user_id, grade=None):
    """获取教师成绩摘要（按考试聚合本人任教班级×科目的均分/最高/最低/及格率/年级名次）

    口径（2026-10-10 收紧）：只统计 TeacherSubjectLink(user_id, active=True) 中的
    (年级, 班级, 学科) 组合 —— 即"本人任教"；同年级其它班只参与聚合对比
    （年级均分与名次），不出现任何明细。

    性能：每场考试一条 group_by(class_name, subject) 聚合查询（原实现是按
    「班科 × 考试」逐组合查询），教师 6 个班科 × 10 场考试从约 60 次降到 20 次。

    返回: [{exam_id, exam_name, exam_date, exam_type, classes: [{
        grade, class_name, class_name_raw, subject, count, avg, max, min,
        pass_rate, grade_avg, grade_rank, grade_class_count}]}, ...]
    """
    # 获取任课班级（唯一准入依据）
    links_q = TeacherSubjectLink.query.filter_by(user_id=user_id, active=True)
    if grade:
        links_q = links_q.filter_by(grade=grade)
    links = links_q.all()
    if not links:
        return []

    allowed = {(lk.grade, lk.class_name, lk.subject) for lk in links}
    grades = sorted({lk.grade for lk in links})

    exams = (Exam.query.filter(Exam.grade.in_(grades))
             .order_by(Exam.exam_date.desc(), Exam.id.desc()).limit(10).all())

    results = []
    for exam in exams:
        fm = exam.full_marks() or {}
        # 该考试出现的科目（用于按科目阈值判定及格，阈值缺失时与原逻辑一致按 100 分）
        subjects = [r[0] for r in (db.session.query(ExamScore.subject)
                                   .filter(ExamScore.exam_id == exam.id)
                                   .distinct().all()) if r[0]]
        if not subjects:
            continue
        pass_expr = func.sum(case(
            *[((ExamScore.subject == s) & (ExamScore.score >= float(fm.get(s, 100)) * 0.6), 1)
              for s in subjects],
            else_=0))

        rows = (db.session.query(
                    ExamScore.class_name.label('class_name'),
                    ExamScore.subject.label('subject'),
                    func.count(ExamScore.id).label('cnt'),
                    func.avg(ExamScore.score).label('avg_score'),
                    func.max(ExamScore.score).label('max_score'),
                    func.min(ExamScore.score).label('min_score'),
                    pass_expr.label('pass_cnt'))
                .filter(ExamScore.exam_id == exam.id)
                .group_by(ExamScore.class_name, ExamScore.subject).all())

        by_class_subject = {}
        by_subject = {}
        for r in rows:
            cnt = int(r.cnt or 0)
            if not cnt or r.class_name is None or not r.subject:
                continue
            raw_avg = float(r.avg_score or 0)
            by_class_subject[(r.class_name, r.subject)] = {
                'count': cnt,
                'avg': round(raw_avg, 1),
                'max': float(r.max_score or 0),
                'min': float(r.min_score or 0),
                'pass_rate': round(float(r.pass_cnt or 0) / cnt * 100, 1),
            }
            by_subject.setdefault(r.subject, []).append(
                {'class_name': r.class_name, 'avg': raw_avg, 'count': cnt})

        exam_classes = []
        for g, cn, subj in sorted(allowed):
            if g != exam.grade:
                continue
            stat = by_class_subject.get((cn, subj))
            if not stat:
                continue
            # 年级对比：按均分降序取本班名次 + 年级加权均分（仅聚合值）
            peers = by_subject.get(subj) or []
            ordered = sorted(peers, key=lambda x: (-x['avg'], x['class_name']))
            rank = next((i + 1 for i, x in enumerate(ordered)
                         if x['class_name'] == cn), None)
            total_cnt = sum(x['count'] for x in ordered)
            grade_avg = (round(sum(x['avg'] * x['count'] for x in ordered) / total_cnt, 1)
                         if total_cnt else None)
            exam_classes.append({
                'grade': g,
                'class_name': f'{g}{cn}',
                'class_name_raw': cn,
                'subject': subj,
                'count': stat['count'],
                'avg': stat['avg'],
                'max': stat['max'],
                'min': stat['min'],
                'pass_rate': stat['pass_rate'],
                'grade_avg': grade_avg,
                'grade_rank': rank,
                'grade_class_count': len(ordered),
                # 同年级各班均分（仅聚合值，供"我教的班 vs 同年级"对比图，不暴露他班明细）
                'peers': [{'class_name': f'{g}{x["class_name"]}',
                           'avg': round(x['avg'], 1),
                           'mine': x['class_name'] == cn} for x in ordered],
            })

        if exam_classes:
            results.append({
                'exam_id': exam.id,
                'exam_name': exam.name,
                'exam_date': exam.exam_date.strftime('%Y-%m-%d') if exam.exam_date else '',
                'exam_type': exam.exam_type or '',
                'classes': exam_classes,
            })

    return results


def _require_teaching_link(user_id, grade, class_name, subject):
    """归属校验：该 (年级, 班级, 学科) 必须属于本人任课映射，否则 PermissionError。

    工作台成绩页的唯一准入依据就是 TeacherSubjectLink(user_id, active=True)，
    与 grades 模块「只到年级级」的宽口径解耦，避免越权看到别的班/别的科。
    """
    link = (TeacherSubjectLink.query
            .filter_by(user_id=user_id, active=True, grade=grade,
                       class_name=class_name, subject=subject).first())
    if not link:
        raise PermissionError('该班级学科不在您的任教范围内')
    return link


def get_grade_detail(user_id, exam_id, class_name, subject):
    """某次考试某班某科的学生明细（含班内名次），仅限本人任教范围。

    越权（不在 TeacherSubjectLink）抛 PermissionError，由路由转 403 JSON。
    返回: {exam_id, exam_name, exam_date, grade, class_name, class_name_raw,
           subject, rows: [{rank, student_no, student_name, score, rank_class}],
           stats: {count, avg, max, min, pass_rate}}
    """
    exam = db.session.get(Exam, exam_id)
    if not exam:
        raise ValueError('考试不存在')
    _require_teaching_link(user_id, exam.grade, class_name, subject)

    scores = (ExamScore.query
              .filter_by(exam_id=exam.id, class_name=class_name, subject=subject)
              .order_by(ExamScore.score.desc(), ExamScore.student_no).all())

    rows = []
    for i, s in enumerate(scores, 1):
        rows.append({
            'rank': i,                       # 按分数降序的班内名次（库里 rank_class 可能缺）
            'student_no': s.student_no or '',
            'student_name': s.student_name or '',
            'score': float(s.score) if s.score is not None else None,
            'rank_class': s.rank_class,
        })

    vals = [r['score'] for r in rows if r['score'] is not None]
    full = float((exam.full_marks() or {}).get(subject, 100))
    threshold = full * 0.6
    stats = {
        'count': len(vals),
        'avg': round(sum(vals) / len(vals), 1) if vals else None,
        'max': max(vals) if vals else None,
        'min': min(vals) if vals else None,
        'pass_rate': (round(sum(1 for v in vals if v >= threshold) / len(vals) * 100, 1)
                      if vals else None),
    }
    return {
        'exam_id': exam.id,
        'exam_name': exam.name,
        'exam_date': exam.exam_date.strftime('%Y-%m-%d') if exam.exam_date else '',
        'grade': exam.grade,
        'class_name': f'{exam.grade}{class_name}',
        'class_name_raw': class_name,
        'subject': subject,
        'full': full,       # 满分（前端按比例做分数颜色/分数段）
        'rows': rows,
        'stats': stats,
    }


def get_grade_trend(user_id, class_name, subject, grade=None, limit=8):
    """同一班级+学科历次考试的走势（平均分、及格率、年级该科均值）。

    仅限本人任教范围（越权抛 PermissionError）；按考试日期升序返回最近 limit 场。
    """
    lk_q = TeacherSubjectLink.query.filter_by(user_id=user_id, active=True,
                                              class_name=class_name, subject=subject)
    if grade:
        lk_q = lk_q.filter_by(grade=grade)
    links = lk_q.all()
    if not links:
        raise PermissionError('该班级学科不在您的任教范围内')
    grades = sorted({lk.grade for lk in links})

    exams = (Exam.query.filter(Exam.grade.in_(grades))
             .order_by(Exam.exam_date.desc(), Exam.id.desc())
             .limit(max(2, min(int(limit or 8), 20))).all())
    exams.reverse()      # 图表按时间升序

    points = []
    for exam in exams:
        row = (db.session.query(func.count(ExamScore.id),
                                func.avg(ExamScore.score))
               .filter(ExamScore.exam_id == exam.id,
                       ExamScore.class_name == class_name,
                       ExamScore.subject == subject).first())
        cnt = int(row[0] or 0) if row else 0
        if not cnt:
            continue
        threshold = float((exam.full_marks() or {}).get(subject, 100)) * 0.6
        pass_cnt = (db.session.query(func.count(ExamScore.id))
                    .filter(ExamScore.exam_id == exam.id,
                            ExamScore.class_name == class_name,
                            ExamScore.subject == subject,
                            ExamScore.score >= threshold).scalar() or 0)
        grade_avg = (db.session.query(func.avg(ExamScore.score))
                     .filter(ExamScore.exam_id == exam.id,
                             ExamScore.subject == subject).scalar())
        points.append({
            'exam_name': exam.name,
            'exam_date': exam.exam_date.strftime('%Y-%m-%d') if exam.exam_date else '',
            'avg': round(float(row[1] or 0), 1),
            'pass_rate': round(pass_cnt / cnt * 100, 1),
            'count': cnt,
            'grade_avg': round(float(grade_avg), 1) if grade_avg is not None else None,
        })

    return {
        'grade': links[0].grade,
        'class_name': f'{links[0].grade}{class_name}',
        'class_name_raw': class_name,
        'subject': subject,
        'points': points,
    }


def get_points_summary(user_id, month_str=None):
    """获取积分汇总（按班级聚合，本月加减分统计 + Top5 学生）

    返回: {classes: [{class_name, plus, minus, net}], top_plus: [...], top_minus: [...]}
    """
    links = UserClassLink.query.filter_by(user_id=user_id).all()
    if not links:
        return {'classes': [], 'top_plus': [], 'top_minus': []}

    month_start, month_end = _month_range(month_str)
    class_names = [(lk.grade, lk.class_name) for lk in links]

    # 收集管辖班级名列表用于聚合与 Top5
    all_class_names = [cn for _, cn in class_names]
    all_grades = [g for g, _ in class_names]

    # 按班级聚合
    # v1.16.0 性能改造：旧版每班 1 条聚合查询，多班时线性膨胀；现一次 GROUP BY 取回全部班级。
    pts_map = {}
    for g, c, plus, minus in db.session.query(
            PointRecord.grade, PointRecord.class_name,
            func.sum(case((PointRecord.points > 0, PointRecord.points), else_=0)),
            func.sum(case((PointRecord.points < 0, func.abs(PointRecord.points)), else_=0)),
    ).filter(
            PointRecord.grade.in_(all_grades), PointRecord.class_name.in_(all_class_names),
            PointRecord.recorded_at >= month_start, PointRecord.recorded_at < month_end,
    ).group_by(PointRecord.grade, PointRecord.class_name).all():
        pts_map[(g, c)] = (int(plus or 0), int(minus or 0))

    classes_data = []
    for grade, cn in class_names:
        plus_val, minus_val = pts_map.get((grade, cn), (0, 0))
        classes_data.append({
            'grade': grade,
            'class_name': cn,
            'full_class': f'{grade}{cn}',
            'plus': plus_val,
            'minus': minus_val,
            'net': plus_val - minus_val,
        })

    # Top5 加分学生
    top_plus = db.session.query(
        PointRecord.student_no,
        PointRecord.student_name,
        PointRecord.class_name,
        func.sum(PointRecord.points).label('total'),
    ).filter(
        PointRecord.grade.in_(all_grades),
        PointRecord.class_name.in_(all_class_names),
        PointRecord.recorded_at >= month_start,
        PointRecord.recorded_at < month_end,
        PointRecord.points > 0,
    ).group_by(
        PointRecord.student_no, PointRecord.student_name, PointRecord.class_name
    ).order_by(
        func.sum(PointRecord.points).desc()
    ).limit(5).all()

    # Top5 减分学生
    top_minus = db.session.query(
        PointRecord.student_no,
        PointRecord.student_name,
        PointRecord.class_name,
        func.sum(PointRecord.points).label('total'),
    ).filter(
        PointRecord.grade.in_(all_grades),
        PointRecord.class_name.in_(all_class_names),
        PointRecord.recorded_at >= month_start,
        PointRecord.recorded_at < month_end,
        PointRecord.points < 0,
    ).group_by(
        PointRecord.student_no, PointRecord.student_name, PointRecord.class_name
    ).order_by(
        func.sum(PointRecord.points).asc()
    ).limit(5).all()

    return {
        'classes': classes_data,
        'top_plus': [
            {'student_no': r.student_no, 'student_name': r.student_name,
             'class_name': r.class_name, 'total': int(r.total or 0)}
            for r in top_plus
        ],
        'top_minus': [
            {'student_no': r.student_no, 'student_name': r.student_name,
             'class_name': r.class_name, 'total': int(r.total or 0)}
            for r in top_minus
        ],
    }
