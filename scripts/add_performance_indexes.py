"""性能索引补建脚本（v1.16.0）

配套「后端查询性能优化（P0/P1）」：为高频筛选/聚合查询补建复合索引，
消除全表 SCAN。脚本特性：

- 幂等：CREATE INDEX IF NOT EXISTS + 建前查 sqlite_master，可重复执行；
- 安全：改动任一库前先自动备份该 .db 文件（.bak-时间戳）；
- 自校验：建索引前用 PRAGMA table_info 确认表/列存在，缺列则跳过并告警；
- 去重：若目标表已存在「相同列、相同顺序」的索引，则报告跳过，不重复建；
- 只读 grades.db：本脚本不触碰 grades.db（其索引由 migrate_perf_indexes_v1132.py 负责）；
- 末尾打印各表最终索引清单 + 代表性查询的 EXPLAIN QUERY PLAN 验证走索引。

用法：
    python scripts/add_performance_indexes.py
"""
import os
import shutil
import sqlite3
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')

# 每个库要补建的索引：name / table / columns（顺序即索引列顺序）
INDEX_SPECS = {
    'dormitory.db': [
        # 宿舍分配数据接口、统计页按 boarding_type='住校' 过滤
        {'name': 'idx_acc_boarding_type',
         'table': 'student_accommodation',
         'columns': ['boarding_type']},
    ],
    'academic.db': [
        # 工作台班级概览：按 class_name + attend_date 区间聚合考勤
        {'name': 'idx_attend_class_date',
         'table': 'attendance_records',
         'columns': ['class_name', 'attend_date']},
        # 单个学生考勤：按 student_no + attend_date 区间查询
        {'name': 'idx_attend_stuno_date',
         'table': 'attendance_records',
         'columns': ['student_no', 'attend_date']},
        # 查课记录：按 inspect_date + period 查询
        {'name': 'idx_inspect_date_period',
         'table': 'inspection_records',
         'columns': ['inspect_date', 'period']},
    ],
    'system.db': [
        # 学生按 grade + class_name 筛选/统计。
        # 注意：system.db 已存在等价索引 idx_student_grade_class(grade, class_name)，
        # 脚本会检测到「相同列相同顺序」的索引并报告跳过，不会重复创建。
        {'name': 'idx_stu_grade_class',
         'table': 'students',
         'columns': ['grade', 'class_name']},
    ],
}

# 代表性查询（用于 EXPLAIN QUERY PLAN 验证新索引被采用）
VERIFY_PLANS = {
    'dormitory.db': [
        ("SELECT student_id FROM student_accommodation WHERE boarding_type='住校'",),
    ],
    'academic.db': [
        ("SELECT status, COUNT(id) FROM attendance_records "
         "WHERE class_name='01班' AND attend_date>='2026-09-01' "
         "AND attend_date<'2026-10-01' GROUP BY status",),
        ("SELECT * FROM inspection_records "
         "WHERE inspect_date='2026-09-01' AND period=1",),
    ],
}


def backup(path):
    """改动前备份单个 .db 文件"""
    if not os.path.exists(path):
        return None
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    dst = f'{path}.bak-{stamp}'
    shutil.copy2(path, dst)
    return dst


def table_columns(conn, table):
    """PRAGMA table_info 取列名集合；表不存在返回 None"""
    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    if not rows:
        return None
    return {r[1] for r in rows}


def existing_indexes(conn, table):
    """返回 {index_name: [col, ...]}（按索引列顺序），仅普通索引"""
    out = {}
    for ix in conn.execute(f'PRAGMA index_list("{table}")').fetchall():
        name = ix[1]
        cols = [c[2] for c in conn.execute(f'PRAGMA index_info("{name}")').fetchall()]
        out[name] = cols
    return out


def index_by_name(conn, name):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (name,)
    ).fetchone() is not None


def process_db(dbfile, specs):
    path = os.path.join(DATA_DIR, dbfile)
    print(f'\n==== {dbfile} ====')
    if not os.path.exists(path):
        print(f'  [跳过] 数据库文件不存在：{path}')
        return

    conn = sqlite3.connect(path)
    created, skipped = [], []
    pending = []  # 需要真正创建的 spec

    # 先做只读校验，确定哪些需要创建
    for spec in specs:
        name, table, columns = spec['name'], spec['table'], spec['columns']
        cols = table_columns(conn, table)
        if cols is None:
            skipped.append((name, f'表 {table} 不存在'))
            continue
        missing = [c for c in columns if c not in cols]
        if missing:
            skipped.append((name, f'列缺失 {missing}（表 {table} 实际列：{sorted(cols)}）'))
            continue
        # 同名索引已存在
        if index_by_name(conn, name):
            skipped.append((name, '同名索引已存在'))
            continue
        # 等价索引（相同表 + 相同列顺序）已存在
        equiv = None
        for ix_name, ix_cols in existing_indexes(conn, table).items():
            if ix_cols == columns:
                equiv = ix_name
                break
        if equiv:
            skipped.append((name, f'已存在等价索引 {equiv}{tuple(columns)}'))
            continue
        pending.append(spec)

    # 有需创建的索引才备份
    if pending:
        bak = backup(path)
        print(f'  备份 -> {os.path.basename(bak) if bak else "(无)"}')

    for spec in pending:
        name, table, columns = spec['name'], spec['table'], spec['columns']
        col_sql = ', '.join(f'"{c}"' for c in columns)
        conn.execute(f'CREATE INDEX IF NOT EXISTS "{name}" ON "{table}" ({col_sql})')
        created.append((name, f'{table}({", ".join(columns)})'))
        print(f'  [创建] {name} ON {table}({", ".join(columns)})')

    for name, reason in skipped:
        print(f'  [跳过] {name}：{reason}')

    if created:
        conn.execute('ANALYZE')
    conn.commit()

    # 打印相关表的最终索引清单
    touched_tables = sorted({s['table'] for s in specs})
    for table in touched_tables:
        if table_columns(conn, table) is None:
            continue
        print(f'  -- {table} 现有索引：')
        for ix_name, ix_cols in existing_indexes(conn, table).items():
            print(f'       {ix_name}: {ix_cols}')

    # EXPLAIN QUERY PLAN 验证
    for (sql,) in VERIFY_PLANS.get(dbfile, []):
        plan = conn.execute(f'EXPLAIN QUERY PLAN {sql}').fetchall()
        print(f'  -- 查询计划：{sql[:60]}...')
        for row in plan:
            print(f'       {row}')

    conn.close()
    return created, skipped


def main():
    print('性能索引补建脚本 v1.16.0（幂等，grades.db 不触碰）')
    print(f'数据目录：{DATA_DIR}')
    total_created = 0
    for dbfile, specs in INDEX_SPECS.items():
        result = process_db(dbfile, specs)
        if result:
            total_created += len(result[0])
    print(f'\n完成：本次新建 {total_created} 个索引。请重启应用使连接生效。')


if __name__ == '__main__':
    main()
