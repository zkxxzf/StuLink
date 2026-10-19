# -*- coding: utf-8 -*-
"""为 ai_reports 增加 kind / trace 两列（成绩汇报区「AI 一句话分析」）。

kind  : md（旧口径 Markdown 报告）/ html（工具调用后直接产出的 HTML 报告，已白名单消毒）
trace : 这一轮的工具调用轨迹（JSON：[{tool,args,ok,summary,ms}]），便于回溯数字来源

- 幂等：已存在则 SKIP；
- 列名与类型为脚本内硬编码常量，无外部输入拼接。
"""
import os as _os
import sys as _sys
_HERE = _os.path.dirname(_os.path.abspath(__file__))
_SCRIPTS = _os.path.dirname(_HERE)
_ROOT = _os.path.dirname(_SCRIPTS)
for _p in (_ROOT, _SCRIPTS):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import glob
import os
import sqlite3
import sys

# 2026-10-14 第四轮审计（CONS-02）：补迁移登记（只登记不早退）
SCRIPT_NAME = os.path.basename(__file__)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import _mig_log
except Exception as _e:  # noqa: BLE001
    _mig_log = None
    print(f'[WARN] 迁移登记模块不可用（迁移照常执行，仅不登记）：{_e}')


def _record():
    if _mig_log:
        _mig_log.record(SCRIPT_NAME, note='ai_reports 补 kind/trace 两列（扫描 data/*.db，幂等）')


BASE_DIR = _ROOT
DATA_DIR = os.path.join(BASE_DIR, 'data')

NEW_COLUMNS = [('kind', "VARCHAR(10) DEFAULT 'md'"), ('trace', 'TEXT')]

if not os.path.isdir(DATA_DIR):
    print(f'[SKIP] 数据目录不存在: {DATA_DIR}')
    _record()
    sys.exit(0)

touched = 0
for db_path in sorted(glob.glob(os.path.join(DATA_DIR, '*.db'))):
    try:
        conn = sqlite3.connect(db_path)
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_reports'"
        ).fetchone()
        if not exists:
            conn.close()
            continue
        columns = [c[1] for c in conn.execute('PRAGMA table_info(ai_reports)')]
        for col, ddl in NEW_COLUMNS:
            if col in columns:
                print(f'[SKIP] {os.path.basename(db_path)} 已存在 ai_reports.{col}')
                continue
            conn.execute(f'ALTER TABLE ai_reports ADD COLUMN {col} {ddl}')
            print(f'[OK]   {os.path.basename(db_path)} 新增 ai_reports.{col} {ddl}')
            touched += 1
        conn.commit()
        conn.close()
    except Exception as e:  # noqa: BLE001  单库失败不影响其余库
        print(f'[ERR]  {os.path.basename(db_path)}: {e}')

print(f'[DONE] 迁移完成，共新增 {touched} 列')
_record()

# StuLink v1.19.47.29 2026-10-13
# Copyright (c) 2026 zkxxzf. Apache License 2.0
