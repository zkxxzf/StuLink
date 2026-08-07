# StuLink v1.7.0 2026-08-02
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from datetime import datetime
from app.extensions import db


class Room(db.Model):
    __bind_key__ = 'dormitory'
    __tablename__ = 'rooms'

    id = db.Column(db.Integer, primary_key=True)
    building = db.Column(db.String(50), nullable=False, default='')  # 宿舍楼名称
    room_number = db.Column(db.String(10), nullable=False)  # 201
    gender = db.Column(db.String(2), nullable=False)  # 男/女
    floor = db.Column(db.Integer, nullable=False)
    capacity = db.Column(db.Integer, nullable=False, default=8)
    grade = db.Column(db.String(10))      # 宿管分配的年级
    class_name = db.Column(db.String(100))  # 宿管分配的班级（合班时存完整合班名，多个班用+拼接，不分主次）
    combined_class = db.Column(db.String(100))  # 合班（兼容字段：与 class_name 同步；含+即合班宿舍）
    combined_details = db.Column(db.Text)  # 合班详情JSON: [{"class_name":"07班","count":4},{"class_name":"08班","count":4}]
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
        """显示名称：宿舍楼 + 房间号"""
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

    @property
    def is_combined(self):
        """是否为合班宿舍：class_name 含多个班（+）即自动识别，无需手动设置"""
        if self.class_name and '+' in self.class_name:
            return True
        return bool(self.combined_class and self.combined_class.strip())

    @property
    def class_list(self):
        """班级列表（合班名自动拆分，不分主次）"""
        if not self.class_name:
            return []
        return [c.strip() for c in self.class_name.split('+') if c.strip()]

    @property
    def combined_name(self):
        """合班完整名称（兼容历史数据：合班名可能存于 combined_class）"""
        if self.class_name and '+' in self.class_name:
            return self.class_name
        if self.combined_class and '+' in self.combined_class:
            return self.combined_class
        return None


class BedAssignment(db.Model):
    __bind_key__ = 'dormitory'
    __tablename__ = 'bed_assignments'

    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.Integer, db.ForeignKey('rooms.id'), nullable=False)
    bed_number = db.Column(db.Integer, nullable=False)  # 1-8
    # 跨库引用：System.db 的 students / users，FK 已拆除
    student_id = db.Column(db.Integer, nullable=True, index=True)
    assigned_by = db.Column(db.Integer)
    assigned_at = db.Column(db.DateTime, default=datetime.now)

    # 跨库 relationship（viewonly）
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


class StudentAccommodation(db.Model):
    __bind_key__ = 'dormitory'
    __tablename__ = 'student_accommodation'

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, nullable=False, unique=True, index=True)
    boarding_type = db.Column(db.String(10))
    day_student_type = db.Column(db.String(20))
    textbook = db.Column(db.String(50))
    teacher_notes = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)


