# StuLink v1.9.0 2026-09-03
# 成绩管理：通用小工具
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import re
from datetime import date


_NUM_CLASS_RE = re.compile(r'^\d{1,2}班$')


def numeric_classes(class_names):
    """只保留数字班级（01班~10班…），过滤 不分班/已转出/离校 等非教学班"""
    out = [str(c).strip() for c in (class_names or [])
           if c and _NUM_CLASS_RE.match(str(c).strip())]
    out.sort(key=lambda c: int(''.join(filter(str.isdigit, c)) or 0))
    return out


def term_of_date(d):
    """由日期推导学年，如 2025-09-01 → '2025-2026'"""
    if d.month >= 9:
        return f'{d.year}-{d.year + 1}'
    return f'{d.year - 1}-{d.year}'


def parse_grade_any(value):
    """宽松识别年级：'2025级' 保留；'高三/2025级(高二)' 提取 2025级 形式；无法识别返回 None"""
    if not value:
        return None
    text = str(value).strip()
    import re
    m = re.search(r'(20\d{2})\s*级', text)
    if m:
        return f'{m.group(1)}级'
    # 称谓行（高三/高二/高一）不认，返回 None
    return None


def delete_cache_prefix(prefix):
    """按前缀清除内存缓存"""
    try:
        from app.utils.cache import cache
        for key in list(cache._cache.keys()):
            if key.startswith(prefix):
                cache.delete(key)
    except Exception:
        pass
