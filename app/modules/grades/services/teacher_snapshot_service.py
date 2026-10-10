# StuLink v1.18.9.1 2026-10-10
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""考试任课快照服务（v1.18.9.1）

背景：`TeacherSubjectLink` 只存"当前"任课映射。教师一换（尤其重新分班后），
历史考试的教师维度分析会全部归到现在的教师名下，上线率/去差均分排名失真。

本服务把「考试 × 班级 × 科目 → 当时任课教师」定格到 `exam_teacher_links`：
- 成绩导入时自动快照（source='import'，可信）
- 旧成绩 Excel 的任课表可回填（source='excel'）
- 分析层读不到快照时可惰性生成（source='backfill'，标注为推测）

调用入口：
    snapshot_exam(exam, source='import')      # 写快照（幂等，默认不覆盖既有）
    links_of(exam_id, auto_backfill=True)     # 读快照（缺则惰性生成并落库）
    teacher_map(exam_id)                      # {(class_name, subject): (name, user_id)}
"""
from __future__ import annotations

from app.extensions import db
from app.models.grades import ExamTeacherLink, TeacherSubjectLink

# v1.18.9.1 班主任也归入同一张快照表：它和任课教师一样是“本场考试该班的老师”，
# 区别仅在于任课老师看单科成绩、班主任看总分。用伪科目名占位，不污染 SUBJECTS。
HEAD_SUBJECT = '班主任'

# 快照来源说明
SOURCE_DESC = {
    'import': '导入时快照',
    'excel': '旧成绩表提取',
    'backfill': '用当前映射回填（推测）',
    'manual': '人工修订',
}


def _current_headteacher_rows(grade):
    """从当前 UserClassLink 取该年级各班班主任（作为快照源）

    ⚠ 同班常有主/副两位班主任，而表上 `UNIQUE(exam_id, class_name, subject)`，
    所以合并为一行（姓名顿号连接，与 headteacher_map 输出口径一致）；
    user_id 置空（多师无法对应单个账号，班主任也不参与分数聚合）。
    """
    from app.models import User, UserClassLink
    links = UserClassLink.query.filter_by(grade=grade).all()
    uids = {lk.user_id for lk in links}
    names = {u.id: u.real_name for u in
             User.query.filter(User.id.in_(uids), User.is_active.is_(True)).all()} if uids else {}
    per_cls = {}
    for lk in links:
        nm = names.get(lk.user_id)
        if nm and lk.class_name:
            bag = per_cls.setdefault(lk.class_name, [])
            if nm not in bag:
                bag.append(nm)
    return [{'class_name': c, 'subject': HEAD_SUBJECT,
             'teacher_name': '、'.join(v), 'user_id': None}
            for c, v in per_cls.items()]


def snapshot_exam(exam, source='import', rows=None, replace=False):
    """把任课安排写入某场考试的快照。

    rows=None  ：从当前 TeacherSubjectLink（该年级 active）复制
    rows=[...] ：显式给定 [{class_name, subject, teacher_name, user_id?}]（Excel 回填用）
    replace=True：先清空本场既有快照再写（人工修订/Excel 覆盖用）

    返回写入行数。幂等：已存在且 replace=False 时不重复写。
    """
    if exam is None:
        return 0
    if replace:
        ExamTeacherLink.query.filter_by(exam_id=exam.id).delete()
        db.session.flush()

    if rows is None:
        rows = [
            {'class_name': l.class_name, 'subject': l.subject,
             'teacher_name': None, 'user_id': l.user_id}
            for l in TeacherSubjectLink.query.filter_by(
                grade=exam.grade, active=True).all()
        ]
        # v1.18.9.1 班主任一并快照（以前只存了任课老师，历史考试的班主任会跟到现在）
        rows += _current_headteacher_rows(exam.grade)

    # 姓名补全（无 user_id 时留空，由调用方给 teacher_name）
    need_uid = {r['user_id'] for r in rows if r.get('user_id')}
    name_of = {}
    if need_uid:
        from app.models import User
        name_of = {u.id: u.real_name for u in
                   User.query.filter(User.id.in_(need_uid)).all()}

    existing = {(r.class_name, r.subject) for r in
                ExamTeacherLink.query.filter_by(exam_id=exam.id).all()}
    added = 0
    for r in rows:
        cls = (r.get('class_name') or '').strip()
        subj = (r.get('subject') or '').strip()
        if not cls or not subj:
            continue
        if (cls, subj) in existing:
            continue
        uid = r.get('user_id')
        name = (r.get('teacher_name') or name_of.get(uid) or '').strip() or None
        existing.add((cls, subj))
        db.session.add(ExamTeacherLink(
            exam_id=exam.id, grade=getattr(exam, 'grade', '') or '',
            class_name=cls, subject=subj,
            user_id=uid, teacher_name=name, source=source))
        added += 1
    return added


def links_of(exam_id, grade='', auto_backfill=True):
    """读某场考试的任课快照；为空且 auto_backfill → 用当前映射生成（source=backfill）。

    返回 ExamTeacherLink 列表（可能为空）。惰性生成会自行 commit。
    """
    rows = ExamTeacherLink.query.filter_by(exam_id=exam_id).all()
    if rows or not auto_backfill:
        return rows
    from app.models.grades import Exam
    exam = Exam.query.get(exam_id)
    if exam is None:
        return []
    n = snapshot_exam(exam, source='backfill')
    if n:
        db.session.commit()
        rows = ExamTeacherLink.query.filter_by(exam_id=exam_id).all()
    return rows


def teacher_map(exam_id, grade='', auto_backfill=True):
    """{(class_name, subject): {'name':…, 'user_id':…, 'source':…}}"""
    out = {}
    for r in links_of(exam_id, grade=grade, auto_backfill=auto_backfill):
        out[(r.class_name, r.subject)] = {
            'name': r.teacher_name, 'user_id': r.user_id, 'source': r.source}
    return out


def teacher_user_map(exam_id, grade=''):
    """{(class_name, subject): user_id}：供"教师维度报表按 user_id 聚合"使用

    只包含有 user_id 的快照（历史教师未建号的不参与聚合，但姓名仍可在展示层出现）。
    """
    return {(r.class_name, r.subject): r.user_id
            for r in links_of(exam_id, grade=grade) if r.user_id}


def name_map_of(exam_id, grade=''):
    """{(class_name, subject): 教师姓名}；teacher_name 为空时用 user_id 反查 users"""
    rows = links_of(exam_id, grade=grade)
    need = {r.user_id for r in rows if not r.teacher_name and r.user_id}
    name_of = {}
    if need:
        from app.models import User
        name_of = {u.id: u.real_name for u in
                   User.query.filter(User.id.in_(need)).all()}
    return {(r.class_name, r.subject): (r.teacher_name or name_of.get(r.user_id, ''))
            for r in rows}


def headteacher_map_of(exam_id, grade=''):
    """{(grade, class_name): '班主任A、班主任B'}：取**本场考试当时**的班主任

    快照优先；快照缺失（旧库未回填）则回落当前 UserClassLink，保证不空白。
    返回的键形状与 report_service.headteacher_map(grade) 一致，可直接替用。
    """
    out = {}
    for r in links_of(exam_id, grade=grade):
        if r.subject == HEAD_SUBJECT and r.teacher_name:
            out[(r.grade or grade, r.class_name)] = r.teacher_name
    if out:
        return out
    from app.modules.grades.services import report_service as _rs
    return _rs.headteacher_map(grade)


def has_snapshot(exam_id):
    return ExamTeacherLink.query.filter_by(exam_id=exam_id).count() > 0


def source_of(exam_id):
    """本场快照的整体来源（取第一条）；无快照返回 ''"""
    r = ExamTeacherLink.query.filter_by(exam_id=exam_id).first()
    return (r.source or '') if r else ''
