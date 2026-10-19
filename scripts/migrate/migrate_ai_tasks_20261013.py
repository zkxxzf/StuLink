# -*- coding: utf-8 -*-
"""新建 ai_tasks 表（成绩汇报区「AI 一句话分析」服务端任务持久化）。

为什么需要这张表：AI 报告生成要跑多轮工具调用（几十秒~几分钟），
若只靠一次 SSE 请求直通，用户切页/刷新就断、也看不到进度与历史。
现在提问即建任务，后台线程执行，进度可随时（重新）订阅。

- 幂等：表/索引已存在则跳过；
- 执行前自动备份 grades.db（同目录 .bak-<时间戳>）；
- 新部署环境由 db.create_all() 自动建表，本脚本仅用于升级已有库。

用法：
    python scripts/migrate/migrate_ai_tasks_20261013.py
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
import shutil
import sqlite3
from datetime import datetime
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
        _mig_log.record(SCRIPT_NAME, note='grades.db 建 ai_tasks 表（AI 分析任务持久化）')


BASE_DIR = _ROOT
DB = os.path.join(BASE_DIR, 'data', 'grades.db')

CREATE_TABLE = '''
    CREATE TABLE IF NOT EXISTS ai_tasks (
        id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        exam_id INTEGER NOT NULL,
        question VARCHAR(500) NOT NULL,
        direction VARCHAR(10),
        layer VARCHAR(30),
        status VARCHAR(12),
        stage VARCHAR(200),
        error VARCHAR(500),
        tool_count INTEGER,
        report_id INTEGER,
        trace TEXT,
        heartbeat DATETIME,
        created_at DATETIME,
        finished_at DATETIME
    )
'''

INDEXES = [
    ('idx_ai_task_user', 'ai_tasks (user_id)'),
    ('idx_ai_task_exam', 'ai_tasks (exam_id)'),
    ('idx_ai_task_status', 'ai_tasks (status)'),
]


def backup(path):
    if not os.path.exists(path):
        return None
    dst = f"{path}.bak-{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(path, dst)
    return dst


def main():
    if not os.path.exists(DB):
        print('grades.db 不存在，跳过')
        _record()
        return
    print('备份 ->', os.path.basename(backup(DB)))
    conn = sqlite3.connect(DB)
    conn.execute(CREATE_TABLE)
    print('ai_tasks 表已就绪')
    for name, target in INDEXES:
        conn.execute(f'CREATE INDEX IF NOT EXISTS {name} ON {target}')
    conn.commit()
    conn.close()
    print('完成。请重启应用。')
    _record()


if __name__ == '__main__':
    main()

# StuLink v1.19.47.29 2026-10-13
# Copyright (c) 2026 zkxxzf. Apache License 2.0
