# -*- coding: utf-8 -*-
# StuLink v1.19.49.42 2026-10-20
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""教师教育事业统计字段（2026-10-20）：给 teachers 补 7 列。

背景
----
教育事业综合统计基表里，教师侧三张表（教基4149 教职工情况 / 4153 专任教师分年龄 /
4156 分课程分学历）需要的属性，身份证里没有、任课映射也推不出：

    gender       性别（男/女）          4149「#女」、4153「#女」
    ethnicity    民族                   4149/4153「#少数民族」
    education    学历                   4156 分学历行
    title        职称                   4153 分职称行
    staff_type   人员类别               4149 专任/行政/教辅/工勤/其他
    is_official  是否在编               4149「#在编人员」
    is_foreign   是否外籍               4149「#外籍人员」

年龄不单独存列：由 `id_card_enc` 解密后解析（与学生的分年龄统计同一口径）。

幂等：列已存在则跳过；改库前备份 academic.db；执行后登记 migration_log。
表结构必须与 `app/models/academic.py::Teacher` 一致（check_columns.py 会逐列比对）。

用法：
    py -3.12 scripts/migrate/migrate_teacher_edu_fields_20261020.py          # 预览
    py -3.12 scripts/migrate/migrate_teacher_edu_fields_20261020.py --apply  # 执行
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

BASE_DIR = _ROOT
DB = os.path.join(BASE_DIR, 'data', 'academic.db')
TABLE = 'teachers'
SCRIPT_NAME = os.path.basename(__file__)

try:
    from _db_backup import backup_db
except Exception as _e:  # noqa: BLE001
    backup_db = None
    print(f'[WARN] 备份模块不可用（跳过备份，谨慎执行）：{_e}')
try:
    import _mig_log
except Exception as _e:  # noqa: BLE001
    _mig_log = None
    print(f'[WARN] 迁移登记模块不可用（照常执行，仅不登记）：{_e}')

# (列名, 类型与默认值, 说明)
COLUMNS = [
    ('gender', 'VARCHAR(2)', '性别：男/女'),
    ('ethnicity', 'VARCHAR(20)', '民族（空=按汉族统计）'),
    ('education', 'VARCHAR(20)', '学历'),
    ('title', 'VARCHAR(20)', '职称'),
    ('staff_type', 'VARCHAR(20)', '人员类别'),
    ('is_official', 'BOOLEAN', '是否在编'),
    ('is_foreign', 'BOOLEAN', '是否外籍'),
]


def _have(conn):
    return {r[1] for r in conn.execute(f'PRAGMA table_info({TABLE})')}


def main():
    apply = '--apply' in sys.argv
    if not os.path.exists(DB):
        print(f'库不存在：{DB}')
        return 1
    conn = sqlite3.connect(DB, timeout=15)
    try:
        have = _have(conn)
        todo = [(c, t, d) for c, t, d in COLUMNS if c not in have]
        print(f'== teachers 现有 {len(have)} 列；待补 {len(todo)} 列 ==')
        for c, _t, d in todo:
            print(f'  + {c}  ({d})')
        if not todo:
            print('  结构已是最新，无需改动。')
            return 0
        if not apply:
            print('\n预览模式，未改动。执行：--apply')
            return 0

        if backup_db is not None:
            pairs = backup_db(DB, tag='before_teacher_edu_fields')
            if not pairs or not pairs[0][1]:
                print('中止：备份失败，未执行 DDL。')
                return 2
            print(f'  已备份：{os.path.basename(pairs[0][1])}')

        for c, typ, _d in todo:
            conn.execute(f'ALTER TABLE {TABLE} ADD COLUMN {c} {typ}')
            print(f'  + 列 {c}')
        conn.commit()
        print(f'\n完成：新增 {len(todo)} 列')
        if _mig_log is not None:
            _mig_log.record(SCRIPT_NAME,
                            note='teachers 补教育事业统计属性（性别/民族/学历/职称/人员类别/在编/外籍）')
        return 0
    finally:
        conn.close()


if __name__ == '__main__':
    sys.exit(main())
