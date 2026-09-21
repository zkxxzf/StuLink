# StuLink v1.17.0 2026-09-21
# 学生画像数据汇聚服务：从各数据库只读汇聚学生数据
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""跨库数据汇聚服务

设计原则：
- 只读汇聚：从 grades/points/dormitory/academic 等数据库读取数据
- 不修改各模块原始数据
- 通过 Flask-SQLAlchemy 的 bind_key 机制自动路由到对应数据库
"""
from datetime import datetime, date, timedelta
from sqlalchemy import func, desc, case

from app.extensions import db
from app.models.grades import Exam, ExamScore, TOTAL_SUBJECT, SUBJECTS
from app.models.points import PointRecord
from app.models.portrait import PortraitComment, PortraitEvent
from app.models.room import Room, BedAssignment
from app.models.academic import InspectionRecord


def get_academic_trend(student_no, limit=10):
    """从 grades.db 获取成绩趋势数据

    返回：[{exam_name, exam_date, total_score, rank_dir, rank_class, avg_score}, ...]
    最近 limit 次考试（按时间倒序）
    """
    # 查询该学生的总分记录
    # v1.16.0 性能改造：一次 JOIN 同时取出成绩与考试信息，避免循环内 Exam.query.get 的 N+1；
    # 年级平均分改为一次 GROUP BY 批量求取，避免每场考试一条 avg 查询。口径与旧版一致。
    rows = (db.session.query(ExamScore, Exam)
            .filter(ExamScore.student_no == student_no,
                    ExamScore.subject == TOTAL_SUBJECT,
                    ExamScore.score.isnot(None))
            .join(Exam, ExamScore.exam_id == Exam.id)
            .order_by(Exam.exam_date.desc())
            .limit(limit)
            .all())

    if not rows:
        return []

    exam_ids = [exam.id for _s, exam in rows]
    avg_map = dict(db.session.query(
        ExamScore.exam_id, func.avg(ExamScore.score)
    ).filter(
        ExamScore.exam_id.in_(exam_ids),
        ExamScore.subject == TOTAL_SUBJECT,
        ExamScore.score.isnot(None)
    ).group_by(ExamScore.exam_id).all())

    result = []
    for s, exam in rows:
        avg_result = avg_map.get(exam.id)
        result.append({
            'exam_id': exam.id,
            'exam_name': exam.name,
            'exam_date': exam.exam_date.strftime('%Y-%m-%d'),
            'total_score': s.score,
            'rank_dir': s.rank_dir,
            'rank_class': s.rank_class,
            'avg_score': round(avg_result, 1) if avg_result else None,
        })

    return list(reversed(result))  # 按时间正序返回


def get_subject_balance(student_no, exam_id=None):
    """从 grades.db 获取学科均衡度

    返回：[{subject, score, full_mark, percentage}, ...]
    如果 exam_id 为空，取最近一场考试
    """
    if exam_id:
        exam = Exam.query.get(exam_id)
    else:
        # 获取该学生最近一场考试
        latest = (ExamScore.query
                  .filter(ExamScore.student_no == student_no,
                          ExamScore.subject == TOTAL_SUBJECT,
                          ExamScore.score.isnot(None))
                  .join(Exam, ExamScore.exam_id == Exam.id)
                  .order_by(Exam.exam_date.desc())
                  .first())
        exam_id = latest.exam_id if latest else None
        exam = Exam.query.get(exam_id) if exam_id else None

    if not exam:
        return []

    full_marks = exam.full_marks()
    scores = ExamScore.query.filter_by(
        exam_id=exam.id, student_no=student_no
    ).filter(ExamScore.subject != TOTAL_SUBJECT).all()

    result = []
    for s in scores:
        fm = full_marks.get(s.subject, 100)
        result.append({
            'subject': s.subject,
            'score': s.score,
            'full_mark': fm,
            'percentage': round(s.score / fm * 100, 1) if fm else 0,
        })

    return result


def get_points_summary(student_no):
    """从 points.db 获取积分汇总

    返回：{total, this_month, this_semester, category_breakdown: [{category, count, total_points}]}
    """
    today = date.today()
    month_start = today.replace(day=1)
    # 学期：9月~次年1月=上学期，2月~8月=下学期
    if today.month >= 9:
        semester_start = date(today.year, 9, 1)
    else:
        semester_start = date(today.year, 2, 1)

    # 总积分 / 本月积分 / 本学期积分
    # v1.16.0 性能改造：三次独立 sum 合并为一次条件聚合（CASE WHEN），4 条 SQL → 2 条，口径一致。
    total, this_month, this_semester = db.session.query(
        func.coalesce(func.sum(PointRecord.points), 0),
        func.coalesce(func.sum(case((PointRecord.recorded_at >= month_start, PointRecord.points), else_=0)), 0),
        func.coalesce(func.sum(case((PointRecord.recorded_at >= semester_start, PointRecord.points), else_=0)), 0),
    ).filter(PointRecord.student_no == student_no).one()

    # 类别分布
    category_stats = (PointRecord.query
                      .filter(PointRecord.student_no == student_no)
                      .with_entities(
                          PointRecord.category,
                          func.count(PointRecord.id).label('count'),
                          func.sum(PointRecord.points).label('total_points')
                      )
                      .group_by(PointRecord.category)
                      .all())

    category_breakdown = [{
        'category': c.category or '其他',
        'count': c.count,
        'total_points': c.total_points or 0,
    } for c in category_stats]

    return {
        'total': total or 0,
        'this_month': this_month or 0,
        'this_semester': this_semester or 0,
        'category_breakdown': category_breakdown,
    }


def get_dormitory_info(student_no):
    """从 dormitory.db 获取宿舍信息

    返回：{room_number, building, has_record}
    注：当前宿舍模块无检查扣分记录，仅返回宿舍分配信息
    """
    # 通过学号找到学生，再通过学生ID找宿舍
    from app.models import Student
    student = Student.query.filter_by(student_number=student_no).first()
    if not student:
        return {'room_number': '', 'building': '', 'has_record': False}

    # v1.16.0 性能改造：床位与房间同库(dormitory)，一次 JOIN 取回，避免 bed + room.get 两条查询。
    row = db.session.query(
        BedAssignment.bed_number, Room.room_number, Room.building
    ).join(Room, BedAssignment.room_id == Room.id).filter(
        BedAssignment.student_id == student.id
    ).first()
    if not row:
        return {'room_number': '', 'building': '', 'has_record': False}

    return {
        'room_number': row.room_number,
        'building': row.building,
        'bed_number': row.bed_number,
        'has_record': False,  # 当前无宿舍检查扣分记录
    }


def get_attendance_records(student_no):
    """获取考勤/查课相关记录

    注：academic.db 的 InspectionRecord 是查课记录（检查教师上课情况），
    不是学生考勤记录。这里通过积分中的纪律类记录作为考勤参考。

    返回：{total_records, records: [{date, category, reason, points}]}
    """
    # 查询纪律类积分记录（作为考勤参考）
    records = (PointRecord.query
               .filter(PointRecord.student_no == student_no,
                       PointRecord.category == '纪律')
               .order_by(PointRecord.recorded_at.desc())
               .limit(50)
               .all())

    result = [{
        'date': r.recorded_at.strftime('%Y-%m-%d') if r.recorded_at else '',
        'category': r.category,
        'reason': r.reason,
        'points': r.points,
        'operator_name': r.operator_name or '',
    } for r in records]

    return {
        'total_records': len(result),
        'records': result,
    }


def get_portrait_comments(student_no):
    """从 portrait.db 获取评语列表

    返回：[{id, teacher_id, comment_type, content, term, created_at}, ...]
    """
    comments = (PortraitComment.query
                .filter_by(student_no=student_no)
                .order_by(PortraitComment.created_at.desc())
                .all())
    return [c.to_dict() for c in comments]


def get_portrait_events(student_no):
    """从 portrait.db 获取事件列表

    返回：[{id, event_type, title, description, event_date, evidence, created_at}, ...]
    """
    events = (PortraitEvent.query
              .filter_by(student_no=student_no)
              .order_by(PortraitEvent.event_date.desc())
              .all())
    return [e.to_dict() for e in events]
