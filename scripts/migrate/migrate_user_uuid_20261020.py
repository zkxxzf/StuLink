# -*- coding: utf-8 -*-
"""2026-10-20 用户对外标识改为标准 UUID。

- `users.uuid` VARCHAR(36)，UUID4（含连字符的标准写法），唯一索引；
- 内部外键（user_id / 各类日志）仍使用自增 id，不改动任何业务关联；
- 页面不展示该值（`/users/` 已去掉「系统 UID」列）。

## 用法

    py -3.12 scripts/migrate/migrate_user_uuid_20261020.py
    py -3.12 scripts/migrate/migrate_user_uuid_20261020.py --apply

## 安全

- 幂等：列/索引已存在则跳过，已有 uuid 不回写；
- `--apply` 前自动备份 system.db。
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
import uuid

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
    has_col = 'uuid' in cols
    null_rows = 0
    if has_col:
        null_rows = con.execute(
            'SELECT COUNT(*) FROM users WHERE uuid IS NULL OR uuid = ""').fetchone()[0]
    idx = list(con.execute("PRAGMA index_list(users)"))
    has_idx = any(r[1] == 'uq_users_uuid' for r in idx)

    print('== users.uuid（标准 UUID）==')
    print('  列存在:', has_col, '| 待回填:', null_rows, '| 唯一索引:', has_idx)
    if has_col and not null_rows and has_idx:
        print('  无需变更')
        con.close()
        return 0
    if not APPLY:
        print('\n[DRY-RUN] 未改库；确认后加 --apply')
        con.close()
        return 0

    from _db_backup import backup_db
    backup_db(DB, tag='before_user_uuid_20261020')
    if not has_col:
        con.execute('ALTER TABLE users ADD COLUMN uuid VARCHAR(36)')
        print('  + users.uuid VARCHAR(36)')
    rows = con.execute(
        'SELECT id FROM users WHERE uuid IS NULL OR uuid = ""').fetchall()
    for (uid,) in rows:
        con.execute('UPDATE users SET uuid=? WHERE id=?', (str(uuid.uuid4()), uid))
    if rows:
        print(f'  回填 uuid：{len(rows)} 行')
    if not has_idx:
        con.execute('CREATE UNIQUE INDEX IF NOT EXISTS uq_users_uuid ON users(uuid)')
        print('  + 唯一索引 uq_users_uuid')
    con.commit()
    con.close()

    try:
        import _mig_log
        _mig_log.record(SCRIPT_NAME, note=f'users.uuid 标准 UUID，回填 {len(rows)} 行')
    except Exception as _e:  # noqa: BLE001
        print(f'[WARN] 迁移登记失败：{_e}')
    print('\n[APPLIED] 完成')
    return 0


if __name__ == '__main__':
    sys.exit(main())
