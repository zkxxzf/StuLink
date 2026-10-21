# StuLink v1.19.49.50 2026-10-21
# 限时练场次命名：届 + 8 位日期 + 限时练 + No.序号
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""限时练场次名称的**唯一口径**（2026-10-21 用户定案）。

格式：``2025级20260905限时练No.17``

- 前缀「届 + 8 位日期 + 限时练」与新建页 ``_auto_name`` 一致
  （``2025级20260905限时练``），导入/新建后自动补 ``No.序号``；
- 序号按**届内日期升序**连续（同日按 id），跨学期不重置；
- 年级学期（高一上/高二上…）只作列表标签，**不进名字**。

改名入口只有 ``timed_service.renumber_grade()``，迁移脚本与导入流程
都调它，避免两处各写一套格式（曾出现「No.1」与「限时练No.1」并存）。
"""


def exam_name(grade, exam_date, seq):
    """拼一个场次名：`2025级20260905限时练No.17`。

    入参：
        grade: 届别（如 ``2025级``）
        exam_date: ``date`` 对象（调用方保证非空）
        seq: 届内序号（从 1 开始）
    返回：
        符合命名口径的字符串；``exam_date`` 为空时退回不带日期的形式，
        避免脏数据直接抛异常把导入整批失败。
    """
    prefix = f'{grade or ""}'
    if exam_date is not None:
        prefix += exam_date.strftime('%Y%m%d')
    return f'{prefix}限时练No.{int(seq)}'
