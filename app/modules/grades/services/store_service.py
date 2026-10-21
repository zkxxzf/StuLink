# StuLink v1.19.50.57 2026-10-10
# 成绩落库服务：解析结果按 模式A(增量覆盖)/模式B(整场重置) 合并写入 exam_scores
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from collections import defaultdict

from app.extensions import db
from app.models.grades import ExamScore, TOTAL_SUBJECT, subjects_of_selection


def _detach(by_no, no, row):
    """从倒排索引里摘掉一行（删除成绩行时同步维护，避免索引与 existing 不一致）"""
    rows = by_no.get(no)
    if not rows:
        return
    try:
        rows.remove(row)
    except ValueError:
        pass


def apply_import(exam, parsed, mode='A', remove_missing=False):
    """将 parsed(parse_score_excel 结果) 写入成绩表（不 commit，由调用方统一提交）
    模式 A：行×列增量覆盖；文件未出现的科目列保留；文件科目列为空=该科缺考（删旧行）
    模式 B：清空本场全部成绩后导入
    未出现在文件中的旧学生默认保留；remove_missing=True 时移除
    返回差异摘要 dict
    """
    # P1 M-18：已毕业年级冻结成绩写入（导入/覆盖都拒绝）
    from app.utils.helpers import assert_grade_not_graduated
    assert_grade_not_graduated(exam.grade)
    rows = parsed['rows']
    file_subjects = parsed['headers']['subjects']
    summary = {'new_students': 0, 'updated_rows': 0, 'deleted_rows': 0,
               'created_rows': 0, 'unseen': 0, 'total_auto': 0, 'total_external': 0}

    existing = {}   # (no, subject) -> ExamScore
    # PERF-01：学号倒排索引 no -> [ExamScore]。
    # 原先每次刷新快照都要全量遍历 existing（本场 2 万+ 行），导入 2 千学生即 4 千万次
    # 无谓迭代；改为按学号直取，整体由 O(人数×行数) 降到 O(人数×每人行数)。
    by_no = defaultdict(list)
    for row in ExamScore.query.filter_by(exam_id=exam.id).all():
        existing[(row.student_no, row.subject)] = row
        by_no[row.student_no].append(row)
    initial_nos = {no for (no, _sub) in existing}

    if mode == 'B':
        for key in list(existing.keys()):
            db.session.delete(existing.pop(key))
        by_no.clear()
        summary['deleted_rows'] = 0  # 重置计数（整场清空不计入"缺考删除"）
        # 修复：模式B删除后立即 flush，确保 DELETE 先于后续 INSERT 落库，
        # 避免重导文件包含本场已有学生时同事务内触发
        # UNIQUE(exam_id,student_no,subject) 冲突导致整场导入回滚
        db.session.flush()

    # 本次文件出现的学生集合
    nos_in_file = set()
    for r in rows:
        nos_in_file.add(r['no'])
        raw_map = r.get('raw_subjects') or {}      # v1.19.0 原始分（可选）
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
                    by_no[r['no']].append(old)
                    db.session.add(old)
                    summary['created_rows'] += 1
                old.score = score
                old.raw_score = raw_map.get(sub)       # 无原始分列时置空
            else:
                # 文件含该科列但为空 = 本次缺考：删除旧行
                if old is not None:
                    db.session.delete(old)
                    del existing[key]
                    _detach(by_no, r['no'], old)
                    summary['deleted_rows'] += 1
        # 注：此处原有一句 `_refresh_snapshot(..., skip_deleted=True)`，
        # 该参数会让循环内每行直接 continue（纯空转，一次导入白跑 4 千万次迭代）。
        # 快照刷新统一由下方总分行 upsert 之后的 _refresh_snapshot 完成（覆盖该生全部行）。

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
                _detach(by_no, r['no'], total_row)
                summary['deleted_rows'] += 1
        else:
            if total_row is None:
                total_row = ExamScore(exam_id=exam.id, student_no=r['no'],
                                      subject=TOTAL_SUBJECT)
                existing[key_total] = total_row
                by_no[r['no']].append(total_row)
                db.session.add(total_row)
                summary['created_rows'] += 1
            total_row.score = total_score
            total_row.raw_score = raw_map.get(TOTAL_SUBJECT)   # v1.19.0 总分原始分（可选）
        _refresh_snapshot(by_no.get(r['no'], ()), r)

    # 未出现学生处理（PERF-02：原先对每个未出现学号再全量扫一遍 existing，
    # 整场换人时是 4 千万次迭代；改为一次遍历收集待删键）
    unseen_nos = set()
    stale_keys = []
    for key in existing:
        no = key[0]
        if no not in nos_in_file:
            unseen_nos.add(no)
            stale_keys.append(key)
    if remove_missing:
        for key in stale_keys:
            db.session.delete(existing.pop(key))
        for no in unseen_nos:
            by_no.pop(no, None)
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


def _refresh_snapshot(rows, r, skip_deleted=False):
    """把 r 对应学生在本场全部行的快照字段刷新（统一以主库当前信息为准）

    rows：该学生的成绩行集合（由调用方按学号倒排索引直取，避免全量扫描）。
    skip_deleted：保留仅为向后兼容，恒为 False 时行为不变（为 True 会跳过全部刷新，
    历史上曾因此造成每次导入纯空转一次全量遍历）。
    """
    if skip_deleted:
        return
    for row in rows:
        row.student_name = r.get('name', row.student_name)
        row.grade = r.get('grade', row.grade)
        row.class_name = r.get('class_name', row.class_name)
        row.class_type = r.get('class_type', row.class_type)   # v1.19.50.57 班型快照
        row.direction = r.get('direction', row.direction)
        row.subject_selection = r.get('subject_selection', row.subject_selection)
        if r.get('exam_no'):
            row.exam_no = r['exam_no']                 # v1.19.0 考号快照（文件没给就不覆盖）
