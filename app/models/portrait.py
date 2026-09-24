# StuLink v1.18.2.1 2026-09-24
# 学生画像模块模型（独立库 portrait.db）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""学生画像数据模型

三张核心表（均绑定 portrait.db）：
- student_portraits：学生画像主表，存储各维度标准化评分与综合评级
- portrait_comments：教师评语（学期评语 / 操行评语 / 班主任评语）
- portrait_events：重要事件记录（荣誉 / 违纪 / 活动 / 其他）

数据关联：经学号快照关联 system.db 学生基础信息（跨库不设外键，
任一来源模块异常不影响画像模块自身与系统管理基础数据）。
"""
from datetime import datetime, date

from app.extensions import db


class StudentPortrait(db.Model):
    """学生画像主表：存储各维度标准化评分与综合评级"""
    __bind_key__ = 'portrait'
    __tablename__ = 'student_portraits'

    id = db.Column(db.Integer, primary_key=True)
    student_no = db.Column(db.String(30), nullable=False, index=True)  # 学号快照
    grade = db.Column(db.String(20))          # 年级快照
    class_name = db.Column(db.String(30))     # 班级快照
    academic_score = db.Column(db.Float, default=0)      # 学业得分（标准化）
    behavior_score = db.Column(db.Float, default=0)      # 行为积分（标准化）
    dormitory_score = db.Column(db.Float, default=0)     # 宿舍表现（标准化）
    attendance_rate = db.Column(db.Float, default=100)   # 出勤率（%）
    overall_level = db.Column(db.String(20))             # 综合评级（A/B/C/D）
    last_updated = db.Column(db.DateTime, default=datetime.now)

    __table_args__ = (
        db.UniqueConstraint('student_no', name='uq_portrait_student'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'student_no': self.student_no,
            'grade': self.grade or '',
            'class_name': self.class_name or '',
            'academic_score': self.academic_score,
            'behavior_score': self.behavior_score,
            'dormitory_score': self.dormitory_score,
            'attendance_rate': self.attendance_rate,
            'overall_level': self.overall_level or '',
            'last_updated': self.last_updated.strftime('%Y-%m-%d %H:%M') if self.last_updated else '',
        }

    def __repr__(self):
        return f'<StudentPortrait {self.student_no}>'


class PortraitComment(db.Model):
    """评语表：教师评语（学期评语 / 操行评语 / 班主任评语）"""
    __bind_key__ = 'portrait'
    __tablename__ = 'portrait_comments'

    id = db.Column(db.Integer, primary_key=True)
    student_no = db.Column(db.String(30), nullable=False, index=True)  # 学号快照
    teacher_id = db.Column(db.Integer, nullable=False)    # 教师用户 ID
    comment_type = db.Column(db.String(20))               # 学期评语/操行评语/班主任评语
    content = db.Column(db.Text)                          # 评语内容
    term = db.Column(db.String(20))                       # 学期（如 2025-2026-1）
    created_at = db.Column(db.DateTime, default=datetime.now)

    __table_args__ = (
        db.Index('idx_comment_student', 'student_no'),
        db.Index('idx_comment_term', 'term'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'student_no': self.student_no,
            'teacher_id': self.teacher_id,
            'comment_type': self.comment_type or '',
            'content': self.content or '',
            'term': self.term or '',
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M') if self.created_at else '',
        }

    def __repr__(self):
        return f'<PortraitComment {self.student_no} {self.term}>'


class PortraitEvent(db.Model):
    """事件表：重要事件记录（荣誉 / 违纪 / 活动 / 其他）"""
    __bind_key__ = 'portrait'
    __tablename__ = 'portrait_events'

    id = db.Column(db.Integer, primary_key=True)
    student_no = db.Column(db.String(30), nullable=False, index=True)  # 学号快照
    event_type = db.Column(db.String(30))                 # 荣誉/违纪/活动/其他
    title = db.Column(db.String(100))                     # 事件标题
    description = db.Column(db.Text)                      # 事件描述
    event_date = db.Column(db.Date)                       # 事件发生日期
    evidence = db.Column(db.String(200))                  # 附件路径
    created_by = db.Column(db.Integer)                    # 创建人用户 ID
    created_at = db.Column(db.DateTime, default=datetime.now)

    __table_args__ = (
        db.Index('idx_event_student', 'student_no'),
        db.Index('idx_event_type', 'event_type'),
        db.Index('idx_event_date', 'event_date'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'student_no': self.student_no,
            'event_type': self.event_type or '',
            'title': self.title or '',
            'description': self.description or '',
            'event_date': self.event_date.strftime('%Y-%m-%d') if self.event_date else '',
            'evidence': self.evidence or '',
            'created_by': self.created_by,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M') if self.created_at else '',
        }

    def __repr__(self):
        return f'<PortraitEvent {self.student_no} {self.event_type}>'
