# -*- coding: utf-8 -*-
"""验证：删除学籍前会归档，且能从回收站恢复"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

import re  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import User  # noqa: E402
from app.utils.session_guard import fingerprint  # noqa: E402

a = create_app()
a.login_manager.session_protection = None
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


with a.app_context():
    from app.models.student import Student
    admin = User.query.filter_by(username='admin').first()
    uid, fp = admin.id, fingerprint(admin)
    tmp = Student.query.filter_by(student_number='TMP_DEL_9001').first()
    if tmp is None:
        tmp = Student(student_number='TMP_DEL_9001', name='审计待删除',
                      gender='男', grade='2026级', class_name='03班')
        db.session.add(tmp)
        db.session.commit()
    sid, sno = tmp.id, tmp.student_number
    print(f'   临时学生 #{sid} {sno}')

with a.test_client() as c:
    with c.session_transaction() as s:
        s['_user_id'] = str(uid)
        s['_fresh'] = True
        s['_pwd_fp'] = fp
    csrf = (re.search(r'csrfToken\s*=\s*"([^"]+)"',
                      c.get('/students/').get_data(as_text=True)) or [None, ''])[1]
    r = c.post(f'/students/{sid}/delete', headers={'X-CSRFToken': csrf})
    case('删除接口成功', r.status_code in (200, 302), f'HTTP {r.status_code}')

    with a.app_context():
        from app.models.student import Student as S
        from app.models.trash import TrashRecord
        db.session.expire_all()
        case('学生已删除', db.session.get(S, sid) is None)
        rec = (TrashRecord.query.filter_by(kind='student', ref_id=sid)
               .order_by(TrashRecord.id.desc()).first())
        case('删除前已归档（kind=student）', rec is not None,
             f'#{rec.id} {rec.summary}' if rec else '无记录')
        rid = rec.id if rec else None

    if rid:
        r = c.post(f'/trash/{rid}/restore', headers={'X-CSRFToken': csrf})
        case('从回收站恢复成功', r.status_code == 200, (r.get_json() or {}).get('message'))
        with a.app_context():
            from app.models.student import Student as S
            from app.models.trash import TrashRecord
            db.session.expire_all()
            back = S.query.filter_by(student_number=sno).first()
            case('恢复后学籍回到库中（新 context）', back is not None,
                 f'{back.name} {back.grade}{back.class_name}' if back else '无')
            # 用独立连接清理：同一 session 里"先读后写"在 SQLite 上会读锁升级失败
            # （database is locked），测试清场没必要走 ORM。
            import sqlite3
            con = sqlite3.connect('data/system.db', timeout=15)
            con.execute('DELETE FROM trash_records WHERE id=?', (rid,))
            con.execute('DELETE FROM students WHERE student_number=?', (sno,))
            con.commit()
            con.close()

print(f'\n===== 学籍删除归档：通过 {ok} / 失败 {fail} =====')
sys.exit(1 if fail else 0)
