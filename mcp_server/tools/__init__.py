# -*- coding: utf-8 -*-
"""工具注册总入口：按模块加载并注册全部 MCP 工具。"""
import importlib

# 注册顺序即客户端展示顺序：基础信息 → 成绩 → 分析 → 考勤 → 课表 → 宿舍 → 画像
MODULES = (
    ('basic', '基础信息与学生'),
    ('exams', '考试与成绩'),
    ('analytics', '统计分析'),
    ('attendance', '考勤'),
    ('schedule', '课表'),
    ('dormitory', '宿舍'),
    ('portrait', '学生画像'),
)


def register_all(server):
    """逐模块注册工具，返回 [(模块名, 说明), ...] 供启动日志展示。"""
    loaded = []
    for name, label in MODULES:
        module = importlib.import_module(f'.{name}', package=__package__)
        module.register(server)
        loaded.append((name, label))
    return loaded