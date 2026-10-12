# -*- coding: utf-8 -*-
"""Teacher 索引补建脚本（v1.19.41.2，2026-10-12）

为 academic.db 的 teachers 表幂等补建三个高频筛选索引：
  idx_teacher_user_id / idx_teacher_subject / idx_teacher_status
（与 app/models/academic.py 的 Teacher.__table_args__ 同名对齐：新装环境由
create_all 建，存量库由本脚本补，避免"模型有、库里没有"的静默缺口。）

特性（沿用 scripts/add_performance_indexes.py 的既有写法）：
- 幂等：CREATE INDEX IF NOT EXISTS + 建前查 sqlite_master + 等价索引去重；
- 安全：改动前用 scripts/_db_backup.py 的 backup_db 备份 academic.db；
- 自校验：PRAGMA table_info 确认表/列存在，缺列则跳过并告警；
- --dry-run：只打印将要执行的动作，不写库、不备份；
- R-11：索引名/表名/列名过 _ddl_guard.assert_ident 白名单（DDL 无法绑定参数）。

用法：
    python scripts/add_teacher_indexes.py            # 执行
    python scripts/add_teacher_indexes.py --dry-run  # 只预演
"""
import argparse
import os
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # _db_backup / _ddl_guard
DATA_DIR = os.path.join(BASE_DIR, 'data')
DB_FILE = 'academic.db'
TABLE = 'teachers'

INDEX_SPECS = [
    {'name': 'idx_teacher_user_id', 'table': TABLE, 'columns': ['user_id']},
    {'name': 'idx_teacher_subject', 'table': TABLE, 'columns': ['subject']},
    {'name': 'idx_teacher_status', 'table': TABLE, 'columns': ['status']},
]

VERIFY_PLANS = [
    "SELECT id, name FROM teachers WHERE user_id=1 AND status='active'",
    "SELECT id, name FROM teachers WHERE subject='数学' AND status='active'",
]


def table_columns(conn, table):
    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    return {r[1] for r in rows} if rows else None


def existing_indexes(conn, table):
    out = {}
    for ix in conn.execute(f'PRAGMA index_list("{table}")').fetchall():
        cols = [c[2] for c in conn.execute(f'PRAGMA index_info("{ix[1]}")').fetchall()]
        out[ix[1]] = cols
    return out


def index_by_name(conn, name):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (name,)
    ).fetchone() is not None


def main():
    parser = argparse.ArgumentParser(description='teachers 表索引幂等补建')
    parser.add_argument('--dry-run', action='store_true',
                        help='只打印计划，不执行、不备份')
    args = parser.parse_args()

    print('Teacher 索引补建脚本 v1.19.41.2（幂等）')
    print(f'数据目录：{DATA_DIR}')
    path = os.path.join(DATA_DIR, DB_FILE)
    if not os.path.exists(path):
        print(f'[跳过] 数据库文件不存在：{path}')
        return 0

    conn = sqlite3.connect(path)
    created, skipped, pending = [], [], []

    cols = table_columns(conn, TABLE)
    if cols is None:
        print(f'[跳过] 表 {TABLE} 不存在（{DB_FILE}），无需补建')
        conn.close()
        return 0

    for spec in INDEX_SPECS:
        name, columns = spec['name'], spec['columns']
        missing = [c for c in columns if c not in cols]
        if missing:
            skipped.append((name, f'列缺失 {missing}（实际列：{sorted(cols)}）'))
            continue
        if index_by_name(conn, name):
            skipped.append((name, '同名索引已存在'))
            continue
        equiv = next((n for n, c in existing_indexes(conn, TABLE).items()
                      if c == columns), None)
        if equiv:
            skipped.append((name, f'已存在等价索引 {equiv}{tuple(columns)}'))
            continue
        pending.append(spec)

    for name, reason in skipped:
        print(f'  [跳过] {name}：{reason}')

    if not pending:
        print('  三个索引均已就绪，无需补建。')
    elif args.dry_run:
        for spec in pending:
            print(f'  [DRY-RUN 将创建] {spec["name"]} ON {spec["table"]}'
                  f'({", ".join(spec["columns"])})')
        print('  [DRY-RUN] 未做任何修改（不备份、不建索引）。')
    else:
        from _db_backup import backup_db
        backup_db(path)

        from _ddl_guard import assert_ident   # R-11：标识符白名单
        for spec in pending:
            name, table, columns = spec['name'], spec['table'], spec['columns']
            assert_ident(name, '索引名')
            assert_ident(table, '表名')
            for c in columns:
                assert_ident(c, '列名')
            col_sql = ', '.join(f'"{c}"' for c in columns)
            conn.execute(f'CREATE INDEX IF NOT EXISTS "{name}" ON "{table}" ({col_sql})')
            created.append(name)
            print(f'  [创建] {name} ON {table}({", ".join(columns)})')
        conn.execute('ANALYZE')
        conn.commit()

    conn.commit()
    print(f'  -- {TABLE} 现有索引：')
    for ix_name, ix_cols in existing_indexes(conn, TABLE).items():
        print(f'       {ix_name}: {ix_cols}')
    for sql in VERIFY_PLANS:
        print(f'  -- 查询计划：{sql}')
        for row in conn.execute(f'EXPLAIN QUERY PLAN {sql}').fetchall():
            print(f'       {row}')
    conn.close()
    print(f'\n完成：本次新建 {len(created)} 个索引。请重启应用使连接生效。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
