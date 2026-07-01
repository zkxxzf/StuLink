"""寝室分配历史记录模型 — 独立数据库 data/history.db"""
from datetime import datetime
from app.extensions import db


class AssignmentHistory(db.Model):
    __bind_key__ = 'history'
    __tablename__ = 'assignment_history'

    id = db.Column(db.Integer, primary_key=True)

    # 模块标识
    target_type = db.Column(db.String(20), nullable=False, default='dormitory', index=True)

    # 学生快照
    student_id = db.Column(db.Integer, nullable=False, index=True)
    student_name = db.Column(db.String(50), nullable=False)
    grade = db.Column(db.String(10), nullable=False)
    class_name = db.Column(db.String(10), nullable=False)
    gender = db.Column(db.String(2))

    # 宿舍专用字段（其他模块为 NULL）
    room_id = db.Column(db.Integer, nullable=True, index=True)
    room_display = db.Column(db.String(80), nullable=True)
    bed_number = db.Column(db.Integer, nullable=True)

    # 操作信息
    action_type = db.Column(db.String(20), nullable=False, index=True)
    semester = db.Column(db.String(20))
    operator_id = db.Column(db.Integer)
    operator_name = db.Column(db.String(50))

    # 额外上下文（JSON）
    detail = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.now, index=True)

    __table_args__ = (
        db.Index('idx_ah_target_type', 'target_type'),
        db.Index('idx_ah_student_time', 'student_id', 'created_at'),
        db.Index('idx_ah_room_time', 'room_id', 'created_at'),
        db.Index('idx_ah_action_time', 'action_type', 'created_at'),
        db.Index('idx_ah_grade_class', 'grade', 'class_name'),
    )

# StuLink v1.5.0 2026-07-01
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0
