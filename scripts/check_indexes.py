#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""多库索引自检 / 修复工具（2026-10-10）

背景（为什么需要它）
------------------
SQLAlchemy 的 ``create_all()`` 只会创建"缺失的表"，**不会给已存在的表补建索引**。
所以模型里新加一个 ``db.Index(...)``（或列上的 ``index=True``）之后，存量库——
尤其是做过分库/迁移的库——可能**永远拿不到这个索引，且不会有任何报错**。
2026-10-10 教务分库时就踩到过：``idx_inspect_date_period``（只写在补建脚本里、
旧库从未建成）与 ``ix_form_submissions_round_id``（模型声明了、迁移漏建）都属这类
"静默缺口"。本工具把当时人工 ``PRAGMA index_list`` 逐库核对的做法自动化。

特点
----
- **不启动 Web 应用**：只 import 模型填 ``db.metadatas``，不调用 ``create_app()``
  （后者会 ``create_all`` 甚至写入 admin 记录，有副作用）；默认**严格只读**。
- **不硬编码任何库 / 表 / 索引清单**：模型侧从 ``db.metadatas`` 动态读取，现网侧用
  ``sqlalchemy.inspect(engine).get_indexes()`` 反射，库与绑定来自 ``config.SQLALCHEMY_BINDS``。
  （这正是 ``add_performance_indexes.py`` 的 ``INDEX_SPECS`` 式硬编码会翻车的根因。）
- **按「列集合 + 唯一性」判定是否已满足，而不是按名字**：
  模型声明的非唯一索引，现网存在"同列集合"的任意索引即视为已满足；唯一索引则要求
  现网"同列集合且唯一"。这样 ``idx_stu_grade_class`` 与现网等价索引
  ``idx_student_grade_class`` 会被判为已满足，自检不会天天报假缺口。
- ``--fix`` 才建索引，且动手前用 ``scripts/_db_backup.py`` 备份对应库，幂等可重复执行。

用法
----
    py -3.12 scripts/check_indexes.py          # 只读自检：打印差异表；有缺口时退出码 1
    py -3.12 scripts/check_indexes.py --fix    # 补建缺失索引（改库前自动备份，幂等）

输出分三类：**缺失**（模型有、库没有 = 真缺口）/ **等价改名**（列集合+唯一性相同、
仅名字不同 = 已满足）/ **多余**（库里有、模型未声明 = 提示，不一定是问题）。
"""
import os
import sys
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _db_backup import backup_db      # noqa: E402
from _ddl_guard import assert_ident   # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from sqlalchemy import create_engine, inspect, text  # noqa: E402
import config as config_mod                          # noqa: E402
from app.extensions import db                        # noqa: E402
import app.models                                    # noqa: E402,F401

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

# SQLite 为 UNIQUE 约束 / 主键自动生成的索引名，不属于模型 Index 声明，比对时忽略
_AUTO_PREFIX = 'sqlite_autoindex_'


def bind_engines():
    """按 config 的绑定关系建引擎（create_engine 是惰性的，不会创建文件）。"""
    engines = {None: create_engine(config_mod.Config.SQLALCHEMY_DATABASE_URI)}
    for key, uri in config_mod.Config.SQLALCHEMY_BINDS.items():
        engines[key] = create_engine(uri)
    return engines


def declared_indexes(table):
    """模型声明的索引：[{'name','cols','unique'}]（列按定义顺序）。"""
    out = []
    for ix in table.indexes:
        cols = tuple(c.name for c in ix.columns if c.name)
        out.append({'name': ix.name, 'cols': cols, 'unique': bool(ix.unique)})
    return sorted(out, key=lambda d: d['name'])


def actual_indexes(conn_insp, table_name):
    """现网实际索引：[{'name','cols','unique'}]，去掉 SQLite 自动索引。"""
    out = []
    for ix in conn_insp.get_indexes(table_name):
        name = ix.get('name') or ''
        if name.startswith(_AUTO_PREFIX):
            continue
        cols = tuple(c for c in (ix.get('column_names') or []) if c)
        out.append({'name': name, 'cols': cols, 'unique': bool(ix.get('unique'))})
    return out


def match_indexes(declared, actual):
    """按「列集合 + 唯一性」配对，返回 (已满足[(声明, 现网)], 缺失[声明], 多余[现网])。"""
    used, satisfied, missing = set(), [], []
    for d in declared:
        hit = None
        for i, a in enumerate(actual):
            if i in used:
                continue
            if set(a['cols']) == set(d['cols']) and (not d['unique'] or a['unique']):
                hit = (i, a)
                break
        if hit:
            used.add(hit[0])
            satisfied.append((d, hit[1]))
        else:
            missing.append(d)
    extra = [a for i, a in enumerate(actual) if i not in used]
    return satisfied, missing, extra


def scan():
    """扫描全部绑定库，返回逐表比对结果列表。只读。"""
    engines = bind_engines()
    rows = []
    for bind_key in sorted(db.metadatas, key=lambda k: (k is not None, str(k))):
        metadata = db.metadatas[bind_key]
        engine = engines.get(bind_key)
        bind_label = '(默认/system)' if bind_key is None else bind_key
        if engine is None:
            for tname in sorted(metadata.tables):
                rows.append({'bind': bind_label, 'table': tname, 'db_path': None,
                             'engine': None, 'no_engine': True,
                             'satisfied': [], 'missing': [], 'extra': []})
            continue
        db_path = engine.url.database
        # 只读保护：SQLite 在 connect 时会把不存在的库文件"建"出来（0 字节），
        # 因此库文件缺失时直接跳过，绝不去连它；否则只读自检也会产生副作用。
        is_sqlite = engine.url.get_backend_name() == 'sqlite'
        if is_sqlite and (not db_path or not os.path.exists(db_path)):
            for tname in sorted(metadata.tables):
                rows.append({'bind': bind_label, 'table': tname, 'db_path': db_path,
                             'engine': engine, 'no_engine': False, 'no_file': True,
                             'missing_table': False, 'satisfied': [],
                             'missing': declared_indexes(metadata.tables[tname]),
                             'extra': []})
            continue
        insp = inspect(engine)
        for tname in sorted(metadata.tables):
            table = metadata.tables[tname]
            row = {'bind': bind_label, 'table': tname, 'db_path': db_path,
                   'engine': engine, 'no_engine': False, 'no_file': False,
                   'missing_table': False, 'satisfied': [], 'missing': [], 'extra': []}
            if not insp.has_table(tname):
                row['missing_table'] = True
                # 整表缺失时，其声明索引也一并列为缺失，便于 --fix 报告
                row['missing'] = declared_indexes(table)
                rows.append(row)
                continue
            sat, miss, extra = match_indexes(declared_indexes(table),
                                             actual_indexes(insp, tname))
            row.update(satisfied=sat, missing=miss, extra=extra)
            rows.append(row)
    return rows


def report(rows):
    """打印差异表，返回缺口数量（缺失索引 + 缺失表）。"""
    n_tables = sum(1 for r in rows if not r.get('no_engine'))
    n_decl = sum(len(r['satisfied']) + len(r['missing']) for r in rows if not r.get('no_engine'))
    print(f'扫描 {len(db.metadatas)} 个绑定库 / {n_tables} 张表 / 模型声明 {n_decl} 个索引\n')

    miss_rows = [(r, m) for r in rows
                 if not r.get('no_file') and not r.get('missing_table')
                 for m in r['missing']]
    renamed = [(r, d, a) for r in rows for d, a in r['satisfied'] if d['name'] != a['name']]
    extra_rows = [(r, a) for r in rows for a in r['extra']]
    gone_tables = [r for r in rows if r.get('missing_table') or r.get('no_file')]

    print('【缺失】模型声明、但现网不存在（真缺口，--fix 可补）：')
    if miss_rows:
        for r, m in miss_rows:
            cols = ','.join(m['cols']) or '?'
            uniq = ' UNIQUE' if m['unique'] else ''
            print(f'  {r["bind"]:16s} {r["table"]:24s} {m["name"]:34s} ({cols}){uniq}')
    else:
        print('  （无）')

    if gone_tables:
        print('\n【整表/整库缺失】库文件或表不存在（请先跑建表/迁移脚本，--fix 不处理）：')
        for r in gone_tables:
            why = '库文件不存在' if r.get('no_file') else '表不存在'
            print(f'  {r["bind"]:16s} {r["table"]:24s} ({why}: {r["db_path"]})')

    print('\n【等价改名】列集合与唯一性相同、仅名字不同（已满足，无需处理）：')
    if renamed:
        for r, d, a in renamed:
            print(f'  {r["bind"]:16s} {r["table"]:24s} 声明 {d["name"]} -> 现网 {a["name"]}')
    else:
        print('  （无）')

    print('\n【多余】现网存在、模型未声明（提示，通常为历史遗留或脚本建的索引）：')
    if extra_rows:
        for r, a in extra_rows:
            cols = ','.join(a['cols']) or '?'
            print(f'  {r["bind"]:16s} {r["table"]:24s} {a["name"]:34s} ({cols})')
    else:
        print('  （无）')

    gap = len(miss_rows)
    if gap == 0 and not gone_tables:
        print('\n结论：三处已对齐，无缺口。')
    else:
        print(f'\n结论：发现 {gap} 个缺失索引'
              + (f'、{len(gone_tables)} 张缺失表' if gone_tables else '')
              + '。可执行 --fix 补建（会先备份对应库）。')
    return gap + len(gone_tables)


def fix(rows):
    """补建缺失索引：按库分组，改前备份，CREATE [UNIQUE] INDEX IF NOT EXISTS。"""
    by_db = OrderedDict()
    for r in rows:
        if r.get('no_engine') or r.get('missing_table') or not r['missing']:
            continue
        by_db.setdefault(r['db_path'], []).append((r['engine'], r['table'], r['missing']))

    if not by_db:
        print('\n无需创建：没有可补建的缺失索引。')
        return

    for db_path, items in by_db.items():
        if not db_path or not os.path.exists(db_path):
            print(f'\n[跳过] 库文件不存在：{db_path}')
            continue
        # 项目约定：改动 .db 前先备份（scripts/_db_backup.py）
        backup_db(db_path)
        conn_engine = items[0][0]
        n = 0
        with conn_engine.begin() as conn:
            for _engine, tname, miss_list in items:
                tbl = assert_ident(tname, '表名')
                for m in miss_list:
                    if not m['cols']:
                        continue
                    name = assert_ident(m['name'], '索引名')
                    cols = ', '.join(f'"{assert_ident(c, "列名")}"' for c in m['cols'])
                    uniq = 'UNIQUE ' if m['unique'] else ''
                    conn.execute(text(
                        f'CREATE {uniq}INDEX IF NOT EXISTS "{name}" ON "{tbl}" ({cols})'))
                    print(f'  [创建] {os.path.basename(db_path)}: {name} ON {tbl}({cols})')
                    n += 1
        print(f'  -> {os.path.basename(db_path)} 新建 {n} 个索引')
    print('\n完成。若应用正在运行，请重启使新索引生效。')


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
