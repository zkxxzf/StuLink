# StuLink v1.18.0.0 2026-09-23
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""会话有效性守卫（M-2 的无迁移实现）。

需求：改密 / 重置口令 / 禁用账号后，既有会话必须失效。
约束：本轮不允许新增数据库列（用户明确决定），因此不引入 `session_version` 列，
改用「口令哈希摘要」：登录时把 sha256(user.password_hash) 写进 session，
每次请求比对——口令一变（改密/重置）摘要即变，旧设备会话自动失效；
账号被禁用时 is_active=False 同样立即失效。

配套（见 config.py / auth/routes.py）：
  - 登录前 session.clear()（防会话固定）
  - session.permanent=True + PERMANENT_SESSION_LIFETIME（服务端过期）
  - login_manager.session_protection='strong'
"""
import hashlib

SESSION_FP_KEY = '_pwd_fp'


def fingerprint(user):
    """用户口令摘要（口令变化 → 摘要变化）"""
    return hashlib.sha256((getattr(user, 'password_hash', '') or '').encode('utf-8')).hexdigest()


def stamp(user):
    """登录/改密成功后写入当前会话摘要"""
    from flask import session
    session[SESSION_FP_KEY] = fingerprint(user)
    session.modified = True


def is_valid(user):
    """当前会话是否仍然有效（账号启用 + 摘要匹配）"""
    from flask import session
    if user is None:
        return False
    if not getattr(user, 'is_active', True):
        return False
    saved = session.get(SESSION_FP_KEY)
    # 升级后首次访问会缺少摘要（旧会话）：要求重新登录，顺带完成会话加固
    return bool(saved) and saved == fingerprint(user)
