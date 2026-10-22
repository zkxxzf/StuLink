# -*- coding: utf-8 -*-
# StuLink v1.19.49.30 2026-10-14
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""AI 提示词生成器（2026-10-14）：建 ai_prompt_templates 表 + ai_tasks 补 gen 列。

背景
----
AI 分析工作台（/grades/ai）新增「提示词生成器」：老师用下拉选项（分析范围＝年级/班级/学科、
分析侧重、输出格式、附加要求）拼出一次分析的提示词，可复制、可直接发起分析、可存成模板复用。

本脚本做两件事（都幂等）：

1. 建 ``ai_prompt_templates``（模板表：user_id + name + payload(JSON) + 时间戳）；
   存的是**结构化选项 + 当时生成的 prompt 文本**，仅本人可见。
2. 给 ``ai_tasks`` 补 ``gen`` 列（生成器参数 JSON）—— 中断/失败后「重新编辑提示词」
   靠它把表单选项无损回填；老任务该列为空串，走"只回填提示词文本"的降级路径。

表结构必须与 ``app/models/grades.py::AiPromptTemplate / AiTask`` 一致
（``tests/check_columns.py`` / ``check_indexes.py`` 会逐列逐索引比对）。

用法：
    py -3.12 scripts/migrate/migrate_ai_prompt_tpl_20261014.py
"""
import os as _os
import sys as _sys
_HERE = _os.path.dirname(_os.path.abspath(__file__))
_SCRIPTS = _os.path.dirname(_HERE)
_ROOT = _os.path.dirname(_SCRIPTS)
for _p in (_ROOT, _SCRIPTS):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import os
import sqlite3
import sys

BASE_DIR = _ROOT
DB = os.path.join(BASE_DIR, 'data', 'grades.db')

SCRIPT_NAME = os.path.basename(__file__)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _db_backup import backup_db
except Exception as _e:  # noqa: BLE001
    backup_db = None
    print(f'[WARN] 备份模块不可用（跳过备份，谨慎执行）：{_e}')
try:
    import _mig_log
except Exception as _e:  # noqa: BLE001
    _mig_log = None
    print(f'[WARN] 迁移登记模块不可用（迁移照常执行，仅不登记）：{_e}')

DDL_TPL = """
CREATE TABLE IF NOT EXISTS ai_prompt_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name VARCHAR(50) NOT NULL,
    payload TEXT,
    created_at DATETIME,
    updated_at DATETIME
)
"""

DDL_INDEXES = (
    ('idx_ai_prompt_tpl_user', 'ai_prompt_templates (user_id)'),
)


def _columns(conn, table):
    try:
        return {r[1] for r in conn.execute(f'PRAGMA table_info({table})').fetchall()}
    except sqlite3.Error:
        return set()


def main():
    if not os.path.exists(DB):
        print(f'grades.db 不存在，跳过：{DB}')
        return
    if _mig_log is not None and _mig_log.is_applied(SCRIPT_NAME):
        print(f'[SKIP] {SCRIPT_NAME} 已应用过，跳过迁移')
        return
    if backup_db is not None:
        print('备份 ->', backup_db(DB))
    conn = sqlite3.connect(DB)
    try:
        conn.execute(DDL_TPL)
        print('ai_prompt_templates 表已就绪')
        for name, target in DDL_INDEXES:
            conn.execute(f'CREATE INDEX IF NOT EXISTS {name} ON {target}')
            print(f'索引 {name} 已就绪')
        cols = _columns(conn, 'ai_tasks')
        if cols and 'gen' not in cols:
            conn.execute("ALTER TABLE ai_tasks ADD COLUMN gen TEXT")
            print('ai_tasks.gen 已补列')
        elif not cols:
            print('[WARN] 未发现 ai_tasks 表（若库未初始化属正常，建表时会带 gen 列）')
        else:
            print('ai_tasks.gen 已存在，跳过')
        conn.commit()
    finally:
        conn.close()
    if _mig_log is not None:
        _mig_log.record(SCRIPT_NAME, note='AI 提示词生成器：模板表 + ai_tasks.gen')
    print('完成。请重启应用。')


if __name__ == '__main__':
    main()
