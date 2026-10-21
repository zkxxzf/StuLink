# StuLink v1.19.50.52 2026-10-10
# 成绩排名服务：方向排名 + 班排名 + 总分进退步（同分同名次，后延跳号）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from sqlalchemy import or_, and_
from app.extensions import db
from app.models.grades import ExamScore, TOTAL_SUBJECT, Exam


def _rank_map(rows):
    """rows → {row_id: rank}：降序，同分同名次后延跳号；score None → None。

    2026-10-14 第四轮审计修复（PERF-110）：与 _assign_ranks 同规则，但只产出
    数据（不 setattr 对象），供 bulk_update_mappings 批量写库。
    """
    ordered = sorted(rows, key=lambda r: (r.score is None, -(r.score or 0)))
    out = {}
    rank = 0
    idx = 0
    n = len(ordered)
    while idx < n:
        cur_score = ordered[idx].score
        if cur_score is None:
            out[ordered[idx].id] = None
            idx += 1
            continue
        end = idx
        while end < n and ordered[end].score == cur_score:
            end += 1
        # 2026-10-19 审计：按「组首位置」取名次（同分同名次、后延跳号
        # 1,1,3）。原实现 rank += 1 是密集名次（1,1,2），与文件头、表彰
        # 名单（honor_rank，按位置）以及全系统「并列 1,1,3」口径冲突。
        rank = idx + 1
        for j in range(idx, end):
            out[ordered[j].id] = rank
        idx = end
    return out


def recalc_exam(exam_id):
    """整场重算排名（事务内批量更新，不 commit 由调用方控制）
    排名规则：方向内 + 班内（班内同方向子集）；缺考/未参考无行不参与
    总分进退步：与上一场同方向考试的方向排名比较

    2026-10-14 第四轮审计修复（PERF-110）：原实现逐行 setattr（单场约 5,000 行
    一次性进入 ORM unit-of-work，flush 时逐行 UPDATE）。改为先纯内存算好排名，
    再用 ``bulk_update_mappings`` 批量写（仅写真正变化的列），并把会话中相关行的
    三个排名列 expire（bulk 绕过身份映射，防止后续同事务读取到旧值）。
    """
    exam = Exam.query.get(exam_id)
    if not exam:
        return 0
    rows = ExamScore.query.filter_by(exam_id=exam_id).all()
    if not rows:
        return 0

    changes = {}          # row_id -> {'id':.., 列: 新值}

    def _put(row_id, col, val):
        changes.setdefault(row_id, {'id': row_id})[col] = val

    # 1) 方向排名：按 (subject, direction) 分组（同方向跨班统一排名）
    # 修复：原实现仅按班级分组且双写两字段，方向排名错成班内名次
    groups = {}
    for r in rows:
        if r.score is None:
            continue  # 无成绩不参与（缺考无行，正常不会出现）
        groups.setdefault((r.subject, r.direction), []).append(r)
    for g_rows in groups.values():
        for rid, rank in _rank_map(g_rows).items():
            _put(rid, 'rank_dir', rank)
    # 2) 班排名：按 (subject, direction, class_name) 分组（班内同方向子集）
    groups = {}
    for r in rows:
        if r.score is None:
            continue
        groups.setdefault((r.subject, r.direction, r.class_name), []).append(r)
    for g_rows in groups.values():
        for rid, rank in _rank_map(g_rows).items():
            _put(rid, 'rank_class', rank)

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

    # 4) 更新进退步（仅总分行）—— 读**本次算出的** rank_dir（尚未写回对象）
    for r in rows:
        if r.subject != TOTAL_SUBJECT:
            continue
        new_rank = changes.get(r.id, {}).get('rank_dir', r.rank_dir)
        prev = prev_ranks.get((r.direction, r.student_no))
        _put(r.id, 'move_rank', (new_rank - prev) if (prev and new_rank) else None)

    # 5) 只写真正变化的列
    row_by_id = {r.id: r for r in rows}
    updates = []
    for rid, d in changes.items():
        r = row_by_id.get(rid)
        if r is None:
            continue
        changed = {k: v for k, v in d.items()
                   if k != 'id' and getattr(r, k, None) != v}
        if changed:
            changed['id'] = rid
            updates.append(changed)
    if updates:
        db.session.bulk_update_mappings(ExamScore, updates)
        # bulk 绕过 ORM 身份映射：让会话内这些行重新载入（防同事务读到旧排名）
        for rid in {d['id'] for d in updates}:
            db.session.expire(row_by_id[rid],
                              ['rank_dir', 'rank_class', 'move_rank'])

    return len(rows)
