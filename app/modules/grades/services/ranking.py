# StuLink v1.17.0 2026-09-21
# 成绩排名服务：方向排名 + 班排名 + 总分进退步（同分同名次，后延跳号）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from sqlalchemy import or_, and_
from app.extensions import db
from app.models.grades import ExamScore, TOTAL_SUBJECT, Exam


def _assign_ranks(rows, attr):
    """rows: list[ExamScore]，原地写排名属性 attr（rank_dir/rank_class，不提交）
    按 score 降序；同分同名次，下一名次按人数顺延（1,1,3,…）；score 为 None 的不参与
    修复：原实现将同一名次同时双写 rank_dir/rank_class，导致"方向排"实为班内排名
    """
    rows.sort(key=lambda r: (r.score is None, -(r.score or 0)))
    rank = 0
    idx = 0
    n = len(rows)
    while idx < n:
        cur_score = rows[idx].score
        if cur_score is None:
            setattr(rows[idx], attr, None)
            idx += 1
            continue
        # 找同分段
        end = idx
        while end < n and rows[end].score == cur_score:
            end += 1
        rank += 1
        for j in range(idx, end):
            setattr(rows[j], attr, rank)
        idx = end
    return rows


def recalc_exam(exam_id):
    """整场重算排名（事务内批量更新，不 commit 由调用方控制）
    排名规则：方向内 + 班内（班内同方向子集）；缺考/未参考无行不参与
    总分进退步：与上一场同方向考试的方向排名比较
    """
    exam = Exam.query.get(exam_id)
    if not exam:
        return 0
    rows = ExamScore.query.filter_by(exam_id=exam_id).all()
    if not rows:
        return 0

    # 1) 方向排名：按 (subject, direction) 分组（同方向跨班统一排名）
    # 修复：原实现仅按班级分组且双写两字段，方向排名错成班内名次
    groups = {}
    for r in rows:
        if r.score is None:
            continue  # 无成绩不参与（缺考无行，正常不会出现）
        groups.setdefault((r.subject, r.direction), []).append(r)
    for g_rows in groups.values():
        _assign_ranks(g_rows, attr='rank_dir')
    # 2) 班排名：按 (subject, direction, class_name) 分组（班内同方向子集）
    groups = {}
    for r in rows:
        if r.score is None:
            continue
        groups.setdefault((r.subject, r.direction, r.class_name), []).append(r)
    for g_rows in groups.values():
        _assign_ranks(g_rows, attr='rank_class')

    # 3) 总分进退步：找上一场同年级考试
    prev_exam = (Exam.query
                 .filter(Exam.grade == exam.grade,
                         or_(Exam.exam_date < exam.exam_date,
                             and_(Exam.exam_date == exam.exam_date,
                                  Exam.id < exam.id)))
                 .order_by(Exam.exam_date.desc(), Exam.id.desc())
                 .first())
    prev_ranks = {}  # (direction, student_no) -> 总分方向排名
    if prev_exam:
        prev_total_rows = ExamScore.query.filter_by(
            exam_id=prev_exam.id, subject=TOTAL_SUBJECT).all()
        for r in prev_total_rows:
            if r.score is not None and r.rank_dir:
                prev_ranks[(r.direction, r.student_no)] = r.rank_dir

    # 4) 更新进退步（仅总分行）
    for r in rows:
        if r.subject != TOTAL_SUBJECT:
            continue
        prev = prev_ranks.get((r.direction, r.student_no))
        if prev and r.rank_dir:
            r.move_rank = r.rank_dir - prev
        else:
            r.move_rank = None

    return len(rows)
