# StuLink v1.18.2.2 2026-09-24
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
    """
    out = {}
    try:
        from app.models import ClassProfile
        for cp in ClassProfile.query.all():
            try:
                combo = cp.subject_display or ''
                # 高中口头习惯叫"物化生/史政地"，这里再给一份缩写
                short = ''.join(_SUBJECT_SHORT.get(s, s[0] if s else '')
                                for s in cp.subject_list if s)
            except Exception:  # noqa: BLE001  关联表异常时退化为不显示
                combo, short = '', ''
            out[(cp.grade, cp.class_name)] = {
                'class_type': cp.class_type or '',
                'direction': cp.subject_direction or '',
                'combo': combo if combo not in ('—', '') else '',
                'combo_short': short or (combo if combo not in ('—', '') else ''),
            }
    except Exception:  # noqa: BLE001
        pass
    return out
