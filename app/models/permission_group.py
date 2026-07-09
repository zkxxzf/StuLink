"""权限组模型：定义用户组及其菜单/范围权限"""
from app.extensions import db


class PermissionGroup(db.Model):
    """权限组：用户只能通过组获得权限，不允许单独设置"""
# StuLink v1.6.1 2026-07-09
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0
    __tablename__ = 'permission_groups'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(30), unique=True, nullable=False)
    # 管理范围: 'class'=所管班级 / 'grade'=所管年级 / 'school'=全校 / 'none'=无范围
    scope_type = db.Column(db.String(10), nullable=False, default='none')
    # 可见菜单: JSON数组，如 ["students.view","dormitory.assign"]
    menu_keys = db.Column(db.Text, default='[]')
    role = db.Column(db.String(20), default='')  # admin/grade_leader/homeroom_teacher/dorm_manager/teacher/viewer
    description = db.Column(db.String(200), default='')

    users = db.relationship('User', backref='permission_group', lazy='dynamic')

    def get_menu_keys(self):
        import json
        try:
            return json.loads(self.menu_keys) if self.menu_keys else []
        except (json.JSONDecodeError, TypeError):
            return []

    def set_menu_keys(self, keys):
        import json
        self.menu_keys = json.dumps(keys, ensure_ascii=False)

    def has_menu(self, key):
        return key in self.get_menu_keys()

    def __repr__(self):
        return f'<PermissionGroup {self.name}>'


