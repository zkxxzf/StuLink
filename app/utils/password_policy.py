# StuLink v1.18.0.0 2026-09-23
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""口令强度策略（清单 H-1④ / M-15 的**唯一实现**）。

同类校验不写两份（清单第 0 节铁律②）：改密表单、新建/编辑用户表单、教师导入
等所有入口统一调用 `validate_password()`，禁止再各自内联 `Length(min=6)`。

策略：
  - 长度 ≥ 8
  - 必须同时包含字母与数字（挡掉纯字母/纯数字弱口令）
  - 不得命中弱口令黑名单（精确命中，或以黑名单项为前缀后仅接数字）
  - 不得与登录名/手机号/真实姓名相同（或以其为主体）
  - 不得是单一字符重复
"""
import re

MIN_LENGTH = 8

# 弱口令黑名单：常见口令 + 本项目历史默认口令 + 易被撞库的键盘序列
WEAK_PASSWORDS = {
    'admin123', 'admin888', 'admin', 'administrator', 'root', 'root123',
    '123456', '1234567', '12345678', '123456789', '1234567890',
    '111111', '11111111', '000000', '00000000', '888888', '666666', '5201314',
    'password', 'password123', 'passw0rd', 'p@ssw0rd',
    'abc123', 'a123456', 'a1234567', 'a12345678', 'abcd1234',
    'qwerty', 'qwerty123', 'qwe123', '1q2w3e4r', '1qaz2wsx', 'qazwsx',
    'iloveyou', 'woaini', 'woaini1314', 'letmein', 'welcome', 'monkey',
    'teacher', 'student', 'school', 'test123', 'test1234', 'changeme',
    'stu12345', 'stulink', '123abc', '123123', '654321', '112233',
}

_DIGIT_RE = re.compile(r'\d')
_LETTER_RE = re.compile(r'[A-Za-z]')


def _normalize(value):
    return (value or '').strip().lower()


def validate_password(password, username=None, real_name=None, phone=None):
    """校验口令强度。

    返回 `(True, '')` 或 `(False, 用户可见的中文错误提示)`。
    `username/real_name/phone` 可选，用于禁止「口令即账号/姓名/手机号」。
    """
    pwd = password or ''
    if len(pwd) < MIN_LENGTH:
        return False, f'密码至少 {MIN_LENGTH} 位'
    if not _LETTER_RE.search(pwd) or not _DIGIT_RE.search(pwd):
        return False, '密码必须同时包含字母和数字'

    low = _normalize(pwd)
    if low in WEAK_PASSWORDS:
        return False, '该密码属于常见弱口令，请更换'
    # 黑名单项 + 纯数字后缀（如 admin1234 / abc123456）同样视为弱口令
    for weak in WEAK_PASSWORDS:
        if low.startswith(weak) and low[len(weak):].isdigit():
            return False, '该密码属于常见弱口令，请更换'

    for ident in (username, real_name, phone):
        ident_n = _normalize(ident)
        if ident_n and len(ident_n) >= 3 and (low == ident_n or low.startswith(ident_n)):
            return False, '密码不能与登录名/姓名/手机号相同'

    if len(set(low)) == 1:
        return False, '密码不能是同一字符重复'

    return True, ''


class PasswordPolicyError(Exception):
    """口令不满足策略（服务层使用；表单层用 validate_password 的返回值）"""

    def __init__(self, message):
        super().__init__(message)
        self.message = message
