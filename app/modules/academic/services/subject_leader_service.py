# StuLink v1.19.48.1 2026-10-14
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""学科组长（2026-10-14 由「备课组长」升级）。

页面 `/academic/leaders` 只有一张**矩阵表**（与「任课教师映射」同形）：
行＝学科（9 个文化科 + 综合组 6 科），列＝高一 / 高二 / 高三 / 教研组（不分年级），
格子里选本学科教师。落库 academic.db.subject_leaders（无法从课表推导）。

**两类组长**：

- **备课组长**：年级 × 学科（高一 / 高二 / 高三 各学科各一名），年级必填；
- **教研组长**：只按学科（不分年级），年级留空＝全校。

同一 学年 × 学期 × 年级 × 学科 × 类别 重复保存即更新。

**组长必须是本学科教师**：以「任课教师映射」（TeacherSubjectLink）为唯一依据，
课表条目兜底（音体美信通心在任课映射里通常没有，只有课表），教师档案学科最后兜底；
三处都没有任课数据时放行（外聘 / 新教师），有数据但不含该学科则拒绝保存。

**综合组**：体育 / 音乐 / 美术 / 信息技术 / 通用技术 / 心理 在展示与导出里
合并成一个「综合组」，落库仍按具体学科（不同年级可各设备课组长）。

2026-10-14：原「职责 / 组范围」（members / duty / sort_order 三列 + 编辑弹窗 + 明细页签）
**整功能删除** —— 用户确认从未使用过，页面只留矩阵维护。

任课安排（原 `/academic/duty`，由课表条目实时聚合）已于 2026-10-10 下线，
教师-学科关系以「任课教师映射」（TeacherSubjectLink）为唯一依据。
"""
import io
import logging

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from app.extensions import db
from app.models.academic import (COMPREHENSIVE_GROUP, COMPREHENSIVE_SUBJECTS,
                                 LEADER_CATEGORIES, LEADER_CATEGORY_DEFAULT,
                                 LEADER_CATEGORY_LESSON, LEADER_CATEGORY_RESEARCH,
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


def _category_key(category):
    try:
        return (LEADER_CATEGORIES.index(category or LEADER_CATEGORY_DEFAULT),)
    except ValueError:
        return (len(LEADER_CATEGORIES), category or '')


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
                    .with_entities(ScheduleEntry.teacher_uid, ScheduleEntry.teacher_name,
                                   ScheduleEntry.subject, ScheduleEntry.grade)
                    .distinct().all())
            for uid, tname, subj, grade in rows:
                if uid not in idx:
                    # 2026-10-14：课表里有、教师名单里没有的老师（音体美信通心常见）
                    # 也纳入索引 —— 否则这些学科没人可选、组长无从维护。
                    idx[uid] = {'name': (tname or '').strip() or uid, 'subjects': set(),
                                'grades': set(), 'archive_subject': '', 'external': True}
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


def teacher_candidates(subject, keyword=None, index=None):
    """某学科的候选组长（只返回本学科教师）。"""
    std = _canon_subject(subject)
    idx = index or teacher_subject_index()
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


# ── 学科组长读取 ───────────────────────────────────────────────────────────

def leader_years():
    """已有登记的学年列表（倒序），供页面下拉。"""
    rows = db.session.query(SubjectLeader.school_year).distinct().all()
    return sorted({r[0] for r in rows if r[0]}, reverse=True)


def leaders_of_term(school_year, term=''):
    """精确取某「学年 × 学期」的登记（term='' 匹配空值与 NULL，＝整学年）。"""
    q = SubjectLeader.query.filter_by(school_year=school_year)
    if term:
        return q.filter(SubjectLeader.term == term).all()
    return q.filter(db.or_(SubjectLeader.term == '', SubjectLeader.term.is_(None))).all()


def _leader_index(leaders):
    """{(标准学科, 年级, 类别): leader}：兼容历史数据里的学科别名（英语→外语、心理健康→心理）。"""
    out = {}
    for ld in leaders:
        key = (_canon_subject(ld.subject), ld.grade or '',
               ld.category or LEADER_CATEGORY_DEFAULT)
        out.setdefault(key, ld)     # 同一格有历史重复行时，保留先出现的一条
    return out


# ── 矩阵视图（页面唯一视图）────────────────────────────────────────────────
# 行＝学科（9 个文化科 + 综合组 6 科），列＝高一 / 高二 / 高三 / 教研组（不分年级）。
# 备课组长＝年级 × 学科；教研组长＝只按学科（列即「教研组」）。格子里选本学科教师。

# 矩阵行（外语＝含英语与小语种，与成绩模块的科目口径一致）
MATRIX_SUBJECTS = (['语文', '数学', '外语', '物理', '化学', '生物', '政治', '历史', '地理']
                   + list(COMPREHENSIVE_SUBJECTS))

# 矩阵列：(键, 表头, 组长类别, 存的年级)
MATRIX_COLUMNS = (
    ('高一', '高一', LEADER_CATEGORY_LESSON, '高一'),
    ('高二', '高二', LEADER_CATEGORY_LESSON, '高二'),
    ('高三', '高三', LEADER_CATEGORY_LESSON, '高三'),
    ('教研组', '教研组（不分年级）', LEADER_CATEGORY_RESEARCH, ''),
)


def _column_of(key):
    for k, label, cat, grade in MATRIX_COLUMNS:
        if k == key:
            return {'key': k, 'label': label, 'category': cat, 'grade': grade}
    return None


def matrix_data(school_year=None, term=None):
    """矩阵视图数据：列 / 行 / 每格当前组长 / 每行候选教师。

    `fallback` 里的学科＝任课数据里查不到该学科教师（如美术、通用技术在课表里没填教师），
    此时候选退化为「全部教师」，避免这些学科完全无法维护组长。
    """
    idx = teacher_subject_index()
    all_teachers = sorted(({'uid': u, 'name': i.get('name') or ''}
                           for u, i in idx.items()), key=lambda x: x['name'])
    candidates, fallback = {}, []
    for subj in MATRIX_SUBJECTS:
        std = _canon_subject(subj)
        rows = []
        for uid, info in idx.items():
            subjects, _src = _teacher_subjects_of(uid, idx)
            if std and std in subjects:
                rows.append({'uid': uid, 'name': info.get('name') or ''})
        if not rows:
            fallback.append(subj)
            rows = list(all_teachers)
        rows.sort(key=lambda x: x['name'])
        candidates[subj] = rows

    leaders = leaders_of_term(school_year, term) if school_year else []
    lookup = _leader_index(leaders)
    cells = {}
    for col_key, _label, col_cat, col_grade in MATRIX_COLUMNS:
        for subj in MATRIX_SUBJECTS:
            ld = lookup.get((_canon_subject(subj), col_grade, col_cat))
            cand_uids = {c['uid'] for c in candidates[subj]}
            cells[f'{col_key}|{subj}'] = (
                {'id': ld.id, 'uid': ld.leader_uid or '', 'name': ld.leader_name,
                 # 历史数据里的组长可能不在候选（换了学科 / 没有编号）→ 页面标出来但保留原值
                 'selectable': (not cand_uids) or (ld.leader_uid in cand_uids)}
                if ld else None)
    return {
        'school_year': school_year or '',
        'term': term or '',
        'columns': [dict(key=k, label=label, category=cat, grade=grade)
                    for k, label, cat, grade in MATRIX_COLUMNS],
        'subjects': list(MATRIX_SUBJECTS),
        'groups': [('文化科目', [s for s in MATRIX_SUBJECTS
                             if s not in COMPREHENSIVE_SUBJECTS]),
                   (COMPREHENSIVE_GROUP, list(COMPREHENSIVE_SUBJECTS))],
        'cells': cells,
        'candidates': candidates,
        'fallback': fallback,
        'count': len(leaders),
    }


def save_matrix(data, operator_id=None):
    """矩阵批量保存：{'school_year','term','cells': {'高一|语文': teacher_uid 或 ''}}

    - 有值＝登记/改派（必须是本学科教师；该学科候选为空时放行任意教师）；
    - 空值＝清空该格（删除这条组长登记）；
    - 未出现在 `cells` 里的格子不动（支持局部保存）。

    返回 (changes, skipped)：changes = {'set','changed','cleared'}，skipped 为被忽略的格子说明。
    """
    school_year = (data.get('school_year') or '').strip()
    term = (data.get('term') or '').strip()
    cells = data.get('cells') or {}
    if not school_year:
        raise ValueError('学年必填（如 2026-2027）')
    if not isinstance(cells, dict):
        raise ValueError('cells 格式不正确')

    idx = teacher_subject_index()
    by_uid = {u: (i.get('name') or '') for u, i in idx.items()}
    candidates = {subj: {c['uid'] for c in teacher_candidates(subj, index=idx)}
                  for subj in MATRIX_SUBJECTS}

    leaders = leaders_of_term(school_year, term)
    lookup = _leader_index(leaders)
    changes = {'set': 0, 'changed': 0, 'cleared': 0}
    skipped = []

    for key, uid in cells.items():
        col_key, _, subject = str(key).partition('|')
        col = _column_of(col_key)
        if not col or subject not in MATRIX_SUBJECTS:
            skipped.append(f'{key}（未知的格子）')
            continue
        uid = str(uid or '').strip()
        lkey = (_canon_subject(subject), col['grade'], col['category'])
        cur = lookup.get(lkey)

        if not uid:                                   # 清空
            if cur is not None:
                db.session.delete(cur)
                lookup.pop(lkey, None)
                changes['cleared'] += 1
            continue

        allowed = candidates.get(subject) or set()
        if allowed and uid not in allowed and uid != ((cur.leader_uid or '') if cur else ''):
            # 该学科无候选（任课数据缺失）→ 放行任意教师；否则必须是本学科教师
            # （原值照旧放行：换了学科的历史记录不因批量保存被静默抹掉）
            skipped.append(f'{col["label"]}{subject}：该教师不是本学科教师')
            continue
        name = by_uid.get(uid, '')
        if not name:
            skipped.append(f'{col["label"]}{subject}：教师编号 {uid} 不在教师名单里')
            continue

        if cur is None:
            db.session.add(SubjectLeader(
                school_year=school_year, term=term, grade=col['grade'],
                subject=subject, category=col['category'],
                leader_uid=uid, leader_name=name, updated_by=operator_id))
            changes['set'] += 1
        elif (cur.leader_uid or '') != uid or cur.leader_name != name:
            cur.leader_uid = uid
            cur.leader_name = name
            cur.updated_by = operator_id
            changes['changed'] += 1
    db.session.commit()
    return changes, skipped


# ── 导出 ───────────────────────────────────────────────────────────────────

def export_leaders_workbook(leaders, school_year=None, term=None):
    """学科组长名单导出（按 类别 → 学科 → 年级 排序）"""
    wb = Workbook()
    ws = wb.active
    ws.title = '学科组长名单'
    tb = Border(left=Side('thin'), right=Side('thin'),
                top=Side('thin'), bottom=Side('thin'))
    center = Alignment(horizontal='center', vertical='center', wrap_text=True)

    title = f'{school_year or ""} {term or "整学年"} 学科组长名单'.strip()
    ws.cell(row=1, column=1, value=xl_safe(title)).font = Font(bold=True, size=13)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=7)
    headers = ['类别', '学科组', '学科', '年级', '学期', '组长', '教师编号']
    for ci, h in enumerate(headers, 1):
        c = ws.cell(row=2, column=ci, value=h)
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
        c.alignment = center
        c.border = tb
    for ci, w in enumerate([12, 12, 12, 10, 12, 12, 14], 1):
        ws.column_dimensions[get_column_letter(ci)].width = w

    rows = sorted(leaders, key=lambda x: (_category_key(x.category or LEADER_CATEGORY_DEFAULT),
                                          _subject_key(x.subject), x.grade or '',
                                          x.leader_name or ''))
    ri = 3
    for ld in rows:
        vals = [ld.category or LEADER_CATEGORY_DEFAULT, leader_group_name(ld.subject),
                ld.subject, ld.grade or '全校', ld.term or '整学年',
                ld.leader_name, ld.leader_uid or '']
        for ci, v in enumerate(vals, 1):
            c = ws.cell(row=ri, column=ci, value=xl_safe(v))
            c.border = tb
            c.alignment = center
        ri += 1
    if ri == 3:
        ws.cell(row=3, column=1, value='暂无记录')
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
