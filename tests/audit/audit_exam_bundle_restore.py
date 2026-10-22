# -*- coding: utf-8 -*-
"""验证：整场考试删除 → 整组快照 → 回收站恢复（含考务批次，外键不断链）

不走 HTTP：`exam_delete` 需要输入当前账号密码，测试不掌握真实口令。
这里复刻它的**归档 + 删除序列**，验证快照与恢复本身正确。
"""
import os
import sqlite3
import sys
from datetime import date

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


with a.app_context():
    from app.models.grades import (AiReport, AffairInvigilation, AffairInvigilator,
                                   AffairRoom, AffairSchedule, AffairStudent,
                                   AffairTeacher, Exam, ExamAffair, ExamBand,
                                   ExamScore, ExamTeacherLink, ScoreChangeLog)
    admin = User.query.filter_by(username='admin').first()
    # 清掉上次残留
    for e in Exam.query.filter_by(name='TMP_EXAM').all():
        for m in (ExamScore, ExamBand, AiReport, ScoreChangeLog, ExamTeacherLink):
            m.query.filter_by(exam_id=e.id).delete()
        for af in ExamAffair.query.filter_by(exam_id=e.id).all():
            for m in (AffairRoom, AffairStudent, AffairSchedule,
                      AffairInvigilation, AffairInvigilator, AffairTeacher):
                m.query.filter_by(affair_id=af.id).delete()
            db.session.delete(af)
        db.session.delete(e)
    db.session.commit()

    exam = Exam(name='TMP_EXAM', grade='2024级', exam_date=date.today())
    db.session.add(exam)
    db.session.commit()
    EID = exam.id
    db.session.add_all([
        ExamScore(exam_id=EID, student_no='T1', subject='语文'),
        ExamScore(exam_id=EID, student_no='T2', subject='语文'),
        ExamBand(exam_id=EID, seq=1, name='A档', lower_value=80),
        AiReport(user_id=admin.id, exam_id=EID),
        ScoreChangeLog(exam_id=EID, student_no='T1', subject='语文', action='update'),
        ExamTeacherLink(exam_id=EID, grade='2024级', class_name='01班', subject='语文'),
    ])
    db.session.commit()
    aff = ExamAffair(name='TMP_EXAM_AFF', grade='2024级', exam_id=EID)
    db.session.add(aff)
    db.session.commit()
    AFF = aff.id
    sch = AffairSchedule(affair_id=AFF, subject='语文')
    db.session.add_all([
        AffairRoom(affair_id=AFF, room_no='R1'),
        AffairStudent(affair_id=AFF, student_no='T1'),
        sch,
    ])
    db.session.commit()
    SCHID = sch.id
    db.session.add_all([
        AffairInvigilation(affair_id=AFF, schedule_id=SCHID, room_no='R1'),
        AffairInvigilator(affair_id=AFF, name='监考甲'),
        AffairTeacher(affair_id=AFF, class_name='01班', subject='语文'),
    ])
    db.session.commit()
    print(f'   临时考试 #{EID}，批次 #{AFF}，场次 #{SCHID}')

    # ---- 复刻 exam_delete 的归档段 ----
    from app.utils.trash import archive_snapshot
    _affairs = ExamAffair.query.filter_by(exam_id=EID).all()
    tables = [
        (Exam, exam),
        (ExamScore, ExamScore.query.filter_by(exam_id=EID).all()),
        (ExamBand, ExamBand.query.filter_by(exam_id=EID).all()),
        (AiReport, AiReport.query.filter_by(exam_id=EID).all()),
        (ScoreChangeLog, ScoreChangeLog.query.filter_by(exam_id=EID).all()),
        (ExamTeacherLink, ExamTeacherLink.query.filter_by(exam_id=EID).all()),
    ]
    for _a in _affairs:
        tables += [
            (ExamAffair, _a),
            (AffairRoom, AffairRoom.query.filter_by(affair_id=_a.id).all()),
            (AffairStudent, AffairStudent.query.filter_by(affair_id=_a.id).all()),
            (AffairInvigilation,
             AffairInvigilation.query.filter_by(affair_id=_a.id).all()),
            (AffairSchedule, AffairSchedule.query.filter_by(affair_id=_a.id).all()),
            (AffairInvigilator,
             AffairInvigilator.query.filter_by(affair_id=_a.id).all()),
            (AffairTeacher, AffairTeacher.query.filter_by(affair_id=_a.id).all()),
        ]
    rec = archive_snapshot('grades', 'exam_bundle', '整场考试', tables=tables,
                           summary='TMP_EXAM 测试', operator=admin)
    db.session.commit()
    case('整组快照已生成', rec is not None,
         f'#{rec.id} {rec.item_count} 行' if rec else '无')
    RID = rec.id if rec else None

    # ---- 复刻删除序列 ----
    from app.modules.grades.routes.exam_affairs import delete_affair_cascade
    ExamScore.query.filter_by(exam_id=EID).delete()
    ExamBand.query.filter_by(exam_id=EID).delete()
    AiReport.query.filter_by(exam_id=EID).delete()
    ScoreChangeLog.query.filter_by(exam_id=EID).delete()
    ExamTeacherLink.query.filter_by(exam_id=EID).delete()
    for _a in _affairs:
        delete_affair_cascade(_a.id)
    db.session.delete(db.session.get(Exam, EID))
    db.session.commit()

    left = {
        'Exam': Exam.query.filter_by(id=EID).count(),
        'ExamScore': ExamScore.query.filter_by(exam_id=EID).count(),
        'ExamBand': ExamBand.query.filter_by(exam_id=EID).count(),
        'AiReport': AiReport.query.filter_by(exam_id=EID).count(),
        'ScoreChangeLog': ScoreChangeLog.query.filter_by(exam_id=EID).count(),
        'ExamTeacherLink': ExamTeacherLink.query.filter_by(exam_id=EID).count(),
        'ExamAffair': ExamAffair.query.filter_by(id=AFF).count(),
    }
    case('删除后全部清空', all(v == 0 for v in left.values()), left)

    if RID:
        from app.models.trash import TrashRecord
        from app.utils.trash import restore
        db.session.expire_all()
        rc = db.session.get(TrashRecord, RID)
        okk, msg, n = restore(rc)
        case('恢复成功', okk, msg)

with a.app_context():
    from app.models.grades import (AiReport, AffairInvigilation, AffairInvigilator,
                                   AffairRoom, AffairSchedule, AffairStudent,
                                   AffairTeacher, Exam, ExamAffair, ExamBand,
                                   ExamScore, ExamTeacherLink, ScoreChangeLog)
    db.session.expire_all()
    back = {
        'Exam': Exam.query.filter_by(id=EID).count(),
        'ExamScore': ExamScore.query.filter_by(exam_id=EID).count(),
        'ExamBand': ExamBand.query.filter_by(exam_id=EID).count(),
        'AiReport': AiReport.query.filter_by(exam_id=EID).count(),
        'ScoreChangeLog': ScoreChangeLog.query.filter_by(exam_id=EID).count(),
        'ExamTeacherLink': ExamTeacherLink.query.filter_by(exam_id=EID).count(),
        'ExamAffair': ExamAffair.query.filter_by(id=AFF).count(),
        'AffairRoom': AffairRoom.query.filter_by(affair_id=AFF).count(),
        'AffairInvigilation': AffairInvigilation.query.filter_by(affair_id=AFF).count(),
    }
    case('考试/成绩/分层/报告/留痕/任课 全部按原 id 回来',
         all(v > 0 for v in back.values()), back)
    inv = AffairInvigilation.query.filter_by(affair_id=AFF).first()
    case('监考格 schedule_id 未断链',
         inv is not None and inv.schedule_id == SCHID,
         f'schedule_id={getattr(inv, "schedule_id", None)} 期望 {SCHID}')

print('\n=== 清理 ===')
with a.app_context():
    from app.models.grades import (AiReport, AffairInvigilation, AffairInvigilator,
                                   AffairRoom, AffairSchedule, AffairStudent,
                                   AffairTeacher, Exam, ExamAffair, ExamBand,
                                   ExamScore, ExamTeacherLink, ScoreChangeLog)
    for m in (ExamScore, ExamBand, AiReport, ScoreChangeLog, ExamTeacherLink):
        m.query.filter_by(exam_id=EID).delete()
    for m in (AffairRoom, AffairStudent, AffairSchedule,
              AffairInvigilation, AffairInvigilator, AffairTeacher):
        m.query.filter_by(affair_id=AFF).delete()
    ExamAffair.query.filter_by(id=AFF).delete()
    Exam.query.filter_by(id=EID).delete()
    db.session.commit()

con = sqlite3.connect('data/system.db', timeout=15)
con.execute("DELETE FROM trash_records WHERE kind='exam_bundle'")
con.commit()
con.close()

print(f'\n===== 整场考试删除/恢复：通过 {ok} / 失败 {fail} =====')
sys.exit(1 if fail else 0)
