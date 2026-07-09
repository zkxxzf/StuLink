# StuLink v1.6.1 2026-07-09
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0
from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app.extensions import db, login_manager


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    real_name = db.Column(db.String(50), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # 管理员/宿管教师/班主任/普通教师
    grade = db.Column(db.String(10))       # 班主任所管年级
    class_name = db.Column(db.String(10))  # 班主任所管班级
    permission_group_id = db.Column(db.Integer, db.ForeignKey('permission_groups.id'), nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    must_change_pwd = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def role_display(self):
        role_names = {
            'admin': '管理员',
            'dorm_manager': '宿管教师',
            'homeroom_teacher': '班主任',
            'grade_leader': '年级长',
            'school_viewer': '全校组',
            'teacher': '普通教师',
        }
        return role_names.get(self.role, self.role)

    def has_role(self, *roles):
        return self.role in roles

    def has_perm(self, perm_key):
        """检查用户是否有指定权限"""
        if self.role == 'admin':
            return True
        pg = self.permission_group
        if pg:
            return pg.has_menu(perm_key)
        return False


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


