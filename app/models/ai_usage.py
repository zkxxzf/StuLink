# StuLink 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""P2：AI 每日调用配额（按用户 × 自然日）。"""
from datetime import datetime

from app.extensions import db


class AiUsage(db.Model):
    __bind_key__ = 'system'
    __tablename__ = 'ai_usage'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)
    day = db.Column(db.String(10), nullable=False)       # YYYY-MM-DD
    count = db.Column(db.Integer, nullable=False, default=0)
    updated_at = db.Column(db.DateTime, default=datetime.now,
                           onupdate=datetime.now)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'day', name='uq_ai_usage_user_day'),
        db.Index('idx_ai_usage_day', 'day'),
    )

    def __repr__(self):
        return f'<AiUsage user={self.user_id} {self.day}={self.count}>'
