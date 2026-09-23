# StuLink v1.18.0.0 2026-09-23
# 成绩落库服务：解析结果按 模式A(增量覆盖)/模式B(整场重置) 合并写入 exam_scores
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from app.extensions import db
from app.models.grades import ExamScore, TOTAL_SUBJECT, subjects_of_selection


def apply_import(exam, parsed, mode='A', remove_missing=False):
    """将 parsed(parse_score_excel 结果) 写入成绩表（不 commit，由调用方统一提交）
    模式 A：行×列增量覆盖；文件未出现的科目列保留；文件科目列为空=该科缺考（删旧行）
    模式 B：清空本场全部成绩后导入
    未出现在文件中的旧学生默认保留；remove_missing=True 时移除
    返回差异摘要 dict
    """
    rows = parsed['rows']
    file_subjects = parsed['headers']['subjects']
    summary = {'new_students': 0, 'updated_rows': 0, 'deleted_rows': 0,
               'created_rows': 0, 'unseen': 0, 'total_auto': 0, 'total_external': 0}

    existing = {}   # (no, subject) -> ExamScore
    for row in ExamScore.query.filter_by(exam_id=exam.id).all():
        existing[(row.student_no, row.subject)] = row
    initial_nos = {no for (no, _sub) in existing}

    if mode == 'B':
        for key in list(existing.keys()):
            db.session.delete(existing.pop(key))
        summary['deleted_rows'] = 0  # 重置计数（整场清空不计入"缺考删除"）
        # 修复：模式B删除后立即 flush，确保 DELETE 先于后续 INSERT 落库，
        # 避免重导文件包含本场已有学生时同事务内触发
        # UNIQUE(exam_id,student_no,subject) 冲突导致整场导入回滚
        db.session.flush()

    # 本次文件出现的学生集合
    nos_in_file = set()
    for r in rows:
        nos_in_file.add(r['no'])
        for sub in file_subjects:
            score = r['subjects'].get(sub)
            key = (r['no'], sub)
            old = existing.get(key)
            if score is not None:
                if old is not None:
                    old.score = score
                    summary['updated_rows'] += 1
                else:
                    old = ExamScore(exam_id=exam.id, student_no=r['no'], subject=sub)
                    existing[key] = old
                    db.session.add(old)
                    summary['created_rows'] += 1
                old.score = score
            else:
                # 文件含该科列但为空 = 本次缺考：删除旧行
                if old is not None:
                    db.session.delete(old)
                    del existing[key]
                    summary['deleted_rows'] += 1
        # 刷新该生全部行的快照（姓名/班级/方向/选科，本场考试内统一）
        _refresh_snapshot(existing, r, skip_deleted=True)

        # 总分行（upsert）
        key_total = (r['no'], TOTAL_SUBJECT)
        total_row = existing.get(key_total)
        sel_subs = subjects_of_selection(r['subject_selection']) or []
        if r['total'] is not None:
            total_score = r['total']
            summary['total_external'] += 1
        else:
            # 合并后应考科目得分（缺考按 0），任一应考科有分才产生总分行
            merged = {}
            for sub in sel_subs:
                cur = existing.get((r['no'], sub))
                if cur is not None and cur.score is not None:
                    merged[sub] = cur.score
            if merged:
                total_score = round(sum(merged.get(s, 0) for s in sel_subs), 1)
                summary['total_auto'] += 1
            else:
                total_score = None
        if total_score is None:
            if total_row is not None:
                db.session.delete(total_row)
                del existing[key_total]
                summary['deleted_rows'] += 1
        else:
            if total_row is None:
                total_row = ExamScore(exam_id=exam.id, student_no=r['no'],
                                      subject=TOTAL_SUBJECT)
                existing[key_total] = total_row
                db.session.add(total_row)
                summary['created_rows'] += 1
            total_row.score = total_score
        _refresh_snapshot(existing, r)

    # 未出现学生处理
    unseen_nos = set()
    for (no, sub) in list(existing.keys()):
        if no not in nos_in_file:
            unseen_nos.add(no)
    if remove_missing:
        for no in unseen_nos:
            for key in [k for k in existing if k[0] == no]:
                db.session.delete(existing.pop(key))
    summary['unseen'] = len(unseen_nos)
    summary['new_students'] = len([no for no in nos_in_file if no not in initial_nos])

    exam.import_draft = None
    exam.import_token = None
    exam.status = 'imported'
    return summary


def refresh_student_total(exam, student_no):
    """单科成绩修改/删除后重算该生总分行（缺考按 0，与导入口径一致）
    修复：此前总分仅在导入时计算，手工改分后总分行不联动，导致总分≠Σ单科，
    排名与全部统计分析基于错误总分。直接修改总分行时不应调用本函数（保留手工修正值）。
    """
    rows = ExamScore.query.filter_by(exam_id=exam.id, student_no=student_no).all()
    if not rows:
        return
    # 选科组合取自该生任一行快照（同场考试内统一）
    sel_subs = subjects_of_selection(rows[0].subject_selection) or []
    total_row = None
    merged = {}
    for r in rows:
        if r.subject == TOTAL_SUBJECT:
            total_row = r
        elif r.subject in sel_subs and r.score is not None:
            merged[r.subject] = r.score
    if not sel_subs or not merged:
        # 应考科目全缺考或无法推断选科：删除总分行
        if total_row is not None:
            db.session.delete(total_row)
        return
    total = round(sum(merged.get(s, 0) for s in sel_subs), 1)
    if total_row is None:
        total_row = ExamScore(exam_id=exam.id, student_no=student_no, subject=TOTAL_SUBJECT)
        db.session.add(total_row)
    total_row.score = total


def _refresh_snapshot(existing, r, skip_deleted=False):
    """把 r 对应学生在本场全部行的快照字段刷新（统一以主库当前信息为准）"""
    for key, row in existing.items():
        if key[0] != r['no']:
            continue
        if skip_deleted:
            continue
        row.student_name = r.get('name', row.student_name)
        row.grade = r.get('grade', row.grade)
        row.class_name = r.get('class_name', row.class_name)
        row.direction = r.get('direction', row.direction)
        row.subject_selection = r.get('subject_selection', row.subject_selection)
