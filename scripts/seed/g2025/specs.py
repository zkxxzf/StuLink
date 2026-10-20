# -*- coding: utf-8 -*-
"""2025 级七场历次考试的源文件与考试元信息。

口径说明：
* 高一上四场（未分科）成绩在「全科纯分」一页，9 科全考；
  高一下三场（已分科）分「物理类纯分 / 历史类纯分」两页。
* 源文件只有月份（见「高中2025级历次考试成绩汇总.xlsx」页签名），
  exam_date 按当月 1 日登记 —— 只影响排序与学期推算，页面可改。
* 高一下三场系统内已存在（32/25/33），本轮只补划线 / 校正口径。
"""
from datetime import date

SOURCE_DIR = r'c:\Users\sakaay\Desktop\2025级考试数据'

# split=True 表示该场有物理类 / 历史类两页
SPECS = [
    {
        'file': '2025级高一上学期第一次月考成绩备份No.1.xlsx',
        'sheets': ['全科纯分'],
        'name': '2025级202510月考（高一上）',
        'date': date(2025, 10, 1), 'type': '月考', 'kind': '本校考',
        'split': False,
    },
    {
        'file': '2025级高一上学期期中考试备份No.2.xlsx',
        'sheets': ['全科纯分'],
        'name': '2025级202511期中考（高一上）',
        'date': date(2025, 11, 1), 'type': '期中考', 'kind': '本校考',
        'split': False,
    },
    {
        'file': '2025级高一上学期第三次月考九师备份No.3.xlsx',
        'sheets': ['全科纯分'],
        'name': '2025级202512九师月考（高一上）',
        'date': date(2025, 12, 1), 'type': '月考', 'kind': '联考',
        'split': False,
    },
    {
        'file': '2025级高一上学期第四次期末备份No.4.xlsx',
        'sheets': ['全科纯分'],
        'name': '2025级202602期末考（高一上）',
        'date': date(2026, 2, 1), 'type': '期末考', 'kind': '本校考',
        'split': False,
    },
    {
        'file': '2025级高一下学期第一次学情评估No.5.xlsx',
        'sheets': ['物理类纯分', '历史类纯分'],
        'name': '2025级20260401月考（高一下）',
        'date': date(2026, 4, 1), 'type': '月考', 'kind': '本校考',
        'split': True,
    },
    {
        'file': '2025级高一下学期第二次期中学情评估No.6.xlsx',
        'sheets': ['物理类纯分', '历史类纯分'],
        'name': '2025级20260510期中考（高一下）',
        'date': date(2026, 5, 10), 'type': '期中考', 'kind': '本校考',
        'split': True,
    },
    {
        'file': '2025级高一下学期期末学情评估No.7.xlsx',
        'sheets': ['物理类纯分', '历史类纯分'],
        'name': '2025级20260712期末考（高一下）',
        'date': date(2026, 7, 12), 'type': '期末考', 'kind': '本校考',
        'split': True,
    },
]


def spec_by_name(name):
    """按考试名取配置；找不到返回 None。"""
    for spec in SPECS:
        if spec['name'] == name:
            return spec
    return None
