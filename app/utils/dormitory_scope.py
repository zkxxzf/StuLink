# StuLink v1.19.5.0 2026-10-11
# 宿舍数据范围唯一口径（H-2 同类残留修复）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""宿舍模块数据范围过滤（Room / 班级 / 学生）的唯一实现。

背景（2026-10-11 审计）：宿舍模块长期缺少范围函数——
`/dormitory/rooms/` 全量可见、`/assignments/manage` 非班主任可用 URL 参数任选班级、
`/dormitory/search` 用内联角色判断（H-2 反模式复活）。
本模块把「当前用户能看哪些宿舍/班级/学生」收敛成一组函数，宿舍相关查询统一调用，
禁止再各自内联角色判断（清单第 0 节铁律②）。

口径（P2 起统一委托 app.utils.scope，唯一口径）：
- admin / 组级 scope=school（含宿管组）→ 全校，不过滤
- 用户级数据范围（UserDataScope）→ 仅授权年级
- 组级 scope=grade（年级长）→ 仅本年级
- 组级 scope=class（班主任）→ 仅所辖班级（含合班房，'+' 拼接不分主次）
- 无权限组 / 其它 → 空集（fail-closed，不回落全校）
"""
from sqlalchemy import and_, or_

from app.models import Room, Student
from app.utils.scope import resolve_scope as _canonical_scope


def _current(user):
    """未显式传 user 时取当前登录用户（须处于请求上下文）"""
    if user is not None:
        return user
    from flask_login import current_user
    return current_user


def _resolve(user):
    """解析范围 → (kind, grades, pairs)

    kind: 'school' | 'grades' | 'grade' | 'class' | 'none'
    P2：委托 app.utils.scope 唯一口径（class 范围保留 user.grade/class_name 回落）。
    """
    s = _canonical_scope(_current(user), class_fallback=True)
    if s.origin in ('admin', 'school'):
        return 'school', None, None
    if s.origin == 'user_grades':
        # 2026-10-14 第四轮审计修复：用户级范围带班级白名单（UserDataScope.class_name）
        # 时 resolve_scope 返回精确到班的 classes，不再放大为整年级。
        if s.kind == 'classes':
            return 'class', None, set(s.classes)
        return 'grades', set(s.grades), None
    if s.origin == 'grade':
        return 'grade', set(s.grades), None
    if s.origin == 'class':
        return 'class', None, set(s.classes)
    return 'none', None, None


def scope_kind(user=None):
    """返回当前用户宿舍范围类型：school|grades|grade|class|none。

    2026-10-11 三轮审计新增：供 MCP 等调用方标注 scope 文案，
    避免在调用侧再写一套角色/范围判定（保持唯一口径）。
    """
    return _resolve(_current(user))[0]


def resolve_scope(user=None):
    """公开的范围解析入口 → (kind, grades, pairs)。

    2026-10-13：宿舍统计页 statistics.index 原自带 _get_scope（只读组级 scope_type、
    不读用户级数据范围，且 scope_type='none' 时 fail-open 到全校）。改为统一走本函数，
    与 rooms/assignments 同口径（含 UserDataScope 与 fail-closed）。
    """
    return _resolve(_current(user))


def _class_match(class_name):
    """房间 class_name/combined_class 命中某班（含合班 '+' 拼接，不分主次）"""
    return or_(
        Room.class_name == class_name,
        Room.class_name.like(class_name + '+%'),
        Room.class_name.like('%+' + class_name + '+%'),
        Room.class_name.like('%+' + class_name),
        Room.combined_class.like(class_name + '+%'),
        Room.combined_class.like('%+' + class_name + '+%'),
        Room.combined_class.like('%+' + class_name),
    )


def room_scope_query(query, user=None):
    """把 Room 查询收敛到该用户可见范围（无任何范围 → 过滤成空集）"""
    kind, grades, pairs = _resolve(_current(user))
    if kind == 'school':
        return query
    if kind in ('grades', 'grade'):
        if not grades:
            return query.filter(Room.id == -1)
        return query.filter(Room.grade.in_(grades))
    if kind == 'class':
        if not pairs:
            return query.filter(Room.id == -1)
        conds = [and_(Room.grade == g, _class_match(c)) for g, c in pairs]
        return query.filter(or_(*conds))
    return query.filter(Room.id == -1)


def student_scope_query(query, user=None):
    """把 Student 查询收敛到该用户可见范围（无任何范围 → 过滤成空集）。

    2026-10-14 第三轮审计（SEC-01）：`/assignments/manage` 的「未分配住校生」名单
    原先未套范围，非班主任不带参数时会列出**全校**未分配住校生。此处与
    room_scope_query 同口径（fail-closed，无范围即空集），供宿舍侧学生名单统一调用。
    """
    kind, grades, pairs = _resolve(_current(user))
    if kind == 'school':
        return query
    if kind in ('grades', 'grade'):
        if not grades:
            return query.filter(Student.id == -1)
        return query.filter(Student.grade.in_(grades))
    if kind == 'class':
        if not pairs:
            return query.filter(Student.id == -1)
        conds = [and_(Student.grade == g, Student.class_name == c) for g, c in pairs]
        return query.filter(or_(*conds))
    return query.filter(Student.id == -1)


def class_in_scope(user, grade, class_name):
    """(grade, class_name) 是否在用户宿舍可见范围（未配置范围 → False）"""
    kind, grades, pairs = _resolve(_current(user))
    if kind == 'school':
        return True
    if kind in ('grades', 'grade'):
        return bool(grade) and grade in (grades or set())
    if kind == 'class':
        return (grade, class_name) in (pairs or set())
    return False


def student_in_dorm_scope(user, student):
    """学生是否在用户宿舍可见范围（按学生所属年级/班级判定）"""
    if student is None:
        return False
    return class_in_scope(user, student.grade, student.class_name)
