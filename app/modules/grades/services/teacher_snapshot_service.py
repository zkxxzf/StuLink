# StuLink v1.19.55.64 2026-10-10
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""考试任课快照服务（v1.19.55.64）

背景：`TeacherSubjectLink` 只存"当前"任课映射。教师一换（尤其重新分班后），
历史考试的教师维度分析会全部归到现在的教师名下，上线率/去差均分排名失真。

本服务把「考试 × 班级 × 科目 → 当时任课教师」定格到 `exam_teacher_links`：
- 成绩导入时自动快照（source='import'，可信）
- 旧成绩 Excel 的任课表可回填（source='excel'）
- 分析层读不到快照时可惰性生成（source='backfill'，标注为推测）

**考务④ 覆盖（2026-10-21 用户口径）**：考务批次④「核对任课教师」是人工核对过的
本场任课安排（`affair_teachers`），用户要求「考务安排跟着老师走 —— ④ 变了，
成绩分析里的老师也要变」。因此读路径（`links_of` / `headteacher_map_of`）在快照之上
叠一层④：④ 里有的 `(班级, 科目)` 用④ 的教师，其余仍用 `exam_teacher_links`。
只覆盖快照里**已有**的键（不往分析里凭空补科目）。

调用入口：
    snapshot_exam(exam, source='import')      # 写快照（幂等，默认不覆盖既有）
    links_of(exam_id, auto_backfill=True)     # 读快照（缺则惰性生成并落库；已叠④）
    teacher_user_map(exam_id)                 # {(class_name, subject): user_id}
    name_map_of(exam_id)                      # {(class_name, subject): 教师姓名}
    headteacher_map_of(exam_id)               # {(grade, class_name): 班主任姓名}
"""
from __future__ import annotations

import logging

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.grades import (AFFAIR_EXCLUDED_SUBJECTS, AFFAIR_HEAD_SUBJECTS,
                               AffairTeacher, ExamAffair, ExamTeacherLink,
                               TeacherSubjectLink, normalize_subject)

logger = logging.getLogger(__name__)

# v1.19.55.64 班主任也归入同一张快照表：它和任课教师一样是“本场考试该班的老师”，
# 区别仅在于任课老师看单科成绩、班主任看总分。用伪科目名占位，不污染 SUBJECTS。
HEAD_SUBJECT = '班主任'

# 2026-10-21 用户口径：班主任不是一个格子里的三个人，而是**三列**（正班 / 副班1 / 副班2）。
# 顺序依据 `UserClassLink.id`（登记先后）：教师安排表导入按 正班→副班1→副班2 写入，
# 天然对位；手工在班型设置里补的人按添加先后排。
HOMEROOM_POSITIONS = ('正班', '副班1', '副班2')

# 快照来源说明
SOURCE_DESC = {
    'import': '导入时快照',
    'excel': '旧成绩表提取',
    'backfill': '用当前映射回填（推测）',
    'manual': '人工修订',
}


def current_homeroom_positions(grade, classes=None):
    """该年级各班班主任，按「正班 / 副班1 / 副班2」**分列**返回。

    返回 ``[{class_name, subject, teacher_name, user_id}]``；``subject`` 是本班
    第几班主任（不是学科）。顺序依据 `UserClassLink.id`（登记先后），与
    教师安排表导入的 正班→副班1→副班2 一致。超出 3 人的不再列出。
    """
    from app.models import User, UserClassLink
    links = (UserClassLink.query.filter_by(grade=grade)
             .order_by(UserClassLink.id).all())
    if not links:
        return []
    uids = {lk.user_id for lk in links}
    # 2026-10-21 审计修复：**不按 is_active 过滤**。班主任账号停用/离职不代表这个位次
    # 不存在；过滤掉会让「副班1」顶到「正班」列（位次错乱）。停用账号仍保留姓名与
    # user_id，页面按姓名显示，重新选人时再绑定新账号。
    users = {u.id: u for u in
             User.query.filter(User.id.in_(uids)).all()}
    per_cls = {}
    for lk in links:
        u = users.get(lk.user_id)
        if not (u and lk.class_name and u.real_name):
            continue
        if classes and lk.class_name not in classes:
            continue
        bag = per_cls.setdefault(lk.class_name, [])
        if all(x['user_id'] != lk.user_id for x in bag):
            bag.append({'user_id': lk.user_id, 'name': u.real_name})
    out = []
    for cls, people in per_cls.items():
        for idx, p in enumerate(people[:len(HOMEROOM_POSITIONS)]):
            out.append({'class_name': cls, 'subject': HOMEROOM_POSITIONS[idx],
                        'teacher_name': p['name'], 'user_id': p['user_id']})
    return out


def current_headteacher_rows(grade):
    """从当前 UserClassLink 取该年级各班班主任（**合并**成一行，快照/展示用）

    ⚠ 表上 `UNIQUE(exam_id, class_name, subject)`，同班多人只能合并为一行
    （姓名顿号连接，与 headteacher_map 输出口径一致）。只有一个班主任时给出
    user_id；多人时置空。考务环节④ 要分列请用 ``current_homeroom_positions``。
    """
    from app.models import User, UserClassLink
    links = UserClassLink.query.filter_by(grade=grade).all()
    uids = {lk.user_id for lk in links}
    users = {u.id: u for u in
             User.query.filter(User.id.in_(uids), User.is_active.is_(True)).all()} if uids else {}
    per_cls = {}
    for lk in links:
        u = users.get(lk.user_id)
        if not (u and lk.class_name and u.real_name):
            continue
        bag = per_cls.setdefault(lk.class_name, [])
        if u.real_name not in bag:
            bag.append(u.real_name)
    out = []
    for cls, names in per_cls.items():
        ids = [lk.user_id for lk in links
               if lk.class_name == cls and lk.user_id in users]
        out.append({'class_name': cls, 'subject': HEAD_SUBJECT,
                    'teacher_name': '、'.join(names),
                    'user_id': ids[0] if len(ids) == 1 else None})
    return out


class _LinkView:
    """`ExamTeacherLink` 的只读替身（考务④ 覆盖用）。

    `links_of()` 的消费方全部按属性读（class_name / subject / user_id /
    teacher_name / grade），所以这里给同名字段即可。刻意**不改 ORM 行**：
    读路径上改 ORM 属性会被 session 自动 flush 静默写回 `exam_teacher_links`。
    """

    __slots__ = ('id', 'exam_id', 'grade', 'class_name', 'subject',
                 'user_id', 'teacher_name', 'source', 'snapshot_at')

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


def _latest_affair(exam_id):
    """该场考试**最新**一个考务批次（与全仓库口径一致：id 倒序取第一个）。"""
    if not exam_id:
        return None
    return (ExamAffair.query.filter_by(exam_id=exam_id)
            .order_by(ExamAffair.id.desc()).first())


def _affair_override(exam_id):
    """考务④ → {(班级, 科目): (user_id, 姓名, 年级)}；无批次 / 无有效行 → {}。

    班主任三列是伪科目，不进任课映射（由 headteacher_map_of 单独处理）；
    考务侧排除的科目（通用技术）也不算；空格子（人已清掉）不覆盖快照。
    """
    affair = _latest_affair(exam_id)
    if affair is None:
        return {}
    out = {}
    for t in AffairTeacher.query.filter_by(affair_id=affair.id).all():
        subj = (t.subject or '').strip()
        if not subj or subj in HOMEROOM_POSITIONS \
                or subj in AFFAIR_HEAD_SUBJECTS \
                or subj in AFFAIR_EXCLUDED_SUBJECTS:
            continue
        subj = normalize_subject(subj)      # 英语/日语 → 外语 等，与快照同口径
        nm = (t.teacher_name or '').strip()
        if not t.user_id and not nm:
            continue
        key = ((t.class_name or '').strip(), subj)
        out[key] = (t.user_id, nm, t.grade or affair.grade or '')
    return out


def _merge_affair(rows, override):
    """把考务④ 的覆盖叠到快照行上（④ 优先；④ 没有的键保留快照）。

    只覆盖、不追加：分析里出现的科目集合仍由快照决定，避免在考务里临时填了
    体育/心理就给分析凭空多出一行空科目。
    """
    if not override or not rows:
        return rows
    merged = []
    for r in rows:
        ov = override.get((r.class_name, r.subject))
        if ov is None:
            merged.append(r)
            continue
        uid, nm, grd = ov
        merged.append(_LinkView(
            id=None, exam_id=r.exam_id, grade=r.grade or grd,
            class_name=r.class_name, subject=r.subject,
            user_id=uid, teacher_name=(nm or r.teacher_name),
            source='affair', snapshot_at=getattr(r, 'snapshot_at', None)))
    return merged


def _affair_headteacher_map(exam_id):
    """考务④ 三列班主任 → {(年级, 班级): 'A、B'}（按列序，空格子跳过）。"""
    affair = _latest_affair(exam_id)
    if affair is None:
        return {}
    # 2026-10-21 合并后统一用「正班/副班1/副班2」；旧库里的「正班主任/…」
    # 也要认（别名归一到同位次），否则④ 已经带入的班主任在分析页读不到
    alias = dict(zip(AFFAIR_HEAD_SUBJECTS, HOMEROOM_POSITIONS))
    all_pos = list(HOMEROOM_POSITIONS) + list(AFFAIR_HEAD_SUBJECTS)
    order = {s: i for i, s in enumerate(HOMEROOM_POSITIONS)}
    rows = (AffairTeacher.query.filter_by(affair_id=affair.id)
            .filter(AffairTeacher.subject.in_(all_pos)).all())
    rows.sort(key=lambda t: ((t.class_name or ''),
                             order.get(alias.get(t.subject, t.subject), 9),
                             t.id or 0))
    per = {}
    for t in rows:
        nm = (t.teacher_name or '').strip()
        if not nm:
            continue
        key = (t.grade or affair.grade or '', (t.class_name or '').strip())
        bag = per.setdefault(key, [])
        if nm not in bag:
            bag.append(nm)
    return {k: '、'.join(v) for k, v in per.items() if v}


def snapshot_exam(exam, source='import', rows=None, replace=False):
    """把任课教师映射（当时状态）写入某场考试的快照。

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
        # v1.19.55.64 班主任一并快照（以前只存了任课老师，历史考试的班主任会跟到现在）
        rows += current_headteacher_rows(exam.grade)

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

    2026-10-16 第九轮审计（PERF-P07）：惰性回填发生在**GET 读路径**上，多个请求同时
    看到空表会各插同一批 `(exam_id, class_name, subject)`，后提交者撞唯一索引
    `uq_exam_cls_subj` → IntegrityError → 500。改为撞索引就回滚重读（对方已写好，
    结果一致），把并发冷启从 500 变成正常返回。
    """
    rows = ExamTeacherLink.query.filter_by(exam_id=exam_id).all()
    if not rows and auto_backfill:
        from app.models.grades import Exam
        exam = Exam.query.get(exam_id)
        if exam is not None:
            n = snapshot_exam(exam, source='backfill')
            if n:
                try:
                    db.session.commit()
                except IntegrityError:
                    db.session.rollback()
                    logger.info('links_of 并发回填撞唯一索引，已回滚重读（exam_id=%s）',
                                exam_id)
                rows = ExamTeacherLink.query.filter_by(exam_id=exam_id).all()
    # 2026-10-21：考务④（人工核对过的本场任课安排）优先于导入快照
    return _merge_affair(rows, _affair_override(exam_id))


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

    优先级（2026-10-21 用户口径）：考务④ 的三列 → 快照 `班主任` 行 →
    当前 UserClassLink（保证不空白）。返回的键形状与
    `report_service.headteacher_map(grade)` 一致，可直接替用。
    """
    out = _affair_headteacher_map(exam_id)
    if out:
        return out
    out = {}
    for r in links_of(exam_id, grade=grade):
        if r.subject == HEAD_SUBJECT and r.teacher_name:
            out[(r.grade or grade, r.class_name)] = r.teacher_name
    if out:
        return out
    from app.modules.grades.services import report_service as _rs
    return _rs.headteacher_map(grade)
