# -*- coding: utf-8 -*-
"""2025 级历次考试数据导入工具包。

模块划分：
    specs.py         源文件 → 考试元信息（七场）
    score_reader.py  读「全科纯分 / 物理类纯分 / 历史类纯分」成绩页
    line_reader.py   读「年级成绩分析」里的特控线 / 本科线
    name_index.py    姓名 / 考号 → 主库学号（重名按班级消歧）
    workbook.py      生成 import_service 认识的中间 xlsx
    band_writer.py   写 exam_bands（整场先删后插）
    runner.py        建考试 → 导成绩 → 重排名 → 写划线
"""
