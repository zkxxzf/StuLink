# -*- coding: utf-8 -*-
"""验证教师手机号加密（方案 C，与学生同口径）

本库 94 名教师的 phone 当前全为空，所以重点验证**结构与链路**：
1) 写入后库内是密文、读取是明文
2) 精确查询走盲索引能命中（导入去重 / 同步 / 改号查重都依赖它）
3) 列表关键词搜索：完整号码能命中、片段也能命中
4) 登录名 users.username 不受影响（仍可读）
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


NUM = '13700002222'

with a.app_context():
    from app.models.academic import Teacher
    t = Teacher.query.filter_by(name='TMP_TEACHER_PHONE').first()
    if t is None:
        t = Teacher(name='TMP_TEACHER_PHONE', teacher_uid='TMP_TP_UID',
                    subject='语文', status='active')
        db.session.add(t)
        db.session.commit()
    TID = t.id

    print('=== 1) 写入后：库内密文 / 读取明文 ===')
    t.phone = NUM
    db.session.commit()
    case('读取拿到明文', t.phone == NUM, t.phone)
    con = sqlite3.connect('data/academic.db', timeout=15)
    raw = con.execute('SELECT phone FROM teachers WHERE id=?', (TID,)).fetchone()[0]
    con.close()
    case('库内不是明文', raw != NUM, f'{(raw or "")[:20]}...')
    case('库内是密文（base64 形态）', len(raw or '') > 20)

    print('\n=== 2) 精确查询走盲索引 ===')
    hit = Teacher.query.filter_by(phone_idx=Teacher.phone_index(NUM)).first()
    case('按盲索引命中', hit is not None and hit.id == TID)
    case('带横线格式也能命中',
         Teacher.phone_index(NUM[:3] + '-' + NUM[3:7] + '-' + NUM[7:])
         == Teacher.phone_index(NUM))

    print('\n=== 3) 列表关键词搜索（_teacher_query） ===')
    from app.modules.academic.routes.teachers import _teacher_query
    r_full = _teacher_query({'kw': NUM}).all()
    case('完整号码能搜到', TID in {x.id for x in r_full}, f'{len(r_full)} 人')
    r_seg = _teacher_query({'kw': NUM[3:7]}).all()
    case('中间片段能搜到', TID in {x.id for x in r_seg}, f'{len(r_seg)} 人')
    r_none = _teacher_query({'kw': '不存在的关键词XYZ'}).all()
    case('无匹配时不误报', TID not in {x.id for x in r_none})

    print('\n=== 4) 登录名不受影响 ===')
    from app.models import User
    # 登录名本身不受本次加密影响：users 表没动，取任意账号验证仍可读
    u = User.query.first()
    case('users.username 仍可读（登录名）', u is not None and bool(u.username),
         f'样本 {getattr(u, "username", None)}')

print('\n=== 清理 ===')
with a.app_context():
    from app.models.academic import Teacher
    Teacher.query.filter_by(id=TID).delete()
    db.session.commit()

print(f'\n===== 教师手机号加密：通过 {ok} / 失败 {fail} =====')
sys.exit(1 if fail else 0)
