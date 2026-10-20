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
5. **动态**：最近考试（按更新时间）/ 最近开具的证明 / 最近改分记录 /
   最近**成绩复核**（2026-10-20 用户口径：复核申请与审批进度必须出现在这里）。
   · 改分记录：只对教学管理口径返回（admin、`grades.edit`、`grades.score_edit`）；
   · 证明开具：只对管理类身份返回（管理员 / 校长 / 年级长 / 教务主任 / 教务员），
     普通教师不必知道；
   · 成绩复核：按 `score_guard.ScoreScope` 收敛 —— **别人发起的也要显示**
     （同年级同科即可见），不再只推本人操作；可审批者给审批页链接，
     其他人只有自己的单子可点（别人的单子只作信息展示）。
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import func

from app.extensions import db
from app.models.grades import (TOTAL_SUBJECT, Certificate, Exam, ExamScore,
                               ScoreChangeLog, ScoreReviewRequest)
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

# 成绩复核（score_review_requests）：状态文案与时间线配色
REVIEW_STATUS_LABEL = {'pending': '待审核', 'approved': '已批准',
                       'rejected': '已驳回', 'withdrawn': '已撤回'}
REVIEW_TONE = {'pending': 'danger', 'approved': 'success',
               'rejected': 'secondary', 'withdrawn': 'secondary'}
# 复核入口（与 score_review.py 的 bp.route 对应；不能用 url_for —— 本服务
# 也会在无请求上下文时被调用，如脚本/CLI）
REVIEW_MANAGE_URL = '/grades/reviews/manage'
REVIEW_MY_URL = '/grades/reviews'
# 复核单要按 ScoreScope 逐条过滤（待审的可能被过滤掉），多扫几倍再截断
REVIEW_SCAN_FACTOR = 10


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


def _timed_exam_count(grades):
    """限时练场次数（2026-10-20：限时练改独立表，与 exams 的 exam_type
    关键词统计分开 —— 否则 KPI 上「限时练」会恒为 0）。"""
    from app.models.timed import TimedExam
    if not grades:
        return 0
    return TimedExam.query.filter(TimedExam.grade.in_(grades)).count()


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

# 2026-10-20 用户口径：证明开具属于教务/管理事务，**普通教师不必知道** ——
# 动态里只对 管理员 / 校长 / 年级长 / 教务主任 / 教务员 开放。
CERT_ACTIVITY_ROLES = {'admin', 'principal', 'grade_leader'}
CERT_ACTIVITY_GROUPS = {'管理员组', '校长', '年级长组', '教务主任', '教务员'}


def _active_group(user):
    pg = (getattr(user, 'active_group', None)
          or getattr(user, 'permission_group', None))
    if pg is None and getattr(user, 'permission_group_id', None):
        try:                                     # 新建/改组后 backref 未刷新
            from app.models.permission_group import PermissionGroup
            pg = PermissionGroup.query.get(user.permission_group_id)
        except Exception:                        # noqa: BLE001
            pg = None
    return pg


def can_see_cert_activity(user):
    """证明开具动态：仅管理类身份可见（比 `can_view_certs` 更严）"""
    if (user.role or '') in CERT_ACTIVITY_ROLES:
        return True
    pg = _active_group(user)
    if pg is None:
        return False
    return ((getattr(pg, 'name', '') or '') in CERT_ACTIVITY_GROUPS
            or (getattr(pg, 'role', '') or '') in ('principal', 'grade_leader'))


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


def can_audit_reviews(user):
    """复核审批权限（与 /grades/reviews/manage 的 perm_required 同口径）"""
    return user.role == 'admin' or user.has_perm('grades.score_edit')


def _review_title(r):
    """时间线标题：成绩复核 · 考试 · 学生 科目"""
    who = ' '.join(p for p in [r.student_name or '', r.subject or ''] if p)
    return '成绩复核 · ' + ' · '.join(p for p in [r.exam_name or '', who] if p)


def _review_meta(r):
    """时间线副标题：申请人 · 增减分 · 状态 （审批人）"""
    pending = r.status == 'pending'
    parts = []
    if r.applicant_name:
        parts.append(f'{r.applicant_name} 申请')
    parts.append(f'{r.delta:+g} 分' if r.delta is not None else '增减分未填')
    parts.append(REVIEW_STATUS_LABEL.get(r.status, r.status or ''))
    if r.reviewer_name and not pending:
        parts.append(f'{r.reviewer_name} 审批')
    return ' · '.join(p for p in parts if p)


def _review_scope(user):
    """复核动态的可见范围：与成绩分析同一份口径（ScoreScope）。

    解析失败一律 fail-closed（返回 None → 不推任何复核动态）。
    """
    from app.modules.grades.services import score_guard
    try:
        return score_guard.resolve(user)
    except Exception as e:                                   # noqa: BLE001
        logger.warning('复核动态范围解析失败，按空集处理: %s', e)
        return None


def _review_visible(sc, r):
    """这条复核单是否落在本人可见范围内（年级班级 + 学科双收口）"""
    grade = (r.grade or '').strip()
    if not grade:
        return False
    class_name = (r.class_name or '').strip()
    if not sc.can_view_class(grade, class_name):
        return False
    return sc.can_view_subject(grade, class_name, (r.subject or '').strip())


def _review_items(user, sc, audit):
    """最近复核单（待审优先，最多 ACTIVITY_PER_SOURCE 条）"""
    if sc is None:
        return []
    q = ScoreReviewRequest.query
    scan = ACTIVITY_PER_SOURCE * REVIEW_SCAN_FACTOR
    pending_rows = (q.filter(ScoreReviewRequest.status == 'pending')
                    .order_by(ScoreReviewRequest.id.desc()).limit(scan).all())
    done_rows = (q.filter(ScoreReviewRequest.status != 'pending')
                 .order_by(ScoreReviewRequest.reviewed_at.desc())
                 .limit(scan).all())
    picked, seen = [], set()
    for r in list(pending_rows) + list(done_rows):
        if len(picked) >= ACTIVITY_PER_SOURCE:
            break
        if r.id in seen or not _review_visible(sc, r):
            continue
        seen.add(r.id)
        picked.append(r)
    items = []
    for r in picked:
        pending = r.status == 'pending'
        when = r.created_at if pending else (r.reviewed_at or r.created_at)
        if audit:
            url = (REVIEW_MANAGE_URL + '?status='
                   + ('pending' if pending else (r.status or 'pending')))
        elif r.applicant_id == user.id:
            url = REVIEW_MY_URL           # 自己的单子：点进去看进度
        else:
            url = None                    # 别人的单子：只展示（我的复核里没有它）
        items.append({
            'type': 'review', 'icon': 'bi-clipboard-check',
            'tone': REVIEW_TONE.get(r.status, 'secondary'),
            'title': _review_title(r),
            'meta': _review_meta(r),
            'time': _s(when, '%m-%d %H:%M'),
            'sort': when or datetime.min,
            'exam_id': r.exam_id,
            'url': url,
        })
    return items


def _activity_source_names(user):
    """动态来源说明（卡片头那行小字，按本人可见性给出）"""
    names = ['考试登记']
    if can_see_cert_activity(user):
        names.append('证明开具')
    if _can_see_changes(user):
        names.append('分数修改')
    names.append('成绩复核')
    return names


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
        'activity_sources': _activity_source_names(user),
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

    # KPI 阶段卡：按关键词包含匹配求和（'期中' 同时命中「期中」与「期中考试」）。
    # 「限时练」例外：它已是独立表，按 TimedExam 计数（2026-10-20）。
    timed_n = _timed_exam_count(grades)
    cards = []
    for name, kw in STAGE_CARDS:
        if name == '限时练':
            cards.append({'name': name, 'exams': timed_n})
            continue
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

    # 当前动态：考试 / 证明 / 改分 / 成绩复核 四源合并，按时间倒序取前 N
    if payload['can_view_certs']:
        payload['kpi']['cert_total'], payload['kpi']['cert_invalid'] = _cert_stats(user)
    activity = _exam_activity_items(exams)
    if can_see_cert_activity(user):
        activity += _cert_activity_items(user)
    if _can_see_changes(user):
        activity += _change_activity_items(exam_ids)
    activity += _review_items(user, _review_scope(user), can_audit_reviews(user))
    # 2026-10-20 用户口径（修正）：成绩仪表盘只放成绩自己的事 —— 调课属于教务，
    # 不放这里（调课入口在教师工作台 / 教务模块）。
    activity.sort(key=lambda it: it['sort'], reverse=True)
    for it in activity:
        it.pop('sort', None)
    payload['activity'] = activity[:ACTIVITY_LIMIT]

    payload['has_data'] = bool(exams)
    return payload
