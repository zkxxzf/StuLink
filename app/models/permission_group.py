"""权限组模型：定义用户组及其菜单/范围权限"""
from app.extensions import db
from app.utils.cache import cache

# v1.16.0 权限链缓存：菜单配置解析结果按 group_id 全局缓存 300s（跨请求复用），
# 并以 raw menu_keys 字符串自校验（DB 变更即使漏掉失效也会因 raw 不匹配而重解析）。
_GROUP_MENU_PREFIX = 'perm_group_menus_'
_GROUP_MENU_TTL = 300


def invalidate_group_menu_cache(group_id=None):
    """清除权限组菜单配置缓存（perm_groups 保存/删除后主动调用）。

    group_id 为 None 时清除全部权限组缓存。
    """
    if group_id is not None:
        cache.delete(f'{_GROUP_MENU_PREFIX}{group_id}')
    else:
        cache.clear(_GROUP_MENU_PREFIX)


class PermissionGroup(db.Model):
    """权限组：用户只能通过组获得权限，不允许单独设置"""
# StuLink v1.7.0 2026-08-02
# Copyright (c) 2026 zkxxzf. Apache License 2.0
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
        """解析 menu_keys JSON 为列表

        两级缓存（返回的列表只读，调用方勿就地修改）：
        1. 实例级：同一实例（同一请求）多次 has_menu/get_menu_keys 只解析一次；
        2. 全局级：300s TTL，跨请求复用，以 raw 字符串自校验防止陈旧。
        """
        import json
        raw = self.menu_keys or '[]'
        # 1) 实例级缓存
        if self.__dict__.get('_parsed_menus_raw') == raw:
            return self.__dict__['_parsed_menus']
        # 2) 全局 300s TTL 缓存（raw 匹配才复用）
        parsed = None
        if self.id is not None:
            entry = cache.get(f'{_GROUP_MENU_PREFIX}{self.id}')
            if entry is not None and entry.get('raw') == raw:
                parsed = entry.get('parsed')
        if parsed is None:
            try:
                parsed = json.loads(raw) if raw else []
            except (json.JSONDecodeError, TypeError):
                parsed = []
            if not isinstance(parsed, list):
                parsed = []
            if self.id is not None:
                cache.set(f'{_GROUP_MENU_PREFIX}{self.id}',
                          {'raw': raw, 'parsed': parsed}, timeout=_GROUP_MENU_TTL)
        self.__dict__['_parsed_menus_raw'] = raw
        self.__dict__['_parsed_menus'] = parsed
        return parsed

    def set_menu_keys(self, keys):
        import json
        self.menu_keys = json.dumps(keys, ensure_ascii=False)
        # 使实例级与全局解析缓存失效
        self.__dict__.pop('_parsed_menus_raw', None)
        self.__dict__.pop('_parsed_menus', None)
        if self.id is not None:
            invalidate_group_menu_cache(self.id)

    def has_menu(self, key):
        return key in self.get_menu_keys()

    def __repr__(self):
        return f'<PermissionGroup {self.name}>'


