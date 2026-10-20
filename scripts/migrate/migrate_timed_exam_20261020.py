# -*- coding: utf-8 -*-
"""2026-10-20 新增「限时练」三张独立表（与普通考试完全隔离）。

- ``timed_exams``          限时练场次
- ``timed_scores``         限时练单科成绩（缺考不落库）
- ``timed_band_templates`` 限时练专属名次区间模板（含一条内置模板）

## 为什么必须单独迁移
`create_all` 只会建"缺失的表"，但生产容器启动时也会按 bind 建表；
本脚本用于**显式、可审计**地建表 + 登记，避免上线漏跑
（`docs/规范与说明/数据库迁移清单.md` 有对应条目）。

## 用法
    py -3.12 scripts/migrate/migrate_timed_exam_20261020.py           # 预览
    py -3.12 scripts/migrate/migrate_timed_exam_20261020.py --apply   # 建表

## 安全
- 幂等：`CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`，
  内置模板按名称判重，已有同名不覆盖；
- `--apply` 前自动备份 grades.db，并调用 `_mig_log` 登记；
- 只新增表，绝不改动 `exams` / `exam_scores`。
"""
import json
import os as _os
import sqlite3
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_SCRIPTS = _os.path.dirname(_HERE)
_ROOT = _os.path.dirname(_SCRIPTS)
for _p in (_ROOT, _SCRIPTS):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

SCRIPT_NAME = _os.path.basename(__file__)
APPLY = '--apply' in _sys.argv
GRD_DB = _os.path.join(_ROOT, 'data', 'grades.db')

BUILTIN_NAME = '限时练名次分层'
BUILTIN_LAYERS = [
    {'name': '前10名', 'from_rank': 1, 'to_rank': 10},
    {'name': '11-50名', 'from_rank': 11, 'to_rank': 50},
    {'name': '51-100名', 'from_rank': 51, 'to_rank': 100},
    {'name': '101-300名', 'from_rank': 101, 'to_rank': 300},
    {'name': '300名以后', 'from_rank': 301, 'to_rank': None},
]

DDL = (
    """CREATE TABLE IF NOT EXISTS timed_exams (
        id INTEGER NOT NULL PRIMARY KEY,
        name VARCHAR(100) NOT NULL,
        grade VARCHAR(10) NOT NULL,
        exam_date DATE NOT NULL,
        term VARCHAR(20),
        grade_semester VARCHAR(10),
        template_id INTEGER,
        status VARCHAR(10) NOT NULL DEFAULT 'draft',
        remark VARCHAR(200),
        import_draft TEXT,
        import_token VARCHAR(36),
        operator_id INTEGER,
        created_at DATETIME,
        updated_at DATETIME
    )""",
    """CREATE TABLE IF NOT EXISTS timed_scores (
        id INTEGER NOT NULL PRIMARY KEY,
        exam_id INTEGER NOT NULL,
        student_no VARCHAR(20) NOT NULL,
        student_name VARCHAR(50),
        zhixue_no VARCHAR(20),
        grade VARCHAR(10),
        class_name VARCHAR(10),
        subject VARCHAR(10) NOT NULL,
        score FLOAT NOT NULL,
        rank_grade INTEGER,
        updated_at DATETIME,
        CONSTRAINT uq_timed_stu_subject
            UNIQUE (exam_id, student_no, subject)
    )""",
    """CREATE TABLE IF NOT EXISTS timed_band_templates (
        id INTEGER NOT NULL PRIMARY KEY,
        name VARCHAR(50) NOT NULL UNIQUE,
        layers_json TEXT,
        is_builtin BOOLEAN,
        sort_order INTEGER,
        created_by INTEGER,
        created_at DATETIME,
        updated_at DATETIME
    )""",
)

INDEXES = (
    'CREATE INDEX IF NOT EXISTS idx_timed_exam_grade_date '
    'ON timed_exams (grade, exam_date)',
    'CREATE INDEX IF NOT EXISTS idx_timed_scores_exam_subject '
    'ON timed_scores (exam_id, subject)',
    'CREATE INDEX IF NOT EXISTS idx_timed_scores_rank '
    'ON timed_scores (exam_id, subject, rank_grade)',
)


def _table_names(con):
    """当前库里已有的表名集合"""
    return {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def main():
    if not _os.path.exists(GRD_DB):
        print(f'[ERROR] 找不到 {GRD_DB}')
        return 1
    con = sqlite3.connect(GRD_DB)
    existing = _table_names(con)
    need = [t for t in ('timed_exams', 'timed_scores',
                        'timed_band_templates') if t not in existing]
    has_builtin = False
    if 'timed_band_templates' in existing:
        row = con.execute('SELECT COUNT(*) FROM timed_band_templates '
                          'WHERE name=?', (BUILTIN_NAME,)).fetchone()
        has_builtin = bool(row and row[0])

    print('== 限时练独立表 ==')
    print('  已存在:', sorted(existing & {'timed_exams', 'timed_scores',
                                          'timed_band_templates'}))
    print('  待创建:', need or '无')
    print('  内置模板:', '已存在' if has_builtin else '待插入')
    if not need and has_builtin:
        print('  无需变更')
        con.close()
        return 0
    if not APPLY:
        print('\n[DRY-RUN] 未改库；确认后加 --apply')
        con.close()
        return 0

    from _db_backup import backup_db
    backup_db(GRD_DB, tag='before_timed_exam_20261020')
    for ddl in DDL:
        con.execute(ddl)
    for idx in INDEXES:
        con.execute(idx)
    if not has_builtin:
        con.execute(
            'INSERT INTO timed_band_templates '
            '(name, layers_json, is_builtin, sort_order, created_at, updated_at) '
            "VALUES (?, ?, 1, 0, datetime('now'), datetime('now'))",
            (BUILTIN_NAME, json.dumps(BUILTIN_LAYERS, ensure_ascii=False)))
    con.commit()
    con.close()

    try:
        import _mig_log
        _mig_log.record(SCRIPT_NAME,
                        note='新建 timed_exams / timed_scores / '
                             'timed_band_templates + 内置名次模板')
    except Exception as _e:  # noqa: BLE001
        print(f'[WARN] 迁移登记失败：{_e}')
    print('\n[APPLIED] 完成（三张表 + 内置模板）')
    return 0


if __name__ == '__main__':
    _sys.exit(main())
