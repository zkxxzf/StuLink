#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""多库「缺列自检 / 补列」工具（2026-10-10）

背景（为什么需要它）
------------------
SQLAlchemy 的 ``create_all()`` 只创建"缺失的表"，**不会给已存在的表补列**。
模型里新增一个字段（如 ``Exam.exam_kind``）后，存量库不会长出这一列、也不报错；
等到页面去查就 500：

    2026-10-10 现场：成绩分析页一进就报错，真因
    ``sqlite3.OperationalError: no such column: exams.exam_kind``
    （79ef3d8 考试四段式重构新增 exam_kind / score_mode / import_draft 等列，
      data/grades.db 从未补列；而冒烟测试全绿，是因为测试用临时库现建、列齐全
      —— 存量真库与测试库的"结构差"只能靠本工具兜住。）

特点
----
- **不启动 Web 应用 / 不 create_all**：只 import 模型填 ``db.metadatas``，默认**严格只读**。
- **不硬编码任何库 / 表 / 列清单**：模型侧从 ``db.metadatas[bind].tables`` 读，
  现网侧用 ``sqlalchemy.inspect(engine).get_columns()`` 反射；库与绑定来自
  ``config.SQLALCHEMY_BINDS``。模型加了新列，本工具自动就能发现缺口。
- **补列 DDL 与 ORM 同源**：用 ``CreateColumn(col).compile(dialect)`` 生成，类型一致。
- **SQLite 限制处理**：``ADD COLUMN`` 加不了 "NOT NULL 且无默认值" 的列，此时自动降级为
  可空列（Python 端 ``default`` 照常生效；存量旧行按 NULL 处理），并在输出中标注 ``→可空``。
  ``UNIQUE`` 列无法用 ADD COLUMN 补，会跳过并提示人工处理。
- ``--fix`` 才动手，且改动前用 ``scripts/_db_backup.py`` 备份对应库，幂等可重复执行。

用法
----
    py -3.12 scripts/check_columns.py          # 只读自检：打印缺列清单；有缺口时退出码 1
    py -3.12 scripts/check_columns.py --fix    # 补列（改库前自动备份，幂等）
"""
import os
import sys
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _db_backup import backup_db      # noqa: E402
from _ddl_guard import assert_ident   # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from sqlalchemy import create_engine, inspect, text   # noqa: E402
from sqlalchemy.schema import CreateColumn            # noqa: E402
import config as config_mod                           # noqa: E402
from app.extensions import db                         # noqa: E402
import app.models                                     # noqa: E402,F401

# 显式 import 各模块，确保所有模型都注册进 db.metadatas（与 app/__init__.py 建表清单同源）
from app.models.grades import (Exam, ExamScore, ExamBand, BandTemplate,      # noqa: E402,F401
                               TeacherSubjectLink, AiKey, AiGlobalKey, AiReport,
                               AiChatMessage, Certificate)
from app.models.academic import (Teacher, InspectionRecord, TeacherAchievement,  # noqa: E402,F401
                                 FormCategory, FormTemplate, FormQuestion,
                                 FormSubmission, FormAnswer)
from app.models.portrait import StudentPortrait, PortraitComment, PortraitEvent  # noqa: E402,F401
from app.models.timetable import (TermSchedule, PeriodDef, ScheduleEntry,    # noqa: E402,F401
                                  ScheduleSwap, ScheduleVersion)


def bind_engines():
    """按 config 的绑定关系建引擎（create_engine 是惰性的，不会创建文件）。"""
    engines = {None: create_engine(config_mod.Config.SQLALCHEMY_DATABASE_URI)}
    for key, uri in config_mod.Config.SQLALCHEMY_BINDS.items():
        engines[key] = create_engine(uri)
    return engines


def column_ddl(col, dialect):
    """生成某列的 SQLite 列定义；返回 (ddl, downgraded)。

    非空且无 server_default 的列降级为可空（SQLite 的 ADD COLUMN 不接受
    "NOT NULL 且默认值为 NULL"），否则会直接报错。
    """
    ddl = str(CreateColumn(col).compile(dialect=dialect)).strip()
    downgraded = False
    if col.nullable is False and col.server_default is None and 'NOT NULL' in ddl:
        ddl = ddl.replace(' NOT NULL', '')
        downgraded = True
    return ddl, downgraded


def scan():
    """扫描全部绑定库，返回逐表"模型列 vs 现网列"比对结果。只读。"""
    engines = bind_engines()
    rows = []
    for bind_key in sorted(db.metadatas, key=lambda k: (k is not None, str(k))):
        metadata = db.metadatas[bind_key]
        engine = engines.get(bind_key)
        bind_label = '(默认/system)' if bind_key is None else bind_key
        if engine is None:
            for tname in sorted(metadata.tables):
                rows.append({'bind': bind_label, 'table': tname, 'db_path': None,
                             'engine': None, 'no_engine': True, 'no_file': False,
                             'missing_table': False, 'missing': [], 'extra': []})
            continue
        db_path = engine.url.database
        # 只读保护：SQLite 在 connect 时会把不存在的库文件"建"出来（0 字节），
        # 因此库文件缺失时直接跳过，绝不去连它；否则只读自检也会产生副作用。
        is_sqlite = engine.url.get_backend_name() == 'sqlite'
        if is_sqlite and (not db_path or not os.path.exists(db_path)):
            for tname in sorted(metadata.tables):
                rows.append({'bind': bind_label, 'table': tname, 'db_path': db_path,
                             'engine': engine, 'no_engine': False, 'no_file': True,
                             'missing_table': False, 'missing': [], 'extra': []})
            continue
        insp = inspect(engine)
        for tname in sorted(metadata.tables):
            table = metadata.tables[tname]
            row = {'bind': bind_label, 'table': tname, 'db_path': db_path,
                   'engine': engine, 'no_engine': False, 'no_file': False,
                   'missing_table': False, 'missing': [], 'extra': []}
            if not insp.has_table(tname):
                row['missing_table'] = True
                rows.append(row)
                continue
            model_cols = [c.name for c in table.columns]
            real_cols = [c['name'] for c in insp.get_columns(tname)]
            row['missing'] = [c for c in model_cols if c not in real_cols]
            row['extra'] = [c for c in real_cols if c not in model_cols]
            rows.append(row)
    return rows


def report(rows):
    """打印差异表，返回缺口数量（缺失列 + 缺失表）。"""
    n_tables = sum(1 for r in rows if not r.get('no_engine'))
    n_gap = sum(len(r['missing']) for r in rows if not r.get('no_engine'))
    print(f'扫描 {len(db.metadatas)} 个绑定库 / {n_tables} 张表 / 缺列 {n_gap} 个\n')

    miss_rows = [r for r in rows
                 if not r.get('no_file') and not r.get('missing_table') and r['missing']]
    extra_rows = [r for r in rows if r.get('extra')]
    gone_tables = [r for r in rows if r.get('missing_table') or r.get('no_file')]

    print('【缺列】模型声明、但现网表里没有（真缺口，--fix 可补）：')
    if miss_rows:
        for r in miss_rows:
            print(f'  {r["bind"]:16s} {r["table"]:24s} {", ".join(r["missing"])}')
    else:
        print('  （无）')

    if gone_tables:
        print('\n【整表/整库缺失】库文件或表不存在（请先跑建表/迁移脚本，--fix 不处理）：')
        for r in gone_tables:
            why = '库文件不存在' if r.get('no_file') else '表不存在'
            print(f'  {r["bind"]:16s} {r["table"]:24s} ({why}: {r["db_path"]})')

    print('\n【多余】现网有、模型未声明（多为历史遗留，本工具不动）：')
    if extra_rows:
        for r in extra_rows:
            print(f'  {r["bind"]:16s} {r["table"]:24s} {", ".join(r["extra"])}')
    else:
        print('  （无）')

    gap = len(miss_rows)
    if gap == 0 and not gone_tables:
        print('\n结论：模型列与现网列已对齐，无缺口。')
    else:
        print(f'\n结论：发现 {gap} 张表共 {sum(len(r["missing"]) for r in miss_rows)} 个缺失列'
              + (f'、{len(gone_tables)} 张缺失表' if gone_tables else '')
              + '。可执行 --fix 补列（会先备份对应库）。')
    return gap + len(gone_tables)


def fix(rows):
    """补列：按库分组，改前备份，ALTER TABLE ADD COLUMN（幂等：先查列是否存在）。"""
    # 按表名在全部 metadata 中定位（同名表分属不同 bind，按名取首个即可）
    by_db = OrderedDict()
    table_meta = {}
    for meta in db.metadatas.values():
        for tname, tbl in meta.tables.items():
            table_meta.setdefault(tname, tbl)
    for r in rows:
        if r.get('no_engine') or r.get('missing_table') or not r['missing']:
            continue
        by_db.setdefault(r['db_path'], []).append((r['engine'], r['table'], r['missing']))

    if not by_db:
        print('\n无需补列：没有可补的缺失列。')
        return

    for db_path, items in by_db.items():
        if not db_path or not os.path.exists(db_path):
            print(f'\n[跳过] 库文件不存在：{db_path}')
            continue
        # 项目约定：改动 .db 前先备份（scripts/_db_backup.py）
        backup_db(db_path)
        engine = items[0][0]
        insp = inspect(engine)
        n = 0
        with engine.begin() as conn:
            for _engine, tname, miss_list in items:
                tbl_name = assert_ident(tname, '表名')
                table = table_meta.get(tname)
                if table is None:
                    print(f'  [跳过] {tname}：模型侧未找到该表')
                    continue
                real_now = {c['name'] for c in insp.get_columns(tname)}
                for cname in miss_list:
                    cname_safe = assert_ident(cname, '列名')
                    if cname_safe in real_now:      # 幂等：别人已补过就跳过
                        continue
                    col = table.columns.get(cname)
                    if col is None:
                        continue
                    if col.unique:
                        print(f'  [跳过] {tname}.{cname}：SQLite 不支持 ADD COLUMN 加 UNIQUE 列，'
                              f'请人工处理')
                        continue
                    ddl, downgraded = column_ddl(col, engine.dialect)
                    note = '   （非空列降级为可空：SQLite 限制）' if downgraded else ''
                    conn.execute(text(f'ALTER TABLE "{tbl_name}" ADD COLUMN {ddl}'))
                    print(f'  [补列] {os.path.basename(db_path)}: {tname}.{cname}'
                          f'  <- {ddl}{note}')
                    n += 1
        print(f'  -> {os.path.basename(db_path)} 新增 {n} 列')
    print('\n完成。若应用正在运行，请重启后再访问相关页面。')


def main():
    auto_fix = '--fix' in sys.argv[1:]
    rows = scan()
    gap = report(rows)
    if auto_fix and gap:
        fix(rows)
        print('\n复查：')
        gap = report(scan())
    return 1 if gap else 0


if __name__ == '__main__':
    raise SystemExit(main())
