# StuLink v1.9.3 2026-09-19
# 积分管理：数据模型（独立库 points.db）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from datetime import datetime, date
from app.extensions import db


class PointRecord(db.Model):
    """积分记录：一条记录 = 一次加减分（学生属性快照，跨库不设外键）"""
    __bind_key__ = 'points'
    __tablename__ = 'point_records'

    id = db.Column(db.Integer, primary_key=True)
    student_no = db.Column(db.String(20), nullable=False)   # 学号快照
    student_name = db.Column(db.String(50))                 # 姓名快照
    grade = db.Column(db.String(10))                        # 年级快照
    class_name = db.Column(db.String(10))                   # 班级快照
    points = db.Column(db.Integer, nullable=False)          # 分值：正=加分，负=扣分
    category = db.Column(db.String(20))                     # 类别：纪律/学习/卫生/活动/其他
    reason = db.Column(db.String(200), nullable=False)      # 事由（必填）
    remark = db.Column(db.String(200))                      # 备注
    recorded_at = db.Column(db.Date, default=date.today)    # 积分日期
    operator_id = db.Column(db.Integer)                     # 操作人（user_id）
    operator_name = db.Column(db.String(50))                # 操作人姓名快照
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        db.Index('idx_point_student', 'student_no'),
        db.Index('idx_point_grade_class', 'grade', 'class_name'),
        db.Index('idx_point_date', 'recorded_at'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'student_no': self.student_no,
            'student_name': self.student_name,
            'grade': self.grade,
            'class_name': self.class_name,
            'points': self.points,
            'category': self.category or '',
            'reason': self.reason,
            'remark': self.remark or '',
            'recorded_at': self.recorded_at.strftime('%Y-%m-%d') if self.recorded_at else '',
            'operator_name': self.operator_name or '',
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M') if self.created_at else '',
        }
