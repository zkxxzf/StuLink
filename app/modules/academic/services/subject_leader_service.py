# StuLink v1.19.48.1 2026-10-14
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""学科组长（2026-10-14 由「备课组长」升级）。

**两类组长**（落库 academic.db.subject_leaders，无法从课表推导）：

- **备课组长**：年级 × 学科（高一 / 高二 / 高三 各学科各一名），年级必填；
- **教研组长**：只按学科（不分年级），年级留空＝全校。

同一 学年 × 学期 × 年级 × 学科 × 类别 重复保存即更新。

**组长必须是本学科教师**：以「任课教师映射」（TeacherSubjectLink）为唯一依据，
课表条目兜底（音体美信通心在任课映射里通常没有，只有课表），教师档案学科最后兜底；
三处都没有任课数据时放行（外聘 / 新教师），有数据但不含该学科则拒绝保存。

**综合组**：体育 / 音乐 / 美术 / 信息技术 / 通用技术 / 心理 在展示与导出里
合并成一个「综合组」，落库仍按具体学科（不同年级可各设备课组长）。

任课安排（原 `/academic/duty`，由课表条目实时聚合）已于 2026-10-10 下线，
教师-学科关系以「任课教师映射」（TeacherSubjectLink）为唯一依据。
"""
import io
import logging
from collections import OrderedDict

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from app.extensions import db
from app.models.academic import (COMPREHENSIVE_GROUP, COMPREHENSIVE_SUBJECTS,
                                 DUTY_TEMPLATES, LEADER_CATEGORIES,
                                 LEADER_CATEGORY_DEFAULT, LEADER_CATEGORY_LESSON,
                                 LEADER_CATEGORY_RESEARCH, RESEARCH_DUTY_TEMPLATES,
                                 SUBJECT_ORDER, SubjectLeader, leader_group_name)
from app.utils.export_helpers import xl_safe

# 2026-10-10 架构审计修复：本文件原有静默 `except Exception: pass`，失败不可观测。
# 统一加 logger 记录 warning（仅留痕，不改变任何降级行为与返回值）。
logger = logging.getLogger(__name__)

# 学科别名字面差异（课表 / 档案里常见写法 → 组长页标准科名）
_SUBJECT_LITERAL_ALIAS = {
    '心理健康': '心理', '心理课': '心理', '心理健康教育': '心理',
    '信息': '信息技术', '微机': '信息技术', '计算机': '信息技术',
    '通用': '通用技术', '通技': '通用技术',
    '体育与健康': '体育', '音': '音乐', '美': '美术', '体': '体育',
}


# ── 公共小工具 ─────────────────────────────────────────────────────────────

def _canon_subject(name):
    """学科名归一：先走成绩模块别名（英语→外语），再走组长页字面别名。"""
    if not name:
        return ''
    s = str(name).replace('\u3000', ' ').strip()
    try:
        from app.models.grades import normalize_subject
        s = normalize_subject(s)
    except Exception as _e:  # noqa: BLE001  grades 模型不可用时按原样比对
        logger.warning('subject_leader_service::_canon_subject 归一失败（按原样）: %s', _e)
    return _SUBJECT_LITERAL_ALIAS.get(s, s)


def _subject_key(subject):
    try:
        return (SUBJECT_ORDER.index(subject), subject)
    except ValueError:
        return (len(SUBJECT_ORDER), subject or '')


def _group_key(group):
    """学科组排序：常规学科按学科顺序，综合组排在最后。"""
    return (len(SUBJECT_ORDER), group) if group == COMPREHENSIVE_GROUP else _subject_key(group)


# ── 教师任课索引（组长候选 + 本学科校验 的唯一数据源）──────────────────────

def teacher_subject_index(with_schedule=True):
    """{teacher_uid: {'name', 'subjects': set, 'grades': set, 'archive_subject'}}

    口径：任课教师映射（TeacherSubjectLink）→ 课表条目兜底 → 教师档案学科。
    任课映射通过 `teachers.user_id` 对齐教务的 teacher_uid。
    """
    from app.models.academic import Teacher

    teachers = Teacher.query.all()
    by_user = {t.user_id: t.teacher_uid for t in teachers if t.user_id}
    idx = {t.teacher_uid: {'name': t.name, 'subjects': set(), 'grades': set(),
                           'archive_subject': _canon_subject(t.subject) if t.subject else ''}
           for t in teachers}

    try:
        from app.models.grades import TeacherSubjectLink
        rows = (TeacherSubjectLink.query
                .filter(TeacherSubjectLink.active.is_(True))
                .with_entities(TeacherSubjectLink.user_id,
                               TeacherSubjectLink.subject,
                               TeacherSubjectLink.grade).all())
        for user_id, subj, grade in rows:
            uid = by_user.get(user_id)
            if not uid:
                continue
            idx[uid]['subjects'].add(_canon_subject(subj))
            if grade:
                idx[uid]['grades'].add(grade)
    except Exception as _e:  # noqa: BLE001  grades 库不可用时只用课表
        logger.warning('subject_leader_service::teacher_subject_index 读任课映射失败: %s', _e)

    if with_schedule:
        try:
            from app.models.timetable import ScheduleEntry
            rows = (ScheduleEntry.query
                    .filter(ScheduleEntry.is_deleted.is_(False),
                            ScheduleEntry.teacher_uid.isnot(None))
                    .with_entities(ScheduleEntry.teacher_uid, ScheduleEntry.subject,
                                   ScheduleEntry.grade).distinct().all())
            for uid, subj, grade in rows:
                if uid not in idx:
                    continue
                idx[uid]['subjects'].add(_canon_subject(subj))
                if grade:
                    idx[uid]['grades'].add(grade)
        except Exception as _e:  # noqa: BLE001  课表库不可用时只用任课映射
            logger.warning('subject_leader_service::teacher_subject_index 读课表失败: %s', _e)
    return idx


def _teacher_subjects_of(uid, idx=None):
    """(任教学科集合, 来源说明)：任课映射 / 课表优先，都没有才用教师档案学科。"""
    info = (idx or teacher_subject_index()).get(uid) or {}
    if info.get('subjects'):
        return set(info['subjects']), '任课映射 / 课表'
    if info.get('archive_subject'):
        return {info['archive_subject']}, '教师档案'
    return set(), ''


def teacher_candidates(subject, keyword=None):
    """某学科的候选组长（只返回本学科教师），供页面下拉 / 自动补全。"""
    std = _canon_subject(subject)
    idx = teacher_subject_index()
    out = []
    for uid, info in idx.items():
        subjects, _src = _teacher_subjects_of(uid, idx)
        if std and std not in subjects:
            continue
        if keyword and keyword not in (info.get('name') or '') and keyword not in uid:
            continue
        out.append({'uid': uid, 'name': info.get('name') or '',
                    'subjects': sorted(subjects)})
    out.sort(key=lambda x: x['name'])
    return out


# ── 学科组长 ───────────────────────────────────────────────────────────────

def leader_years():
    """已有登记的学年列表（倒序），供页面下拉。"""
    rows = db.session.query(SubjectLeader.school_year).distinct().all()
    return sorted({r[0] for r in rows if r[0]}, reverse=True)


def list_leaders(school_year=None, term=None, subject=None, grade=None,
                 keyword=None, category=None):
    q = SubjectLeader.query
    if school_year:
        q = q.filter_by(school_year=school_year)
    if term is not None and term != '':
        q = q.filter_by(term=term)
    if subject:
        if subject == COMPREHENSIVE_GROUP:      # 选「综合组」＝六个学科都算
            q = q.filter(SubjectLeader.subject.in_(COMPREHENSIVE_SUBJECTS))
        else:
            q = q.filter_by(subject=subject)
    if grade:
        q = q.filter_by(grade=grade)
    if category:
        q = q.filter_by(category=category)
    if keyword:
        like = f'%{keyword}%'
        q = q.filter(db.or_(SubjectLeader.leader_name.like(like),
                            SubjectLeader.subject.like(like),
                            SubjectLeader.grade.like(like),
                            SubjectLeader.duty.like(like)))
    items = q.all()
    items.sort(key=lambda x: (_subject_key(x.subject), x.grade or '', x.leader_name or ''))
    return items


def group_leaders(leaders):
    """按学科组分组：[(学科组, [leader, ...]), ...]。

    综合组学科（音体美信通心）合并成一个「综合组」块；组内按学科、年级排序。
    """
    grouped = OrderedDict()
    for ld in leaders:
        grouped.setdefault(leader_group_name(ld.subject), []).append(ld)
    for rows in grouped.values():
        rows.sort(key=lambda x: (_subject_key(x.subject), x.grade or '',
                                 x.sort_order or 0, x.leader_name or ''))
    return [(g, grouped[g]) for g in sorted(grouped, key=_group_key)]


def group_leaders_by_category(leaders, categories=None):
    """按「类别 → 学科组」两级分组：[(类别, [(学科组, [leader...]), ...]), ...]。"""
    cats = list(categories or LEADER_CATEGORIES)
    buckets = OrderedDict((c, []) for c in cats)
    for ld in leaders:
        buckets.setdefault(ld.category or LEADER_CATEGORY_DEFAULT, []).append(ld)
    return [(c, group_leaders(buckets.get(c, []))) for c in buckets]


def _normalize_category(data):
    category = (data.get('category') or '').strip()
    if not category:
        return LEADER_CATEGORY_DEFAULT
    if category not in LEADER_CATEGORIES:
        raise ValueError(f'组长类别只能是：{" / ".join(LEADER_CATEGORIES)}')
    return category


def assert_leader_in_subject(subject, leader_uid=None, leader_name=''):
    """校验组长是本学科教师，并把姓名补成教师编号。

    返回 (leader_uid, 命中教师档案与否)：无档案 / 无任课数据时放行（外聘、新教师）；
    有任课数据但不含该学科 → 抛 ValueError。
    """
    std = _canon_subject(subject)
    idx = teacher_subject_index()
    uid = (leader_uid or '').strip()
    info = idx.get(uid) if uid else None
    if info is None and leader_name:
        for _u, _i in idx.items():
            if _i.get('name') == leader_name.strip():
                uid, info = _u, _i
                break
    if info is None:
        return uid, False
    subjects, src = _teacher_subjects_of(uid, idx)
    if subjects and std and std not in subjects:
        raise ValueError(f'「{leader_name or info.get("name")}」的任教学科'
                         f'（{src}：{"、".join(sorted(subjects))}）不含「{subject}」，'
                         f'{subject}组长必须是本学科教师')
    return uid, True


def save_leader(data, operator_id=None):
    """新增或更新（同 学年×学期×年级×学科×类别 视为同一条）。

    返回 (leader, created, known_teacher)；数据不合法抛 ValueError。
    - 备课组长：年级必填；教研组长：年级强制留空（不分年级）；
    - 两类组长都必须是本学科教师。
    """
    school_year = (data.get('school_year') or '').strip()
    term = (data.get('term') or '').strip()
    subject = (data.get('subject') or '').strip()
    leader_name = (data.get('leader_name') or '').strip()
    category = _normalize_category(data)
    grade = '' if category == LEADER_CATEGORY_RESEARCH else (data.get('grade') or '').strip()

    if not school_year:
        raise ValueError('学年必填（如 2026-2027）')
    if not subject:
        raise ValueError('学科必填')
    if not leader_name:
        raise ValueError('组长姓名必填')
    if category == LEADER_CATEGORY_LESSON and not grade:
        raise ValueError('备课组长必须选年级（高一 / 高二 / 高三）')

    leader_uid, known = assert_leader_in_subject(subject, data.get('leader_uid'), leader_name)

    leader = SubjectLeader.query.filter(
        SubjectLeader.school_year == school_year,
        SubjectLeader.term == term,
        SubjectLeader.grade == grade,
        SubjectLeader.subject == subject,
        db.or_(SubjectLeader.category == category,
               SubjectLeader.category.is_(None)),
    ).first()
    created = leader is None
    if created:
        leader = SubjectLeader(school_year=school_year, term=term, grade=grade,
                               subject=subject, category=category)
        db.session.add(leader)

    leader.category = category
    leader.leader_uid = leader_uid or None
    leader.leader_name = leader_name[:50]
    leader.members = (data.get('members') or '').strip()[:300] or None
    leader.duty = (data.get('duty') or '').strip()[:500] or None
    try:
        leader.sort_order = int(data.get('sort_order') or 0)
    except (TypeError, ValueError):
        leader.sort_order = 0
    leader.updated_by = operator_id
    db.session.commit()
    return leader, created, known


def delete_leader(leader_id):
    leader = db.session.get(SubjectLeader, leader_id)
    if not leader:
        return None
    info = (f'{leader.school_year} {leader.grade or "全校"}{leader.subject}'
            f' {leader.category or LEADER_CATEGORY_DEFAULT} {leader.leader_name}')
    db.session.delete(leader)
    db.session.commit()
    return info


def duty_templates(category=None):
    """职责预设：教研组长用教研模板，备课组长用备课模板。"""
    return RESEARCH_DUTY_TEMPLATES if category == LEADER_CATEGORY_RESEARCH else DUTY_TEMPLATES


def export_leaders_workbook(leaders, school_year=None):
    """学科组长名单导出（按 类别 → 学科组 分组，含职责与组范围）"""
    wb = Workbook()
    ws = wb.active
    ws.title = '学科组长名单'
    tb = Border(left=Side('thin'), right=Side('thin'),
                top=Side('thin'), bottom=Side('thin'))
    center = Alignment(horizontal='center', vertical='center', wrap_text=True)
    left = Alignment(horizontal='left', vertical='center', wrap_text=True)

    ws.cell(row=1, column=1,
            value=xl_safe(f'{school_year or ""} 学科组长名单'.strip())).font = Font(bold=True, size=13)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=9)
    headers = ['类别', '学科组', '学科', '年级', '学期', '组长', '教师编号',
               '组范围 / 成员', '主要职责']
    for ci, h in enumerate(headers, 1):
        c = ws.cell(row=2, column=ci, value=h)
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
        c.alignment = center
        c.border = tb
    for ci, w in enumerate([12, 12, 12, 10, 12, 12, 14, 30, 70], 1):
        ws.column_dimensions[get_column_letter(ci)].width = w

    ri = 3
    for category, groups in group_leaders_by_category(leaders):
        for group, rows in groups:
            for ld in rows:
                vals = [category, group, ld.subject, ld.grade or '全校',
                        ld.term or '整学年', ld.leader_name, ld.leader_uid or '',
                        ld.members or '', ld.duty or '']
                for ci, v in enumerate(vals, 1):
                    c = ws.cell(row=ri, column=ci, value=xl_safe(v))
                    c.border = tb
                    c.alignment = center if ci <= 7 else left
                ri += 1
    if ri == 3:
        ws.cell(row=3, column=1, value='暂无记录')
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
