# StuLink v1.19.50.54 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""微信绑定与扫码票据。

一个 openid 最多绑定 MAX_WECHAT_BINDINGS 个系统账号；
扫码票据一次性使用，过期即失效，避免二维码被转发后二次登录。
"""
from datetime import datetime

from app.extensions import db

MAX_WECHAT_BINDINGS = 2


class WeChatBinding(db.Model):
    __tablename__ = 'wechat_bindings'

    id = db.Column(db.Integer, primary_key=True)
    openid = db.Column(db.String(64), nullable=False, index=True)
    unionid = db.Column(db.String(64), default='')
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                        nullable=False, index=True)
    nickname = db.Column(db.String(80), default='')
    created_at = db.Column(db.DateTime, default=datetime.now)
    last_login_at = db.Column(db.DateTime)

    __table_args__ = (
        db.UniqueConstraint('openid', 'user_id',
                            name='uq_wechat_openid_user'),
    )


class WeChatTicket(db.Model):
    __tablename__ = 'wechat_tickets'

    id = db.Column(db.Integer, primary_key=True)
    ticket = db.Column(db.String(80), unique=True, nullable=False,
                       index=True)
    action = db.Column(db.String(12), nullable=False)  # login / bind
    status = db.Column(db.String(12), default='pending',
                       nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)
    openid = db.Column(db.String(64), default='', index=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    approved_at = db.Column(db.DateTime)
    consumed_at = db.Column(db.DateTime)
