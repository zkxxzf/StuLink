#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""给 students 加 zhixue_account 列（智学网登录账号，10 位）+ 索引。

2026-10-21：智学网成绩表只给准考证号，而准考证号**每场重排**
（同一届 47637703 → 47642671），必须再存一个稳定的账号做匹配键。
本迁移只加列，不写数据；数据由 `scripts/sync_zhixue_roster.py` 灌入。

幂等：列已存在直接登记跳过。
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _mig_log  # noqa: E402

SCRIPT_NAME = os.path.basename(__file__)
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, 'data', 'system.db')


def main():
    if _mig_log.is_applied(SCRIPT_NAME):
        print('[SKIP] %s 已应用过' % SCRIPT_NAME)
        return
    if not os.path.exists(DB):
        print('[WARN] 找不到 %s（新环境由 create_all 直接建列）' % DB)
        return
    conn = sqlite3.connect(DB)
    cols = [r[1] for r in conn.execute('PRAGMA table_info(students)')]
    if 'zhixue_account' in cols:
        print('[SKIP] students.zhixue_account 已存在')
    else:
        conn.execute('ALTER TABLE students ADD COLUMN zhixue_account VARCHAR(20)')
        conn.execute('CREATE INDEX IF NOT EXISTS ix_students_zhixue_account '
                     'ON students (zhixue_account)')
        conn.commit()
        print('[OK] students.zhixue_account 已添加')
    try:
        _mig_log.record(SCRIPT_NAME, note='students.zhixue_account')
    except Exception as e:                                   # noqa: BLE001
        print('[WARN] 登记失败（不影响迁移）：%s' % e)


if __name__ == '__main__':
    main()
