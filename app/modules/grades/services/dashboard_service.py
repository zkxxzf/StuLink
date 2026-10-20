# StuLink v1.19.47.30 2026-10-13
# 成绩工作台首页仪表盘：概览数据聚合（只读，不写库）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""成绩工作台首页仪表盘的数据聚合（2026-10-13 用户口径）。

口径说明（与模块其它页面保持一致，改动前先看这里）：
1. **数据范围**：所有考试类指标一律先按 `scope.visible_grades(user)` 收敛
   （与「历次考试」同口径，管理员=全部未毕业年级）。可见年级为空 → 全部为 0，
   不泄露任何越界元数据；范围解析异常 likewise fail-closed。
2. **考试次数**：`exams` 表条数，按 `Exam.grade` / `Exam.exam_type` 分组。
   ⚠️ `exam_type` 是**自由文本**：现网既有字典里的「期中」也有历史数据里的
   「期中考试」——所以阶段分类**按真实取值分组展示**，另在 KPI 卡上用「包含
   关键词」求和（限时练 / 期中 / 期末 / 月考），避免出现"看着是 0 其实有数据"。
3. **参与人数**：统一用 `exam_scores` 的「总分」行口径（与历次考试「参考人数」一致），
   跨考试按 `COUNT(DISTINCT student_no)` **去重**（同一学生考多场只算 1 人）。
4. **证明条数**：沿用成绩证明台账的可见口径 —— 管理员看全校，其他用户仅看本人开具的
   （与 `/grades/student-query/cert-records` 一致）。
5. **动态**：最近考试（按更新时间）/ 最近开具的证明 / 最近改分记录。
   其中改分记录只对具备教学管理口径的用户返回（admin、`grades.edit`、
   `grades.score_edit`），避免把改分留痕推到只读身份面前。
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import func

from app.extensions import db
from app.models.grades import (TOTAL_SUBJECT, Certificate, Exam, ExamScore,
                               ScoreChangeLog)
from app.models.timetable import ScheduleSwap
from app.modules.grades.services import scope as scope_service

logger = logging.getLogger(__name__)

OTHER_STAGE = '未标注'
STATUS_LABEL = {'draft': '未导入', 'imported': '已导入', 'dirty': '待重算'}

# KPI 卡：展示名 → 匹配 exam_type 的关键词（自由文本，用包含匹配，多个命中相加）
STAGE_CARDS = (
    ('限时练', '限时练'),
    ('期中考', '期中'),
    ('期末考', '期末'),
    ('月考', '月考'),
)
# 阶段行排序权重：命中越靠前越先展示（未命中的按场次倒序排后面）
STAGE_ORDER_KEYWORDS = ('期末', '期中', '月考', '限时练', '一测', '二测')

RECENT_EXAM_LIMIT = 8
ACTIVITY_LIMIT = 12
ACTIVITY_PER_SOURCE = 6


def _s(dt, fmt='%Y-%m-%d'):
    return dt.strftime(fmt) if dt else ''


def _visible_grades(user):
    """可见年级（fail-closed：解析异常一律空集）"""
    try:
        return [g for g in (scope_service.visible_grades(user) or []) if g]
    except Exception as e:                                   # noqa: BLE001
        logger.warning('仪表盘可见年级解析失败，按空集处理: %s', e)
        return []


# ==================== 参与人数（总分口径，去重） ====================

def _participants_by_exam(exam_ids):
    """{exam_id: 参与人数}（单条聚合，与本模块其它页面同口径）"""
    if not exam_ids:
        return {}
    rows = (db.session.query(ExamScore.exam_id,
                             func.count(func.distinct(ExamScore.student_no)))
            .filter(ExamScore.exam_id.in_(exam_ids),
                    ExamScore.subject == TOTAL_SUBJECT)
            .group_by(ExamScore.exam_id).all())
    return {eid: int(n or 0) for eid, n in rows}


def _participants_by(grades, group_col):
    """按 group_col（Exam.grade / Exam.exam_type）去重统计参与人数"""
    rows = (db.session.query(group_col, func.count(func.distinct(ExamScore.student_no)))
            .select_from(Exam)
            .join(ExamScore, ExamScore.exam_id == Exam.id)
            .filter(Exam.grade.in_(grades),
                    ExamScore.subject == TOTAL_SUBJECT)
            .group_by(group_col).all())
    return {(k or '').strip(): int(n or 0) for k, n in rows}


def _participants_total(grades):
    n = (db.session.query(func.count(func.distinct(ExamScore.student_no)))
         .select_from(Exam)
         .join(ExamScore, ExamScore.exam_id == Exam.id)
         .filter(Exam.grade.in_(grades),
                 ExamScore.subject == TOTAL_SUBJECT)
         .scalar())
    return int(n or 0)


# ==================== 成绩证明 ====================

def can_view_certs(user):
    """证明台账入口权限（与页面 perm_required 一致）"""
    return user.role == 'admin' or user.has_perm('grades.student_query')


def cert_query(user):
    """证明台账可见范围：管理员=全校，其他用户=本人开具"""
    q = Certificate.query
    if user.role != 'admin':
        q = q.filter(Certificate.generated_by == user.id)
    return q


def _cert_stats(user):
    try:
        q = cert_query(user)
        total = q.count()
        invalid = q.filter(Certificate.invalid.is_(True)).count()
    except Exception as e:                                   # noqa: BLE001
        logger.warning('仪表盘证明统计失败，按 0 处理: %s', e)
        return 0, 0
    return total, invalid


# ==================== 动态 ====================

def _can_see_changes(user):
    """改分留痕：只有教学管理口径的身份看得到（只读身份不推）"""
    return (user.role == 'admin' or user.has_perm('grades.edit')
            or user.has_perm('grades.score_edit'))


def _exam_activity_items(exams):
    items = []
    for e in sorted(exams, key=lambda x: (x.updated_at or x.created_at or datetime.min),
                    reverse=True)[:ACTIVITY_PER_SOURCE]:
        items.append({
            'type': 'exam', 'icon': 'bi-journal-plus', 'tone': 'primary',
            'title': f'{e.grade} · {e.name}',
            'meta': ' · '.join(p for p in [e.exam_type or '', e.exam_date and
                                           e.exam_date.strftime('%Y-%m-%d')] if p),
            'time': _s(e.updated_at or e.created_at, '%m-%d %H:%M'),
            'sort': e.updated_at or e.created_at or datetime.min,
            'exam_id': e.id,
        })
    return items


def _cert_activity_items(user):
    rows = (cert_query(user).order_by(Certificate.generated_at.desc())
            .limit(ACTIVITY_PER_SOURCE).all())
    return [{
        'type': 'cert', 'icon': 'bi-patch-check', 'tone': 'success',
        'title': f'开具成绩证明：{c.student_name or ""}（{c.student_no}）',
        'meta': ' · '.join(p for p in [c.grade or '', c.scope_desc or '',
                                       '已作废' if c.invalid else ''] if p),
        'time': _s(c.generated_at, '%m-%d %H:%M'),
        'sort': c.generated_at or datetime.min,
        'exam_id': None,
    } for c in rows]


def _change_activity_items(exam_ids):
    if not exam_ids:
        return []
    rows = (ScoreChangeLog.query.filter(ScoreChangeLog.exam_id.in_(exam_ids))
            .order_by(ScoreChangeLog.operated_at.desc())
            .limit(ACTIVITY_PER_SOURCE).all())
    items = []
    for r in rows:
        # action=add（2026-10-19 补录）改前也是缺考，与新值并排才看得出来源
        old = ('缺考' if r.action in ('delete', 'add')
               else (f'{r.old_score:g}' if r.old_score is not None else '—'))
        new = f'{r.new_score:g}' if r.new_score is not None else '缺考'
        items.append({
            'type': 'score', 'icon': 'bi-pencil-square', 'tone': 'warning',
            'title': f'{r.exam_name or ""} · {r.student_name or ""} {r.subject}',
            'meta': f'{old} → {new}'
                    + (f'（{r.operator_name}）' if r.operator_name else ''),
            'time': _s(r.operated_at, '%m-%d %H:%M'),
            'sort': r.operated_at or datetime.min,
            'exam_id': r.exam_id,
        })
    return items


SWAP_TONE = {'pending': 'primary', 'approved': 'success',
             'rejected': 'danger', 'executed': 'secondary'}
SWAP_LABEL = {'pending': '待审批', 'approved': '已通过',
              'rejected': '已驳回', 'executed': '已执行'}


def _swap_activity_items(user):
    """调课申请 / 审批动态（2026-10-20 用户口径：动态里要能申请审批）。

    可见性 fail-closed（宁可少推，不泄露他人调课信息）：
    - 本人申请：始终可见（便于跟踪审批进度）；
    - 待审批：仅 `swap_service.can_review` 通过者可见（管理员 / 年级长 /
      课表终审；**不需要"上级"关系**，有审批权限点且在年级范围内即可）；
    - 已审：仅本人或管理员/审批权限者，且按可见年级过滤。
    异常一律返回空，不影响仪表盘其它数据。
    """
    try:
        rows = (ScheduleSwap.query
                .order_by(ScheduleSwap.created_at.desc())
                .limit(ACTIVITY_PER_SOURCE).all())
        from app.modules.academic.services import swap_service
    except Exception as e:  # noqa: BLE001
        logger.warning('仪表盘调课动态查询失败（已跳过）: %s', e)
        return []
    try:
        me = swap_service.reviewer_teacher_uid(user)
    except Exception:  # noqa: BLE001
        me = None
    is_admin = getattr(user, 'role', None) == 'admin'
    has_review = (is_admin or user.has_perm('academic.swap_approve')
                  or user.has_perm('academic.timetable'))
    visible = swap_service.review_visible_grades(user)
    items = []
    for sw in rows:
        mine = bool(me and sw.applicant_uid == me)
        if sw.status == 'pending':
            if not (mine or swap_service.can_review(sw, user)):
                continue
        else:
            if not (mine or has_review):
                continue
            grade = swap_service.swap_grade(sw)
            if visible is not None and grade and grade not in visible:
                continue
        grade = swap_service.swap_grade(sw) or ''
        meta = ' · '.join(p for p in [
            SWAP_LABEL.get(sw.status, sw.status or ''),
            sw.applicant_name or '',
            _s(sw.swap_date),
        ] if p)
        items.append({
            'type': 'swap', 'icon': 'bi-arrow-left-right',
            'tone': SWAP_TONE.get(sw.status, 'secondary'),
            'title': f'调课申请 #{sw.id}' + (f'：{grade}' if grade else ''),
            'meta': meta,
            'time': _s(sw.reviewed_at or sw.created_at, '%m-%d %H:%M'),
            'sort': sw.reviewed_at or sw.created_at or datetime.min,
            'url': f'/academic/swap/{sw.id}',
        })
    return items


# ==================== 主入口 ====================

def build_overview(user):
    """仪表盘总览数据（模板与 /api/dashboard 共用，保证两端同口径）

    返回值全部为 JSON 安全类型（int/str/list/dict），可直接 jsonify 或交给模板。
    """
    now = datetime.now()
    grades = _visible_grades(user)
    payload = {
        'generated_at': now.strftime('%Y-%m-%d %H:%M:%S'),
        'grades': grades,
        'has_data': False,
        'can_edit_exam': user.role == 'admin' or user.has_perm('grades.edit'),
        'can_view_certs': can_view_certs(user),
        'cert_scope': '全校' if user.role == 'admin' else '我开具的',
        'kpi': {
            'total_exams': 0, 'participants': 0,
            'cert_total': 0, 'cert_invalid': 0,
            'latest_date': '', 'stage_cards': [],
        },
        'stage_rows': [],
        'grade_rows': [],
        'recent_exams': [],
        'activity': [],
    }

    if not grades:
        # 可见范围为空：证明数仍按本人口径给出（页面不展示图表），其余一律 0
        if payload['can_view_certs']:
            payload['kpi']['cert_total'], payload['kpi']['cert_invalid'] = _cert_stats(user)
        return payload

    exams = Exam.query.filter(Exam.grade.in_(grades)).all()
    exam_ids = [e.id for e in exams]

    # —— 参与人数（三处：逐场 / 按年级 / 按阶段，全部走聚合查询） ——
    part_by_exam = _participants_by_exam(exam_ids)
    part_by_grade = _participants_by(grades, Exam.grade)
    part_by_stage = _participants_by(grades, Exam.exam_type)
    payload['kpi']['participants'] = _participants_total(grades) if exams else 0

    # —— 考试次数（按年级 / 按阶段） ——
    exam_cnt_by_grade, exam_cnt_by_stage = {}, {}
    for e in exams:
        exam_cnt_by_grade[e.grade] = exam_cnt_by_grade.get(e.grade, 0) + 1
        key = (e.exam_type or '').strip() or OTHER_STAGE
        exam_cnt_by_stage[key] = exam_cnt_by_stage.get(key, 0) + 1

    payload['kpi']['total_exams'] = len(exams)
    payload['kpi']['latest_date'] = _s(max((e.exam_date for e in exams
                                            if e.exam_date), default=None))

    # KPI 阶段卡：按关键词包含匹配求和（'期中' 同时命中「期中」与「期中考试」）
    cards = []
    for name, kw in STAGE_CARDS:
        cards.append({'name': name,
                      'exams': sum(c for k, c in exam_cnt_by_stage.items() if kw in k)})
    payload['kpi']['stage_cards'] = cards

    # 阶段明细：按真实取值展示（未命中关键词的排在后面，按场次倒序）
    def _stage_rank(label):
        for i, kw in enumerate(STAGE_ORDER_KEYWORDS):
            if kw in label:
                return i
        return len(STAGE_ORDER_KEYWORDS)

    stage_rows = [{'name': k, 'exams': c,
                   'participants': part_by_stage.get(k, 0)}
                  for k, c in exam_cnt_by_stage.items()]
    stage_rows.sort(key=lambda r: (_stage_rank(r['name']), -r['exams'], r['name']))
    payload['stage_rows'] = stage_rows

    # 年级行：考试次数 + 参与人数（去重）+ 最近一场
    grade_rows = []
    for g in grades:
        if not exam_cnt_by_grade.get(g):
            continue
        g_exams = [e for e in exams if e.grade == g]
        last = max(g_exams, key=lambda e: (e.exam_date or datetime.min.date(), e.id))
        grade_rows.append({
            'grade': g,
            'exams': exam_cnt_by_grade[g],
            'participants': part_by_grade.get(g, 0),
            'latest': _s(last.exam_date),
            'latest_name': last.name,
        })
    grade_rows.sort(key=lambda r: (-r['exams'], r['grade']))
    payload['grade_rows'] = grade_rows

    # 最近考试（按考试日期倒序，仪表盘右侧列表用）
    payload['recent_exams'] = [{
        'id': e.id, 'name': e.name, 'grade': e.grade,
        'stage': (e.exam_type or '').strip() or OTHER_STAGE,
        'date': _s(e.exam_date),
        'status': e.status or 'draft',
        'status_label': STATUS_LABEL.get(e.status or 'draft', e.status or ''),
        'participants': part_by_exam.get(e.id, 0),
    } for e in sorted(exams, key=lambda x: (x.exam_date or datetime.min.date(), x.id),
                      reverse=True)[:RECENT_EXAM_LIMIT]]

    # 当前动态：考试 / 证明 / 改分 三源合并，按时间倒序取前 N
    if payload['can_view_certs']:
        payload['kpi']['cert_total'], payload['kpi']['cert_invalid'] = _cert_stats(user)
    activity = _exam_activity_items(exams)
    if payload['can_view_certs']:
        activity += _cert_activity_items(user)
    if _can_see_changes(user):
        activity += _change_activity_items(exam_ids)
    # 2026-10-20 用户口径：动态里要能看到「调课申请 / 审批」
    activity += _swap_activity_items(user)
    activity.sort(key=lambda it: it['sort'], reverse=True)
    for it in activity:
        it.pop('sort', None)
    payload['activity'] = activity[:ACTIVITY_LIMIT]

    payload['has_data'] = bool(exams)
    return payload
