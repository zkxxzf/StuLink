# StuLink v1.18.2.0 2026-09-24
# 通知公告模型（system.db）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from datetime import datetime
from app.extensions import db

# v2.0 通知分类字典（列表筛选/徽章展示用）
CATEGORY_LABELS = {
    'system': '系统公告',
    'reminder': '催交提醒',
    'academic': '教务通知',
    'workbench': '工作台',
    'other': '其他',
}


class Notification(db.Model):
    """通知公告"""
    __tablename__ = 'notifications'
    __bind_key__ = 'system'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    content = db.Column(db.Text, nullable=False)
    target_type = db.Column(db.String(20), default='all')  # all/grade/class/role
    target_scope = db.Column(db.Text)  # JSON
    priority = db.Column(db.String(10), default='normal')  # normal/urgent
    published_by = db.Column(db.Integer)
    published_at = db.Column(db.DateTime, default=datetime.now)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    # ── v2.0 收件人表改造增量字段（全部可空/带默认，不破坏既有数据）──
    # target_type 新增 'users'（精确到人）；target_uids 为显式收件人 uid JSON 数组
    target_uids = db.Column(db.Text)                       # JSON 数组，如 ["20260001","admin"]
    recipient_count = db.Column(db.Integer, default=0)     # 收件人总数快照
    read_count = db.Column(db.Integer, default=0)          # 已读数快照
    category = db.Column(db.String(20))                    # system/reminder/academic/workbench/other
    biz_type = db.Column(db.String(30))                    # 业务类型，如 form_remind
    biz_id = db.Column(db.Integer)                         # 业务主键，如表单 id
    link_url = db.Column(db.String(200))                   # 点击直达的业务页面 URL

    # 收件人行（v2.0 可见性唯一权威来源）；管理员删通知时级联删除
    recipients = db.relationship(
        'NotificationRecipient', backref='notification',
        lazy='dynamic', cascade='all, delete-orphan')

    @property
    def category_label(self):
        return CATEGORY_LABELS.get(self.category or '', '')

    def to_dict(self):
        return {
            'id': self.id, 'title': self.title, 'content': self.content,
            'target_type': self.target_type, 'target_scope': self.target_scope,
            'target_uids': self.target_uids,
            'priority': self.priority,
            'published_by': self.published_by,
            'published_at': self.published_at.strftime('%Y-%m-%d %H:%M') if self.published_at else None,
            'is_active': self.is_active,
            'recipient_count': self.recipient_count or 0,
            'read_count': self.read_count or 0,
            'category': self.category, 'biz_type': self.biz_type,
            'biz_id': self.biz_id, 'link_url': self.link_url,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M') if self.created_at else None,
        }

    def __repr__(self):
        return f'<Notification {self.title[:20]}>'


class NotificationRead(db.Model):
    """通知已读状态（v2.0 起弃用：保留表与模型供历史数据及无收件人行
    的历史通知兑底，新通知的已读状态以 NotificationRecipient.is_read 为准）"""
    __tablename__ = 'notification_reads'
    __bind_key__ = 'system'

    id = db.Column(db.Integer, primary_key=True)
    notification_id = db.Column(db.Integer, db.ForeignKey('notifications.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    read_at = db.Column(db.DateTime, default=datetime.now)

    __table_args__ = (db.UniqueConstraint('notification_id', 'user_id', name='uq_notif_read'),)

    def __repr__(self):
        return f'<NotificationRead notif={self.notification_id} user={self.user_id}>'


class NotificationRecipient(db.Model):
    """通知收件人（v2.0）：发布时将可见范围展开为一行一个收件人，
    之后列表/未读数/已读进度全部变成单表索引查询。

    user_id 取值约定（逻辑键，跨库不建物理外键）：
    - 正数：system.db users.id（有登录账号的教职工）
    - 负数 -student.id：无账号学生（user_uid 存学号）
    - 负数 -(1000000+teacher.id)：无账号教师（user_uid 存 teacher_uid）
    """
    __tablename__ = 'notification_recipients'
    __bind_key__ = 'system'

    id = db.Column(db.Integer, primary_key=True)
    notification_id = db.Column(db.Integer, db.ForeignKey('notifications.id'),
                                nullable=False, index=True)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    user_uid = db.Column(db.String(16))    # 业务编号（username/学号/teacher_uid）
    user_name = db.Column(db.String(50))   # 姓名快照
    is_read = db.Column(db.Boolean, default=False, index=True)
    read_at = db.Column(db.DateTime)
    is_deleted = db.Column(db.Boolean, default=False)  # 收件人侧软删，不影响他人
    created_at = db.Column(db.DateTime, default=datetime.now)

    __table_args__ = (
        db.Index('uq_notif_recipient', 'notification_id', 'user_id', unique=True),
        # 未读数查询关键路径：WHERE user_id=? AND is_read=0 AND is_deleted=0
        db.Index('idx_notif_recip_user_read', 'user_id', 'is_read', 'is_deleted'),
    )

    def to_dict(self):
        return {
            'id': self.id, 'notification_id': self.notification_id,
            'user_id': self.user_id, 'user_uid': self.user_uid,
            'user_name': self.user_name, 'is_read': bool(self.is_read),
            'read_at': self.read_at.strftime('%Y-%m-%d %H:%M') if self.read_at else None,
            'is_deleted': bool(self.is_deleted),
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M') if self.created_at else None,
        }

    def __repr__(self):
        return f'<NotificationRecipient notif={self.notification_id} user={self.user_id}>'
