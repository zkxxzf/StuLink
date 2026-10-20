# StuLink v1.7.0 2026-08-02
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app.extensions import db, login_manager


def _session_active_group_id():
    """读取 session 中「当前身份」的组 id；无请求上下文时返回 None。"""
    try:
        from flask import session
        return session.get('active_group_id')
    except Exception:  # noqa: BLE001  非请求上下文（脚本/后台任务）
        return None


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    real_name = db.Column(db.String(50), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # 管理员/宿管教师/班主任/普通教师
    # 管理年级：班主任固定 1 个（与其班级同年级）；年级长可多选（2026-10-19），
    # 多值时用英文逗号分隔（如 "2025级,2026级"），读取一律走 `grades`。
    grade = db.Column(db.String(100))
    class_name = db.Column(db.String(10))  # 班主任所管班级
    permission_group_id = db.Column(db.Integer, db.ForeignKey('permission_groups.id'), nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    must_change_pwd = db.Column(db.Boolean, default=True)
    # P1 M-03：小程序令牌版本。令牌载荷带 ver，登出/改密/停用后递增 → 旧令牌立即失效。
    wx_token_ver = db.Column(db.Integer, nullable=False, default=0,
                             server_default='0')
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    # v1.19.49.29（多身份改造 P1）：一人可属多组的关联（含主组标记）。
    # 旧字段 permission_group_id 保留做兼容，由 sync_primary_link() 双写保持一致。
    group_links = db.relationship('UserPermissionGroup', backref='user',
                                  lazy='select', cascade='all, delete-orphan')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
        # P1 M-03：改密即吊销小程序旧令牌（与网页端 session_guard 的"改密踢会话"同口径）
        self.wx_token_ver = int(self.wx_token_ver or 0) + 1

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
            'staff': '教职人员',
        }
        return role_names.get(self.role, self.role)

    @property
    def display_role(self):
        """显示用身份：当前身份组名（身份可自定义），无组时回落角色名"""
        pg = self.active_group
        if pg and pg.name:
            return pg.name
        return self.role_display

    @property
    def grades(self):
        """管理年级列表（多选；兼容历史单值与中文分隔符）。

        入参：无。返回：去重保序的年级列表（无配置返回 []）。
        """
        raw = (self.grade or '').strip()
        if not raw:
            return []
        out = []
        for part in raw.replace('，', ',').replace('、', ',').split(','):
            part = part.strip()
            if part and part not in out:
                out.append(part)
        return out

    def set_grades(self, values):
        """写入管理年级列表（去重保序后逗号拼接；空列表存 NULL）。"""
        seen = []
        for val in (values or []):
            val = (val or '').strip()
            if val and val not in seen:
                seen.append(val)
        self.grade = ','.join(seen) or None

    @property
    def grade_display(self):
        """显示用管理年级（多值用「、」分隔）。"""
        return '、'.join(self.grades)

    def has_role(self, *roles):
        return self.role in roles

    @property
    def permission_groups(self):
        """该用户所属的**全部**权限组（多身份）。

        P1：以关联表为准；关联表为空时回落旧字段 permission_group_id（保证
        未迁移/新建未及时双写的场景行为不变）。
        """
        groups = [l.group for l in self.group_links if l.group is not None]
        if groups:
            return groups
        pg = self.permission_group
        return [pg] if pg is not None else []

    @property
    def primary_group(self):
        """主组：标记 is_primary 的组；无标记时回落旧字段 permission_group_id。"""
        for l in self.group_links:
            if l.is_primary and l.group is not None:
                return l.group
        return self.permission_group

    @property
    def active_group(self):
        """当前身份组。

        P3：仅对「当前登录用户本人」生效——优先取 session 中切换的身份
        （须属于本人），否则回落主组。其它用户对象一律返回其主组。
        基础能力（如通知收件箱）不受身份切换影响，另由业务层放行。
        """
        if self._is_current_user():
            gid = _session_active_group_id()
            if gid is not None:
                for l in self.group_links:
                    if l.group_id == gid and l.group is not None:
                        return l.group
        return self.primary_group

    def _is_current_user(self):
        """self 是否就是当前登录用户（非请求上下文返回 False）"""
        try:
            from flask_login import current_user
            return getattr(current_user, 'id', None) == self.id
        except Exception:  # noqa: BLE001
            return False

    def sync_primary_link(self):
        """让关联表与旧字段 permission_group_id 保持一致（P1 双写，幂等）。

        由所有「设置用户权限组」的入口调用（用户新建/编辑、教师导入、自动开户），
        避免关联表与旧字段漂移导致 active_group 取到陈旧组。
        """
        gid = self.permission_group_id
        for l in list(self.group_links):
            if l.is_primary and l.group_id != gid:
                l.is_primary = False
        if gid is None:
            return
        link = next((l for l in self.group_links if l.group_id == gid), None)
        if link is None:
            from app.models.user_permission_group import UserPermissionGroup
            self.group_links.append(
                UserPermissionGroup(group_id=gid, is_primary=True))
        else:
            link.is_primary = True

    def has_perm(self, perm_key):
        """检查用户是否有指定权限（多身份改造 P1：按当前身份组判定）"""
        if self.role == 'admin':
            return True
        g = self.active_group
        if g:
            return g.has_menu(perm_key)
        return False


@login_manager.user_loader
def load_user(user_id):
    # v1.16.0 性能改造：joinedload 预加载权限组，避免每个请求访问
    # current_user.permission_group 时额外触发一次懒加载查询（每请求省 1 条 SQL）。
    # P1：同时预加载多身份关联（group_links → group），供 has_perm/active_group 使用。
    from sqlalchemy.orm import joinedload
    from app.models.user_permission_group import UserPermissionGroup
    return db.session.query(User).options(
        joinedload(User.permission_group),
        joinedload(User.group_links).joinedload(UserPermissionGroup.group),
    ).filter(User.id == int(user_id)).first()


