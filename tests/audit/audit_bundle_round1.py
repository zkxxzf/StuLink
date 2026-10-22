# -*- coding: utf-8 -*-
"""轮1 验证：学科组长方案删除 / 监考场次删除 → 整组快照 → 恢复

要点：
- 方案删的是"方案 + 整学期组长登记"，组长无法由课表推导，必须能恢复；
- 删场次连带删监考格与排除项，排除项原来会变孤儿，现在一起归档、一起清。
"""
import os
import sqlite3
import sys
from datetime import date

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


print('=== A) 学科组长方案：删除 → 恢复 ===')
with a.app_context():
    from app.models.academic import SubjectLeader, SubjectLeaderPlan
    # 清残留
    for p in SubjectLeaderPlan.query.filter_by(name='TMP_LEADER_PLAN').all():
        SubjectLeader.query.filter_by(school_year=p.school_year).delete()
        db.session.delete(p)
    db.session.commit()

    plan = SubjectLeaderPlan(name='TMP_LEADER_PLAN', school_year='2099-2100',
                             term='上', is_current=False)
    db.session.add(plan)
    db.session.commit()
    PID = plan.id
    db.session.add_all([
        SubjectLeader(school_year='2099-2100', term='上', grade='高一',
                      subject='语文', leader_name='甲', category='备课组长'),
        SubjectLeader(school_year='2099-2100', term='上', grade='高一',
                      subject='数学', leader_name='乙', category='备课组长'),
    ])
    db.session.commit()
    n_before = SubjectLeader.query.filter_by(school_year='2099-2100').count()
    print(f'   临时方案 #{PID}，登记 {n_before} 条')

    from app.modules.academic.services.subject_leader_service import delete_plan
    admin = User.query.filter_by(username='admin').first()
    p2, removed = delete_plan(PID, operator_id=admin.id)
    case('删除返回条数正确', removed == 2, f'removed={removed}')
    case('方案与登记均已删',
         SubjectLeaderPlan.query.filter_by(id=PID).count() == 0
         and SubjectLeader.query.filter_by(school_year='2099-2100').count() == 0)

    from app.models.trash import TrashRecord
    from app.utils.trash import restore
    db.session.expire_all()
    rec = (TrashRecord.query.filter_by(kind='leader_plan_bundle')
           .order_by(TrashRecord.id.desc()).first())
    case('回收站有整组快照', rec is not None,
         f'#{rec.id} {rec.item_count} 行' if rec else '无')
    if rec is not None:
        okk, msg, n = restore(rec)
        case('恢复成功', okk, msg)
        db.session.expire_all()
        case('方案按原 id 回来', SubjectLeaderPlan.query.filter_by(id=PID).count() == 1)
        back = SubjectLeader.query.filter_by(school_year='2099-2100').count()
        case('两条登记回来', back == 2, f'{back} 条')

print('\n=== B) 监考场次：删除 → 恢复（含排除项不再孤儿） ===')
with a.app_context():
    from app.models.grades import (AffairInvigExclude, AffairInvigilation,
                                   AffairSchedule, ExamAffair)
    for af in ExamAffair.query.filter_by(name='TMP_SCH_BATCH').all():
        for m in (AffairSchedule, AffairInvigilation, AffairInvigExclude):
            m.query.filter_by(affair_id=af.id).delete()
        db.session.delete(af)
    db.session.commit()
    aff = ExamAffair(name='TMP_SCH_BATCH', grade='2024级')
    db.session.add(aff)
    db.session.commit()
    AID = aff.id
    sch = AffairSchedule(affair_id=AID, subject='语文', exam_date=date.today())
    db.session.add(sch)
    db.session.commit()
    SID = sch.id
    db.session.add_all([
        AffairInvigilation(affair_id=AID, schedule_id=SID, room_no='R1'),
        AffairInvigilation(affair_id=AID, schedule_id=SID, room_no='R2'),
        AffairInvigExclude(affair_id=AID, schedule_id=SID, name='张三'),
    ])
    db.session.commit()
    print(f'   临时批次 #{AID}，场次 #{SID}，2 监考格 + 1 排除项')

with a.test_client() as c:
    csrf = login(c, 'admin')
    r = c.post(f'/grades/affairs/{AID}/schedules/{SID}/delete',
               headers={'X-CSRFToken': csrf})
    case('删除接口成功', r.status_code == 200 and (r.get_json() or {}).get('ok'),
         f'HTTP {r.status_code}')

with a.app_context():
    from app.models.grades import (AffairInvigExclude, AffairInvigilation,
                                   AffairSchedule)
    from app.models.trash import TrashRecord
    from app.utils.trash import restore
    db.session.expire_all()
    case('场次已删除', AffairSchedule.query.filter_by(id=SID).count() == 0)
    case('监考格已清空',
         AffairInvigilation.query.filter_by(schedule_id=SID).count() == 0)
    case('排除项已一并清掉（不再孤儿）',
         AffairInvigExclude.query.filter_by(schedule_id=SID).count() == 0)
    rec = (TrashRecord.query.filter_by(kind='affair_schedule_bundle')
           .order_by(TrashRecord.id.desc()).first())
    case('回收站有整组快照', rec is not None,
         f'#{rec.id} {rec.item_count} 行' if rec else '无')
    if rec is not None:
        okk, msg, n = restore(rec)
        case('恢复成功', okk, msg)
        db.session.expire_all()
        case('场次按原 id 回来', AffairSchedule.query.filter_by(id=SID).count() == 1)
        case('2 格监考回来',
             AffairInvigilation.query.filter_by(schedule_id=SID).count() == 2)
        case('排除项回来',
             AffairInvigExclude.query.filter_by(schedule_id=SID).count() == 1)

print('\n=== 清理 ===')
with a.app_context():
    from app.models.academic import SubjectLeader, SubjectLeaderPlan
    from app.models.grades import (AffairInvigExclude, AffairInvigilation,
                                   AffairSchedule, ExamAffair)
    SubjectLeader.query.filter_by(school_year='2099-2100').delete()
    SubjectLeaderPlan.query.filter_by(name='TMP_LEADER_PLAN').delete()
    for m in (AffairSchedule, AffairInvigilation, AffairInvigExclude):
        m.query.filter_by(affair_id=AID).delete()
    ExamAffair.query.filter_by(id=AID).delete()
    db.session.commit()

con = sqlite3.connect('data/system.db', timeout=15)
con.execute("DELETE FROM trash_records WHERE kind IN "
            "('leader_plan_bundle','affair_schedule_bundle')")
con.commit()
con.close()

print(f'\n===== 轮1：通过 {ok} / 失败 {fail} =====')
sys.exit(1 if fail else 0)
