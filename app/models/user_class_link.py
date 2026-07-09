"""用户-班级多对多关联：一个人可管多个班，一个班最多3个班主任"""
from app.extensions import db


class UserClassLink(db.Model):
    """用户与班级的多对多关联"""
# StuLink v1.6.1 2026-07-09
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0
    __tablename__ = 'user_class_links'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    grade = db.Column(db.String(10), nullable=False)
    class_name = db.Column(db.String(10), nullable=False)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'grade', 'class_name', name='uq_user_class'),
        db.Index('idx_ucl_grade_class', 'grade', 'class_name'),
    )

    user = db.relationship('User', backref='class_links')

    def __repr__(self):
        return f'<UserClassLink {self.grade}{self.class_name} → user:{self.user_id}>'


