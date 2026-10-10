# StuLink v1.18.9.2 2026-10-10
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""高中年级 / 班型 / 选科 工具（2026-09-26）。

教务界面要"像高中"，有几处约定必须和教务老师口头习惯一致：

1. **年级叫法**：教务说"高三"而不是"2024级"。这里按入学年份推算：
   入学年份最大的年级＝高一，依次往下高二、高三。展示成「高三(2024级)」。
2. **年级排序**：教务习惯**毕业年级在前**（高三→高二→高一），而不是字符串升序。
3. **班型与选科**：`ClassProfile` 里已有机读数据——`class_type`（强基班/卓越班）、
   `subject_direction`（物理/历史，即新高考"3+1+2"的"1"）、`subjects`（选科组合，
   如物化生）。课表/任课表/总课表都应该带上，否则老师看不出这是理科班还是文科班。

这些只是"读法"，不新增表字段，也不改变既有数据。
"""
import re

from flask import g, has_request_context

_HIGH_LABELS = {1: '高一', 2: '高二', 3: '高三'}

# 选科组合的高中口头简称：物化生 / 史政地 / 政史地 …
# （直接取首字会变成"历政地"，教务口头说的是"史政地"）
_SUBJECT_SHORT = {'语文': '语', '数学': '数', '英语': '英', '物理': '物', '化学': '化',
                  '生物': '生', '政治': '政', '历史': '史', '地理': '地',
                  '信息技术': '信息', '通用技术': '通用'}


def grade_entry_year(grade):
    """从「2024级」提取入学年份；非年份型年级（如「高一」）返回 None。"""
    m = re.search(r'(19|20)\d{2}', grade or '')
    return int(m.group(0)) if m else None


def grade_labels(grades):
    """{年级: 高中叫法}：如 {'2026级': '高一(2026级)', '2025级': '高二(2025级)'}。

    推算规则：全体年级里入学年份**最大**的是高一（最新入学），每小一岁降一级。
    年级名里没有年份的（如直接叫「高一」）原样返回。
    """
    grades = [g for g in (grades or []) if g]
    years = {g: grade_entry_year(g) for g in grades}
    known = [y for y in years.values() if y]
    top = max(known) if known else None
    out = {}
    for g in grades:
        y = years.get(g)
        if not y or not top:
            out[g] = g
            continue
        level = top - y + 1                      # 1=高一 2=高二 3=高三
        label = _HIGH_LABELS.get(level)
        out[g] = f'{label}({g})' if label else g
    return out


def grade_sort_key(grade, labels=None):
    """年级排序键：值越大越"高年级"，用于毕业年级在前（高三→高二→高一）。

    能识别「高三/高二/高一」字样就直接映射；否则按入学年份反向（年份越小＝年级越高）。
    """
    g = grade or ''
    for level, name in _HIGH_LABELS.items():
        if name in g:
            return level
    y = grade_entry_year(g)
    if y:
        # 没有全局参照时退化为"年份越小越资深"，配合 labels 可精确
        return 1000 - y
    return 0


def class_profile_map():
    """{(年级, 班级): {'class_type':…, 'direction':…, 'combo':…}}。

    读取班级档案里的班型与选科（新高考 3+1+2：direction=物理/历史，combo=物化生）。
    班级档案不可用（模块未启用/表缺失）时返回空 dict，调用方按"未设置"处理。

    2026-10-10 优化：同一请求内多个视图/模板会重复调用（总课表、班级课表、
    查课实时课表各一次），每次固定 2 条 SQL；这里加请求内缓存。
    调用方均为只读渲染，请求内不会改班级档案，故缓存安全。
    """
    cache = None
    if has_request_context():
        cache = getattr(g, '_class_profile_map', None)
        if cache is not None:
            return cache
    out = {}
    try:
        from app.models import ClassProfile, ClassSubject
        # 性能（2026-10-09）：原来走 cp.subject_display / cp.subject_list —— 它们是
        # lazy='dynamic' 关系上的属性，**每访问一次就发一条 SQL**，480 个班级 ≈ 976 条
        # 语句/次（全校总课表、今日课表每次渲染都调本函数，且今日课表 30 秒整页刷新，
        # 于是"一直在读库"）。改成一次把 class_subjects 全取回、内存里按班级归并：2 条。
        subs = {}
        for cs in ClassSubject.query.order_by(ClassSubject.class_profile_id,
                                              ClassSubject.id).all():
            if cs.subject_value:
                subs.setdefault(cs.class_profile_id, []).append(cs.subject_value)
        for cp in ClassProfile.query.all():
            names = subs.get(cp.id) or []
            combo = '、'.join(names)
            # 高中口头习惯叫"物化生/史政地"，这里再给一份缩写
            short = ''.join(_SUBJECT_SHORT.get(s, s[0] if s else '') for s in names)
            out[(cp.grade, cp.class_name)] = {
                'class_type': cp.class_type or '',
                'direction': cp.subject_direction or '',
                'combo': combo,
                'combo_short': short or combo,
            }
    except Exception:  # noqa: BLE001
        pass
    if cache is not None:          # 请求内缓存（见函数 docstring）
        g._class_profile_map = out
    return out
