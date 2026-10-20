# -*- coding: utf-8 -*-
"""P1 M-01（2026-10-19）：新建 login_throttles 表（登录限流/账号锁定持久化）。

幂等：CREATE TABLE / INDEX IF NOT EXISTS；表已存在则跳过。
用法：
    py -3.12 scripts/migrate/migrate_login_throttle_20261019.py          # 预览
    py -3.12 scripts/migrate/migrate_login_throttle_20261019.py --apply  # 执行

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
CREATE TABLE IF NOT EXISTS login_throttles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key VARCHAR(160) NOT NULL UNIQUE,
    count INTEGER NOT NULL DEFAULT 0,
    window_start FLOAT NOT NULL,
    locked_until FLOAT,
    updated_at FLOAT NOT NULL
)
"""
_IDX = ('CREATE INDEX IF NOT EXISTS ix_login_throttles_key '
        'ON login_throttles(key)')


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
            "AND name='login_throttles'").fetchone()
        print(f'  login_throttles 表：{"已存在" if exists else "不存在（将新建）"}')
        if not APPLY:
            print('\n预览模式，未改动。加 --apply 执行。')
            return 0
        if _mig_log is not None and _mig_log.is_applied(SCRIPT_NAME):
            print(f'[SKIP] {SCRIPT_NAME} 已登记为已应用，跳过迁移')
            return 0
        backup_db([DB], tag='login_throttle')
        con.execute(_DDL)
        con.execute(_IDX)
        con.commit()
        print('  ✓ login_throttles + 索引已就绪')
    finally:
        con.close()

    if _mig_log is not None:
        try:
            _mig_log.record(SCRIPT_NAME,
                            note='新建 login_throttles（M-01 登录限流持久化）')
        except Exception as _e:  # noqa: BLE001
            print(f'[WARN] 迁移登记失败（不影响迁移结果）：{_e}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
