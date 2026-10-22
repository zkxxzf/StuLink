# -*- coding: utf-8 -*-
"""验证：整批考务删除 → 快照归档 → 回收站恢复（外键不断链）

关键点（普通 archive 做不到的）：
- 快照保留原 id，父行 ExamAffair 恢复后仍是同一个 id；
- 子行 AffairRoom/AffairStudent/AffairInvigilation 的 affair_id /
  schedule_id 因此仍指向真实父行，不会变成孤儿。
"""
import os
import sqlite3
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


def login(c, username):
    with a.app_context():
        u = User.query.filter_by(username=username).first()
        uid, fp = u.id, fingerprint(u)
    with c.session_transaction() as s:
        s['_user_id'] = str(uid)
        s['_fresh'] = True
        s['_pwd_fp'] = fp
    html = c.get('/grades/exams').get_data(as_text=True)
    m = re.search(r'csrfToken\s*=\s*"([^"]+)"', html)
    return m.group(1) if m else ''


# ---------- 造一个最小批次（7 张表都有行） ----------
with a.app_context():
    from app.models.grades import (ExamAffair, AffairRoom, AffairStudent, AffairSchedule,
                                   AffairInvigilation, AffairInvigilator, AffairTeacher)
    old = ExamAffair.query.filter_by(name='TMP_BUNDLE').first()
    if old is not None:
        for m in (AffairRoom, AffairStudent, AffairSchedule,
                  AffairInvigilation, AffairInvigilator, AffairTeacher):
            m.query.filter_by(affair_id=old.id).delete()
        db.session.delete(old)
        db.session.commit()
    aff = ExamAffair(name='TMP_BUNDLE', grade='2024级')
    db.session.add(aff)
    db.session.commit()
    AID = aff.id
    sch = AffairSchedule(affair_id=AID, subject='语文')
    db.session.add_all([
        AffairRoom(affair_id=AID, room_no='R1'),
        AffairStudent(affair_id=AID, student_no='T1', name='甲'),
        AffairStudent(affair_id=AID, student_no='T2', name='乙'),
        sch,
    ])
    db.session.commit()
    SCHID = sch.id
    db.session.add_all([
        AffairInvigilation(affair_id=AID, schedule_id=SCHID, room_no='R1'),
        AffairInvigilator(affair_id=AID, name='监考甲'),
        AffairTeacher(affair_id=AID, class_name='01班', subject='语文'),
    ])
    db.session.commit()
    print(f'   临时批次 #{AID}（场次 #{SCHID}）：2 考场相关行 / 2 学生 / 1 场次 / '
          f'1 监考格 / 1 监考员 / 1 任课')

MODELS = ('ExamAffair', 'AffairRoom', 'AffairStudent', 'AffairSchedule',
          'AffairInvigilation', 'AffairInvigilator', 'AffairTeacher')


def count_all(aid):
    with a.app_context():
        from app.models.grades import (ExamAffair, AffairRoom, AffairStudent,
                                       AffairSchedule, AffairInvigilation,
                                       AffairInvigilator, AffairTeacher)
        return {
            'ExamAffair': ExamAffair.query.filter_by(id=aid).count(),
            'AffairRoom': AffairRoom.query.filter_by(affair_id=aid).count(),
            'AffairStudent': AffairStudent.query.filter_by(affair_id=aid).count(),
            'AffairSchedule': AffairSchedule.query.filter_by(affair_id=aid).count(),
            'AffairInvigilation': AffairInvigilation.query.filter_by(affair_id=aid).count(),
            'AffairInvigilator': AffairInvigilator.query.filter_by(affair_id=aid).count(),
            'AffairTeacher': AffairTeacher.query.filter_by(affair_id=aid).count(),
        }


print(f'\n   删除前：{count_all(AID)}')

print('\n=== 1) 无 confirm → 拒绝 ===')
with a.test_client() as c:
    csrf = login(c, 'admin')
    r = c.post(f'/grades/affairs/{AID}/delete', headers={'X-CSRFToken': csrf})
    case('无 confirm 被拒（302 回详情页）', r.status_code == 302, f'HTTP {r.status_code}')
    case('数据未被删除', count_all(AID)['ExamAffair'] == 1)

print('\n=== 2) confirm=CLEAR → 删除并进回收站 ===')
with a.test_client() as c:
    csrf = login(c, 'admin')
    r = c.post(f'/grades/affairs/{AID}/delete', data={'confirm': 'CLEAR'},
               headers={'X-CSRFToken': csrf})
    case('删除成功', r.status_code == 302, f'HTTP {r.status_code}')
    after = count_all(AID)
    case('七张表全部清空', all(v == 0 for v in after.values()), after)

    with a.app_context():
        from app.models.trash import TrashRecord
        db.session.expire_all()
        rec = (TrashRecord.query.filter_by(kind='affair_bundle')
               .order_by(TrashRecord.id.desc()).first())
        case('回收站有整批快照', rec is not None,
             f'#{rec.id} {rec.item_count} 行' if rec else '无记录')
        rid = rec.id if rec else None

    if rid:
        print('\n=== 3) 从回收站整批恢复 ===')
        r = c.post(f'/trash/{rid}/restore', headers={'X-CSRFToken': csrf})
        body = r.get_json() or {}
        case('恢复接口成功', r.status_code == 200 and body.get('ok'), body.get('message'))
        back = count_all(AID)
        case('批次本体按原 id 回来', back['ExamAffair'] == 1, f'id={AID}')
        case('考场/名单回来', back['AffairRoom'] == 1 and back['AffairStudent'] == 2, back)
        case('场次/监考/监考员回来',
             back['AffairSchedule'] == 1 and back['AffairInvigilation'] == 1
             and back['AffairInvigilator'] == 1, back)
        case('任课核对回来', back['AffairTeacher'] == 1)
        # 关键：外键没断
        with a.app_context():
            from app.models.grades import AffairInvigilation, AffairStudent
            db.session.expire_all()
            inv = AffairInvigilation.query.filter_by(affair_id=AID).first()
            case('监考格的 schedule_id 仍指向原场次',
                 inv is not None and inv.schedule_id == SCHID,
                 f'schedule_id={getattr(inv, "schedule_id", None)} 期望 {SCHID}')
            nos = sorted(x.student_no for x in
                         AffairStudent.query.filter_by(affair_id=AID).all())
            case('学生行原样（T1/T2）', nos == ['T1', 'T2'], nos)

print('\n=== 清理 ===')
with a.app_context():
    from app.models.grades import (ExamAffair, AffairRoom, AffairStudent,
                                   AffairSchedule, AffairInvigilation,
                                   AffairInvigilator, AffairTeacher)
    for m in (AffairRoom, AffairStudent, AffairSchedule,
              AffairInvigilation, AffairInvigilator, AffairTeacher):
        m.query.filter_by(affair_id=AID).delete()
    ExamAffair.query.filter_by(id=AID).delete()
    db.session.commit()

con = sqlite3.connect('data/system.db', timeout=15)
con.execute("DELETE FROM trash_records WHERE kind='affair_bundle'")
con.commit()
con.close()
print('   已清理临时批次与快照')

print(f'\n===== 整批删除/恢复：通过 {ok} / 失败 {fail} =====')
sys.exit(1 if fail else 0)
