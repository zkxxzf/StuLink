# -*- coding: utf-8 -*-
"""新建 ai_report_shares 表（AI 报告分享链接）。

背景（2026-10-22 用户要求）：「AI 分析能不能做成链接，别人打开链接、登录即可查看」。
AI 报告默认仅生成者本人可见（2026-10-18 口径，管理员也不例外）；分享是生成者
主动创建、可撤销的一次授权，因此单独建表存 token / 有效期 / 撤销时间 / 访问次数。

- 幂等：表与索引已存在则跳过；
- 执行前自动备份 grades.db（同目录 .bak-<时间戳>）；
- 新部署环境由 db.create_all() 自动建表，本脚本仅用于升级已有库。

用法：
    python scripts/migrate/migrate_ai_report_share_20261022.py
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
import sys
from datetime import datetime

SCRIPT_NAME = os.path.basename(__file__)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import _mig_log
except Exception as _e:  # noqa: BLE001
    _mig_log = None
    print(f'[WARN] 迁移登记模块不可用（迁移照常执行，仅不登记）：{_e}')

BASE_DIR = _ROOT
DB = os.path.join(BASE_DIR, 'data', 'grades.db')

CREATE_TABLE = '''
    CREATE TABLE IF NOT EXISTS ai_report_shares (
        id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
        report_id INTEGER NOT NULL,
        token VARCHAR(48) NOT NULL UNIQUE,
        created_by INTEGER NOT NULL,
        created_at DATETIME,
        expires_at DATETIME,
        revoked_at DATETIME,
        view_count INTEGER DEFAULT 0,
        last_viewed_at DATETIME
    )
'''

INDEXES = [
    ('idx_ai_share_report', 'ai_report_shares (report_id)'),
]


def _record():
    if _mig_log:
        _mig_log.record(SCRIPT_NAME, note='grades.db 建 ai_report_shares 表（AI 报告分享链接）')


def backup(path):
    """迁移前整库备份（同目录），返回备份文件名。"""
    if not os.path.exists(path):
        return None
    dst = f"{path}.bak-{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(path, dst)
    return dst


def main():
    apply = '--apply' in sys.argv
    if not os.path.exists(DB):
        print('grades.db 不存在，跳过（新环境由 create_all 建表）')
        _record()
        return
    if not apply:
        print('[预览] 将在 grades.db 建表 ai_report_shares + 索引 idx_ai_share_report；')
        print('        确认无误后加 --apply 执行（执行前会自动备份）。')
        return
    print('备份 ->', os.path.basename(backup(DB) or '（无）'))
    conn = sqlite3.connect(DB)
    conn.execute(CREATE_TABLE)
    for name, target in INDEXES:
        conn.execute(f'CREATE INDEX IF NOT EXISTS {name} ON {target}')
    conn.commit()
    cols = [r[1] for r in conn.execute('PRAGMA table_info(ai_report_shares)')]
    conn.close()
    print('ai_report_shares 表已就绪，列：', ', '.join(cols))
    print('完成。请重启应用。')
    _record()


if __name__ == '__main__':
    main()

# StuLink v1.19.62.65 2026-10-22
# Copyright (c) 2026 zkxxzf. Apache License 2.0
