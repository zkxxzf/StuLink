"""迁移：第四轮审计性能索引补建（2026-10-14，PERF-117 / PERF-118）。

补 3 个库共 6 条索引（模型 `__table_args__` 已同步声明，`scripts/check_indexes.py` 可复验）：
  system.db     operation_logs      idx_oplog_created / idx_oplog_module_created /
                                    idx_oplog_action_created / idx_oplog_user_created
  grades.db     certificates        idx_cert_generated_by
  timetable.db  schedule_entries    idx_entry_deleted_teacher

为什么需要本脚本：SQLite 的 `create_all` 不会给**已存在**的表补索引，存量库必须显式建。
幂等：CREATE INDEX IF NOT EXISTS + 建前查 sqlite_master；改库前经
scripts/_db_backup.backup_db 备份（每库最多备份一次）。

用法：python scripts/migrate/migrate_perf_indexes_20261014.py
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

import config as config_mod
from scripts._db_backup import backup_db
from scripts._ddl_guard import assert_ident, assert_table

SCRIPT_NAME = os.path.basename(__file__)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import _mig_log
except Exception as _e:  # noqa: BLE001
    _mig_log = None
    print(f'[WARN] 迁移登记模块不可用（迁移照常执行，仅不登记）：{_e}')

# (库文件, 表, 索引名, 列序)
SPECS = (
    ('system.db', 'operation_logs', 'idx_oplog_created', ('created_at',)),
    ('system.db', 'operation_logs', 'idx_oplog_module_created', ('module', 'created_at')),
    ('system.db', 'operation_logs', 'idx_oplog_action_created', ('action', 'created_at')),
    ('system.db', 'operation_logs', 'idx_oplog_user_created', ('user_id', 'created_at')),
    ('grades.db', 'certificates', 'idx_cert_generated_by',
     ('generated_by', 'generated_at')),
    ('timetable.db', 'schedule_entries', 'idx_entry_deleted_teacher',
     ('term_schedule_id', 'is_deleted', 'teacher_uid')),
)


def _record(created):
    if _mig_log:
        _mig_log.record(SCRIPT_NAME, note=f'PERF-117/118：新建 {created} 个索引')


def main():
    sys_uri = config_mod.Config.SQLALCHEMY_DATABASE_URI
    data_dir = os.path.dirname(sys_uri.replace('sqlite:///', ''))
    print(f'数据目录：{data_dir}')

    created = skipped = 0
    backed = set()
    for dbfile, table, idx, cols in SPECS:
        path = os.path.join(data_dir, dbfile)
        if not os.path.exists(path):
            print(f'[SKIP] {dbfile} 不存在（未建库，create_all 会带索引）')
            continue
        assert_table(table)
        assert_ident(idx, '索引名')
        for c in cols:
            assert_ident(c, '列名')
        con = sqlite3.connect(path)
        try:
            tcols = {r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}
            if not tcols:
                print(f'[SKIP] {dbfile}.{table} 表不存在')
                continue
            missing = [c for c in cols if c not in tcols]
            if missing:
                print(f'[SKIP] {dbfile}.{table} 缺列 {missing}')
                continue
            if con.execute("SELECT 1 FROM sqlite_master "
                           "WHERE type='index' AND name=?", (idx,)).fetchone():
                skipped += 1
                continue
            if path not in backed:
                backup_db(path)
                backed.add(path)
            col_sql = ', '.join(f'"{c}"' for c in cols)
            con.execute(f'CREATE INDEX IF NOT EXISTS "{idx}" '
                        f'ON "{table}" ({col_sql})')
            con.commit()
            print(f'[OK] {dbfile}: {idx} ON {table}({", ".join(cols)})')
            created += 1
        finally:
            con.close()

    print(f'[DONE] 新建 {created} 个索引 / 已存在跳过 {skipped} 个')
    _record(created)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
