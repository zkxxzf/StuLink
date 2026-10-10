# StuLink v1.7.0 2026-08-02
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
    # 2026-10-09：是否启用（在用班级）。班级档案里可能混着历史/批量导入的班级，
    # 教务各页（课表 / 查课 / 调课 / 晚自习）的班级下拉只取 is_active=True 的班，
    # 避免几百个未启用班级把下拉撑爆。由 scripts/migrate_class_active_20261009.py 按课表回填。
    is_active = db.Column(db.Boolean, default=True, nullable=False)
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
        """返回该班级的选科列表（字符串list）。

        性能（2026-10-09）：subjects 是 lazy='dynamic' 关系，`self.subjects.…`
        **每次访问都会发一条 SQL**；一次请求里 subject_list / subject_display 常被
        连用，因此在同一实例内缓存一次结果（选科被改动后请调 invalidate_subjects()）。
        批量场景（如 480 个班的档案汇总）请勿逐个访问，改用一次性 JOIN/全量取回。
        """
        cached = self.__dict__.get('_subject_list_cache')
        if cached is None:
            cached = [s.subject_value
                      for s in self.subjects.order_by(ClassSubject.id).all()]
            self.__dict__['_subject_list_cache'] = cached
        return list(cached)

    def invalidate_subjects(self):
        """选科明细改动后清掉实例级缓存（配合 subject_list 缓存）。"""
        self.__dict__.pop('_subject_list_cache', None)

    @property
    def subject_display(self):
        """选科展示：物化生、史政地"""
        lst = self.subject_list
        return '、'.join(lst) if lst else '—'

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


