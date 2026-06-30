"""年级毕业状态模�?""
from datetime import datetime
from app.extensions import db


class GradeSetting(db.Model):
    __tablename__ = 'grade_settings'

    id = db.Column(db.Integer, primary_key=True)
    grade = db.Column(db.String(20), unique=True, nullable=False, comment='年级名称，如 2023�?)
    is_graduated = db.Column(db.Boolean, default=False, comment='是否已毕�?)
    graduated_at = db.Column(db.DateTime, comment='毕业操作时间')
    backup_path = db.Column(db.String(200), comment='备份文件路径')
    graduated_by = db.Column(db.Integer, db.ForeignKey('users.id'), comment='操作�?)

    operator = db.relationship('User', foreign_keys=[graduated_by])

    def to_dict(self):
        return {
            'grade': self.grade,
            'is_graduated': self.is_graduated,
            'graduated_at': self.graduated_at.strftime('%Y-%m-%d %H:%M') if self.graduated_at else '',
            'backup_path': self.backup_path or '',
        }

# StuLink v1.4.6 2026-06-30
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0
