# -*- coding: utf-8 -*-
"""2026-10-19：把 students.phone1 / phone2 从明文改为随机 IV 密文 + 填盲索引。

方案（用户 2026-10-19 定）：方案 C —— 随机 IV 加密 + HMAC 盲索引
- 密文：encrypt_rand(value)，同样号码每次密文都不同，抗枚举对照
- 索引：phone1_idx / phone2_idx = HMAC-SHA256(规范化号码)，只支持精确等值查询
- 透明：模型用 property 加解密，页面/导出/导入无感

幂等：已经是密文的行（不是 11 位纯数字）跳过。
用法：
    py -3.12 scripts/migrate/migrate_encrypt_student_phone_20261019.py          # 预览
    py -3.12 scripts/migrate/migrate_encrypt_student_phone_20261019.py --apply  # 执行
"""
import os
import sqlite3
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

APPLY = '--apply' in sys.argv
DB = 'data/system.db'


def ensure_columns():
    """加 phone1_idx / phone2_idx 列（SQLAlchemy create_all 不会给已有表加列）"""
    con = sqlite3.connect(DB, timeout=15)
    cur = con.cursor()
    have = {r[1] for r in cur.execute('PRAGMA table_info(students)')}
    for col in ('phone1_idx', 'phone2_idx'):
        if col not in have:
            cur.execute(f'ALTER TABLE students ADD COLUMN {col} TEXT')
            print(f'  + 列 {col}')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_student_phone1 '
                'ON students(phone1_idx)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_student_phone2 '
                'ON students(phone2_idx)')
    con.commit()
    con.close()


def main():
    from app import create_app
    from app.extensions import db
    from app.models.student import Student
    from app.utils.crypto import encrypt_rand, blind_index

    a = create_app()
    with a.app_context():
        # 先补列：ORM 查询会带上 phone1_idx/phone2_idx，列不存在会直接报错
        ensure_columns()
        rows = Student.query.all()
        todo = []
        for s in rows:
            p1, p2 = s.phone1, s.phone2
            need1 = bool(p1) and _is_plain(p1)
            need2 = bool(p2) and _is_plain(p2)
            if need1 or need2:
                todo.append((s, p1, p2, need1, need2))
        print(f'  共 {len(rows)} 名学生，需要加密 {len(todo)} 行')

        for s, p1, p2, n1, n2 in todo[:5]:
            print(f'    示例 #{s.id} {s.name}：{p1} → '
                  f'{(encrypt_rand(p1) or "")[:18]}...  idx={blind_index(p1)}')

        if not APPLY:
            print('\n  预览模式，未改动。加 --apply 执行。')
            return

        ensure_columns()
        n = 0
        for s, p1, p2, n1, n2 in todo:
            # 直接写内部列属性，避免 property 读到已加密值时被二次处理
            if n1:
                s._phone1_encrypted = encrypt_rand(p1)
                s.phone1_idx = blind_index(p1)
            if n2:
                s._phone2_encrypted = encrypt_rand(p2)
                s.phone2_idx = blind_index(p2)
            n += 1
            if n % 500 == 0:
                db.session.commit()
                print(f'    已处理 {n} 行')
        db.session.commit()
        print(f'\n  完成：加密 {n} 行')

        # 抽检
        s = Student.query.filter(Student.phone1_idx.isnot(None)).first()
        if s is not None:
            print(f'  抽检 #{s.id} {s.name}：phone1={s.phone1}')
            print(f'    库内密文={(s._phone1_encrypted or "")[:24]}...')
            print(f'    盲索引={s.phone1_idx}')


def _is_plain(v):
    """是否还是明文（11 位纯数字 = 未加密的手机号）"""
    v = (v or '').strip()
    return len(v) == 11 and v.isdigit()


if __name__ == '__main__':
    main()
