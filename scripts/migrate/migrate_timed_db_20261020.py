# -*- coding: utf-8 -*-
"""限时练独立成库：`grades.db` 的三张 timed_* 表搬到 `timed.db`。

2026-10-20 用户口径「限时练单独做一个成绩库」：

- 新建 `data/timed.db`，表结构直接由模型 DDL 生成（与 ORM 同源，
  避免手写 DDL 与模型不一致被 `check_columns` 判定缺列）；
- 从 `grades.db` 按列名拷贝 `timed_exams` / `timed_scores` /
  `timed_band_templates`，**行数校验一致**后再删旧表；
- 限时练模块内没有任何跨库 JOIN（只查自己的三张表 + 独立查
  `students`），所以拆库对业务透明。

## 用法

    py -3.12 scripts/migrate/migrate_timed_db_20261020.py           # 预览
    py -3.12 scripts/migrate/migrate_timed_db_20261020.py --apply   # 迁移

## 安全

- `--apply` 前自动备份 **`grades.db` 与 `timed.db`**（两个都可能被写）；
- 迁移在一个事务里完成（建表 → 拷贝 → 校验 → 删旧表），失败整体回滚；
- 幂等：grades.db 已无 timed_* 表 → 直接空跑；
- **防覆盖（2026-10-21 审计）**：`timed.db` 目标表已有数据、而 grades.db 仍留着
  旧表（上次用了 `--keep-old`）时，直接拒绝执行 —— 否则会用过期旧表整表覆盖
  现网正在用的成绩。确认要覆盖请显式加 `--force`（仍会先备份）。
- `--keep-old` 可保留旧表。
"""
import os
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_SCRIPTS)
for _p in (_ROOT, _SCRIPTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

SCRIPT_NAME = os.path.basename(__file__)
APPLY = '--apply' in sys.argv
KEEP_OLD = '--keep-old' in sys.argv
FORCE = '--force' in sys.argv         # 允许覆盖 timed.db 已有数据
GRD_DB = os.path.join(_ROOT, 'data', 'grades.db')
TIMED_DB = os.path.join(_ROOT, 'data', 'timed.db')
TABLES = ('timed_exams', 'timed_scores', 'timed_band_templates')


def build_ddl():
    """[(表名, 建表 SQL, [建索引 SQL…])] —— 与模型定义同源"""
    from sqlalchemy.dialects import sqlite
    from sqlalchemy.schema import CreateIndex, CreateTable
    from app.models.timed import TimedBandTemplate, TimedExam, TimedScore
    dialect = sqlite.dialect()
    out = []
    for model in (TimedExam, TimedScore, TimedBandTemplate):
        table = model.__table__
        out.append((table.name,
                    str(CreateTable(table).compile(dialect=dialect)),
                    [str(CreateIndex(ix).compile(dialect=dialect))
                     for ix in table.indexes]))
    return out


def table_cols(con, schema, table):
    return [r[1] for r in con.execute(
        f'PRAGMA {schema}.table_info({table})')]


def count(con, schema, table):
    return con.execute(f'SELECT COUNT(*) FROM {schema}.{table}').fetchone()[0]


def target_state():
    """timed.db 现状：{表名: 行数}（库/表不存在则不在字典里）"""
    if not os.path.exists(TIMED_DB):
        return {}
    con = sqlite3.connect(TIMED_DB)
    try:
        exist = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        return {t: count(con, 'main', t) for t in TABLES if t in exist}
    finally:
        con.close()


def main():
    if not os.path.exists(GRD_DB):
        print(f'[ERROR] 找不到 {GRD_DB}')
        return 1
    src = sqlite3.connect(GRD_DB)
    old = {t: count(src, 'main', t) for t in TABLES
           if src.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                          "AND name=?", (t,)).fetchone()}
    new = target_state()
    if not old:
        if not new:
            # 两边都没有数据：大概率是新环境还没按 A38 建表，不是"已迁移"
            print('[WARN] grades.db 无 timed_* 表、timed.db 也没有数据。')
            print('       这更像"从未初始化"（先确认 A38 已执行），本脚本不做变更。')
        else:
            print('grades.db 里没有 timed_* 表（已迁移过），无需变更')
            print('  timed.db 现有：'
                  + '、'.join(f'{t} {new.get(t, 0)} 行' for t in TABLES))
        src.close()
        return 0
    ddl = build_ddl()
    filled = {t: n for t, n in new.items() if n}
    print('== 限时练拆库 ==')
    print('  源库:', GRD_DB)
    print('  目标:', TIMED_DB)
    for t in TABLES:
        print(f'  {t}: {old.get(t, 0)} 行（旧）'
              + (f' / {new[t]} 行（timed.db 现有）' if t in filled else ''))
    if filled and not FORCE:
        print('\n[ERROR] timed.db 目标表已有数据，而 grades.db 仍留着旧表'
              '（上次可能用了 --keep-old）。')
        print('        直接执行会用旧表整表覆盖现网数据；确认要覆盖请加 --force。')
        src.close()
        return 1
    if not APPLY:
        print('\n[DRY-RUN] 未改库；确认后加 --apply')
        src.close()
        return 0

    from _db_backup import backup_db
    # 两个库都会被写：grades.db 删旧表、timed.db 清表重灌，都要能回滚
    backup_db([GRD_DB, TIMED_DB], tag='before_timed_db_20261020')
    src.close()

    con = sqlite3.connect(TIMED_DB, timeout=30, isolation_level=None)
    try:
        # 表可能已被应用启动时的 create_all 建好（空表），缺什么补什么
        exist = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        con.execute('ATTACH DATABASE ? AS g', (GRD_DB,))
        con.execute('BEGIN')
        for name, create_sql, index_sqls in ddl:
            if name not in exist:
                con.execute(create_sql)
                print(f'  建表 {name}')
            for ix in index_sqls:
                con.execute(ix.replace('CREATE INDEX', 'CREATE INDEX IF NOT EXISTS',
                                       1))
        for t in TABLES:
            if t not in old:
                continue
            con.execute(f'DELETE FROM main.{t}')
            src_cols = table_cols(con, 'g', t)
            dst_cols = table_cols(con, 'main', t)
            cols = [c for c in src_cols if c in dst_cols]
            dropped = [c for c in src_cols if c not in dst_cols]
            added = [c for c in dst_cols if c not in src_cols]
            if dropped or added:
                print(f'  [WARN] {t} 列不一致：旧表多 {dropped or "无"}、'
                      f'新表多 {added or "无"}（新表多出的列留 NULL）')
            col_sql = ', '.join(f'"{c}"' for c in cols)
            con.execute(f'INSERT INTO main.{t} ({col_sql}) '
                        f'SELECT {col_sql} FROM g.{t}')
            n_src = count(con, 'g', t)
            n_dst = count(con, 'main', t)
            if n_src != n_dst:
                raise RuntimeError(f'{t} 行数不一致：{n_src} → {n_dst}')
            print(f'  拷贝 {t}: {n_dst} 行')
        if not KEEP_OLD:
            for t in TABLES:
                if t in old:
                    con.execute(f'DROP TABLE g.{t}')
            print('  已删除 grades.db 中的 timed_* 表')
        con.commit()
        con.execute('DETACH DATABASE g')
    except Exception as exc:                        # noqa: BLE001
        con.rollback()
        con.close()
        print(f'[ERROR] 迁移失败已回滚：{type(exc).__name__}: {exc}')
        return 1
    con.close()

    try:
        import _mig_log
        _mig_log.record(SCRIPT_NAME,
                        note='限时练三表迁入 timed.db（'
                             + '、'.join(f'{t} {old.get(t, 0)}' for t in TABLES)
                             + '）')
    except Exception as exc:                        # noqa: BLE001
        print(f'[WARN] 迁移登记失败：{exc}')
    print('\n[APPLIED] 完成')
    return 0


if __name__ == '__main__':
    sys.exit(main())
