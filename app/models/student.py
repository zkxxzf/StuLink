# StuLink v1.5.0 2026-07-01
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0
from datetime import datetime
from app.extensions import db
from app.utils.crypto import encrypt, decrypt


class Student(db.Model):
    __tablename__ = 'students'

    id = db.Column(db.Integer, primary_key=True)
    # 身份证号透明加密：数据库存密文，Python 侧自动加解密
    _id_card_encrypted = db.Column('id_card_number', db.String(256))
    name = db.Column(db.String(50), nullable=False)
    ethnicity = db.Column(db.String(20), default='汉族')
    phone1 = db.Column(db.String(20))
    phone2 = db.Column(db.String(20))
    gender = db.Column(db.String(2), nullable=False)  # 男/女
    student_number = db.Column(db.String(20), unique=True, nullable=False)  # 业务主键，必填
    grade = db.Column(db.String(10), nullable=False)     # 2025级
    class_name = db.Column(db.String(10), nullable=False) # 01班
    original_class = db.Column(db.String(10))
    subject_selection = db.Column(db.String(20))  # 史政地
    boarding_type = db.Column(db.String(10), nullable=False)  # 住校/男走读/女走读/离校
    day_student_type = db.Column(db.String(20))
    enrollment_status = db.Column(db.String(20))  # 借读/在籍不在校/转入
    textbook = db.Column(db.String(50))
    teacher_notes = db.Column(db.Text)
    enrollment_notes = db.Column(db.String(100))
    graduation_school_code = db.Column(db.String(10))  # 毕业学校代码（如0440）
    graduation_school = db.Column(db.String(100))      # 毕业学校名称
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    # 关联床位（跨库 relationship，viewonly）
    bed_assignment = db.relationship(
        'BedAssignment',
        primaryjoin='Student.id == BedAssignment.student_id',
        foreign_keys='BedAssignment.student_id',
        viewonly=True,
        uselist=False
    )

    # 身份证号透明加解密（确定性加密，支持等值查询）
    @property
    def id_card_number(self):
        return decrypt(self._id_card_encrypted) if self._id_card_encrypted else None

    @id_card_number.setter
    def id_card_number(self, value):
        self._id_card_encrypted = encrypt(value) if value else None

    @staticmethod
    def encrypt_id_card(plaintext):
        """加密身份证号（用于查询时构造等值条件）"""
        return encrypt(plaintext) if plaintext else None

    __table_args__ = (
        db.Index('idx_student_grade_class', 'grade', 'class_name'),
        db.Index('idx_student_gender', 'gender'),
        db.Index('idx_student_boarding', 'boarding_type'),
        db.Index('idx_student_id_card', 'id_card_number'),
        db.Index('idx_student_number', 'student_number'),
    )
