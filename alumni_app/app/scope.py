# StuLink 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""往届站数据范围：与主站 `app/utils/alumni_scope.py` 同口径。

口径（2026-10-12 主站全项目审计结论）：
- admin / 校级范围（scope=school）→ 不限年级；
- self_grade / pick_grades → 本人年级 / 指定年级；
- 班主任（class 范围）与无组账号 → **空集**（fail-closed）。

背景（2026-10-19 第十三轮审计）：往届站此前只校验角色白名单，不做任何年级
过滤。默认白名单含 `staff` 与 `grade_leader`，而同一批 `graduated_students`
在主站对年级长是按「本人当前年级」收敛的 → 年级长登录往届站即可按
`?grade=` 枚举全部毕业年级档案（姓名/班级/身份证掩码/手机掩码/变更流水）。
"""
import logging
import sqlite3

from config import Config

_log = logging.getLogger('alumni.scope')

_SCHOOL = 'school'
_GRADE_KINDS = ('self_grade', 'pick_grades')


def _connect():
    """只读连接主站 system.db（与 auth.load_user 同源）"""
    return sqlite3.connect(f'file:{Config.SYSTEM_DB}?mode=ro', uri=True)


def _split_grades(raw):
    """'2024级,2025级' → {'2024级', '2025级'}"""
    return {x.strip() for x in str(raw or '').split(',') if x.strip()}


def visible_grades(user):
    """返回可见的毕业年级集合；None = 不限（admin / 校级范围）。"""
    if user is None:
        return set()
    if (getattr(user, 'role', '') or '') == 'admin':
        return None
    try:
        conn = _connect()
        row = conn.execute(
            'SELECT u.grade, g.scope_type, g.scope_kind, g.scope_grades '
            'FROM users u LEFT JOIN permission_groups g '
            'ON g.id = u.permission_group_id WHERE u.id = ?',
            (getattr(user, 'id', 0),)
        ).fetchone()
        conn.close()
    except Exception:                    # noqa: BLE001
        _log.exception('往届站范围解析失败 user_id=%s', getattr(user, 'id', None))
        return set()
    if not row:
        return set()
    grade, scope_type, scope_kind, scope_grades = row
    kind = (scope_kind or '').strip()
    legacy = (scope_type or '').strip()
    if kind == _SCHOOL or (not kind and legacy == _SCHOOL):
        return None
    if kind in _GRADE_KINDS or (not kind and legacy == 'grade'):
        allowed = _split_grades(scope_grades)
        if grade:
            allowed.add(str(grade).strip())
        return allowed
    return set()


def filter_grades(user, grades):
    """把库里的毕业年级列表收敛为当前用户可见（保持原顺序）"""
    allowed = visible_grades(user)
    if allowed is None:
        return list(grades)
    return [g for g in grades if g in allowed]


def grade_visible(user, grade):
    """单个毕业年级是否可见"""
    allowed = visible_grades(user)
    return allowed is None or (grade or '') in allowed
