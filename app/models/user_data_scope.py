# StuLink v1.9.2 2026-09-16
# 用户数据范围授权（system.db）：限定用户在已获权限内可见数据的年级/班级范围
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""用户数据范围授权

原则：
- 功能权限全部由身份（权限组）分配，用户本身不直接持有功能权限；
- 本表仅记录"某用户在已获权限内可见的数据范围"（年级/班级），
  例如：教务员张三仅可见 2025 级的数据。
- class_name 为空 = 整个年级可见；有值 = 仅该班级（可组合多条记录）；
- 未配置记录的用户回落到原有组级范围规则（scope_type），保证向后兼容。
"""
from datetime import datetime

from app.extensions import db


class UserDataScope(db.Model):
    __tablename__ = 'user_data_scopes'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    grade = db.Column(db.String(10), nullable=False)       # 授权年级（如 2025级）
    class_name = db.Column(db.String(10))                  # 空=整个年级；有值=仅该班级
    created_at = db.Column(db.DateTime, default=datetime.now)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'grade', 'class_name',
                            name='uq_user_data_scope'),
    )

    def __repr__(self):
        return f'<UserDataScope u{self.user_id} {self.grade} {self.class_name or "*"}>'


def get_user_scope_grades(user_id):
    """用户授权年级清单；未配置返回 None（调用方回落到组级规则）"""
    rows = UserDataScope.query.filter_by(user_id=user_id).all()
    if not rows:
        return None
    return sorted({r.grade for r in rows})


def get_user_scope_classes(user_id, grade):
    """指定年级下用户的班级白名单；None=整个年级（无班级级限制）"""
    rows = UserDataScope.query.filter_by(user_id=user_id, grade=grade).all()
    classes = [r.class_name for r in rows if r.class_name]
    if not classes:
        return None
    return set(classes)


def set_user_scope(user_id, entries):
    """覆盖式设置用户数据范围。

    entries: [{grade, class_name|None}]；空列表 = 清除配置（回落组级规则）。
    """
    UserDataScope.query.filter_by(user_id=user_id).delete()
    seen = set()
    for e in entries:
        grade = (e.get('grade') or '').strip()
        if not grade:
            continue
        cls = (e.get('class_name') or '').strip() or None
        if (grade, cls) in seen:
            continue
        seen.add((grade, cls))
        db.session.add(UserDataScope(user_id=user_id, grade=grade,
                                     class_name=cls))
