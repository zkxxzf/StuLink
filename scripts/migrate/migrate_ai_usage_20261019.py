# -*- coding: utf-8 -*-
"""P2（2026-10-19）：新建 ai_usage 表（AI 每日调用配额）。

幂等：CREATE TABLE / INDEX IF NOT EXISTS；表已存在则跳过。
用法：
    py -3.12 scripts/migrate/migrate_ai_usage_20261019.py          # 预览
    py -3.12 scripts/migrate/migrate_ai_usage_20261019.py --apply  # 执行

说明：create_all 也会在启动时建这张表；本脚本用于生产显式迁移与登记。
"""
import os
import sqlite3
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
for _p in (_ROOT, os.path.join(_ROOT, 'scripts')):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.chdir(_ROOT)

APPLY = '--apply' in sys.argv
DB = os.path.join(_ROOT, 'data', 'system.db')
SCRIPT_NAME = os.path.basename(__file__)

try:
    import _mig_log
except Exception as _e:  # noqa: BLE001
    _mig_log = None
    print(f'[WARN] 迁移登记模块不可用（迁移照常执行，仅不登记）：{_e}')

try:
    from _db_backup import backup_db
except Exception as _e:  # noqa: BLE001
    backup_db = None
    print(f'[WARN] 备份模块不可用（--apply 将拒绝执行）：{_e}')

_DDL = """
CREATE TABLE IF NOT EXISTS ai_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    day VARCHAR(10) NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    updated_at DATETIME
)
"""
_IDX = ('CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_usage_user_day '
        'ON ai_usage (user_id, day)')
_IDX_DAY = 'CREATE INDEX IF NOT EXISTS idx_ai_usage_day ON ai_usage (day)'


def main():
    if APPLY and backup_db is None:
        print('拒绝执行：备份模块不可用。')
        return 2
    if not os.path.exists(DB):
        print(f'库不存在：{DB}')
        return 1

    con = sqlite3.connect(DB, timeout=15)
    try:
        exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='ai_usage'").fetchone()
        print(f'  ai_usage 表：{"已存在" if exists else "不存在（将新建）"}')
        if not APPLY:
            print('\n预览模式，未改动。加 --apply 执行。')
            return 0
        if _mig_log is not None and _mig_log.is_applied(SCRIPT_NAME):
            print(f'[SKIP] {SCRIPT_NAME} 已登记为已应用，跳过迁移')
            return 0
        backup_db([DB], tag='ai_usage')
        con.execute(_DDL)
        con.execute(_IDX)
        con.execute(_IDX_DAY)
        con.commit()
        print('  ✓ ai_usage + 唯一索引/日索引已就绪')
    finally:
        con.close()

    if _mig_log is not None:
        try:
            _mig_log.record(SCRIPT_NAME,
                            note='新建 ai_usage（P2 AI 每日配额）')
        except Exception as _e:  # noqa: BLE001
            print(f'[WARN] 迁移登记失败（不影响迁移结果）：{_e}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
