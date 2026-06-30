# StuLink v1.4.6 2026-06-30
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0
from datetime import datetime
from app.extensions import db


class Room(db.Model):
    __bind_key__ = 'dormitory'
    __tablename__ = 'rooms'

    id = db.Column(db.Integer, primary_key=True)
    building = db.Column(db.String(50), nullable=False, default='')  # 宿舍楼名�?
    room_number = db.Column(db.String(10), nullable=False)  # 201
    gender = db.Column(db.String(2), nullable=False)  # �?�?
    floor = db.Column(db.Integer, nullable=False)
    capacity = db.Column(db.Integer, nullable=False, default=8)
    grade = db.Column(db.String(10))      # 宿管分配的年�?
    class_name = db.Column(db.String(10)) # 宿管分配的班�?
    combined_class = db.Column(db.String(10))  # 合班
    notes = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    # 关联床位
    beds = db.relationship('BedAssignment', back_populates='room', lazy='dynamic',
                           order_by='BedAssignment.bed_number')

    __table_args__ = (
        db.UniqueConstraint('building', 'room_number', name='uq_building_room'),
        db.Index('idx_room_building_floor', 'building', 'floor'),
        db.Index('idx_room_gender_grade', 'gender', 'grade'),
        db.Index('idx_room_grade_class', 'grade', 'class_name'),
        db.Index('idx_room_is_active', 'is_active'),
    )

    @property
    def display_name(self):
        """显示名称：宿舍楼 + 房间�?""
        if self.building:
            return f"{self.building} {self.room_number}"
        return self.room_number

    @property
    def occupancy(self):
        """当前入住人数"""
        return self.beds.filter(
            db.and_(BedAssignment.student_id.isnot(None))
        ).count()

    @property
    def occupancy_display(self):
        return f"{self.occupancy}/{self.capacity}"


class BedAssignment(db.Model):
    __bind_key__ = 'dormitory'
    __tablename__ = 'bed_assignments'

    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.Integer, db.ForeignKey('rooms.id'), nullable=False)
    bed_number = db.Column(db.Integer, nullable=False)  # 1-8
    # 跨库引用：System.db �?students / users，FK 已拆�?
    student_id = db.Column(db.Integer, nullable=True, index=True)
    assigned_by = db.Column(db.Integer)
    assigned_at = db.Column(db.DateTime, default=datetime.now)

    # 跨库 relationship（viewonly�?
    room = db.relationship('Room', back_populates='beds')
    student = db.relationship(
        'Student',
        primaryjoin='BedAssignment.student_id == Student.id',
        foreign_keys=[student_id],
        viewonly=True
    )
    assigner = db.relationship(
        'User',
        primaryjoin='BedAssignment.assigned_by == User.id',
        foreign_keys=[assigned_by],
        viewonly=True
    )

    __table_args__ = (
        db.UniqueConstraint('room_id', 'bed_number', name='uq_room_bed'),
        db.Index('idx_bed_student', 'student_id'),
        db.Index('idx_bed_room', 'room_id'),
    )
