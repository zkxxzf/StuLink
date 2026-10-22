# -*- coding: utf-8 -*-
"""验证手机号加密（方案 C：随机 IV + HMAC 盲索引）

1) 库内不再有明文手机号
2) 精确查询走盲索引能命中
3) 模糊/片段查询（内存过滤）能命中
4) 同样号码两次加密密文不同（抗枚举）
5) 回收站归档的是密文、恢复后仍是同一个号码（不二次加密）
"""
import os
import sqlite3
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402

a = create_app()
ok = fail = 0


def case(name, cond, extra=''):
    global ok, fail
    extra = str(extra).encode('gbk', 'replace').decode('gbk')
    if cond:
        ok += 1
        print(f'[PASS] {name} {extra}')
    else:
        fail += 1
        print(f'[FAIL] {name} {extra}')


print('=== 1) 库内不再有明文手机号 ===')
con = sqlite3.connect('data/system.db', timeout=15)
for col in ('phone1', 'phone2'):
    n = con.execute(
        f"SELECT COUNT(*) FROM students WHERE {col} IS NOT NULL AND {col} != '' "
        f"AND length({col}) = 11 AND {col} GLOB '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'"
    ).fetchone()[0]
    case(f'{col} 无 11 位明文数字', n == 0, f'剩余 {n} 行')
con.close()

with a.app_context():
    from app.models.student import Student
    from app.utils.crypto import blind_index, encrypt_rand, normalize_phone

    s0 = Student.query.filter(Student.phone1_idx.isnot(None)).first()
    NUM = s0.phone1
    print(f'   样本：#{s0.id} {s0.name} 号码 {NUM[:3]}***')

    print('\n=== 2) 精确查询（盲索引等值） ===')
    idx = Student.phone_index(NUM)
    hit = Student.query.filter_by(phone1_idx=idx).first()
    case('按盲索引精确命中', hit is not None and hit.id == s0.id,
         f'id={getattr(hit, "id", None)}')

    # 带格式差异也要能查到
    idx2 = blind_index(NUM[:3] + '-' + NUM[3:7] + '-' + NUM[7:])
    case('带横线格式也能命中（已规范化）', idx2 == idx)

    print('\n=== 3) 模糊查询（解密后内存过滤） ===')
    prefix = NUM[:3]

    def fuzzy(q, limit=None):
        out = []
        for st in Student.query.all():
            if q in (st.phone1 or '') or q in (st.phone2 or ''):
                out.append(st)
        return out

    hits = fuzzy(prefix)
    case(f'按前 3 位 {prefix} 模糊命中', len(hits) > 0 and s0.id in {x.id for x in hits},
         f'{len(hits)} 人')
    tail = NUM[-4:]
    hits2 = fuzzy(tail)
    case(f'按后 4 位 {tail} 模糊命中', s0.id in {x.id for x in hits2}, f'{len(hits2)} 人')

    print('\n=== 4) 抗枚举（同明文两次密文不同） ===')
    c1, c2 = encrypt_rand(NUM), encrypt_rand(NUM)
    case('两次加密密文不同', c1 != c2)
    case('但盲索引相同（仍可精确查）', blind_index(NUM) == blind_index(NUM))
    case('盲索引不可逆（不含号码片段）',
         NUM[:6] not in (idx or '') and NUM[-4:] not in (idx or ''))
    case('规范化去 +86', normalize_phone('+86' + NUM) == NUM)

print('\n=== 5) 回收站归档的是密文，恢复后号码不变 ===')
with a.app_context():
    from app.models.student import Student
    from app.models.trash import TrashRecord
    from app.utils.trash import archive, restore

    tmp = Student.query.filter_by(student_number='TMP_PHONE_ENC').first()
    if tmp is None:
        tmp = Student(student_number='TMP_PHONE_ENC', name='加密测试',
                      gender='男', grade='2026级', class_name='03班')
        db.session.add(tmp)
        db.session.commit()
    tmp.phone1 = '13900001111'
    tmp.phone2 = '13900002222'
    db.session.commit()
    tid, num1 = tmp.id, tmp.phone1

    rec = archive('system', 'student', '学籍', tmp,
                  summary='手机号加密测试', operator=None)
    db.session.commit()
    rid = rec.id if rec else None
    case('归档成功', rid is not None)

    if rid:
        db.session.expire_all()
        raw = db.session.get(TrashRecord, rid)
        payload = raw.payload or ''
        case('归档 payload 里没有明文号码',
             '13900001111' not in payload and '13900002222' not in payload)

        db.session.delete(tmp)
        db.session.commit()
        db.session.expire_all()
        back = db.session.get(Student, tid)
        case('删除后已不在库', back is None)

        r2 = db.session.get(TrashRecord, rid)
        okk, msg, n = restore(r2)
        case('恢复成功', okk, msg)
        db.session.expire_all()
        back = db.session.get(Student, tid)
        case('恢复后号码与原来一致（没被二次加密）',
             back is not None and back.phone1 == num1,
             f'phone1={getattr(back, "phone1", None)}')
        case('恢复后盲索引仍在',
             back is not None and back.phone1_idx == Student.phone_index(num1))

    # 清理
    for m in (Student,):
        m.query.filter_by(student_number='TMP_PHONE_ENC').delete()
    db.session.commit()

con = sqlite3.connect('data/system.db', timeout=15)
con.execute("DELETE FROM trash_records WHERE summary='手机号加密测试'")
con.execute("DELETE FROM students WHERE student_number='TMP_PHONE_ENC'")
con.commit()
con.close()

print(f'\n===== 手机号加密：通过 {ok} / 失败 {fail} =====')
sys.exit(1 if fail else 0)
