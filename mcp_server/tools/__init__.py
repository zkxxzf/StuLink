# -*- coding: utf-8 -*-
"""工具注册总入口：按模块加载并注册全部 MCP 工具。"""
import importlib

# 注册顺序即客户端展示顺序：基础信息 → 成绩 → 分析 → 成绩录入 → 德育 → 考勤
# → 课表 → 课表管理 → 调课 → 查课 → 表单 → 业绩 → 备课组长 → 学生 → 宿舍 → 通知 → 画像
MODULES = (
    ('basic', '基础信息与学生'),
    ('exams', '考试与成绩'),
    ('analytics', '统计分析'),
    ('scores', '成绩录入（写）'),
    ('points', '德育积分（写）'),
    ('attendance', '考勤'),
    ('schedule', '课表'),
    ('schedule_write', '学期课表管理（写）'),
    ('swap', '调课（读/写）'),
    ('inspection', '查课（写）'),
    ('forms', '表单收集（写）'),
    ('achievements', '业绩库（写）'),
    ('duty', '备课组长（写）'),
    ('students', '学生编辑/调班（写）'),
    ('dormitory', '宿舍'),
    ('dormitory_write', '宿舍分配（写）'),
    ('notifications', '通知（写）'),
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