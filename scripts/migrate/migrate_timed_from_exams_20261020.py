# -*- coding: utf-8 -*-
"""把历次考试里 exam_type='限时练' 的场次搬到限时练独立表，并删除原考试。

## 背景
2026-10-20 起「限时练」是独立模块（三张 timed_* 表）。历史上用普通考试流程
建过一批「限时练」场次，它们仍出现在历次考试列表里；用户口径：
**成绩搬进限时练板块，历次考试里把这些场次删掉。**

## 做什么（每场）
1. 建同名的 ``timed_exams`` 场次（年级 / 日期 / 学期 / 原 id 备注）；
2. 把 ``exam_scores`` 的**科目行**（不含「总分」）逐行写入 ``timed_scores``，
   班级优先取**总分行**的考试当时班级（科目行班级可能是当前班）；
3. 每个科目重算全年级名次（同分并列 1,1,3）；
4. 原考试按 ``exam_delete`` 同一套流程**归档进回收站**后级联删除
   （成绩 / 分层 / AI 报告 / 改分留痕 / 任课快照 / 考务批次）。

## 用法
    py -3.12 scripts/migrate/migrate_timed_from_exams_20261020.py           # 预览
    py -3.12 scripts/migrate/migrate_timed_from_exams_20261020.py --apply   # 执行

## 安全
- `--apply` 前自动备份 grades.db，并调用 `_mig_log` 登记；
- 每场一个事务：迁移失败则该场整体回滚，**不会半搬**；
- 已存在同名（年级+名称）限时练场次的，跳过该场并提示（幂等）；
- 删除前必定归档，归档失败则中止（fail-closed）。
"""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_SCRIPTS = _os.path.dirname(_HERE)
_ROOT = _os.path.dirname(_SCRIPTS)
for _p in (_ROOT, _SCRIPTS):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

SCRIPT_NAME = _os.path.basename(__file__)
APPLY = '--apply' in _sys.argv
GRD_DB = _os.path.join(_ROOT, 'data', 'grades.db')

SOURCE_TYPE = '限时练'
MARK = '由历次考试迁移'


def _source_exams():
    """待迁移的普通考试场次（按日期先后）"""
    from app.models.grades import Exam
    return (Exam.query.filter(Exam.exam_type == SOURCE_TYPE)
            .order_by(Exam.exam_date.asc(), Exam.id.asc()).all())


def _subject_rows(exam_id):
    """科目成绩行（排除总分）"""
    from app.models.grades import ExamScore
    return ExamScore.query.filter(ExamScore.exam_id == int(exam_id),
                                  ExamScore.subject != '总分').all()


def _migrate_one(exam):
    """建场次 + 搬科目成绩 + 重算名次；返回 (场次, 成绩行数)"""
    from app.extensions import db
    from app.models.grades import ExamScore
    from app.models.timed import TimedExam, TimedScore
    from app.modules.grades.services import timed_service

    dup = TimedExam.query.filter_by(grade=exam.grade, name=exam.name).first()
    if dup:
        print(f'  [跳过] 已存在同名限时练：{exam.grade} {exam.name}')
        return dup, 0

    te = TimedExam(name=exam.name, grade=exam.grade, exam_date=exam.exam_date,
                   term=exam.term, grade_semester=exam.grade_semester,
                   remark=f'{MARK}（原考试 #{exam.id}）',
                   status='imported', operator_id=exam.operator_id)
    db.session.add(te)
    db.session.flush()

    # 班级口径：总分行是考试当时班级（权威），科目行可能是"现在"的班
    total_cls = {r.student_no: r.class_name for r in ExamScore.query.filter_by(
        exam_id=exam.id, subject='总分').all()}
    rows = _subject_rows(exam.id)
    subjects = set()
    for r in rows:
        subjects.add(r.subject)
        db.session.add(TimedScore(
            exam_id=te.id, student_no=r.student_no,
            student_name=r.student_name, zhixue_no=r.exam_no,
            grade=r.grade, class_name=total_cls.get(r.student_no) or r.class_name,
            subject=r.subject, score=float(r.score)))
    db.session.flush()
    for subject in sorted(subjects):
        timed_service.recalc_subject_ranks(te.id, subject)
    return te, len(rows)


def _archive_and_delete(exam):
    """归档整场考试后级联删除（与 exam_delete 同一套表清单）"""
    from app.extensions import db
    from app.models.grades import (AffairInvigilation, AffairInvigilator,
                                   AffairRoom, AffairSchedule, AffairStudent,
                                   AffairTeacher, AiReport, ExamAffair,
                                   ExamBand, ExamScore, ExamTeacherLink,
                                   ScoreChangeLog)
    from app.modules.grades.routes.exam_affairs import delete_affair_cascade
    from app.utils.trash import archive_snapshot

    affairs = ExamAffair.query.filter_by(exam_id=exam.id).all()
    tables = [
        (exam.__class__, exam),
        (ExamScore, ExamScore.query.filter_by(exam_id=exam.id).all()),
        (ExamBand, ExamBand.query.filter_by(exam_id=exam.id).all()),
        (AiReport, AiReport.query.filter_by(exam_id=exam.id).all()),
        (ScoreChangeLog, ScoreChangeLog.query.filter_by(
            exam_id=exam.id).all()),
        (ExamTeacherLink, ExamTeacherLink.query.filter_by(
            exam_id=exam.id).all()),
    ]
    for a in affairs:
        tables += [
            (ExamAffair, a),
            (AffairRoom, AffairRoom.query.filter_by(affair_id=a.id).all()),
            (AffairStudent, AffairStudent.query.filter_by(affair_id=a.id).all()),
            (AffairInvigilation,
             AffairInvigilation.query.filter_by(affair_id=a.id).all()),
            (AffairSchedule,
             AffairSchedule.query.filter_by(affair_id=a.id).all()),
            (AffairInvigilator,
             AffairInvigilator.query.filter_by(affair_id=a.id).all()),
            (AffairTeacher, AffairTeacher.query.filter_by(affair_id=a.id).all()),
        ]
    snap = archive_snapshot('grades', 'exam_bundle', '整场考试', tables=tables,
                            summary=f'{exam.grade} {exam.name}：{MARK}前归档')
    if snap is None:
        raise RuntimeError('归档失败，已中止（避免数据不可恢复）')
    db.session.commit()                    # 归档先落库，再删

    ExamScore.query.filter_by(exam_id=exam.id).delete()
    ExamBand.query.filter_by(exam_id=exam.id).delete()
    AiReport.query.filter_by(exam_id=exam.id).delete()
    ScoreChangeLog.query.filter_by(exam_id=exam.id).delete()
    ExamTeacherLink.query.filter_by(exam_id=exam.id).delete()
    for a in affairs:
        delete_affair_cascade(a.id)
    db.session.delete(exam)


def main():
    if not _os.path.exists(GRD_DB):
        print(f'[ERROR] 找不到 {GRD_DB}')
        return 1

    from app import create_app
    from app.extensions import db

    app = create_app()
    with app.app_context():
        exams = _source_exams()
        print('== 待迁移的历史限时练 ==')
        total_rows = 0
        for e in exams:
            n = len(_subject_rows(e.id))
            total_rows += n
            print(f'  #{e.id} {e.grade} {e.name} {e.exam_date} · {n} 行')
        print(f'  合计 {len(exams)} 场 / {total_rows} 行成绩')
        if not exams:
            print('  无需迁移')
            return 0
        if not APPLY:
            print('\n[DRY-RUN] 未改库；确认后加 --apply')
            return 0

        from _db_backup import backup_db
        backup_db(GRD_DB, tag='before_timed_from_exams_20261020')

        ok = 0
        for e in exams:
            try:
                te, n = _migrate_one(e)
                _archive_and_delete(e)
                db.session.commit()
                ok += 1
                print(f'  [OK] #{e.id} → 限时练 #{te.id} · {n} 行，原考试已归档删除')
            except Exception as exc:                    # noqa: BLE001
                db.session.rollback()
                print(f'  [FAIL] #{e.id} {e.name}：{exc}')
        print(f'\n[APPLIED] 迁移 {ok}/{len(exams)} 场')

        try:
            import _mig_log
            _mig_log.record(SCRIPT_NAME,
                            note=f'历次考试「限时练」{ok} 场搬入 timed_* 并删除原考试')
        except Exception as exc:                        # noqa: BLE001
            print(f'[WARN] 迁移登记失败：{exc}')
    return 0


if __name__ == '__main__':
    _sys.exit(main())
