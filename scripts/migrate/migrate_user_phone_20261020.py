# -*- coding: utf-8 -*-
"""2026-10-20 账号手机号（支持「手机号 + 密码」登录）。

- `users.phone` VARCHAR(256)：随机 IV 加密后的手机号（**不存明文**，
  与 students.phone1/2 同一套 `encrypt_rand`，号段有限、防枚举对照）；
- `users.phone_idx` VARCHAR(64)：HMAC 盲索引，登录与唯一性校验只走等值查询；
- 留空 = 该账号只能用用户名登录；页面展示一律脱敏（138****8000）。

## 用法

    py -3.12 scripts/migrate/migrate_user_phone_20261020.py
    py -3.12 scripts/migrate/migrate_user_phone_20261020.py --apply

## 安全

- 幂等：列/索引已存在则跳过；**不回填**任何历史手机号（教师档案里的号码不复制，
  避免把「教师通讯录」变成「登录凭证」）；
- `--apply` 前自动备份 system.db；`_mig_log` 登记。
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

sys.path.insert(0, _ROOT)
sys.path.insert(0, _SCRIPTS)

SCRIPT_NAME = os.path.basename(__file__)
APPLY = '--apply' in sys.argv
DB = os.path.join(_ROOT, 'data', 'system.db')


def main():
    if not os.path.exists(DB):
        print(f'[ERROR] 找不到 {DB}')
        return 1
    con = sqlite3.connect(DB)
    cols = {r[1] for r in con.execute('PRAGMA table_info(users)')}
    has_phone = 'phone' in cols
    has_idx = 'phone_idx' in cols
    idx = {r[1] for r in con.execute('PRAGMA index_list(users)')}
    has_index = 'ix_users_phone_idx' in idx

    print('== users.phone / phone_idx（手机号登录）==')
    print('  phone 列:', has_phone, '| phone_idx 列:', has_idx,
          '| 索引:', has_index)
    if has_phone and has_idx and has_index:
        print('  无需变更')
        con.close()
        return 0
    if not APPLY:
        print('\n[DRY-RUN] 未改库；确认后加 --apply')
        con.close()
        return 0

    from _db_backup import backup_db
    backup_db(DB, tag='before_user_phone_20261020')
    if not has_phone:
        con.execute('ALTER TABLE users ADD COLUMN phone VARCHAR(256)')
        print('  + users.phone VARCHAR(256)')
    if not has_idx:
        con.execute('ALTER TABLE users ADD COLUMN phone_idx VARCHAR(64)')
        print('  + users.phone_idx VARCHAR(64)')
    if not has_index:
        con.execute('CREATE INDEX IF NOT EXISTS ix_users_phone_idx '
                    'ON users(phone_idx)')
        print('  + 索引 ix_users_phone_idx')
    con.commit()
    con.close()

    try:
        import _mig_log
        _mig_log.record(SCRIPT_NAME, note='users.phone/phone_idx（手机号登录，加密+盲索引）')
    except Exception as _e:  # noqa: BLE001
        print(f'[WARN] 迁移登记失败：{_e}')
    print('\n[APPLIED] 完成')
    return 0


if __name__ == '__main__':
    sys.exit(main())
