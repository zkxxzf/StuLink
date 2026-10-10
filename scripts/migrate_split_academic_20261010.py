# -*- coding: utf-8 -*-
"""教务分库迁移（2026-10-10）：academic.db「一库混装四件事」→ 按功能拆库。

拆分方式
--------
原 academic.db 同时装四件互不相干的事，按「一个功能一个库」拆开：

    inspection.db    查课巡课   ← inspection_records
    achievement.db   教师业绩   ← teacher_achievements, achievement_attachments
    forms.db         表单收集   ← form_categories / form_templates / form_rounds /
                                  form_questions / form_submissions / form_answers
    academic.db      师资基础（保留）← teachers, subject_leaders,
                                        work_records, attendance_records
    另删除长期 0 行的废弃表          ← course_swaps（调课统一走 timetable.db 的
                                        ScheduleSwap，该表曾让工作台"永远没有待审调课"）

为什么现在拆
------------
以后是真实数据量：表单答案（一次提交 = 每题一行，全校数千人 × 多轮）会到百万行级，
查课记录逐年累积；而教师名册只有几十行。混在一个库里，大表会拖累小表的查询与备份，
SQLite 的写锁又是整库级——一个功能在写，别的功能都要等。

特点
----
- 动手前自动备份 academic.db（项目约定，见 scripts/_db_backup.py），
  出错用备份覆盖 data/academic.db 即可整体回滚；
- 幂等：目标库已有表/数据则跳过，可重复执行；
- 表结构与原库 1:1（含原索引），数据 INSERT SELECT 搬运，逐表校验行数；
- 顺带给大表补索引（表单提交/答案的关键外键原本一个索引都没有），
  与模型 app/models/academic.py 里声明的索引保持一致；
- 安全闸：任何一张表行数对不上，就**不清理源库**（源表原样保留，可重跑）。

用法（请在应用停止时执行）
------------------------
    cd <项目根>
    py -3.12 scripts/migrate_split_academic_20261010.py
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _db_backup import backup_db      # noqa: E402
from _ddl_guard import assert_ident   # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, 'data')
OLD_DB = os.path.join(DATA, 'academic.db')

# 表 → 目标库
SPLIT = {
    'inspection.db': ['inspection_records'],
    'achievement.db': ['teacher_achievements', 'achievement_attachments'],
    'forms.db': ['form_categories', 'form_templates', 'form_rounds',
                 'form_questions', 'form_submissions', 'form_answers'],
}
# 废弃表：直接删（模型类已同步删除）
DROP_DEAD = ['course_swaps']
# 留在 academic.db 的表（迁移后要校验仍在）
KEEP = ['teachers', 'subject_leaders', 'work_records', 'attendance_records']

# 大表补充索引：库 → [(索引名, 表名, (列, ...))]
NEW_INDEXES = {
    'inspection.db': [
        ('ix_inspection_records_inspector', 'inspection_records', ('inspector_id',)),
        ('ix_inspection_records_class', 'inspection_records', ('grade', 'class_name')),
    ],
    'achievement.db': [
        ('ix_teacher_achievements_status', 'teacher_achievements', ('status',)),
        ('ix_teacher_achievements_created', 'teacher_achievements', ('created_at',)),
    ],
    'forms.db': [
        ('ix_form_templates_status', 'form_templates', ('status',)),
        ('ix_form_rounds_status', 'form_rounds', ('status',)),
        ('ix_form_questions_template', 'form_questions', ('template_id',)),
        ('ix_form_submissions_template', 'form_submissions', ('template_id',)),
        ('ix_form_submissions_submitter', 'form_submissions',
         ('submitter_type', 'submitter_id')),
        ('ix_form_submissions_uid', 'form_submissions', ('submitter_uid',)),
        ('ix_form_submissions_status', 'form_submissions', ('status',)),
        ('ix_form_answers_submission', 'form_answers', ('submission_id',)),
        ('ix_form_answers_question', 'form_answers', ('question_id',)),
    ],
}

errors = []
MIGRATED_TABLES = [t for ts in SPLIT.values() for t in ts]


def table_exists(con, name):
    return bool(con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def count_of(con, name):
    return con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]


def index_names(con, table):
    return [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?", (table,)
    ).fetchall()]


def migrate_one_library(src, dbname, tables):
    """把一个目标库建好、把 tables 搬过去（幂等），返回是否全部成功。"""
    dst_path = os.path.join(DATA, dbname)
    dst = sqlite3.connect(dst_path)
    ok_all = True
    try:
        for t in tables:
            assert_ident(t, '表名')
            if not table_exists(src, t):
                # 源库已无此表：可能已迁移过
                if table_exists(dst, t):
                    print(f'  - {t}: 源库已无（上次已迁移），目标 {count_of(dst, t)} 行')
                else:
                    print(f'  - {t}: 源库无此表，跳过')
                    errors.append(f'{dbname} 缺表 {t}（源库也没有）')
                    ok_all = False
                continue

            # ① 表结构（含唯一约束；autoindex 的 sql 为 NULL 会自动带上）
            if not table_exists(dst, t):
                row = src.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (t,)
                ).fetchone()
                if not row or not row[0]:
                    errors.append(f'{t} 在源库没有建表语句')
                    ok_all = False
                    continue
                dst.execute(row[0])

            # ② 原索引（显式 sql 的；与建表语句配套的 autoindex 不重复建）
            for (isql,) in src.execute(
                    "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? "
                    "AND sql IS NOT NULL", (t,)).fetchall():
                try:
                    dst.execute(isql)
                except sqlite3.OperationalError as exc:
                    if 'already exists' not in str(exc):
                        raise

            # ③ 数据（目标为空才搬，保证幂等）
            #    用 Python 端取行 + executemany 搬运：ATTACH/DETACH 方案会因
            #    读连接仍持有游标而报 "database src_db is locked"，数据量不大时
            #    这样更简单可靠。
            n_src = count_of(src, t)
            if count_of(dst, t) == 0 and n_src > 0:
                cur = src.execute(f'SELECT * FROM "{t}"')
                cols = [d[0] for d in cur.description]
                rows = cur.fetchall()
                cur.close()
                for c in cols:
                    assert_ident(c, '列名')
                cols_sql = ', '.join('"' + c + '"' for c in cols)
                marks = ', '.join('?' for _ in cols)
                dst.executemany(f'INSERT INTO "{t}" ({cols_sql}) VALUES ({marks})', rows)
                dst.commit()
            n_dst = count_of(dst, t)
            if n_dst != n_src:
                errors.append(f'{dbname}.{t} 行数不一致：源 {n_src} → 目标 {n_dst}')
                ok_all = False
                print(f'  - {t}: 源 {n_src} 行 → 目标 {n_dst} 行  ✗ 行数不一致')
            else:
                print(f'  - {t}: {n_src} 行  ✓')
            dst.commit()

        # ④ 补大表索引
        added = []
        for idx_name, tbl, cols in NEW_INDEXES.get(dbname, []):
            assert_ident(idx_name, '索引名')
            assert_ident(tbl, '表名')
            for c in cols:
                assert_ident(c, '列名')
            if not table_exists(dst, tbl):
                continue
            if idx_name in index_names(dst, tbl):
                continue
            cols_sql = ', '.join(f'"{c}"' for c in cols)
            dst.execute(f'CREATE INDEX "{idx_name}" ON "{tbl}" ({cols_sql})')
            added.append(idx_name)
        if added:
            print(f'  + 补索引 {len(added)} 个：{"、".join(added)}')
        dst.commit()
        try:
            dst.execute('VACUUM')
        except sqlite3.OperationalError:
            pass
        print(f'  → {dbname}：{os.path.getsize(dst_path) // 1024} KB')
    finally:
        dst.close()
    return ok_all


def main():
    print('=' * 64)
    print(' 教务分库迁移：academic.db → inspection / achievement / forms')
    print('=' * 64)
    if not os.path.exists(OLD_DB):
        print(f'✗ 找不到 {OLD_DB}')
        return 1

    # ── Step 0 备份 ──
    print('\n[Step 0] 备份 academic.db')
    backup_db(OLD_DB, keep=5)

    src = sqlite3.connect(OLD_DB)
    try:
        # ── Step 1-2 建库 + 搬数据 ──
        for dbname, tables in SPLIT.items():
            print(f'\n[Step 1] {dbname}')
            migrate_one_library(src, dbname, tables)

        # ── Step 3 清理旧库（有错就不动，保留可重跑）──
        print('\n[Step 2] 清理 academic.db')
        if errors:
            print('  ⚠ 迁移存在问题，已跳过清理（源表原样保留，修好后可重跑本脚本）')
        else:
            for t in MIGRATED_TABLES + DROP_DEAD:
                assert_ident(t, '表名')
                if table_exists(src, t):
                    src.execute(f'DROP TABLE IF EXISTS "{t}"')
                    print(f'  - 删除已迁出表 {t}')
            src.commit()
            try:
                src.execute('VACUUM')
            except sqlite3.OperationalError:
                pass
            print(f'  → academic.db：{os.path.getsize(OLD_DB) // 1024} KB')

        # ── Step 4 验证 ──
        print('\n[Step 3] 验证')
        for dbname, tables in SPLIT.items():
            con = sqlite3.connect(os.path.join(DATA, dbname))
            try:
                for t in tables:
                    if not table_exists(con, t):
                        errors.append(f'{dbname} 缺少表 {t}')
            finally:
                con.close()
        for t in KEEP:
            if not table_exists(src, t):
                errors.append(f'academic.db 缺少保留表 {t}')
        for t in MIGRATED_TABLES + DROP_DEAD:
            if table_exists(src, t):
                errors.append(f'academic.db 仍残留表 {t}')
    finally:
        src.close()

    print('\n' + '=' * 64)
    if errors:
        print(f'✗ 迁移未完成，{len(errors)} 个问题：')
        for e in errors:
            print(f'  - {e}')
        print('  源库已备份，可重跑本脚本（幂等）；如需回滚，用备份覆盖 data/academic.db')
        return 1

    print('✓ 分库完成。库清单：')
    for f in os.listdir(DATA):
        if f.endswith('.db'):
            fp = os.path.join(DATA, f)
            print(f'  {f:20s} {os.path.getsize(fp) // 1024:>8d} KB')
    print('\n下一步：确认模型/配置已指向新库（app/models/academic.py、config.py 已同步），'
          '\n然后跑回归：py -3.12 tests/academic_regression.py')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())


# StuLink v1.8.0 2026-10-10
# Copyright (c) 2026 zkxxzf. Apache License 2.0
