# -*- coding: utf-8 -*-
"""脚本侧 DDL 标识符白名单（清单 R-11，纵深防御）。

`scripts/` 下的迁移脚本会用 f-string 拼 DDL（SQLite 不支持标识符绑定参数），
标识符目前全部来自脚本内硬编码常量 / CLI 参数，不来自 Web 请求，因此无 Web 暴露面。
但如果将来有人把外部输入接进来，就可能变成 SQL 注入。

本模块提供：
  - `safe_ident(name)`：只允许 `[A-Za-z0-9_]` 且不以数字开头
  - `assert_ident(name)`：不合法直接抛 ValueError（脚本应当中止而不是执行危险 DDL）

用法（迁移脚本里）：
    from _ddl_guard import assert_ident
    assert_ident(col)
    conn.execute(f'ALTER TABLE notifications ADD COLUMN {col} {ddl}')
"""
import re

_IDENT_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')

# 允许拼接 DDL 的表名白名单（超出即拒绝，防止拼出跨表/系统表语句）
ALLOWED_TABLES = {
    'students', 'notifications', 'certificates', 'exam_affairs',
    'term_schedules', 'graduated_students', 'classes',
}


def safe_ident(name):
    """标识符是否安全（仅字母数字下划线、不以数字开头）"""
    return bool(name) and bool(_IDENT_RE.match(str(name)))


def assert_ident(name, kind='标识符'):
    if not safe_ident(name):
        raise ValueError(f'非法的{kind}：{name!r}（只允许字母数字下划线且不以数字开头）')
    return str(name)


def assert_table(name):
    if not safe_ident(name):
        raise ValueError(f'非法的表名：{name!r}')
    if str(name) not in ALLOWED_TABLES:
        raise ValueError(f'表名不在白名单内：{name!r}（如需新增请更新 ALLOWED_TABLES）')
    return str(name)
