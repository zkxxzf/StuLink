# StuLink v1.17.0 2026-09-21
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


def get_grade_summary(user_id, grade=None):
    """获取教师成绩摘要（按考试聚合班级均分/最高分/及格率）

    通过 TeacherSubjectLink 获取任课班级，再聚合 ExamScore。
    返回: [{exam_name, exam_date, classes: [{class_name, avg, max, pass_rate}]}, ...]
    """
    # 获取任课班级
    links_q = TeacherSubjectLink.query.filter_by(user_id=user_id, active=True)
    if grade:
        links_q = links_q.filter_by(grade=grade)
    links = links_q.all()
    if not links:
        return []

    # 收集涉及的年级
    grades = list({lk.grade for lk in links})
    class_names = [lk.class_name for lk in links]

    # 获取相关考试
    exams = Exam.query.filter(
        Exam.grade.in_(grades)
    ).order_by(Exam.exam_date.desc()).limit(10).all()

    results = []
    for exam in exams:
        exam_classes = []
        for lk in links:
            if lk.grade != exam.grade:
                continue
            # 班级统计
            stats = db.session.query(
                func.avg(ExamScore.score).label('avg_score'),
                func.max(ExamScore.score).label('max_score'),
                func.count(ExamScore.id).label('cnt'),
                func.sum(case(
                    (ExamScore.score >= exam.full_marks().get(lk.subject, 100) * 0.6, 1),
                    else_=0
                )).label('pass_cnt'),
            ).filter(
                ExamScore.exam_id == exam.id,
                ExamScore.class_name == lk.class_name,
                ExamScore.subject == lk.subject,
            ).first()

            if stats and stats.cnt and stats.cnt > 0:
                avg_val = round(float(stats.avg_score or 0), 1)
                max_val = float(stats.max_score or 0)
                pass_rate = round(float(stats.pass_cnt or 0) / stats.cnt * 100, 1)
                exam_classes.append({
                    'class_name': f'{lk.grade}{lk.class_name}',
                    'subject': lk.subject,
                    'avg': avg_val,
                    'max': max_val,
                    'pass_rate': pass_rate,
                    'count': stats.cnt,
                })
        if exam_classes:
            results.append({
                'exam_name': exam.name,
                'exam_date': exam.exam_date.strftime('%Y-%m-%d') if exam.exam_date else '',
                'exam_type': exam.exam_type or '',
                'classes': exam_classes,
            })

    return results


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
