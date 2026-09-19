# StuLink v1.15.0 2026-09-18
# 学生画像计算服务：评分标准化、综合评级计算
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""画像计算服务

核心功能：
- 计算学生综合画像（各维度标准化评分 + 综合评级）
- 画像列表查询（带筛选和分页）
- 画像详情聚合
- 评语/事件管理
"""
from datetime import datetime, date

from sqlalchemy import func, or_, desc
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Student
from app.models.grades import Exam, ExamScore, TOTAL_SUBJECT
from app.models.points import PointRecord
from app.models.portrait import StudentPortrait, PortraitComment, PortraitEvent
from app.modules.portrait.services import portrait_aggregation as agg
from app.utils.cache import cached


def calculate_portrait(student_no):
    """计算学生综合画像

    1. 调用聚合服务获取各维度原始数据
    2. 标准化评分（0-100）
    3. 计算综合评级（A/B/C/D）
    4. 更新 student_portraits 表
    返回完整的画像数据 dict
    """
    student = Student.query.filter_by(student_number=student_no).first()
    if not student:
        return None

    # 1. 获取各维度原始数据
    trend = agg.get_academic_trend(student_no, limit=5)
    points_summary = agg.get_points_summary(student_no)
    dorm_info = agg.get_dormitory_info(student_no)
    attendance = agg.get_attendance_records(student_no)

    # 2. 标准化评分
    # 学业得分：基于最近考试成绩在年级中的相对排名（百分位）
    academic_score = _calculate_academic_score(student_no, student.grade)

    # 行为积分：基于积分总和标准化（满分100，基准：100分=50积分）
    behavior_score = _calculate_behavior_score(points_summary['total'])

    # 宿舍表现：当前无检查扣分记录，默认满分
    dormitory_score = 100.0 if not dorm_info.get('has_record') else 80.0

    # 出勤率：基于纪律类积分记录估算（无纪律扣分=100%）
    attendance_rate = _calculate_attendance_rate(attendance)

    # 3. 综合评级：加权平均
    # 权重：学业40% + 行为25% + 宿舍15% + 出勤20%
    overall_score = (academic_score * 0.4 + behavior_score * 0.25 +
                     dormitory_score * 0.15 + attendance_rate * 0.2)

    if overall_score >= 90:
        overall_level = 'A'
    elif overall_score >= 75:
        overall_level = 'B'
    elif overall_score >= 60:
        overall_level = 'C'
    else:
        overall_level = 'D'

    # 4. 更新或创建画像记录
    portrait = StudentPortrait.query.filter_by(student_no=student_no).first()
    if not portrait:
        portrait = StudentPortrait(student_no=student_no)
        db.session.add(portrait)

    portrait.grade = student.grade
    portrait.class_name = student.class_name
    portrait.academic_score = round(academic_score, 1)
    portrait.behavior_score = round(behavior_score, 1)
    portrait.dormitory_score = round(dormitory_score, 1)
    portrait.attendance_rate = round(attendance_rate, 1)
    portrait.overall_level = overall_level
    portrait.last_updated = datetime.now()

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        portrait = StudentPortrait.query.filter_by(student_no=student_no).first()

    return portrait.to_dict()


def _calculate_academic_score(student_no, grade):
    """计算学业得分（百分制）

    基于最近考试成绩在年级中的相对排名（百分位）
    """
    # 获取该学生最近一场考试的总分和排名
    latest_score = (ExamScore.query
                    .filter(ExamScore.student_no == student_no,
                            ExamScore.subject == TOTAL_SUBJECT,
                            ExamScore.score.isnot(None))
                    .join(Exam, ExamScore.exam_id == Exam.id)
                    .order_by(Exam.exam_date.desc())
                    .first())

    if not latest_score or not latest_score.rank_dir:
        return 50.0  # 无成绩数据，默认中等

    # 获取该场考试该年级的总人数
    total_count = (ExamScore.query
                   .filter(ExamScore.exam_id == latest_score.exam_id,
                           ExamScore.subject == TOTAL_SUBJECT,
                           ExamScore.score.isnot(None))
                   .count())

    if total_count <= 1:
        return 80.0

    # 百分位计算：(总人数 - 排名) / 总人数 * 100
    percentile = (total_count - latest_score.rank_dir) / total_count * 100

    # 映射到0-100分：前10%=100分，前30%=85分，前50%=70分，后50%=50-70分
    if percentile >= 90:
        return 100.0
    elif percentile >= 70:
        return 85.0 + (percentile - 70) * 0.75
    elif percentile >= 50:
        return 70.0 + (percentile - 50) * 0.75
    else:
        return 50.0 + percentile * 0.4


def _calculate_behavior_score(total_points):
    """计算行为积分得分（百分制）

    基于积分总和标准化，基准：50积分=满分
    """
    if total_points >= 50:
        return 100.0
    elif total_points >= 30:
        return 85.0 + (total_points - 30) * 0.75
    elif total_points >= 10:
        return 70.0 + (total_points - 10) * 0.75
    elif total_points >= 0:
        return 50.0 + total_points * 2.0
    else:
        # 负积分
        return max(0, 50.0 + total_points * 2)


def _calculate_attendance_rate(attendance_data):
    """计算出勤率（百分制）

    基于纪律类积分记录估算
    """
    total_records = attendance_data.get('total_records', 0)

    if total_records == 0:
        return 100.0

    # 计算纪律扣分值
    total_deduction = sum(abs(r.get('points', 0)) for r in attendance_data.get('records', [])
                         if r.get('points', 0) < 0)

    # 每次纪律扣分扣2%，最低60%
    rate = max(60.0, 100.0 - total_deduction * 2)
    return rate


def get_student_list(grade=None, class_name=None, level=None, page=1, per_page=20):
    """获取画像列表（带筛选和分页）

    从 system.db 获取学生列表，关联 portrait.db 的画像数据
    """
    q = Student.query

    # 过滤已转出/离校的学生
    q = q.filter(Student.enrollment_status.notin_(['已转出', '离校']))

    if grade:
        q = q.filter(Student.grade == grade)
    if class_name:
        q = q.filter(Student.class_name == class_name)

    # v1.16.0 正确性修复：level 筛选必须在 SQL WHERE 层下推。
    # 旧版先分页再在 Python 里按 level 过滤，导致：①每页只筛当页数据（跨页丢失匹配项）；
    # ②total 只统计当页匹配数（分页总数错误）。跨库不能 JOIN，故先查 portrait.db 得到
    # 符合 level 的学号集合，再用 student_number IN (...) 下推到 system.db 查询。
    if level:
        level_nos = [r[0] for r in db.session.query(StudentPortrait.student_no).filter(
            StudentPortrait.overall_level == level).all()]
        if level_nos:
            q = q.filter(Student.student_number.in_(level_nos))
        else:
            q = q.filter(Student.id == -1)  # 无匹配画像

    # 获取学生总数（用于分页）——level 已下推，此处为真实总数
    total = q.count()

    # 分页查询学生
    students = (q.order_by(Student.grade.desc(), Student.class_name, Student.student_number)
                .offset((page - 1) * per_page)
                .limit(per_page)
                .all())

    # 批量获取画像数据
    student_nos = [s.student_number for s in students]
    portraits = {}
    if student_nos:
        portrait_list = StudentPortrait.query.filter(
            StudentPortrait.student_no.in_(student_nos)
        ).all()
        portraits = {p.student_no: p for p in portrait_list}

    rows = []
    for s in students:
        p = portraits.get(s.student_number)
        rows.append({
            'student': s,
            'portrait': p.to_dict() if p else None,
        })

    return {
        'rows': rows,
        'total': total,
        'page': page,
        'per_page': per_page,
        'pages': (total + per_page - 1) // per_page,
    }


def get_student_detail(student_no):
    """获取学生画像详情

    聚合所有维度数据 + 评语 + 事件
    """
    student = Student.query.filter_by(student_number=student_no).first()
    if not student:
        return None

    # 获取或计算画像
    portrait = StudentPortrait.query.filter_by(student_no=student_no).first()
    if not portrait:
        # 自动计算画像
        portrait_dict = calculate_portrait(student_no)
    else:
        portrait_dict = portrait.to_dict()

    # 获取各维度详细数据
    trend = agg.get_academic_trend(student_no, limit=10)
    subject_balance = agg.get_subject_balance(student_no)
    points_summary = agg.get_points_summary(student_no)
    dorm_info = agg.get_dormitory_info(student_no)
    attendance = agg.get_attendance_records(student_no)
    comments = agg.get_portrait_comments(student_no)
    events = agg.get_portrait_events(student_no)

    return {
        'student': {
            'no': student.student_number,
            'name': student.name,
            'grade': student.grade,
            'class_name': student.class_name,
            'gender': student.gender,
            'selection': student.subject_selection or '',
        },
        'portrait': portrait_dict,
        'trend': trend,
        'subjects': subject_balance,
        'points': points_summary,
        'dormitory': dorm_info,
        'attendance': attendance,
        'comments': comments,
        'events': events,
    }


def add_comment(student_no, teacher_id, comment_type, content, term):
    """添加评语"""
    comment = PortraitComment(
        student_no=student_no,
        teacher_id=teacher_id,
        comment_type=comment_type,
        content=content,
        term=term,
    )
    db.session.add(comment)
    db.session.commit()
    return comment.to_dict()


def edit_comment(comment_id, content, comment_type=None, term=None):
    """编辑评语"""
    comment = db.session.get(PortraitComment, comment_id)
    if not comment:
        return None
    comment.content = content
    if comment_type:
        comment.comment_type = comment_type
    if term:
        comment.term = term
    db.session.commit()
    return comment.to_dict()


def delete_comment(comment_id):
    """删除评语"""
    comment = db.session.get(PortraitComment, comment_id)
    if not comment:
        return False
    db.session.delete(comment)
    db.session.commit()
    return True


def add_event(student_no, event_type, title, description, event_date, evidence=None, created_by=None):
    """添加事件记录"""
    if isinstance(event_date, str):
        event_date = datetime.strptime(event_date, '%Y-%m-%d').date()

    event = PortraitEvent(
        student_no=student_no,
        event_type=event_type,
        title=title,
        description=description,
        event_date=event_date,
        evidence=evidence,
        created_by=created_by,
    )
    db.session.add(event)
    db.session.commit()
    return event.to_dict()


def delete_event(event_id):
    """删除事件记录"""
    event = db.session.get(PortraitEvent, event_id)
    if not event:
        return False
    db.session.delete(event)
    db.session.commit()
    return True


@cached(ttl=600, key_prefix='portrait_filter_options')
def get_filter_options():
    """获取筛选选项（年级、班级列表）

    v1.16.0：年级/班级为高频低变参考数据，加 600s TTL 缓存（学生年级/班级
    变动后最多 600s 生效）；返回纯数据 dict，可安全缓存。
    """
    grades = sorted([g[0] for g in Student.query.with_entities(Student.grade).distinct().all()
                     if g[0] and g[0] not in ('已转出', '离校')], reverse=True)

    classes = sorted([c[0] for c in Student.query.with_entities(Student.class_name).distinct().all()
                      if c[0] and c[0] not in ('已转出', '离校')])

    return {
        'grades': grades,
        'classes': classes,
    }
