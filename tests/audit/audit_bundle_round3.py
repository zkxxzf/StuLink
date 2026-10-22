# -*- coding: utf-8 -*-
"""轮3 验证：任课映射方案 / 通知收件人 删除 → 整组快照 → 恢复"""
import os
import sqlite3
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import User  # noqa: E402

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


print('=== A) 任课映射方案：删除 → 恢复 ===')
with a.app_context():
    from app.models.grades import TeacherLinkPlan, TeacherLinkPlanRow
    from app.models.trash import TrashRecord
    from app.utils.trash import restore
    admin = User.query.filter_by(username='admin').first()

    for p in TeacherLinkPlan.query.filter_by(name='TMP_LINK_PLAN').all():
        TeacherLinkPlanRow.query.filter_by(plan_id=p.id).delete()
        db.session.delete(p)
    db.session.commit()

    plan = TeacherLinkPlan(name='TMP_LINK_PLAN', is_current=False)
    db.session.add(plan)
    db.session.commit()
    PID = plan.id
    db.session.add_all([
        TeacherLinkPlanRow(plan_id=PID, grade='2024级', class_name='01班',
                           subject='语文', user_id=admin.id),
        TeacherLinkPlanRow(plan_id=PID, grade='2024级', class_name='01班',
                           subject='数学', user_id=admin.id),
    ])
    db.session.commit()
    print(f'   临时方案 #{PID}，2 条明细')

    from app.modules.grades.services.link_plan_service import delete_plan
    deleted = delete_plan(PID, operator_id=admin.id)
    case('删除返回方案', deleted is not None)
    case('方案与明细均已删',
         TeacherLinkPlan.query.filter_by(id=PID).count() == 0
         and TeacherLinkPlanRow.query.filter_by(plan_id=PID).count() == 0)

    db.session.expire_all()
    rec = (TrashRecord.query.filter_by(kind='link_plan_bundle')
           .order_by(TrashRecord.id.desc()).first())
    case('回收站有整组快照', rec is not None,
         f'#{rec.id} {rec.item_count} 行' if rec else '无')
    if rec is not None:
        okk, msg, n = restore(rec)
        case('恢复成功', okk, msg)
        db.session.expire_all()
        case('方案按原 id 回来',
             TeacherLinkPlan.query.filter_by(id=PID).count() == 1)
        back = TeacherLinkPlanRow.query.filter_by(plan_id=PID).count()
        case('2 条明细回来', back == 2, f'{back} 条')

print('\n=== B) 通知收件人：删除 → 恢复（含已读状态） ===')
with a.app_context():
    from app.models.notification import (Notification, NotificationRead,
                                         NotificationRecipient)
    from app.models.trash import TrashRecord
    from app.utils.trash import restore
    admin = User.query.filter_by(username='admin').first()

    for n in Notification.query.filter_by(title='TMP_NOTIF').all():
        NotificationRecipient.query.filter_by(notification_id=n.id).delete()
        NotificationRead.query.filter_by(notification_id=n.id).delete()
        db.session.delete(n)
    db.session.commit()

    notif = Notification(title='TMP_NOTIF', content='测试', is_active=True,
                         recipient_count=2, read_count=1)
    db.session.add(notif)
    db.session.commit()
    NID = notif.id
    other = (User.query.filter(User.username != 'admin').first())
    db.session.add_all([
        NotificationRecipient(notification_id=NID, user_id=admin.id, is_read=True),
        NotificationRecipient(notification_id=NID, user_id=other.id, is_read=False),
    ])
    db.session.commit()
    print(f'   临时通知 #{NID}，2 位收件人（1 已读）')

    from app.modules.notifications.services.notification_service import (
        delete_notification)
    okdel = delete_notification(NID, operator_id=admin.id)
    case('删除成功', okdel)
    db.session.expire_all()
    case('收回人行已清空',
         NotificationRecipient.query.filter_by(notification_id=NID).count() == 0)
    n2 = db.session.get(Notification, NID)
    case('通知本体仅软删（还在）', n2 is not None and n2.is_active is False)
    case('计数未被清零（恢复后能对上）',
         n2.recipient_count == 2, f'recipient_count={n2.recipient_count}')

    rec = (TrashRecord.query.filter_by(kind='notification_recipients')
           .order_by(TrashRecord.id.desc()).first())
    case('回收站有收件人快照', rec is not None,
         f'#{rec.id} {rec.item_count} 行' if rec else '无')
    if rec is not None:
        okk, msg, n = restore(rec)
        case('恢复成功', okk, msg)
        db.session.expire_all()
        back = NotificationRecipient.query.filter_by(notification_id=NID).count()
        case('2 位收件人回来', back == 2, f'{back} 位')
        rd = NotificationRecipient.query.filter_by(
            notification_id=NID, is_read=True).count()
        case('已读状态保留', rd == 1, f'{rd} 已读')

print('\n=== 清理 ===')
with a.app_context():
    from app.models.grades import TeacherLinkPlan, TeacherLinkPlanRow
    from app.models.notification import (Notification, NotificationRead,
                                         NotificationRecipient)
    TeacherLinkPlanRow.query.filter_by(plan_id=PID).delete()
    TeacherLinkPlan.query.filter_by(id=PID).delete()
    NotificationRecipient.query.filter_by(notification_id=NID).delete()
    NotificationRead.query.filter_by(notification_id=NID).delete()
    Notification.query.filter_by(id=NID).delete()
    db.session.commit()

con = sqlite3.connect('data/system.db', timeout=15)
con.execute("DELETE FROM trash_records WHERE kind IN "
            "('link_plan_bundle','notification_recipients')")
con.commit()
con.close()

print(f'\n===== 轮3：通过 {ok} / 失败 {fail} =====')
sys.exit(1 if fail else 0)
