# -*- coding: utf-8 -*-
"""P1 M-03（2026-10-19）：users 增加 wx_token_ver（小程序令牌版本）。

幂等：列已存在则跳过；改库前备份 system.db。
用法：
    py -3.12 scripts/migrate/migrate_wx_token_ver_20261019.py          # 预览
    py -3.12 scripts/migrate/migrate_wx_token_ver_20261019.py --apply  # 执行
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


def main():
    if APPLY and backup_db is None:
        print('拒绝执行：备份模块不可用。')
        return 2
    if not os.path.exists(DB):
        print(f'库不存在：{DB}')
        return 1

    con = sqlite3.connect(DB, timeout=15)
    try:
        have = {r[1] for r in con.execute('PRAGMA table_info(users)')}
        exists = 'wx_token_ver' in have
        print(f'  users.wx_token_ver：{"已存在" if exists else "不存在（将新增）"}')
        if not APPLY:
            print('\n预览模式，未改动。加 --apply 执行。')
            return 0
        if _mig_log is not None and _mig_log.is_applied(SCRIPT_NAME):
            print(f'[SKIP] {SCRIPT_NAME} 已登记为已应用，跳过迁移')
            return 0
        backup_db([DB], tag='wx_token_ver')
        if not exists:
            con.execute('ALTER TABLE users ADD COLUMN wx_token_ver INTEGER '
                        'NOT NULL DEFAULT 0')
            print('  ✓ 已新增 users.wx_token_ver（默认 0）')
        con.commit()
    finally:
        con.close()

    if _mig_log is not None:
        try:
            _mig_log.record(SCRIPT_NAME,
                            note='users.wx_token_ver（M-03 小程序令牌吊销）')
        except Exception as _e:  # noqa: BLE001
            print(f'[WARN] 迁移登记失败（不影响迁移结果）：{_e}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
