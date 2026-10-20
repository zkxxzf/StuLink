# StuLink 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""登录限流/账号锁定持久化表（P1 M-01）。

旧实现把计数放在进程内 `SimpleCache`：重启清零、多进程不共享，攻击者只要
等到服务重启或换一个 worker 就能继续猜口令。这张表把「IP 窗口计数」与
「来源+账号 锁定」落到 system.db，waitress 多线程/重启后仍然生效。
"""
from app.extensions import db


class LoginThrottle(db.Model):
    __tablename__ = 'login_throttles'

    id = db.Column(db.Integer, primary_key=True)
    # 语义键：ip:<ip> / acct:<ip>:<username>；唯一约束保证 upsert 不重复
    throttle_key = db.Column('key', db.String(160), unique=True,
                             nullable=False, index=True)
    count = db.Column(db.Integer, default=0, nullable=False)
    # epoch 秒：窗口起点 / 锁定截止（0/NULL 表示未锁）
    window_start = db.Column(db.Float, nullable=False)
    locked_until = db.Column(db.Float)
    updated_at = db.Column(db.Float, nullable=False)
