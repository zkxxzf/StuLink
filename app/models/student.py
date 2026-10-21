# StuLink v1.7.0 2026-08-02
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from datetime import datetime

from sqlalchemy import or_

from app.extensions import db
from app.utils.crypto import (blind_index, decrypt, encrypt, encrypt_rand,
                              id_card_index)


class Student(db.Model):
    __tablename__ = 'students'

    id = db.Column(db.Integer, primary_key=True)
    # 身份证号透明加密：数据库存密文，Python 侧自动加解密
    # 2026-10-19 H-06：密文改随机 IV；唯一性/等值查询改走 id_card_idx 盲索引。
    _id_card_encrypted = db.Column('id_card_number', db.String(256))
    id_card_idx = db.Column(db.String(64), index=True)
    name = db.Column(db.String(50), nullable=False)
    ethnicity = db.Column(db.String(20), default='汉族')
    # 2026-10-18（用户定方案 C）：手机号改成**随机 IV 加密**存储 ——
    # 确定性加密（身份证那套）对手机号不安全：号段有限，可被枚举对照。
    # 精确查询改走 phone1_idx / phone2_idx（HMAC 盲索引，不可逆）；
    # 模糊/片段查询走"解密候选集后内存过滤"（全校 2114 人，毫秒级）。
    _phone1_encrypted = db.Column('phone1', db.String(256))
    _phone2_encrypted = db.Column('phone2', db.String(256))
    phone1_idx = db.Column(db.String(64))
    phone2_idx = db.Column(db.String(64))
    gender = db.Column(db.String(2), nullable=False)  # 男/女
    student_number = db.Column(db.String(20), unique=True, nullable=False)  # 业务主键，必填
    # 2026-10-20：智学网考号（智学网给的 8 位号，如 59468662）。
    # 学生档案上的**固定**考号（与每场考试可能重排的 exam_scores.exam_no 不同），
    # 用于把智学网侧的名单/成绩按考号回挂到本系统（不必再靠姓名 + 班级猜）。
    zhixue_no = db.Column(db.String(20), index=True)
    # 2026-10-21：智学网**账号**（10 位，如 4159474334）。准考证号每场考试
    # 会重排（同一届 47637703 → 47642671），账号是稳定的，导入时两个都试。
    zhixue_account = db.Column(db.String(20), index=True)
    grade = db.Column(db.String(10), nullable=False)     # 2025级
    class_name = db.Column(db.String(10), nullable=False) # 01班
    subject_selection = db.Column(db.String(20))  # 史政地
    enrollment_status = db.Column(db.String(20))  # 借读/在籍不在校/转入
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

    # 关联住宿信息（跨库 relationship，viewonly）
    accommodation = db.relationship(
        'StudentAccommodation',
        primaryjoin='Student.id == StudentAccommodation.student_id',
        foreign_keys='StudentAccommodation.student_id',
        viewonly=True,
        uselist=False
    )

    # 身份证号透明加解密（确定性加密，支持等值查询）
    @property
    def id_card_number(self):
        # M-5：解密失败不再回传密文原文，展示侧降级为 None（原因见 crypto 日志）
        if not self._id_card_encrypted:
            return None
        try:
            return decrypt(self._id_card_encrypted)
        except Exception:
            return None

    @id_card_number.setter
    def id_card_number(self, value):
        # H-06：随机 IV 写密文 + 盲索引；旧确定性密文仍可 decrypt。
        self._id_card_encrypted = encrypt_rand(value) if value else None
        self.id_card_idx = id_card_index(value) if value else None

    @staticmethod
    def encrypt_id_card(plaintext):
        """兼容旧调用：返回身份证盲索引（不再返回确定性密文）。"""
        return id_card_index(plaintext) if plaintext else None

    @staticmethod
    def id_card_filter(value):
        """身份证等值查询条件：盲索引优先，兼容存量确定性密文双读。"""
        idx = id_card_index(value)
        if not idx:
            return None
        legacy = encrypt(value)
        return or_(Student.id_card_idx == idx,
                   Student._id_card_encrypted == legacy)

    # ---- 手机号透明加解密（随机 IV + 盲索引） ----
    def _get_phone(self, enc):
        if not enc:
            return None
        try:
            return decrypt(enc)
        except Exception:
            return None

    @property
    def phone1(self):
        return self._get_phone(self._phone1_encrypted)

    @phone1.setter
    def phone1(self, value):
        self._phone1_encrypted = encrypt_rand(value) if value else None
        self.phone1_idx = blind_index(value) if value else None

    @property
    def phone2(self):
        return self._get_phone(self._phone2_encrypted)

    @phone2.setter
    def phone2(self, value):
        self._phone2_encrypted = encrypt_rand(value) if value else None
        self.phone2_idx = blind_index(value) if value else None

    @staticmethod
    def phone_index(value):
        """手机号盲索引（用于加密后的**精确**等值查询）"""
        return blind_index(value)

    __table_args__ = (
        db.Index('idx_student_grade_class', 'grade', 'class_name'),
        db.Index('idx_student_gender', 'gender'),
        db.Index('idx_student_id_card', 'id_card_number'),
        db.Index('idx_student_number', 'student_number'),
    )
