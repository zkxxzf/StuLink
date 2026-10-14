# StuLink v1.19.47.31 2026-10-14
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""顶栏品牌后缀：当前页面属于哪个「工作台 / 模块」（2026-10-14 用户口径）。

**用户口径**：每个页面的顶栏品牌「StuLink」后面都要标出当前所在的工作台 ——
成绩工作台 / 教务工作台 / 我的工作台 —— 不用回头看侧栏就知道自己在哪个模块。

模板用法（base.html 顶栏，`su` 之外的唯一去处）：

    {% set brand_module = workbench_name() %}
    <span class="brand-module">{{ brand_module }}</span>   {# 空串则不渲染 #}

**口径必须与 base.html 侧栏分区一致**（改一处记得改另一处）：

* 蓝图 `students` 的 `students.search`（学生住宿检索）算**宿舍管理**，其余 `students.*`
  算系统管理 —— 与侧栏 `request.endpoint == 'students.search'` 的判断同源；
* 登录页 / welcome 首页 / 通知公告**不属于任何模块**，返回空串（不显示后缀）。
"""
from flask import request

__all__ = ['workbench_name']

# 蓝图 → 顶栏显示名。三个「工作台」用用户口径的正式名字，
# 其余模块沿用侧栏已有的分区名，避免出现第二套说法。
_BLUEPRINT_MODULE = {
    # —— 三个工作台 ——
    'grades': '成绩工作台',
    'academic': '教务工作台',
    'workbench': '我的工作台',
    'overview': '我的工作台',       # 班级概览等教师工作台子菜单
    'attendance': '我的工作台',
    'records': '我的工作台',
    # —— 其它业务模块 ——
    'dashboard': '宿舍管理',
    'rooms': '宿舍管理',
    'assignments': '宿舍管理',
    'statistics': '宿舍管理',
    'points': '德育管理',
    'morality': '德育管理',
    'portrait': '学生画像',
    # —— 系统管理（项目基础库）——
    'system_dashboard': '系统管理',
    'students': '系统管理',
    'users': '系统管理',
    'dictionary': '系统管理',
    'class_profile': '系统管理',
    'perm_groups': '系统管理',
    'grade_mgmt': '系统管理',
    'operation_logs': '系统管理',
}
# 注：auth / welcome / notifications 刻意不在表内 → 返回空串，顶栏不显示后缀。


def workbench_name():
    """当前请求所属模块的显示名；无归属（登录页 / 首页 / 通知）返回 ''。"""
    bp = request.blueprint or ''
    if bp == 'students' and (request.endpoint or '') == 'students.search':
        return '宿舍管理'
    return _BLUEPRINT_MODULE.get(bp, '')
