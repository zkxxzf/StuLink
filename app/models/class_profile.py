# StuLink v1.6.1 2026-07-09
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from datetime import datetime
from app.extensions import db


class ClassProfile(db.Model):
    """班型设置：每个(年级, 班级)组合的班型、选科方向和选科组合"""
    __tablename__ = 'class_profiles'

    id = db.Column(db.Integer, primary_key=True)
    grade = db.Column(db.String(10), nullable=False)
    class_name = db.Column(db.String(10), nullable=False)
    class_type = db.Column(db.String(20), nullable=True)       # 强基班 / 卓越班
    subject_direction = db.Column(db.String(10), nullable=True)  # 物理 / 历史
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    # 一对多：一个班可以有多种选科组合
    subjects = db.relationship('ClassSubject', backref='class_profile', lazy='dynamic',
                               cascade='all, delete-orphan')

    __table_args__ = (
        db.UniqueConstraint('grade', 'class_name', name='uq_class_profile'),
        db.Index('idx_class_profile_grade', 'grade'),
    )

    @property
    def subject_list(self):
        """返回该班级的选科列表（字符串list）"""
        return [s.subject_value for s in self.subjects.order_by(ClassSubject.id).all()]

    @property
    def subject_display(self):
        """选科展示：物化生、史政地"""
        return '、'.join(self.subject_list) if self.subject_list else '—'

    def __repr__(self):
        return f'<ClassProfile {self.grade}{self.class_name} {self.class_type or "-"}>'


class ClassSubject(db.Model):
    """班级选科明细（一对多）"""
    __tablename__ = 'class_subjects'

    id = db.Column(db.Integer, primary_key=True)
    class_profile_id = db.Column(db.Integer, db.ForeignKey('class_profiles.id'), nullable=False)
    subject_value = db.Column(db.String(20), nullable=False)

    __table_args__ = (
        db.UniqueConstraint('class_profile_id', 'subject_value', name='uq_class_subject'),
    )

    def __repr__(self):
        return f'<ClassSubject {self.subject_value}>'


