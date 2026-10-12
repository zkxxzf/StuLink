# -*- coding: utf-8 -*-
"""工具注册总入口。

本服务已收敛为**成绩分析专用**（只读）：只注册 `scores` 一个工具模块。
权限由 `core/score_guard.py` 按三条规则裁决——
R1 任课教师（年级+班级同时命中，且仅所教学科）/ R2 班主任（本班）/ R3 年级长及以上（本年级）。
"""
import importlib

MODULES = (
    ('scores', '成绩查询（只读）'),
    ('score_analysis', '成绩分析（只读）'),
)


def register_all(server):
    """逐模块注册工具，返回 [(模块名, 说明), ...] 供启动日志展示。"""
    loaded = []
    for name, label in MODULES:
        module = importlib.import_module(f'.{name}', package=__package__)
        module.register(server)
        loaded.append((name, label))
    return loaded
