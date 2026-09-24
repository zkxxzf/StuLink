# StuLink v1.18.2.1 2026-09-24
# 教师账号服务：姓名核对 / 自动开户（拼音账号 + 随机初始密码）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import random
import string

from app.extensions import db
from app.models import User, PermissionGroup

DEFAULT_TEACHER_GROUP = '任课教师组'
ROLE_GROUP = {'teacher': '任课教师组', 'homeroom_teacher': '班主任组'}


def _pinyin(name):
    """姓名 → 无调全拼（如 张三→zhangsan）；pypinyin 不可用或异常时返回 None"""
    try:
        from pypinyin import lazy_pinyin
        return ''.join(lazy_pinyin(name))
    except Exception:
        return None


def gen_username(real_name):
    """生成默认用户名：拼音；冲突追加序号（2..）；无法拼音时返回 None 由预览页人工填写"""
    base = _pinyin(real_name)
    if not base:
        return None
    candidate = base
    n = 2
    while User.query.filter_by(username=candidate).first():
        candidate = f'{base}{n:02d}'
        n += 1
    return candidate


def random_password(length=8):
    chars = string.ascii_letters + string.digits
    return ''.join(random.SystemRandom().choice(chars) for _ in range(length))


def _group_by_role(role):
    gname = ROLE_GROUP.get(role, DEFAULT_TEACHER_GROUP)
    return PermissionGroup.query.filter_by(name=gname).first()


def create_teacher_account(real_name, username=None, role='teacher'):
    """自动开户：role=teacher（任课）/homeroom_teacher（班主任）；返回 (user, password)
    username 为 None 时自动拼音；重名冲突自动加序号
    """
    username = username or gen_username(real_name) or f'ts{random.randint(1000, 9999)}'
    if User.query.filter_by(username=username).first():
        username = gen_username(real_name) or f'ts{random.randint(1000, 9999)}'
        while User.query.filter_by(username=username).first():
            username = f'ts{random.randint(1000, 9999)}'
    pwd = random_password()
    user = User(username=username, real_name=real_name, role=role,
                must_change_pwd=True)
    user.set_password(pwd)
    group = _group_by_role(role)
    if group:
        user.permission_group_id = group.id
    db.session.add(user)
    db.session.flush()
    return user, pwd
