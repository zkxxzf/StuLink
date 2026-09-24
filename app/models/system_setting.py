"""系统级配置模型（存库，可在「系统管理 → 学校设置」中维护）"""
# StuLink v1.18.1.0 2026-09-24
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from datetime import datetime

from app.extensions import db


class SystemSetting(db.Model):
    """系统配置键值对

    与 config.py 的分工：
      - config.py：部署级配置（密钥、数据库路径等），改了要重启
      - system_settings 表：运行期可维护的业务配置（如学校名称），改了立即生效
    """
    __tablename__ = 'system_settings'

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), unique=True, nullable=False, comment='配置项键名')
    value = db.Column(db.Text, comment='配置项值')
    description = db.Column(db.String(200), comment='配置项说明')
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    updated_by = db.Column(db.Integer, db.ForeignKey('users.id'), comment='最后修改人')

    operator = db.relationship('User', foreign_keys=[updated_by])

    @classmethod
    def get(cls, key, default=None):
        """读取配置值"""
        row = cls.query.filter_by(key=key).first()
        if row and row.value is not None:
            return row.value
        return default

    @classmethod
    def set(cls, key, value, user_id=None, description=None):
        """写入配置值（已存在则更新），返回模型实例；由调用方负责 commit"""
        row = cls.query.filter_by(key=key).first()
        if not row:
            row = cls(key=key)
            db.session.add(row)
        row.value = value
        if description and not row.description:
            row.description = description
        row.updated_at = datetime.now()
        row.updated_by = user_id
        return row

    def to_dict(self):
        return {
            'key': self.key,
            'value': self.value or '',
            'description': self.description or '',
            'updated_at': self.updated_at.strftime('%Y-%m-%d %H:%M') if self.updated_at else '',
        }
