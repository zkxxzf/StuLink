# -*- coding: utf-8 -*-
"""班级展示顺序：同班型班级必须相邻（2026-10-21 用户口径）。

用户原话：「大型考试在做成绩分析的时候应该将同类型的班级放到连续的几行，
就是让他们挨着」——涉及成绩总览（全年级对比）、年级分析（各班汇总表 /
名次段分布表）、学科分析（各班对比 / 热力矩阵）与汇总打印。

口径：
1. 先按班型分组：卓越班 → 强基班 → 实验班 → 平行班 → 普通班 →
   其它已登记班型（按名称）→ 未登记班型（排最后）；
2. 组内仍按班号升序（01班 / 02班 / … / 10班），保持原有阅读习惯；
3. 班型优先取本场成绩快照（exam_scores.class_type，考试当时），快照为空
   的老考试回落班级档案（ClassProfile，当前班型）——与去差均分、表彰名单
   同一套取数优先级，避免"同一场考试两个页面班型不一样"；
4. 只调整「按班级列出」的表的行序 / 列序，不改任何数值口径。

为什么单独成模块：班号顺序原先散落在 6+ 处（各自调 numeric_classes），
一旦规则要改（例如学校把「强基班」提到最前），得同时改六处，容易漏。
"""
import logging

from app.modules.grades.utils import is_teaching_class

logger = logging.getLogger(__name__)

# 班型展示优先级（未列出的班型按名称排在其后，未登记班型排最后）
CLASS_TYPE_PRIORITY = ('卓越班', '强基班', '实验班', '平行班', '普通班')
# 未登记班型的排序键：(优先级末位 + 1, '') → 永远在最后
_UNSET_RANK = (len(CLASS_TYPE_PRIORITY) + 1, '')
# 班名里取不到数字时给一个足够大的值，让它在组内排末位
_NO_CLASS_NO = 9999


def class_no(class_name):
    """班号（int）：'01班' → 1；取不到数字返回 9999（排组末）。

    参数：class_name —— 班级名（'01班' / '不分班' / None）
    返回：int 班号
    """
    digits = ''.join(ch for ch in str(class_name or '') if ch.isdigit())
    return int(digits) if digits else _NO_CLASS_NO


def type_rank(class_type):
    """班型排序键：(组序号, 班型名)。

    参数：class_type —— 班型名（卓越班 / 强基班 / 空串）
    返回：tuple，可直接与 class_no 拼成完整排序键
    """
    ct = (class_type or '').strip()
    if not ct:
        return _UNSET_RANK
    if ct in CLASS_TYPE_PRIORITY:
        return (CLASS_TYPE_PRIORITY.index(ct), ct)
    return (len(CLASS_TYPE_PRIORITY), ct)


def type_map_of(data):
    """{班级: 班型}：成绩快照优先、班级档案回落（结果缓存在 ExamData 上）。

    参数：data —— ExamData 或其 population() 视图
    返回：dict，班级名 → 班型（取不到给 ''）
    """
    root = getattr(data, '_d', data)      # population 视图 → 底层 ExamData
    cached = getattr(root, '_class_type_map', None)
    if cached is not None:
        return cached
    out = {}
    for r in getattr(root, 'total_rows', None) or []:
        cls = (r.class_name or '').strip()
        ct = (getattr(r, 'class_type', '') or '').strip()
        if cls and ct and cls not in out:
            out[cls] = ct
    grade = getattr(getattr(root, 'exam', None), 'grade', '') or ''
    try:
        from app.models import ClassProfile
        for p in ClassProfile.query.filter_by(grade=grade).all():
            cls = (p.class_name or '').strip()
            if cls and cls not in out:
                out[cls] = (p.class_type or '').strip()
    except Exception as _e:  # noqa: BLE001 班型只用于排序，取不到不影响报表
        logger.warning('class_order.py::type_map_of 异常（按未登记班型处理）: %s', _e)
    try:
        root._class_type_map = out
    except Exception:  # noqa: BLE001 只读对象不缓存，下次重算
        pass
    return out


def order_key(class_name, type_map):
    """单个班级的排序键：(班型组, 班型名, 班号, 班名)。

    参数：class_name —— 班级名；type_map —— type_map_of() 的结果
    返回：tuple
    """
    ct = (type_map or {}).get((class_name or '').strip(), '')
    return (type_rank(ct), class_no(class_name), str(class_name or ''))


def order_classes(classes, type_map):
    """按（班型组, 班号）排序：同班型相邻、组内班号升序。

    参数：classes —— 班级名列表（已过滤非教学班）；
          type_map —— type_map_of() 的结果
    返回：排好序的新列表（不改入参）
    """
    return sorted(classes or [], key=lambda c: order_key(c, type_map))


def teaching_classes(data):
    """本场教学班（01班~99班），按班型分组后返回。

    参数：data —— ExamData 或其 population() 视图
    返回：list[str]，班型相邻 + 组内班号升序
    """
    names = [str(c).strip() for c in (getattr(data, 'classes', None) or [])
             if is_teaching_class(c)]
    return order_classes(names, type_map_of(data))
