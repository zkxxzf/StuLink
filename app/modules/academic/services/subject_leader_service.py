# StuLink v1.19.48.1 2026-10-14
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""学科组长（2026-10-14 由「备课组长」升级）。

页面 `/academic/leaders` 只有一张**矩阵表**（与「任课教师映射」同形）：
行＝学科（9 个文化科 + 综合组），列＝高一 / 高二 / 高三 / 教研组（不分年级），
格子里选本学科教师。落库 academic.db.subject_leaders（无法从课表推导）。

**三类组长**：

- **备课组长**：年级 × 学科（高一 / 高二 / 高三 各学科各一名），年级必填；
- **教研组长**：只按学科（不分年级），年级留空＝全校；
- **综合组组长**（2026-10-14 用户口径二次修订）：**整个综合组只设 1 名** ——
  体育 / 音乐 / 美术 / 信息技术 / 通用技术 / 心理 六科**合用一位组长**，
  不分年级、不分学科；矩阵里综合组只渲染一行一格，落库为
  `subject='综合组'` / `grade=''` / `category='综合组组长'` 的一条记录。

同一 学年 × 学期 × 年级 × 学科 × 类别 重复保存即更新。

**组长必须是本学科教师**：以「任课教师映射」（TeacherSubjectLink）为唯一依据，
课表条目兜底（音体美信通心在任课映射里通常没有，只有课表），教师档案学科最后兜底；
三处都没有任课数据时放行（外聘 / 新教师），有数据但不含该学科则拒绝保存。
综合组的候选＝**六科教师并集**。

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
from app.models.academic import (COMPREHENSIVE_ALIAS, COMPREHENSIVE_GROUP,
                                 COMPREHENSIVE_SUBJECTS, LEADER_CATEGORIES,
                                 LEADER_CATEGORY_COMPREHENSIVE, LEADER_CATEGORY_DEFAULT,
                                 LEADER_CATEGORY_LESSON, LEADER_CATEGORY_RESEARCH,
                                 SUBJECT_ORDER, SubjectLeader, SubjectLeaderPlan,
                                 leader_group_name)
from app.utils.export_helpers import xl_safe

# 2026-10-10 架构审计修复：本文件原有静默 `except Exception: pass`，失败不可观测。
# 统一加 logger 记录 warning（仅留痕，不改变任何降级行为与返回值）。
logger = logging.getLogger(__name__)

# 学科别名字面差异（课表 / 档案里常见写法 → 组长页标准科名）。
# 2026-10-15：改成引用模型层唯一真值（`academic.COMPREHENSIVE_ALIAS`），
# 任课映射导入（grades.services.teacher_import）用同一份，避免两处漂移。
_SUBJECT_LITERAL_ALIAS = COMPREHENSIVE_ALIAS


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


def _candidate_subject_set(subject):
    """候选匹配用的学科集合：综合组＝六科并集（整组一位组长，教这六科的都能当）。"""
    if (subject or '').strip() == COMPREHENSIVE_GROUP:
        return {_canon_subject(s) for s in COMPREHENSIVE_SUBJECTS}
    std = _canon_subject(subject)
    return {std} if std else set()


def teacher_candidates(subject, keyword=None, index=None):
    """某学科的候选组长（只返回本学科教师；综合组＝体育/音乐/美术/信息技术/通用技术/心理 并集）。"""
    wanted = _candidate_subject_set(subject)
    idx = index or teacher_subject_index()
    out = []
    for uid, info in idx.items():
        subjects, _src = _teacher_subjects_of(uid, idx)
        if wanted and not (wanted & subjects):
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
MATRIX_SUBJECTS = ['语文', '数学', '外语', '物理', '化学', '生物', '政治', '历史', '地理']

# 2026-10-14 用户口径（二次修订）：**整个综合组只设 1 名组长**（六科共用一位，不分年级、
# 不分学科）—— 综合组不再占 6 行，矩阵里只渲染"综合组"一行一格。
MATRIX_GROUP_SUBJECTS = [COMPREHENSIVE_GROUP]
# 可维护的学科键全集（候选 / 保存校验都以此为准）＝文化科 + 综合组
MATRIX_ALL_SUBJECTS = MATRIX_SUBJECTS + MATRIX_GROUP_SUBJECTS
# 综合组那一格的键：列键与学科都是「综合组」（不出现在列头里）
COMPREHENSIVE_CELL_KEY = f'{COMPREHENSIVE_GROUP}|{COMPREHENSIVE_GROUP}'

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
    if key == COMPREHENSIVE_GROUP:            # 综合组整组一格（不分年级、不分学科）
        return {'key': COMPREHENSIVE_GROUP, 'label': COMPREHENSIVE_GROUP,
                'category': LEADER_CATEGORY_COMPREHENSIVE, 'grade': ''}
    return None


def _cell_of(ld, cand_uids):
    """格子的当前值（None＝未设置）。历史组长可能不在候选（换学科 / 没编号）→ 标出来但保留原值。"""
    if ld is None:
        return None
    return {'id': ld.id, 'uid': ld.leader_uid or '', 'name': ld.leader_name,
            'selectable': (not cand_uids) or (ld.leader_uid in cand_uids)}


def matrix_data(school_year=None, term=None):
    """矩阵视图数据：列 / 行 / 每格当前组长 / 每行候选教师。

    `fallback` 里的学科＝任课数据里查不到该学科教师（如美术、通用技术在课表里没填教师），
    此时候选退化为「全部教师」，避免这些学科完全无法维护组长。
    """
    idx = teacher_subject_index()
    all_teachers = sorted(({'uid': u, 'name': i.get('name') or ''}
                           for u, i in idx.items()), key=lambda x: x['name'])
    candidates, fallback = {}, []
    for subj in MATRIX_ALL_SUBJECTS:
        rows = [{'uid': c['uid'], 'name': c['name']}
                for c in teacher_candidates(subj, index=idx)]
        if not rows:
            fallback.append(subj)
            rows = list(all_teachers)
        candidates[subj] = rows

    leaders = leaders_of_term(school_year, term) if school_year else []
    lookup = _leader_index(leaders)
    cells = {}
    for col_key, _label, col_cat, col_grade in MATRIX_COLUMNS:
        for subj in MATRIX_SUBJECTS:
            ld = lookup.get((_canon_subject(subj), col_grade, col_cat))
            cells[f'{col_key}|{subj}'] = _cell_of(ld, {c['uid'] for c in candidates[subj]})
    # 综合组：整组一格（不分年级、不分学科）
    cells[COMPREHENSIVE_CELL_KEY] = _cell_of(
        lookup.get((COMPREHENSIVE_GROUP, '', LEADER_CATEGORY_COMPREHENSIVE)),
        {c['uid'] for c in candidates[COMPREHENSIVE_GROUP]})
    return {
        'school_year': school_year or '',
        'term': term or '',
        'columns': [dict(key=k, label=label, category=cat, grade=grade)
                    for k, label, cat, grade in MATRIX_COLUMNS],
        'subjects': list(MATRIX_SUBJECTS),
        'groups': [('文化科目', list(MATRIX_SUBJECTS)),
                   # 综合组整组一行（模板对该组特殊渲染：一格搞定）
                   (COMPREHENSIVE_GROUP, list(MATRIX_GROUP_SUBJECTS))],
        'group_key': COMPREHENSIVE_CELL_KEY,
        'group_subjects': list(COMPREHENSIVE_SUBJECTS),
        'cells': cells,
        'candidates': candidates,
        'fallback': fallback,
        'count': len(leaders),
    }


def save_matrix(data, operator_id=None):
    """矩阵批量保存：{'school_year','term','cells': {'高一|语文': teacher_uid 或 '',
    '综合组|综合组': teacher_uid 或 ''}}

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
                  for subj in MATRIX_ALL_SUBJECTS}

    leaders = leaders_of_term(school_year, term)
    lookup = _leader_index(leaders)
    changes = {'set': 0, 'changed': 0, 'cleared': 0}
    skipped = []

    for key, uid in cells.items():
        col_key, _, subject = str(key).partition('|')
        col = _column_of(col_key)
        if not col or subject not in MATRIX_ALL_SUBJECTS:
            skipped.append(f'{key}（未知的格子）')
            continue
        # 综合组整组只有「综合组|综合组」一个格子：既不许按年级（高一|综合组），
        # 也不许按具体学科（教研组|体育）另开格子
        if (subject == COMPREHENSIVE_GROUP or col_key == COMPREHENSIVE_GROUP) \
                and key != COMPREHENSIVE_CELL_KEY:
            skipped.append(f'{key}（综合组整组只有「{COMPREHENSIVE_CELL_KEY}」一个格子）')
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

        # 提示文案里的位置：综合组整组一格，不重复写「综合组综合组」
        _where = COMPREHENSIVE_GROUP if subject == COMPREHENSIVE_GROUP else f'{col["label"]}{subject}'
        allowed = candidates.get(subject) or set()
        if allowed and uid not in allowed and uid != ((cur.leader_uid or '') if cur else ''):
            # 该学科无候选（任课数据缺失）→ 放行任意教师；否则必须是本学科教师
            # （综合组＝六科并集；原值照旧放行：换了学科的历史记录不因批量保存被静默抹掉）
            skipped.append(f'{_where}：该教师不是本学科教师')
            continue
        name = by_uid.get(uid, '')
        if not name:
            skipped.append(f'{_where}：教师编号 {uid} 不在教师名单里')
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


# ── 方案（2026-10-14：用户要求「做成方案，每学期会变，要有记录、可新建 / 使用」）──
# 方案 ＝ 一个「学年 × 学期」的组长登记（与 subject_leaders 的唯一键口径一致），
# 一个学期只能有一套；「使用」＝把它设为当前学期方案（页面默认打开），
# 登记数据本身仍按 (学年, 学期) 并存，课表徽章按课表自己的学期回查不受影响。

def current_plan():
    """当前学期方案（无则 None）。"""
    return (SubjectLeaderPlan.query.filter_by(is_current=True)
            .order_by(SubjectLeaderPlan.id).first())


def _plan_creator_names(ids):
    ids = {i for i in ids if i}
    if not ids:
        return {}
    from app.models import User
    return {u.id: (u.real_name or u.username or '')
            for u in User.query.filter(User.id.in_(ids)).all()}


def leader_count(school_year, term=''):
    """某「学年 × 学期」的组长登记条数（与 leaders_of_term 同一口径）。"""
    if not school_year:
        return 0
    q = SubjectLeader.query.filter_by(school_year=school_year)
    if term:
        return q.filter(SubjectLeader.term == term).count()
    return q.filter(db.or_(SubjectLeader.term == '', SubjectLeader.term.is_(None))).count()


def list_plans():
    """方案清单（当前在前，其余按学年学期倒序）：含登记条数 / 创建人。"""
    plans = (SubjectLeaderPlan.query
             .order_by(SubjectLeaderPlan.is_current.desc(),
                       SubjectLeaderPlan.school_year.desc(),
                       SubjectLeaderPlan.term.desc(),
                       SubjectLeaderPlan.id.desc()).all())
    names = _plan_creator_names({p.created_by for p in plans})
    items = []
    for p in plans:
        d = p.to_dict()
        d['count'] = leader_count(p.school_year, p.term or '')
        d['creator'] = names.get(p.created_by, '')
        d['scope'] = ''
        items.append(d)
    return items


def ensure_plan(school_year, term='', operator_id=None):
    """某学年学期没有方案记录时自动登记一个（进入页面 / 保存时调用，幂等）。"""
    school_year = (school_year or '').strip()
    if not school_year:
        return None
    term = (term or '').strip()
    plan = SubjectLeaderPlan.query.filter_by(school_year=school_year, term=term).first()
    if plan is not None:
        return plan
    plan = SubjectLeaderPlan(name=f'{school_year}学年 {term or "整学年"}',
                             school_year=school_year, term=term,
                             note='自动登记（进入或保存该学期时创建）',
                             is_current=False, created_by=operator_id)
    db.session.add(plan)
    db.session.commit()
    return plan


def create_plan(name='', school_year='', term='', note='', copy_from='current',
                source_year=None, source_term=None, use_now=True, operator_id=None):
    """新建方案。

    :param copy_from: 'current'=复制当前查看学期的登记（默认）/ 'blank'=空白 /
                      'plan:<id>'=复制某历史方案的登记
    :param use_now: 创建后立即设为当前学期方案（默认 True）
    """
    school_year = (school_year or '').strip()
    term = (term or '').strip()
    if not school_year:
        raise ValueError('学年必填（如 2026-2027）')
    if SubjectLeaderPlan.query.filter_by(school_year=school_year, term=term).first():
        raise ValueError(f'「{school_year}学年 {term or "整学年"}」已有方案，'
                         f'请直接在历史记录里「使用」它')
    name = (name or '').strip() or f'{school_year}学年 {term or "整学年"}'
    if SubjectLeaderPlan.query.filter_by(name=name).first():
        raise ValueError(f'方案「{name}」已存在，请换个名称')
    plan = SubjectLeaderPlan(name=name, school_year=school_year, term=term,
                             note=(note or '').strip(), is_current=False,
                             created_by=operator_id)
    db.session.add(plan)

    source = (copy_from or 'current').strip()
    src_year, src_term = None, ''
    if source.startswith('plan:'):
        try:
            src_id = int(source.split(':', 1)[1])
        except (ValueError, TypeError):
            raise ValueError('复制来源不正确')
        src_plan = SubjectLeaderPlan.query.get(src_id)
        if src_plan is None:
            raise ValueError('复制的来源方案不存在')
        src_year, src_term = src_plan.school_year, src_plan.term or ''
    elif source == 'blank':
        src_year = None
    else:                                   # current（默认）＝当前页面查看的学期
        src_year = (source_year or '').strip() or None
        src_term = (source_term or '').strip()
    if src_year:
        for ld in leaders_of_term(src_year, src_term):
            db.session.add(SubjectLeader(
                school_year=school_year, term=term, grade=ld.grade or '',
                subject=ld.subject, category=ld.category or LEADER_CATEGORY_DEFAULT,
                leader_uid=ld.leader_uid, leader_name=ld.leader_name,
                updated_by=operator_id))
    db.session.commit()
    if use_now:
        use_plan(plan.id, operator_id=operator_id)
    return plan


def use_plan(plan_id, operator_id=None):
    """使用某方案：设为当前学期方案（is_current 唯一）。"""
    plan = SubjectLeaderPlan.query.get(int(plan_id))
    if plan is None:
        raise ValueError('方案不存在（可能已被删除）')
    for p in SubjectLeaderPlan.query.filter_by(is_current=True).all():
        p.is_current = False
    plan.is_current = True
    db.session.commit()
    return plan


def rename_plan(plan_id, name='', note=None, operator_id=None):
    """重命名 / 改说明（学年学期是课表徽章回查的坐标，不支持改）。"""
    plan = SubjectLeaderPlan.query.get(int(plan_id))
    if plan is None:
        raise ValueError('方案不存在（可能已被删除）')
    name = (name or '').strip()
    if not name:
        raise ValueError('方案名称不能为空')
    dup = (SubjectLeaderPlan.query
           .filter(SubjectLeaderPlan.name == name, SubjectLeaderPlan.id != plan.id)
           .first())
    if dup:
        raise ValueError(f'方案「{name}」已存在，请换个名称')
    plan.name = name
    if note is not None:
        plan.note = (note or '').strip()
    db.session.commit()
    return plan


def delete_plan(plan_id, operator_id=None):
    """删除方案：连同该「学年 × 学期」的组长登记一起删（返回 (plan, 删除条数)）。"""
    plan = SubjectLeaderPlan.query.get(int(plan_id))
    if plan is None:
        raise ValueError('方案不存在（可能已被删除）')
    q = SubjectLeader.query.filter_by(school_year=plan.school_year)
    if plan.term:
        q = q.filter(SubjectLeader.term == plan.term)
    else:
        q = q.filter(db.or_(SubjectLeader.term == '', SubjectLeader.term.is_(None)))
    removed = q.delete(synchronize_session=False)
    db.session.delete(plan)
    db.session.commit()
    return plan, removed


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
        # 综合组整组一位（不分年级）→ 年级列写「不分年级」而不是「全校」
        _cat = ld.category or LEADER_CATEGORY_DEFAULT
        grade_txt = ('不分年级' if _cat == LEADER_CATEGORY_COMPREHENSIVE
                     else (ld.grade or '全校'))
        vals = [ld.category or LEADER_CATEGORY_DEFAULT, leader_group_name(ld.subject),
                ld.subject, grade_txt, ld.term or '整学年',
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
