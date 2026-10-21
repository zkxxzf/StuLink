# -*- coding: utf-8 -*-
"""把智学网导出的学生名单挂到主库学生上（写 zhixue_no / zhixue_account）。

用法::

    py -3.12 scripts/sync_zhixue_roster.py "名单.xls" --dry-run   # 只看报告
    py -3.12 scripts/sync_zhixue_roster.py "名单.xls"             # 实写（先自动备份）

名单格式（智学网导出，2026-10 版）：年级 | 班级 | 姓名 | 账号 | 准考证号 | 自定义考号
- 关联键：**自定义考号 == students.student_number**（8 位，如 20261108）
- 写入：``zhixue_no`` ← 准考证号（8 位，每场会变）、
        ``zhixue_account`` ← 账号（10 位，稳定）
两边都写，是因为成绩表只给准考证号、名单里账号才稳定；
导入匹配时两个都试，再加姓名+班级兜底。

2026-10-21 用户要求：高一/高二/高三的「学校学号」与「智学网学号」匹配一次。
只改这两列，不动学号、班级、姓名。
"""
import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime

import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

DB = os.path.join(BASE, 'data', 'system.db')


def read_roster(path):
    """名单 → [{grade, cls, name, account, exam_no, school_no}]"""
    book = pd.ExcelFile(path)
    out = []
    for sheet in book.sheet_names:
        df = book.parse(sheet, header=0, dtype=str)
        df.columns = [str(c).strip() for c in df.columns]
        need = {'年级', '班级', '姓名', '账号', '准考证号', '自定义考号'}
        if not need.issubset(set(df.columns)):
            print('[跳过] sheet %s 缺列：%s' % (sheet, need - set(df.columns)))
            continue
        for _, r in df.iterrows():
            out.append({
                'grade': str(r['年级']).strip(),
                'cls': str(r['班级']).strip(),
                'name': str(r['姓名']).strip(),
                'account': str(r['账号']).strip(),
                'exam_no': str(r['准考证号']).strip(),
                'school_no': str(r['自定义考号']).strip(),
            })
    return out


def ensure_column(conn):
    cols = [r[1] for r in conn.execute('PRAGMA table_info(students)')]
    if 'zhixue_account' not in cols:
        conn.execute('ALTER TABLE students ADD COLUMN zhixue_account VARCHAR(20)')
        conn.execute('CREATE INDEX IF NOT EXISTS ix_students_zhixue_account '
                     'ON students (zhixue_account)')
        print('[迁移] students 增加 zhixue_account')
        return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('xls')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    roster = read_roster(args.xls)
    print('名单 %d 人（%s）' % (len(roster), args.xls))

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    added = False
    if not args.dry_run:
        added = ensure_column(conn)
    cols = [r[1] for r in conn.execute('PRAGMA table_info(students)')]
    has_acct = 'zhixue_account' in cols
    if args.dry_run:
        print('[dry-run] zhixue_account 列%s' % ('' if has_acct else '尚不存在'))

    sel = ('SELECT id, student_number, name, grade, class_name, zhixue_no, '
           + ('zhixue_account' if has_acct else "'' AS zhixue_account")
           + ' FROM students')
    stu = {r['student_number']: dict(r) for r in conn.execute(sel)}
    print('学生表 %d 人' % len(stu))

    upd, same, miss, conflict = [], 0, [], []
    for r in roster:
        s = stu.get(r['school_no'])
        if not s:
            miss.append(r)
            continue
        if r['name'] and s['name'] and r['name'] != s['name']:
            conflict.append((r, s))
            continue
        cur_no = (s['zhixue_no'] or '').strip()
        cur_acct = (s['zhixue_account'] or '').strip()
        if cur_no == r['exam_no'] and cur_acct == r['account']:
            same += 1
            continue
        upd.append((s['id'], r['exam_no'], r['account'], s))

    print('待更新 %d 人，已是最新 %d 人，名单里找不到学生 %d 人，姓名冲突 %d 人'
          % (len(upd), same, len(miss), len(conflict)))
    for r, s in conflict[:5]:
        print('  冲突: 名单 %s(%s %s) vs 学生表 %s(%s %s)'
              % (r['name'], r['grade'], r['cls'], s['name'], s['grade'], s['class_name']))
    for r in miss[:5]:
        print('  名单无学号对应: %s %s %s 自定义考号=%s'
              % (r['grade'], r['cls'], r['name'], r['school_no']))

    if args.dry_run:
        print('[dry-run] 未写库')
        return

    if upd:
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup = DB + '.bak_' + ts
        shutil.copy2(DB, backup)
        print('[备份] %s' % backup)
    conn.executemany(
        'UPDATE students SET zhixue_no=?, zhixue_account=? WHERE id=?',
        [(no, acct, sid) for sid, no, acct, _ in upd])
    conn.commit()
    if added:
        print('[迁移] 已加列 zhixue_account')
    print('已更新 %d 人' % len(upd))
    left = conn.execute(
        "SELECT COUNT(1) FROM students WHERE COALESCE(zhixue_no,'')='' "
        "AND COALESCE(zhixue_account,'')=''").fetchone()[0]
    print('仍无任何智学网号的学生：%d 人' % left)
    conn.close()


if __name__ == '__main__':
    main()
