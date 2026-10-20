# -*- coding: utf-8 -*-
"""2026-10-20 教师「离职/退休」存档字段。

## 背景

`/users/` 新增「删除教师」：仅「错误录入」允许彻底删除；离职 / 退休必须存档，
且存档后其他模块（教务名单、课表、监考、学科组长、小程序通讯录…）不能再调用，
只能在 `/users/` 查阅。归档状态需要独立字段，避免和普通 `status='left'` 混淆：

- `archive_kind`   VARCHAR(10)  离职 / 退休
- `archive_reason` VARCHAR(200) 删除/归档原因（必填）
- `archived_at`    DATETIME
- `archived_by`    VARCHAR(50)  操作人用户名

## 用法

    py -3.12 scripts/migrate/migrate_teacher_archive_20261020.py
    py -3.12 scripts/migrate/migrate_teacher_archive_20261020.py --apply

## 安全

- 幂等：列已存在则跳过；`--apply` 前自动备份 academic.db；
- 只加列，不改动任何存量数据。
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
DB = os.path.join(_ROOT, 'data', 'academic.db')

COLS = [
    ('archive_kind', 'VARCHAR(10)'),
    ('archive_reason', 'VARCHAR(200)'),
    ('archived_at', 'DATETIME'),
    ('archived_by', 'VARCHAR(50)'),
]


def main():
    if not os.path.exists(DB):
        print(f'[ERROR] 找不到 {DB}')
        return 1
    con = sqlite3.connect(DB)
    have = {r[1] for r in con.execute('PRAGMA table_info(teachers)')}
    todo = [(n, t) for n, t in COLS if n not in have]
    print('== teachers 归档字段 ==')
    print('  已有:', sorted(have & {n for n, _ in COLS}))
    if not todo:
        print('  无需变更')
        con.close()
        return 0
    for n, t in todo:
        print(f'  + {n} {t}')
    if not APPLY:
        print('\n[DRY-RUN] 未改库；确认后加 --apply')
        con.close()
        return 0

    from _db_backup import backup_db
    backup_db(DB, tag='before_teacher_archive_20261020')
    for n, t in todo:
        con.execute(f'ALTER TABLE teachers ADD COLUMN {n} {t}')
    con.commit()
    con.close()

    try:
        import _mig_log
        _mig_log.record(SCRIPT_NAME, note='teachers 增加离职/退休存档字段')
    except Exception as _e:  # noqa: BLE001
        print(f'[WARN] 迁移登记失败：{_e}')
    print('\n[APPLIED] 已加列')
    return 0


if __name__ == '__main__':
    sys.exit(main())
