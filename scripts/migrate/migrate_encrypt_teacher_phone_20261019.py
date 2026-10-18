# -*- coding: utf-8 -*-
"""2026-10-19：把 teachers.phone 从明文改为随机 IV 密文 + 填盲索引（phone_idx）。

与学生同一方案 C。注意：登录名 users.username 保持可读（它是账号标识），
本脚本只加密 teachers 表里的**展示用**号码。

用法：
    py -3.12 scripts/migrate/migrate_encrypt_teacher_phone_20261019.py
    py -3.12 scripts/migrate/migrate_encrypt_teacher_phone_20261019.py --apply
"""
import os
import sqlite3
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

APPLY = '--apply' in sys.argv
DB = 'data/academic.db'


def ensure_columns():
    con = sqlite3.connect(DB, timeout=15)
    cur = con.cursor()
    have = {r[1] for r in cur.execute('PRAGMA table_info(teachers)')}
    if 'phone_idx' not in have:
        cur.execute('ALTER TABLE teachers ADD COLUMN phone_idx TEXT')
        print('  + 列 phone_idx')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_teacher_phone '
                'ON teachers(phone_idx)')
    con.commit()
    con.close()


def _is_plain(v):
    v = (v or '').strip()
    return len(v) == 11 and v.isdigit()


def main():
    from app import create_app
    from app.extensions import db
    from app.models.academic import Teacher
    from app.utils.crypto import blind_index, encrypt_rand

    a = create_app()
    with a.app_context():
        ensure_columns()
        rows = Teacher.query.all()
        todo = [(t, t.phone) for t in rows if _is_plain(t.phone)]
        print(f'  共 {len(rows)} 名教师，需要加密 {len(todo)} 行')
        for t, p in todo[:3]:
            print(f'    示例 #{t.id} {t.name}：{p} → '
                  f'{(encrypt_rand(p) or "")[:18]}...')

        if not APPLY:
            print('\n  预览模式，未改动。加 --apply 执行。')
            return

        for t, p in todo:
            t._phone_encrypted = encrypt_rand(p)
            t.phone_idx = blind_index(p)
        db.session.commit()
        print(f'\n  完成：加密 {len(todo)} 行')

        t = Teacher.query.filter(Teacher.phone_idx.isnot(None)).first()
        if t is not None:
            print(f'  抽检 #{t.id} {t.name}：phone={t.phone}')
            print(f'    库内密文={(t._phone_encrypted or "")[:24]}...')


if __name__ == '__main__':
    main()
