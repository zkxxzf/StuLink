# StuLink v1.19.48.29 2026-10-14
# 用户 ↔ 权限组 多对多关联（一人多身份）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""用户 ↔ 权限组 多对多关联表

背景（权限体系多身份改造 · P1）：
此前 ``User.permission_group_id`` 是**单值外键**，一位教师只能挂一个身份组。
本表引入多对多关系，使一位教师可同时拥有多个身份
（如：2026级02班历史老师 + 考试管理员 + 宿管）。

约定：
- ``is_primary=True`` 的组是该用户的「主组」，用于默认身份与显示文案；
- ``User.permission_group_id`` 旧字段**保留一个版本做兼容**（迁移期作为主组来源，
  并由 ``User.sync_primary_link()`` 双写保持二者一致），下一个大版本再删；
- 功能权限仍 **100% 来自权限组**；本表只是「一人可属多组」的关系载体，
  不提供任何「单人授权」能力（见设计 §1.2）。
"""
from datetime import datetime

from app.extensions import db


class UserPermissionGroup(db.Model):
    """用户与权限组的多对多关联（含主组标记）"""

    __tablename__ = 'user_permission_groups'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                        nullable=False, index=True)
    group_id = db.Column(db.Integer, db.ForeignKey('permission_groups.id'),
                         nullable=False, index=True)
    # 主组：该用户的默认身份（用于显示文案与未切换时的兜底）
    is_primary = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.now)

    group = db.relationship('PermissionGroup')

    __table_args__ = (
        db.UniqueConstraint('user_id', 'group_id', name='uq_user_group'),
    )

    def __repr__(self):
        star = ' *' if self.is_primary else ''
        return f'<UserPermissionGroup u{self.user_id} g{self.group_id}{star}>'
