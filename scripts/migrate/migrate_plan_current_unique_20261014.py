"""迁移：方案表「当前方案」加 DB 级部分唯一索引（2026-10-14 第四轮审计 DATA3-01）。

为两张方案表的 `is_current` 建**部分唯一索引**（仅约束 is_current=1 的行，至多一行）：

  grades.db   teacher_link_plans     uq_link_plan_current   ON (is_current) WHERE is_current = 1
  academic.db subject_leader_plans   uq_leader_plan_current ON (is_current) WHERE is_current = 1

背景：两表的「当前方案唯一」原先只由服务层循环翻转保证（正常路径 OK），手工改库 /
并发提交仍可能留下两个 is_current=1，页面会随机取到其中一个。模型 `__table_args__`
已同步声明（`scripts/check_indexes.py` 可复验）。

安全：建索引前先只读核对 `COUNT(*) WHERE is_current=1`，若 > 1 则**报错退出不建**
（先去数据里把多余的当前方案收敛为一个）；幂等；改库前经 _db_backup 备份。

用法：python scripts/migrate/migrate_plan_current_unique_20261014.py
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

# (库文件, 表, 索引名)
SPECS = (
    ('grades.db', 'teacher_link_plans', 'uq_link_plan_current'),
    ('academic.db', 'subject_leader_plans', 'uq_leader_plan_current'),
)


def _record(note):
    if _mig_log:
        _mig_log.record(SCRIPT_NAME, note=note)


def main():
    import sqlite3
    sys_uri = config_mod.Config.SQLALCHEMY_DATABASE_URI
    data_dir = os.path.dirname(sys_uri.replace('sqlite:///', ''))
    print(f'数据目录：{data_dir}')

    created = skipped = 0
    for dbfile, table, idx in SPECS:
        path = os.path.join(data_dir, dbfile)
        if not os.path.exists(path):
            print(f'[SKIP] {dbfile} 不存在（未建库，create_all 会带索引）')
            continue
        assert_table(table)
        assert_ident(idx, '索引名')
        con = sqlite3.connect(path)
        try:
            tcols = {r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}
            if not tcols:
                print(f'[SKIP] {dbfile}.{table} 表不存在')
                continue
            dup = con.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE is_current = 1').fetchone()[0]
            if dup > 1:
                print(f'[FAIL] {dbfile}.{table} 现有 {dup} 条 is_current=1 —— '
                      f'请先人工收敛为 1 条再跑本脚本（未改动）')
                return 1
            exists = con.execute("SELECT 1 FROM sqlite_master "
                                 "WHERE type='index' AND name=?", (idx,)).fetchone()
            if exists:
                skipped += 1
                continue
            backup_db(path)
            con.execute(f'CREATE UNIQUE INDEX IF NOT EXISTS "{idx}" '
                        f'ON "{table}" (is_current) WHERE is_current = 1')
            con.commit()
            print(f'[OK] {dbfile}: {idx} ON {table}(is_current) WHERE is_current = 1')
            created += 1
        finally:
            con.close()

    print(f'[DONE] 新建 {created} 个部分唯一索引 / 已存在 {skipped} 个')
    _record(f'DATA3-01：方案表 is_current 部分唯一索引（新建 {created}）')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
