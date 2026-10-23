# StuLink v1.19.68.64 2026-10-10
# 教师账号服务：姓名核对 / 自动开户（拼音账号 + 随机初始密码）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import random
import string
from functools import lru_cache

from app.extensions import db
from app.models import User, PermissionGroup

DEFAULT_TEACHER_GROUP = '任课教师组'
ROLE_GROUP = {'teacher': '任课教师组', 'homeroom_teacher': '班主任组'}


@lru_cache(maxsize=8192)
def _raw_pinyin(name):
    """姓名 → (全拼, 每字首字母) 原始大小写；pypinyin 不可用/异常时返回 ('', '')。

    2026-10-21：选人下拉要支持「输入姓名 / 全拼 / 每个字的首字母」三路匹配，
    一次算两个码；同一页里会有上百个下拉，故加 lru_cache。
    """
    try:
        from pypinyin import Style, lazy_pinyin
        nm = name or ''
        return (''.join(lazy_pinyin(nm)),
                ''.join(lazy_pinyin(nm, style=Style.FIRST_LETTER)))
    except Exception:  # noqa: BLE001  # 没装 pypinyin 时前端只按姓名匹配
        return '', ''


def _pinyin(name):
    """姓名 → 无调全拼（如 张三→zhangsan）；pypinyin 不可用或异常时返回 None"""
    return _raw_pinyin(name or '')[0] or None


def pinyin_codes(name):
    """对外暴露小写 (全拼, 首字母)，供「按拼音/首字母检索教师」复用。"""
    full, ini = _raw_pinyin(name or '')
    return full.lower(), ini.lower()


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


def pinyin_of(name):
    """v1.19.68.64 对外暴露的无调全拼（供「按姓名首字母排序」等复用）；无 pypinyin 时返回 None"""
    return _pinyin(name)


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
        # P1 双写：同步多身份关联表
        user.sync_primary_link()
    db.session.add(user)
    db.session.flush()
    return user, pwd
