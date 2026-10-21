# StuLink v1.19.50.64 2026-10-10
# 成绩管理：通用小工具
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import re
from datetime import date

# 2026-10-10 架构审计修复：本文件原有静默 `except Exception: pass`，失败不可观测。
# 统一加 logger 记录 warning（仅留痕，不改变任何降级行为与返回值）。
import logging

# 学年口径（8 月 1 日起算新学年）的唯一维护点在 app/models/grades.py
# ——与 Exam.semester_label() 的年级学期标签必须同源。此处保留旧 import 路径。
from app.models.grades import term_of_date  # noqa: E402,F401

logger = logging.getLogger(__name__)


_NUM_CLASS_RE = re.compile(r'^\d{1,2}班$')


def numeric_classes(class_names):
    """只保留数字班级（01班~10班…），过滤 不分班/已转出/离校 等非教学班"""
    out = [str(c).strip() for c in (class_names or [])
           if c and _NUM_CLASS_RE.match(str(c).strip())]
    out.sort(key=lambda c: int(''.join(filter(str.isdigit, c)) or 0))
    return out


def is_teaching_class(class_name):
    """v1.19.50.64：单个班级是否为教学班（01班~99班）。

    不分班 / 已转出 / 离校 等非数字班级返回 False。
    与 numeric_classes 同一正则口径，供成绩治理模块统一过滤无班级学生。
    """
    return bool(class_name and _NUM_CLASS_RE.match(str(class_name).strip()))


# v1.19.50.64 班级名归一化：把外部 Excel 的各种写法统一成系统口径
_CLS_TAIL_RE = re.compile(r'(\d{1,2})\s*班')
_SPECIAL_CLASSES = ('不分班', '已转出', '离校')


def normalize_class_name(value):
    """把外部/历史写法归一化为系统班级名（v1.19.50.64）

    支持：
        '高一03班' / '2024级01班' / '高三10班' → '03班' / '01班' / '10班'
        '03班' / '3班'                          → '03班' / '03班'（统一两位）
        '不分班' / '已转出' / '离校'              → 原值
    无法识别返回 ''（调用方应回落主库班级）。
    """
    s = str(value).strip() if value is not None else ''
    if not s:
        return ''
    if s in _SPECIAL_CLASSES:
        return s
    m = _CLS_TAIL_RE.search(s)
    if m:
        return '%02d班' % int(m.group(1))
    return ''


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
    except Exception as _e:  # noqa: BLE001
        logger.warning('utils.py::delete_cache_prefix 异常（已降级处理）: %s', _e)


def delete_cache_exam_reports(exam_id):
    """按考试精确失效汇报缓存（键形如 grades_report_<kind>_<exam_id>_<...>）

    PERF-19：此前用 delete_cache_prefix('grades_report_') 全清，改一科学分会把全部
    21 场考试的汇报缓存一起作废。改为按考试精确失效。
    PERF-113（2026-10-14 第三轮审计，正确性回归）：旧实现只比对键第 4 段（主 exam_id），
    而 `grades_report_sl_<主考>_<方向>_<层>_<被比对考>_<c|nc>` 的**被比对场次**也在键里
    —— 改被比对场次的分数时该键不失效，最长 CACHE_TIMEOUT=900s 呈现陈旧对比数据。
    现改为**键中任一分段命中该考试**即失效（宁可多清几个键，也不能留陈旧数据）；
    同时不再依赖 `split('_')[3]` 的位置契约（PERF-114：新增分段不再静默不失效）。
    注意 grades_report_ld_*（全局对比）跨考试取数，不在此列，需单独整体失效
    （调用方 invalidate_exam_cache 已整体清）。
    """
    try:
        from app.utils.cache import cache
        eid = str(exam_id)
        for key in list(cache._cache.keys()):
            if not key.startswith('grades_report_') or key.startswith('grades_report_ld_'):
                continue
            # 前 3 段固定（grades/report/<kind>），从 kind 之后找该考试（主考或被比对场次）
            if eid in key.split('_')[3:]:
                cache.delete(key)
    except Exception as _e:  # noqa: BLE001
        logger.warning('utils.py::delete_cache_exam_reports 异常（已降级处理）: %s', _e)


def invalidate_exam_cache(exam_id):
    """成绩/划线变更后失效该考试的全部分析缓存（四 tab + 汇报区）"""
    delete_cache_prefix(f'grades_tab_{exam_id}_')
    # 全局对比（grades_report_ld_*）跨考试取数，任何一场变动都会影响其它场次的对比结果，
    # 故仍需整体失效；其余汇报缓存（cs/sc/nl/tr/sl/co）按考试精确失效（PERF-19）
    delete_cache_prefix('grades_report_ld_')
    delete_cache_exam_reports(exam_id)
    # PERF-06/12：/api/options 的「每场考试班级」缓存（班级可能随导入变化）
    delete_cache_prefix('grades_options_')
    # v1.13.1：ExamData 进程级共享缓存同步失效（跨请求只读快照，改分/划线后必须重建）
    # PERF-19：只丢该场，避免连带丢弃其它考试的常驻快照（每场 2 万+ 行全量重拉）
    try:
        from app.modules.grades.services.stats_service import clear_exam_data_cache
        clear_exam_data_cache(exam_id)
    except Exception as _e:  # noqa: BLE001
        logger.warning('utils.py::invalidate_exam_cache 异常（已降级处理）: %s', _e)


def invalidate_teacher_cache():
    """教师映射等「跨考试基础数据」变更后的失效钩子（2026-10-16 第七轮审计 P-M8）。

    教师姓名/任课快照参与 grades_tab_*（教师 tab、班级 tab 的教师列）与
    grades_report_*（冷热/学科/教师报表）的取值；教师映射（矩阵保存、批量导入、
    方案切换）此前写后不失效，最长 900s 仍显示旧教师。跨所有考试生效 → 按前缀
    全清（低频操作，正确性优先；与汇报区 ld 的"整体失效"取舍一致）。
    """
    delete_cache_prefix('grades_tab_')
    delete_cache_prefix('grades_report_')
