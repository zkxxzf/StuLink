# StuLink v1.19.69.64 2026-10-12
# 考务环节④⑤⑥⑦：核对任课教师 / 考试时间表 / 监考编排 / 监考表导出
#
# 用户口径（2026-10-12 定案）：
#   · 环节④ 仅涉及「本次考试」：把该年级任课安排核对/临时调整后落在本批次，
#     不回写全局 TeacherSubjectLink（应对临时换老师 1-2 月）。
#     2026-10-21 用户口径：科目列 = 9 个文化科 + 综合组 6 科（体育/音乐/美术/
#     信息技术/通用技术/心理）+ 本班班主任（正班 / 副班1 / 副班2），
#     与任课教师映射页、教师安排表导入模板同一份口径。
#   · 环节⑤ 考试时间表承载「时间段 → 考试科目」，是环节⑥ 回避教师的依据。
#   · 环节⑥ 教师池 = 环节④ 任课教师（去重）+ 班主任 + 其他监考员（可为无账号的
#     校外老师，仅本次考试使用，不开系统账号）；每考场监考人数 1/2/3（默认 1，
#     存 affair.invig_slots）；回避规则：考化学 → 任教化学的老师不排该场次
#     （2026-10-19 起可勾选关闭）；每天首场优先排班主任；监考表一人一格。
#   · 环节⑦ 导出监考表（Excel + PDF）。
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import io
import re
from datetime import date, datetime, timedelta

import openpyxl
from flask import jsonify, request, send_file
from flask_login import login_required, current_user

from app.extensions import db
from app.models import User
from app.models.grades import (AFFAIR_EXCLUDED_SUBJECTS, AFFAIR_HEAD_SUBJECTS,
                               AffairTeacher, AffairSchedule, AffairInvigilator,
                               AffairInvigExclude, AffairInvigilation, AffairRoom,
                               AffairStudent, COMPREHENSIVE_SUBJECTS, ExamAffair,
                               TeacherSubjectLink,
                               SUBJECTS, Exam, normalize_subject)
from app.modules.grades import bp
from app.modules.grades.services.invig_balance import Cell, best_assign
# 2026-10-21 用户口径：环节④ 任课表恢复「班主任」三列（正班/副班1/副班2），
# 与任课教师同级别、负责本班总分
from app.modules.grades.services.teacher_snapshot_service import (
    HEAD_SUBJECT, HOMEROOM_POSITIONS, current_homeroom_positions)
from app.modules.grades.utils import numeric_classes
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation
from app.utils.export_helpers import xl_row, xl_safe
from app.utils.text_guard import (sanitize_label, sanitize_prefix, safe_download_name,
                                  pad2)   # 2026-10-13：监考表考场号补零（01/02…）
from app.utils.upload_guard import validate_upload

_MAX_SLOTS = 3
# 2026-10-12 安全审计 M-1：考试科目白名单。时间表科目是环节⑥「同科回避」的匹配键
# （_auto_arrange 按 {sch.subject} 回避任教该科老师），此前任意文本可入库 →
# 手写「化学(」之类的值会让回避规则静默失配，排出来就是化学老师监考化学。
_SUBJ_SET = set(SUBJECTS)

# 2026-10-14 用户口径：内置「每科考试时长」——选了科目就自动算出结束时间，不用自己按科目心算。
#   · 语文 2.5 小时（150 分钟）；数学 / 英语（外语）2 小时（120 分钟）；其它科目 75 分钟；
#   · 一场考多科（如「四选二」写成「物理、化学」）取**最长**的那一科；
#   · 开始时间缺省用 SCHED_DEFAULT_START —— 只在**手填**（save）与环境⑤ 新增行时兜底，
#     导入路径（Excel / 粘贴 / AI）不替用户猜开始时间，只把「有开始、缺结束」的补全。
SCHED_DEFAULT_START = '08:00'
SCHED_DURATION_MINUTES = {'语文': 150, '数学': 120, '英语': 120, '外语': 120}
SCHED_DURATION_FALLBACK = 75

# 2026-10-21 用户口径：考务④「核对任课教师」的列 = 班主任 3 + 文化科 9 + 综合组 5。
#   · 班主任三列是「正班 / 副班1 / 副班2」（master 口径，放在学科左边），
#     用**伪科目名**占位存进 affair_teachers，不写回 TeacherSubjectLink；
#     旧库里的「正班主任 / 副班主任1 / 副班主任2」是同位次的历史别名，
#     读取 / 保存时统一归一（见 _canon_subject），存量数据不用先迁移也能显示；
#   · 综合组科目从教务模型取（COMPREHENSIVE_SUBJECTS），与任课映射页同源；
#     **「通用技术」在考务里去掉**（用户 2026-10-21 明确要求，本校不带这门课；
#     任课映射页仍保留它 —— 那边是全校口径，删了会牵动导入模板与既有映射）；
#   · 伪科目绝不会进考试科目字典 —— 环节⑤ 时间表仍只认 SUBJECTS 9 科，否则
#     「体育」会被当成考试科目，触发莫名其妙的同科回避。
HEAD_SUBJECTS = AFFAIR_HEAD_SUBJECTS   # 历史别名（真值在 models）
_HEAD_ALIAS = dict(zip(AFFAIR_HEAD_SUBJECTS, HOMEROOM_POSITIONS))

AFFAIR_EXTRA_SUBJECTS = tuple(s for s in COMPREHENSIVE_SUBJECTS
                              if s not in AFFAIR_EXCLUDED_SUBJECTS)
AFFAIR_SUBJECTS = list(SUBJECTS) + list(AFFAIR_EXTRA_SUBJECTS)
# 环节⑥ 教师池 / 排除名单的分组顺序：文化科 → 综合组 → 班主任
# （班主任的历史别名也放末尾，避免 _subjRank 落 999 排到「未标注」之后）
AFFAIR_COLUMNS = (AFFAIR_SUBJECTS + list(HOMEROOM_POSITIONS)
                  + list(HEAD_SUBJECTS))
# 环节④ 表头分组（顺序即列顺序：班主任三列在最左，其次文化科、综合组）
AFFAIR_SUBJECT_GROUPS = [
    {'name': '班主任', 'subjects': list(HOMEROOM_POSITIONS)},
    {'name': '', 'subjects': list(SUBJECTS)},
    {'name': '综合组', 'subjects': list(AFFAIR_EXTRA_SUBJECTS)},
]


def _canon_subject(subj):
    """班主任旧伪科目名 → 新三列名（正班主任→正班，其余原样返回）。"""
    return _HEAD_ALIAS.get((subj or '').strip(), subj)


# ==================== 公共小工具 ====================

def _affair(aid):
    """取批次并校验年级可见（复用考务模块的越权校验）"""
    from app.modules.grades.routes.exam_affairs import _get_affair_checked
    return _get_affair_checked(aid)


def _class_list(affair):
    """本批次班级列表：优先取**年级**数字教学班（与任课映射页同口径）；
    学籍无数据时回落本批次名单里的班级。"""
    from app.modules.grades.routes.teachers import _grade_classes
    cls = _grade_classes(affair.grade)
    if cls:
        return cls
    names = [s.class_name for s in
             AffairStudent.query.filter_by(affair_id=affair.id).all() if s.class_name]
    return numeric_classes(sorted(set(names))) or sorted(set(names))


def _teacher_candidates(grade):
    """下拉候选教师（复用成绩模块的筛选：启用 + 教师类角色，拼音排序）"""
    from app.modules.grades.routes.teachers import _matrix_users
    return _matrix_users(grade)


def _invalidate_analysis_cache(affair):
    """④ 改完立刻反映到成绩分析：清该场考试的分析缓存。

    2026-10-21 用户口径：考务安排跟着老师走 —— ④ 的调整要马上出现在成绩分析
    （学科分析各班对比 / 任课教师分析 / 汇报区 / 全局对比），不能等 900s
    页面缓存自然过期。孤儿批次（未绑定考试）没有分析缓存，直接跳过。
    """
    if not affair.exam_id:
        return
    from app.modules.grades.utils import invalidate_exam_cache
    invalidate_exam_cache(affair.exam_id)


def _with_pinyin(items):
    """给 [{name: ...}] 补 py（全拼）/ ini（每字首字母）——前端选人三路输入用。

    2026-10-21 用户口径：选人时可以直接输入姓名、全拼、或每个字的首字母。
    拼音在服务端算（pypinyin 已装），前端只做字符串匹配，不引第三方 JS。
    """
    from app.modules.grades.services.user_account import pinyin_codes
    out = []
    for it in items:
        py, ini = pinyin_codes(it.get('name') or '')
        row = dict(it)
        row['py'], row['ini'] = py, ini
        out.append(row)
    return out


def _headteacher_cells(grade):
    """该年级各班班主任 → {class_name: [{user_id, teacher_name}, ...]}。

    数据源是 `UserClassLink`（与教师映射导入模板的「正班 / 副班1 / 副班2」同源），
    按 id 升序即正班在前；一个班超过 3 位时只保留前 3 位（表上就 3 列）。
    停用账号也保留姓名（老师不上系统不代表不当班主任），只是没有 user_id。
    """
    from app.models import UserClassLink
    links = (UserClassLink.query.filter_by(grade=grade)
             .order_by(UserClassLink.class_name, UserClassLink.id).all())
    if not links:
        return {}
    uids = {l.user_id for l in links}
    names = {u.id: u.real_name for u in
             User.query.filter(User.id.in_(uids)).all()} if uids else {}
    out = {}
    for lk in links:
        bag = out.setdefault(lk.class_name, [])
        if len(bag) < len(HOMEROOM_POSITIONS):
            bag.append({'user_id': lk.user_id,
                        'teacher_name': (names.get(lk.user_id) or '').strip() or None})
    return out


def _head_rows(grade):
    """班主任 → AffairTeacher 行（正班 / 副班1 / 副班2）。"""
    rows = []
    for cls, people in _headteacher_cells(grade).items():
        for i, p in enumerate(people):
            rows.append({'class_name': cls, 'subject': HOMEROOM_POSITIONS[i],
                         'user_id': p['user_id'],
                         'teacher_name': p['teacher_name']})
    return rows


def _sync_teachers_from_links(affair):
    """用当前 TeacherSubjectLink（同年级 active）+ 班主任名单覆盖写入本批次任课核对表。"""
    AffairTeacher.query.filter_by(affair_id=affair.id).delete()
    links = TeacherSubjectLink.query.filter_by(grade=affair.grade, active=True).all()
    uids = {l.user_id for l in links if l.user_id}
    names = ({u.id: u.real_name for u in User.query.filter(User.id.in_(uids)).all()}
             if uids else {})
    n = 0
    for l in links:
        # 2026-10-21：考务不看综合组里被排除的科目（通用技术）—— 不同步进来，
        # 免得教师池里多一条「页面上看不到、也无法编辑」的记录
        if l.subject not in AFFAIR_SUBJECTS:
            continue
        db.session.add(AffairTeacher(
            affair_id=affair.id, grade=affair.grade, class_name=l.class_name,
            subject=l.subject, user_id=l.user_id,
            teacher_name=names.get(l.user_id), source='sync'))
        n += 1
    # 2026-10-21 用户口径：班主任和任课教师同级别（负责本班总分），按
    # 正班 / 副班1 / 副班2 **三列**一并带入本场任课表。
    # 批次还没名单/学籍（_class_list 为空）时按**全年级**带入，否则新批次首次
    # 进来三列会全空，得等有名单后再手动「重新带入」。
    classes = set(_class_list(affair))
    heads = (current_homeroom_positions(affair.grade, classes=classes)
             if classes else current_homeroom_positions(affair.grade))
    for hr in heads:
        db.session.add(AffairTeacher(
            affair_id=affair.id, grade=affair.grade,
            class_name=hr['class_name'], subject=hr['subject'],
            user_id=hr.get('user_id'), teacher_name=hr['teacher_name'],
            source='sync'))
        n += 1
    return n


def _ensure_head_rows(affair):
    """老批次补班主任列（2026-10-21 之前同步进来的批次没有这三列）。

    幂等且不覆盖人工修订：只要本批次已有任意一行班主任伪科目就不再补。
    """
    has = (AffairTeacher.query
           .filter_by(affair_id=affair.id)
           .filter(AffairTeacher.subject.in_(
               list(HOMEROOM_POSITIONS) + list(AFFAIR_HEAD_SUBJECTS)))
           .count())
    if has:
        return 0
    added = 0
    for r in _head_rows(affair.grade):
        db.session.add(AffairTeacher(affair_id=affair.id, grade=affair.grade,
                                     source='sync', **r))
        added += 1
    return added


def ensure_teachers_synced(affair):
    """首次进入环节④ 时自动带入当前任课安排（幂等：已有数据不再覆盖）。"""
    if AffairTeacher.query.filter_by(affair_id=affair.id).count() == 0:
        _sync_teachers_from_links(affair)
        db.session.commit()
        _invalidate_analysis_cache(affair)   # 首次带入的任课会进入成绩分析
        return
    if _ensure_head_rows(affair):
        db.session.commit()
        _invalidate_analysis_cache(affair)   # 老批次补班主任列，全局对比的班主任要跟着变


def _split_subjects(text):
    """学科文本 → 集合（支持顿号/逗号/斜杠/空格分隔，如「语文、数学」）"""
    return {s for s in re.split(r'[、，,/\s]+', (text or '').strip()) if s}


def invig_pool(affair):
    """监考教师池：环节④ 任课教师 + 班主任（按姓名去重，学科取并集）+ 其他监考员。

    返回 [{name, user_id, subjects(set), is_head(bool), source}]
    （保持「先任课教师、后其他」的稳定顺序）。

    2026-10-21：班主任的伪科目（正班 / 副班1 / 副班2，及历史别名
    正班主任 / 副班主任1 / 副班主任2）**不进 subjects** —— subjects 是环节⑥
    「同科回避」的匹配键，混进伪科目只会让一位老师同时出现在「语文」和「班主任」
    两个分组里；班主任身份单独用 is_head 标记，供教师池分组与「首场优先班主任」使用。
    """
    pool = {}

    def _entry(name, user_id, source):
        e = pool.get(name)
        if e is None:
            e = {'name': name, 'user_id': user_id, 'subjects': set(),
                 'is_head': False, 'source': source}
            pool[name] = e
        elif user_id and not e['user_id']:
            e['user_id'] = user_id
        return e

    for t in AffairTeacher.query.filter_by(affair_id=affair.id).all():
        nm = (t.teacher_name or '').strip()
        if not nm:
            continue
        e = _entry(nm, t.user_id, 'teacher')
        # 「正班/副班1/副班2」是角色列不是学科：进教师池（要排监考），
        # 但不能当学科参与同科回避（旧伪科目名与旧合并列「班主任」同样跳过）
        if t.subject in HOMEROOM_POSITIONS or t.subject in AFFAIR_HEAD_SUBJECTS:
            e['is_head'] = True
        elif t.subject and t.subject != HEAD_SUBJECT:
            e['subjects'].add(t.subject)
    for o in AffairInvigilator.query.filter_by(affair_id=affair.id).all():
        nm = (o.name or '').strip()
        if not nm:
            continue
        e = _entry(nm, None, 'other')
        e['subjects'] |= _split_subjects(o.subject)
    return list(pool.values())


def _schedule_key(s):
    return (s.sort_order or 0, s.exam_date or date.min, s.start_time or '', s.id)


# ==================== 环节④ 核对任课教师 ====================

@bp.route('/affairs/<int:aid>/teachers')
@login_required
@perm_required('grades.edit')
def affair_teachers_get(aid):
    affair = _affair(aid)
    ensure_teachers_synced(affair)
    cells = {}
    for t in AffairTeacher.query.filter_by(affair_id=affair.id).all():
        # 旧伪科目名（正班主任/副班主任1/副班主任2）按新三列名返回，存量数据直接可见
        cells[f'{t.class_name}|{_canon_subject(t.subject)}'] = {
            'user_id': t.user_id, 'name': t.teacher_name, 'source': t.source}
    return jsonify(success=True, data={
        'grade': affair.grade,
        'classes': _class_list(affair),
        # 2026-10-21：班主任三列放在学科**左边**（正班/副班1/副班2），
        # 与任课教师同级别、负责本班总分；学科 = 文化科 9 + 综合组 5
        # （通用技术考务侧不带），subject_groups 供两行表头分组
        'subjects': list(HOMEROOM_POSITIONS) + list(AFFAIR_SUBJECTS),
        'head_subjects': list(HOMEROOM_POSITIONS),
        'subject_groups': AFFAIR_SUBJECT_GROUPS,
        'cells': cells,
        'users': _with_pinyin(_teacher_candidates(affair.grade)),
    })


@bp.route('/affairs/<int:aid>/teachers/sync', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_teachers_sync(aid):
    """从当前任课安排表重新带入（覆盖本批次已核对的调整）"""
    affair = _affair(aid)
    n = _sync_teachers_from_links(affair)
    db.session.commit()
    log_operation(current_user, '同步', '考务任课核对', aid,
                  f'{affair.grade} 带入 {n} 条', module='grades')
    _invalidate_analysis_cache(affair)
    return jsonify({'ok': True, 'synced': n})


@bp.route('/affairs/<int:aid>/teachers/save', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_teachers_save(aid):
    """保存本批次任课核对矩阵（仅本场，不回写全局映射）

    body JSON: {cells: {'01班|语文': user_id 或 '' , ...}}
    """
    affair = _affair(aid)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raw = (request.get_data(as_text=True) or '').strip()
        try:
            data = __import__('json').loads(raw) if raw.startswith('{') else {}
        except (ValueError, TypeError):
            data = {}
    cells = data.get('cells') or {}
    classes = set(_class_list(affair))
    # 校验 user_id
    uids = {int(v) for v in cells.values() if str(v).strip()}
    name_of = {}
    if uids:
        rows = User.query.filter(User.id.in_(uids)).all()
        name_of = {u.id: u.real_name for u in rows}
        if uids - set(name_of):
            return jsonify({'ok': False, 'msg': '存在无效的教师账号'})
    existing = {(t.class_name, t.subject): t
                for t in AffairTeacher.query.filter_by(affair_id=affair.id).all()}
    for key, val in cells.items():
        if '|' not in key:
            continue
        cls, subj = key.split('|', 1)
        # 2026-10-13：科目别名归一（英语/日语→外语 等）后再做白名单校验
        # 2026-10-21：班主任伪科目不做别名归一（否则可能被当成学科），
        # 旧伪科目名统一映射到新三列；白名单 = 学科列 + 班主任三列 + 旧合并列
        subj = _canon_subject(subj)
        if subj not in HOMEROOM_POSITIONS:
            subj = normalize_subject(subj)
        if cls not in classes or (subj not in AFFAIR_SUBJECTS
                                  and subj not in HOMEROOM_POSITIONS
                                  and subj != HEAD_SUBJECT):
            continue
        uid = int(val) if str(val).strip() else None
        row = existing.get((cls, subj))
        if uid is None and row is None:
            continue
        if row is None:
            row = AffairTeacher(affair_id=affair.id, grade=affair.grade,
                                class_name=cls, subject=subj)
            db.session.add(row)
            existing[(cls, subj)] = row
        row.user_id = uid
        # 班主任已拆成三列、每列一个人，清空就是清空（不存在多人合并值被误清的问题）
        row.teacher_name = name_of.get(uid) if uid else None
        row.source = 'manual'
    db.session.commit()
    log_operation(current_user, '维护', '考务任课核对', aid,
                  f'{affair.grade} 保存 {len(cells)} 格', module='grades')
    # 2026-10-21：④ 的调整要立刻出现在成绩分析（任课教师列 / 教师排名 / 汇报区）
    _invalidate_analysis_cache(affair)
    return jsonify({'ok': True})


# ==================== 环节⑤ 考试时间表 ====================

_SCHED_HEADERS = ['考试日期', '日期', '开始时间', '结束时间', '考试科目', '科目',
                  '选科', '备注']


def _parse_time(v):
    """时间单元格 → 'HH:MM'（兼容 datetime.time / 'HH:MM' / 'HHMM'）"""
    if v is None:
        return ''
    if isinstance(v, datetime):
        return v.strftime('%H:%M')
    if hasattr(v, 'strftime'):
        try:
            return v.strftime('%H:%M')
        except (ValueError, AttributeError):
            pass
    s = str(v).strip()
    m = re.match(r'^(\d{1,2})[:：](\d{1,2})', s)
    if m:
        return '%02d:%02d' % (int(m.group(1)), int(m.group(2)))
    if re.match(r'^\d{3,4}$', s):
        return '%02d:%02d' % (int(s[:-2]), int(s[-2:]))
    return s


def _parse_date(v):
    if v is None or str(v).strip() == '':
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip().replace('/', '-').replace('.', '-')
    for fmt in ('%Y-%m-%d', '%m-%d', '%Y%m%d', '%m月%d日'):
        try:
            d = datetime.strptime(s, fmt)
            return d.date() if fmt.startswith('%Y') else d.replace(year=date.today().year).date()
        except ValueError:
            continue
    return None


# ==================== ⑤ 考试时间表：粘贴 / Excel 导入的解析与排期 ====================
# 2026-10-13 用户口径：
#   1) 支持从 Excel 直接复制表格区域、粘贴批量导入（不必先存成文件再上传）；
#   2) 每天最多 _SCHED_DAILY_MAX 场，某天排满后自动顺延到次日（导完仍可手动改回）；
#   3) 行里没写日期时，默认用**创建考试时填的考试日期**作为考试时间。
_SCHED_DAILY_MAX = 6
# 表头列名别名（与 Excel 模板一致，另兼容常见写法）
_HEAD_ALIASES = (
    ('exam_date', ('考试日期', '日期', '考试时间')),
    ('start_time', ('开始时间', '开始', '时间段', '时间')),
    ('end_time', ('结束时间', '结束', '止')),
    ('subject', ('考试科目', '科目', '学科')),
    ('selection', ('选科', '选科组合', '分场')),
    ('note', ('备注', '说明')),
)
_RANGE_RE = re.compile(r'(\d{1,2}[:：.]\d{1,2})\s*[-~～—－–至到]\s*(\d{1,2}[:：.]\d{1,2})')
_DATE_ONLY_RE = re.compile(r'^\s*\d{4}\s*[-/.年]\s*\d{1,2}\s*[-/.月]\s*\d{1,2}\s*日?\s*$'
                           r'|^\s*\d{1,2}\s*[-/.月]\s*\d{1,2}\s*日?\s*$'
                           r'|^\s*\d{8}\s*$')


def _sched_default_date(affair):
    """导入时的默认考试日期 = 创建考试（或建批次）时填的考试日期。

    批次 exam_date → 关联考试 exam_date → 今天（都为空时的兜底，避免出现无日期场次）。
    """
    if affair.exam_date:
        return affair.exam_date
    if affair.exam_id:
        ex = Exam.query.get(affair.exam_id)
        if ex is not None and ex.exam_date:
            return ex.exam_date
    return date.today()


def _hhmm(v):
    """规范成 HH:MM；不是时间就返回空串（用于识别「这格到底是不是时间」）"""
    s = _parse_time(v)
    return s if re.match(r'^\d{2}:\d{2}$', s or '') else ''


def _sched_blank_row():
    return {'exam_date': None, 'start_time': '', 'end_time': '',
            'subject': '', 'selection': '', 'note': ''}


# ---------- 内置每科时长（2026-10-14 用户口径：自动算结束时间） ----------

def sched_duration_minutes(subject):
    """本场次的默认考试时长（分钟）：一场多科取**最长**那一科；没科目时用兜底 75 分钟。"""
    mins = [SCHED_DURATION_MINUTES.get(s, SCHED_DURATION_FALLBACK)
            for s in _split_subjects(subject)]
    return max(mins) if mins else SCHED_DURATION_FALLBACK


def add_minutes(hhmm, minutes):
    """'HH:MM' + N 分钟 → 'HH:MM'（跨午夜按 24 小时回绕；考场不跨天，仅防呆）。"""
    m = re.match(r'^(\d{1,2}):(\d{2})$', str(hhmm or '').strip())
    if not m or not minutes:
        return hhmm or ''
    total = (int(m.group(1)) * 60 + int(m.group(2)) + int(minutes)) % (24 * 60)
    return '%02d:%02d' % (total // 60, total % 60)


def fill_sched_end(row, default_start=None):
    """给一行场次补时间（原地改 row 字典）：

    · 结束缺、开始有值 → 结束 = 开始 + 本科内置时长；
    · 开始也缺且传了 default_start（手填场景）→ 先用 default_start 兜底开始，再补结束；
    · 用户已经填了结束 → 一律不动（手填优先）。
    返回是否补出了结束时间。
    """
    if not row.get('start_time') and default_start:
        row['start_time'] = default_start
    if row.get('start_time') and not row.get('end_time'):
        row['end_time'] = add_minutes(row['start_time'],
                                      sched_duration_minutes(row.get('subject')))
        return True
    return False


def _fill_date(row, value):
    d = _parse_date(value)
    if d is None:
        return False
    row['exam_date'] = d
    return True


def _fill_time(row, value, slot=None):
    """把一格时间填进开始/结束。

    支持合并写法「09:00-10:30」（一格顶两列）；单个时间则按 slot 落位
    （slot=None 时优先补空的那一侧）。
    """
    s = str(value or '').strip()
    if not s:
        return False
    m = _RANGE_RE.search(s)
    if m:
        row['start_time'] = _hhmm(m.group(1)) or row['start_time']
        row['end_time'] = _hhmm(m.group(2)) or row['end_time']
        return True
    t = _hhmm(s)
    if not t:
        return False
    if slot == 'end':
        if not row['end_time']:
            row['end_time'] = t
    elif slot == 'start':
        if not row['start_time']:
            row['start_time'] = t
    elif not row['start_time']:
        row['start_time'] = t
    elif not row['end_time']:
        row['end_time'] = t
    return True


def _fill_subject(row, value):
    """科目白名单校验（与手填/Excel 导入同一口径）；多科用「、」累积去重。

    2026-10-13：先对每一段调用 normalize_subject 做别名映射（英语/日语→外语 等），
    再过白名单 —— 不认识的写法仍会被挡下（整行跳过），不会悄悄入库。
    """
    names = [normalize_subject(x)
             for x in re.split(r'[、，,/\\\s]+', str(value or '').strip()) if x]
    if not names or any(x not in _SUBJ_SET for x in names):
        return False
    cur = [x for x in (row['subject'] or '').split('、') if x]
    for n in names:
        if n not in cur:
            cur.append(n)
    row['subject'] = '、'.join(cur)
    return True


def _split_paste_cells(raw):
    """一行文本 → 单元格：Excel 复制是 tab 分隔；其次 2 个以上空格；否则整行当一格"""
    if '\t' in raw:
        return [c.strip() for c in raw.split('\t')]
    if re.search(r'\s{4,}', raw):
        return [c.strip() for c in re.split(r'\s{4,}', raw)]
    return [raw.strip()]


def _detect_head(cells):
    """识别表头行 → {字段: 列下标}；没找到「科目」列就不当表头（返回 None）"""
    idx = {}
    for i, c in enumerate(cells):
        t = re.sub(r'\s+', '', str(c or ''))
        if not t:
            continue
        for key, names in _HEAD_ALIASES:
            if key not in idx and t in names:
                idx[key] = i
    return idx if 'subject' in idx else None


def _parse_line_by_head(cells, head):
    """按表头列序取值（列序不限，短行自动留空）"""
    row = _sched_blank_row()

    def get(key):
        i = head.get(key, -1)
        return cells[i] if 0 <= i < len(cells) else ''

    _fill_date(row, get('exam_date'))
    _fill_time(row, get('start_time'), 'start')
    _fill_time(row, get('end_time'), 'end')
    _fill_subject(row, get('subject'))
    row['selection'] = sanitize_label(get('selection'), 30)
    row['note'] = sanitize_label(get('note'), 100)
    return row


def _parse_line_loose(cells):
    """无表头：按内容识别（日期 / 时间段 / 科目 各归各位），不依赖列顺序。

    兼容「2026-10-08 09:00-10:30 语文」这种一格合体（同格含日期+时间+科目）的写法：
    整格能认出单一字段就直接归位，否则按空白拆 token 逐个识别，剩下的进选科/备注。
    """
    row = _sched_blank_row()
    leftovers = []

    def absorb(token):
        """单个字段值归位，返回是否已消费"""
        if not token:
            return True
        compact = re.sub(r'\s+', '', token)
        if _RANGE_RE.fullmatch(compact) and _fill_time(row, compact):
            return True
        if _DATE_ONLY_RE.match(token) and _fill_date(row, token):
            return True
        if _hhmm(token) and _fill_time(row, token):
            return True
        return _fill_subject(row, token)

    for c in cells:
        s = str(c or '').strip()
        if not s:
            continue
        # 整格 = 单一字段（时间段 / 日期 / 时间 / 科目）→ 直接归位；
        # 否则按空白拆开逐个识别（合体写法），仍未识别的进选科/备注。
        compact = re.sub(r'\s+', '', s)
        # 2026-10-13：「这格是不是科目」也先用归一后的值判断（英语→外语 等别名同样认得出）
        if ((_RANGE_RE.fullmatch(compact) or _DATE_ONLY_RE.match(s) or _hhmm(s)
             or normalize_subject(s) in _SUBJ_SET) and absorb(s)):
            continue
        for t in [x for x in re.split(r'\s+', s) if x]:
            if not absorb(t):
                leftovers.append(t)
    for t in leftovers:
        if not row['selection']:
            row['selection'] = sanitize_label(t, 30)
        elif not row['note']:
            row['note'] = sanitize_label(t, 100)
    return row


def _parse_paste_text(text):
    """粘贴文本 → (场次列表, 跳过行数)。表头行自动跳过。"""
    lines = (text or '').replace('\r\n', '\n').replace('\r', '\n').split('\n')
    head = None
    for l in [x for x in lines if x.strip()][:3]:
        head = _detect_head(_split_paste_cells(l))
        if head:
            break
    rows, skipped, head_seen = [], 0, False
    for l in lines:
        if not l.strip():
            continue
        cells = _split_paste_cells(l)
        if head is not None and not head_seen and _detect_head(cells):
            head_seen = True          # 只跳第一处表头
            continue
        row = _parse_line_by_head(cells, head) if head else _parse_line_loose(cells)
        if not row['subject']:
            skipped += 1
            continue
        rows.append(row)
    return rows, skipped


def _sched_apply(affair, parsed, default_date):
    """按「每天最多 _SCHED_DAILY_MAX 场」排日期后入库。

    已在批次里的场次也占当天名额；当天排满则顺延到次日（导入后用户可手动改回）。
    返回 (新增数, 顺延数, 按内置时长自动补结束时间的场次数)。
    """
    used = {}
    for s in AffairSchedule.query.filter_by(affair_id=affair.id).all():
        if s.exam_date:
            used[s.exam_date] = used.get(s.exam_date, 0) + 1
    base_order = AffairSchedule.query.filter_by(affair_id=affair.id).count()
    added = shifted = 0
    filled = 0
    for r in parsed:
        # 2026-10-14 用户口径：结束时间留空 → 按内置每科时长补（语文 2.5h / 数学·英语 2h / 其它 75min）。
        # 导入路径**不替用户猜开始时间**（没写就保持留空），只补「有开始、缺结束」这种半截数据。
        if fill_sched_end(r):
            filled += 1
        want = r['exam_date'] if r['exam_date'] else default_date
        d = want
        while used.get(d, 0) >= _SCHED_DAILY_MAX:
            d = d + timedelta(days=1)
        if d != want:
            shifted += 1
        used[d] = used.get(d, 0) + 1
        db.session.add(AffairSchedule(
            affair_id=affair.id, exam_date=d,
            start_time=r['start_time'], end_time=r['end_time'],
            subject=r['subject'], selection=r['selection'], note=r['note'],
            sort_order=base_order + added))
        added += 1
    return added, shifted, filled


@bp.route('/affairs/<int:aid>/schedules/template')
@login_required
@perm_required('grades.edit')
def affair_schedules_template(aid):
    affair = _affair(aid)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '考试时间表'
    # 2026-10-14 用户口径：模板列序与页面⑤ 一致 —— 科目列在日期右侧（导入按表头名取列，列序不影响解析）
    ws.append(['考试日期', '考试科目', '开始时间', '结束时间', '选科', '备注'])
    # 2026-10-14：示例行按**内置每科时长**给（语文 150 分钟 → 09:00-11:30；数学 120 分钟 → 14:00-16:00），
    # 结束时间也可以留空 —— 导入时系统会按科目自动补（见 fill_sched_end）。
    ws.append(['2026-10-08', '语文', '09:00', '11:30', '', ''])
    ws.append(['2026-10-08', '数学', '14:00', '16:00', '', ''])
    for ci in range(1, 7):
        c = ws.cell(row=1, column=ci)
        c.font = openpyxl.styles.Font(bold=True, color='FFFFFF')
        c.fill = openpyxl.styles.PatternFill(start_color='4472C4', end_color='4472C4',
                                             fill_type='solid')
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return send_file(out, as_attachment=True,
                     download_name='考试时间表模板.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@bp.route('/affairs/<int:aid>/schedules/import', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_schedules_import(aid):
    """导入考试时间表（表头容错：考试日期/日期、开始时间、结束时间、考试科目/科目、选科、备注）"""
    affair = _affair(aid)
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'ok': False, 'msg': '请选择 Excel 文件'})
    ok, err = validate_upload(f.filename, allowed_exts=['xlsx', 'xls'], stream=f.stream)
    if not ok:
        return jsonify({'ok': False, 'msg': err})
    try:
        wb = openpyxl.load_workbook(f.stream, data_only=True)
    except Exception as e:  # noqa: BLE001
        return jsonify({'ok': False, 'msg': f'无法读取 Excel：{e}'})
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return jsonify({'ok': False, 'msg': '文件为空'})
    head = [str(x or '').strip() for x in rows[0]]

    def _col(*names):
        for nm in names:
            if nm in head:
                return head.index(nm)
        return -1

    ci_date = _col('考试日期', '日期')
    ci_start = _col('开始时间', '开始')
    ci_end = _col('结束时间', '结束')
    ci_subj = _col('考试科目', '科目')
    ci_sel = _col('选科', '选科组合')
    ci_note = _col('备注')
    if ci_subj < 0:
        return jsonify({'ok': False, 'msg': '未找到「考试科目」列'})
    default_date = _sched_default_date(affair)
    parsed, skipped = [], 0
    for r in rows[1:]:
        def cell(i):
            return r[i] if (0 <= i < len(r)) else None
        row = _sched_blank_row()
        if ci_date >= 0:
            _fill_date(row, cell(ci_date))
        if ci_start >= 0:
            _fill_time(row, cell(ci_start), 'start')
        if ci_end >= 0:
            _fill_time(row, cell(ci_end), 'end')
        # 2026-10-12 安全审计 M-1：Excel 导入同样做服务端白名单 + 科目白名单校验
        # 2026-10-13：科目支持多选（「、」分隔，与手填口径一致）
        if not _fill_subject(row, cell(ci_subj)):
            skipped += 1
            continue
        row['selection'] = sanitize_label(cell(ci_sel) if ci_sel >= 0 else '', 30)
        row['note'] = sanitize_label(cell(ci_note) if ci_note >= 0 else '', 100)
        parsed.append(row)
    # 2026-10-13 用户口径：日期缺省用「创建考试时填的考试日期」；
    # 每天最多 _SCHED_DAILY_MAX 场，排满自动顺延到次日（与粘贴导入同一条规则）
    added, shifted, auto_end = _sched_apply(affair, parsed, default_date)
    db.session.commit()
    log_operation(current_user, '导入', '考试时间表', aid,
                  f'Excel 导入 {added} 场（跳过 {skipped} 行，'
                  f'{shifted} 场因当天已满 {_SCHED_DAILY_MAX} 场顺延，'
                  f'{auto_end} 场按科目时长自动补结束时间）', module='grades')
    return jsonify({'ok': True, 'added': added, 'skipped': skipped, 'shifted': shifted,
                    'auto_end': auto_end})


@bp.route('/affairs/<int:aid>/schedules/paste', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_schedules_paste(aid):
    """粘贴导入考试时间表（2026-10-13 用户口径）。

    从 Excel 选中区域 Ctrl+C，直接粘贴到页面弹窗即可，不必先另存成文件。
    解析规则（列顺序不限，带表头按表头、不带表头按内容识别）：
      · 日期 / 开始 / 结束 / 科目 / 选科 / 备注 六列，可缺列；
      · 「09:00-10:30」这种一格合体的时间会自动拆成开始/结束；
      · 每行必须有科目且科目在字典内，多科用「、」连接；
      · 没写日期 → 用创建考试时填的考试日期；每天最多 6 场，排满顺延到次日。
    """
    affair = _affair(aid)
    text = request.form.get('text') or ''
    if not (text or '').strip():
        return jsonify({'ok': False, 'msg': '请先粘贴表格内容（在 Excel 里选中后 Ctrl+C）'})
    if len(text) > 200000:
        return jsonify({'ok': False, 'msg': '一次粘贴的内容过长（不超过 20 万字符），请分批导入'})
    parsed, skipped = _parse_paste_text(text)
    if not parsed:
        return jsonify({'ok': False,
                        'msg': '没有解析出可导入的场次：每行至少要有一个「科目」，'
                               '且科目必须在科目字典内'})
    if len(parsed) > 500:
        return jsonify({'ok': False, 'msg': f'一次最多导入 500 场（本次 {len(parsed)} 场），请分批'})
    default_date = _sched_default_date(affair)
    added, shifted, auto_end = _sched_apply(affair, parsed, default_date)
    db.session.commit()
    log_operation(current_user, '导入', '考试时间表', aid,
                  f'粘贴导入 {added} 场（跳过 {skipped} 行，'
                  f'{shifted} 场因当天已满 {_SCHED_DAILY_MAX} 场顺延，'
                  f'{auto_end} 场按科目时长自动补结束时间）', module='grades')
    return jsonify({'ok': True, 'added': added, 'skipped': skipped, 'shifted': shifted,
                    'auto_end': auto_end,
                    'daily_max': _SCHED_DAILY_MAX,
                    'default_date': default_date.strftime('%Y-%m-%d')})


@bp.route('/affairs/<int:aid>/schedules/save', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_schedules_save(aid):
    """新增/修改一场（表单：id 为空则新增）

    2026-10-13：科目改为**多选**（如"四选二"一场同时考两科）——
    前端勾选后以「、」连接提交；每个科目都必须在字典内；
    存储沿用「、」分隔，与环节⑥「同科回避」的 _split_subjects 口径一致。
    """
    affair = _affair(aid)
    sid = request.form.get('id', type=int)
    raw_names = [s for s in re.split(r'[、，,/\\\s]+', (request.form.get('subject') or '').strip())
                 if s]
    if not raw_names:
        return jsonify({'ok': False, 'msg': '请选择考试科目'})
    # 2026-10-13：先对每一段做别名归一（英语/日语→外语 等）再校验白名单；
    # 报错回显**用户原始写法**，便于排查。
    names = [normalize_subject(s) for s in raw_names]
    bad = [raw for raw, std in zip(raw_names, names) if std not in _SUBJ_SET]
    if bad:
        return jsonify({'ok': False, 'msg': f'科目不在科目字典内：{"、".join(bad)}'})
    subj = '、'.join(names)
    row = AffairSchedule.query.get(sid) if sid else None
    if row is not None and row.affair_id != affair.id:
        return jsonify({'ok': False, 'msg': '场次不属于本批次'})
    if row is None:
        row = AffairSchedule(affair_id=affair.id,
                             sort_order=AffairSchedule.query.filter_by(
                                 affair_id=affair.id).count())
        db.session.add(row)
    row.exam_date = _parse_date(request.form.get('exam_date'))
    # 2026-10-14 用户口径：结束时间留空 → 开始 + 本场科目内置时长
    # （语文 2.5 小时 / 数学·英语 2 小时 / 其它 75 分钟；多科取最长）。
    # 开始时间也留空时整行保持留空（时间仍可不填）；前端「选科目自动带出时间」是同一套规则。
    _times = {'start_time': _parse_time(request.form.get('start_time')),
              'end_time': _parse_time(request.form.get('end_time')),
              'subject': subj}
    fill_sched_end(_times)
    row.start_time = _times['start_time']
    row.end_time = _times['end_time']
    row.subject = subj
    row.selection = sanitize_label(request.form.get('selection'), 30)
    row.note = sanitize_label(request.form.get('note'), 100)
    db.session.commit()
    return jsonify({'ok': True, 'id': row.id})


@bp.route('/affairs/<int:aid>/schedules/<int:sid>/delete', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_schedule_delete(aid, sid):
    affair = _affair(aid)
    row = AffairSchedule.query.get_or_404(sid)
    if row.affair_id != affair.id:
        return jsonify({'ok': False, 'msg': '场次不属于本批次'})
    invs = AffairInvigilation.query.filter_by(
        affair_id=affair.id, schedule_id=sid).all()
    # 2026-10-18：删除前整组快照（场次 + 全部监考格 + 排除项），可整体恢复；
    # 同时把 AffairInvigExclude 里同 schedule_id 的排除行一并清掉 ——
    # 原来只删监考格，排除项会变成孤儿（既看不见也删不掉）。
    from app.utils.trash import archive_snapshot
    excl = AffairInvigExclude.query.filter_by(
        affair_id=affair.id, schedule_id=sid).all()
    _desc = f'{row.exam_date or ""} {row.subject}'
    _snap = archive_snapshot(
        'grades', 'affair_schedule_bundle', '考务场次(含监考)',
        tables=[(AffairSchedule, row),
                (AffairInvigilation, invs),
                (AffairInvigExclude, excl)],
        summary=f'{_desc}：监考 {len(invs)} 格 / 排除 {len(excl)} 条',
        operator=current_user)
    if _snap is None:
        return jsonify({'ok': False, 'msg': '删除前归档失败，已中止'}), 500
    db.session.commit()
    n_inv = len(invs)
    AffairInvigilation.query.filter_by(
        affair_id=affair.id, schedule_id=sid).delete()
    if excl:
        AffairInvigExclude.query.filter_by(
            affair_id=affair.id, schedule_id=sid).delete()
    db.session.delete(row)
    db.session.commit()
    # 2026-10-12 数据可信度审计（§3.8）：删场次会连带删该场次全部监考安排，
    # 此前无业务日志（兜底网只记 endpoint 名）。
    log_operation(current_user, '删除', '考试时间表', aid,
                  f'删除场次「{_desc}」并清理其 {n_inv} 格监考安排', module='grades')
    return jsonify({'ok': True})


# ==================== 环节⑥ 监考编排 ====================

@bp.route('/affairs/<int:aid>/invig')
@login_required
@perm_required('grades.edit')
def affair_invig_get(aid):
    affair = _affair(aid)
    ensure_teachers_synced(affair)
    pool = invig_pool(affair)
    schedules = sorted(AffairSchedule.query.filter_by(affair_id=affair.id).all(),
                       key=_schedule_key)
    rooms = AffairRoom.query.filter_by(affair_id=affair.id).order_by(AffairRoom.room_no).all()
    invigs = AffairInvigilation.query.filter_by(affair_id=affair.id).all()
    by_key = {}
    for iv in invigs:
        by_key.setdefault((iv.schedule_id, iv.room_no), {})[iv.slot] = {
            'name': iv.name or '', 'user_id': iv.user_id}
    excl_all, excl_sch = _excludes(affair)
    # 每人格数（已排的监考位）—— 供前端展示"谁排得多/谁还没排到"
    load = {}
    for iv in invigs:
        if iv.name:
            load[iv.name] = load.get(iv.name, 0) + 1
    return jsonify(success=True, data={
        'slots': max(1, min(_MAX_SLOTS, affair.invig_slots or 1)),
        # py / ini：监考表「一格一人」换人时也能按全拼 / 首字母搜（2026-10-21）
        'pool': _with_pinyin([{'name': p['name'], 'user_id': p['user_id'],
                               'subjects': sorted(p['subjects']), 'source': p['source'],
                               'is_head': bool(p.get('is_head')),
                               'excluded': p['name'] in excl_all,
                               'excl_schedules': sorted(sid for sid, names in excl_sch.items()
                                                        if p['name'] in names)}
                              for p in pool]),
        'load': load,
        'excludes': [{'id': e.id, 'name': e.name, 'schedule_id': e.schedule_id,
                      'note': e.note or ''}
                     for e in _excl_rows(affair)],
        'others': [{'id': o.id, 'name': o.name, 'subject': o.subject or '', 'note': o.note or ''}
                   for o in AffairInvigilator.query.filter_by(affair_id=affair.id).all()],
        'schedules': [{'id': s.id, 'date': s.exam_date.strftime('%Y-%m-%d') if s.exam_date else '',
                       'start': s.start_time or '', 'end': s.end_time or '',
                       'subject': s.subject, 'selection': s.selection or ''} for s in schedules],
        # 2026-10-13：补 capacity —— 矩阵视图（学校张贴格式）行头要显示「容纳人数」
        # 2026-10-16 用户口径「监考表缺个科目」：补 subject（考场科目/适用选科）——
        # 此前监考表只显考场号/位置/人数，看不出某个考场是按哪一科（或哪一选科组合）设的。
        'rooms': [{'room_no': r.room_no, 'location': r.location or '',
                   'capacity': r.capacity,
                   'subject': (r.subject or '')} for r in rooms],
        'invig': {f'{k[0]}|{k[1]}': v for k, v in by_key.items()},
    })


# ---- 监考工作量统计（2026-10-13 用户口径：要能看「每位老师监考了多长时间」）----

def _hhmm_minutes(t):
    """'09:00' → 540（分钟）；空 / 非法 → None"""
    m = re.match(r'^\s*(\d{1,2})\s*[:：]\s*(\d{1,2})\s*$', t or '')
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return None
    return h * 60 + mi


def _session_minutes(s):
    """场次时长（分钟）：起止时间齐全才计；end < start 视为跨零点顺延一天。"""
    if s is None:
        return None
    a, b = _hhmm_minutes(s.start_time), _hhmm_minutes(s.end_time)
    if a is None or b is None:
        return None
    return (b - a) if b >= a else (b + 1440 - a)


def _invig_workload(affair):
    """按老师汇总监考工作量：格数 / 时长（分钟）/ 缺时间格数 + 逐格明细。

    口径：一人一场次只监一个考场（环节⑥ 硬约束），故「格数」即场次数；
    时长按场次起止时间累加；没填时间的场次只计数、计入 missing，不猜时长。
    """
    scheds = {s.id: s for s in AffairSchedule.query.filter_by(affair_id=affair.id).all()}
    rooms = {r.room_no: r for r in AffairRoom.query.filter_by(affair_id=affair.id).all()}
    pool = {p['name']: p for p in invig_pool(affair)}
    items = {}
    for iv in AffairInvigilation.query.filter_by(affair_id=affair.id).all():
        nm = (iv.name or '').strip()
        if not nm:
            continue
        s = scheds.get(iv.schedule_id)
        room = rooms.get(iv.room_no)
        mins = _session_minutes(s)
        it = items.setdefault(nm, {
            'name': nm,
            'user_id': iv.user_id or (pool.get(nm) or {}).get('user_id'),
            'source': (pool.get(nm) or {}).get('source') or 'unknown',
            'is_head': bool((pool.get(nm) or {}).get('is_head')),
            'cells': 0, 'minutes': 0, 'missing': 0, 'detail': []})
        it['cells'] += 1
        if mins is None:
            it['missing'] += 1
        else:
            it['minutes'] += mins
        it['detail'].append({
            'date': s.exam_date.strftime('%Y-%m-%d') if (s is not None and s.exam_date) else '',
            'start': (s.start_time if s is not None else '') or '',
            'end': (s.end_time if s is not None else '') or '',
            'subject': (s.subject if s is not None else '') or '',
            'room_no': pad2(iv.room_no or ''),
            'location': (room.location if room is not None else '') or '',
            'minutes': mins,
        })
    rows = sorted(items.values(),
                  key=lambda x: (-x['minutes'], -x['cells'], x['name']))
    for it in rows:
        it['detail'].sort(key=lambda d: (d['date'], d['start'], d['subject']))
    # 2026-10-19：给出「人均 / 最长 / 最短 / 极差」——用户判断均衡不均衡，
    # 看的就是这几个数（格数一样多不代表时间一样多）。
    mins = [it['minutes'] for it in rows if it['cells']]
    balance = ({'max': max(mins), 'min': min(mins),
                'mean': int(round(sum(mins) / len(mins))),
                'spread': max(mins) - min(mins)} if mins else
               {'max': 0, 'min': 0, 'mean': 0, 'spread': 0})
    return {
        'items': rows,
        'has_time': any(it['minutes'] for it in rows),
        'total_cells': sum(it['cells'] for it in rows),
        'total_minutes': sum(it['minutes'] for it in rows),
        'total_missing': sum(it['missing'] for it in rows),
        'balance': balance,
    }


@bp.route('/affairs/<int:aid>/invig/workload')
@login_required
@perm_required('grades.edit')
def affair_invig_workload(aid):
    affair = _affair(aid)
    return jsonify(success=True, data=_invig_workload(affair))


@bp.route('/affairs/<int:aid>/invig/settings', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_invig_settings(aid):
    """设置每考场监考人数（1/2/3，默认 1）"""
    affair = _affair(aid)
    n = request.form.get('slots', type=int) or 1
    old = affair.invig_slots or 1
    affair.invig_slots = max(1, min(_MAX_SLOTS, n))
    db.session.commit()
    # 2026-10-12 数据可信度审计（§3.8）：该设置决定监考表列数（1/2/3 人），
    # 改动会影响后续所有编排与导出，原先无业务日志。
    log_operation(current_user, '设置', '监考人数', aid,
                  f'每考场监考人数 {old} → {affair.invig_slots}', module='grades')
    return jsonify({'ok': True, 'slots': affair.invig_slots})


@bp.route('/affairs/<int:aid>/invig/others/add', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_invig_others_add(aid):
    """新增其他监考员（可无系统账号：校外老师，仅本次考试使用）"""
    affair = _affair(aid)
    # 2026-10-12 安全审计 M-1：姓名/学科/备注走 sanitize_label 服务端白名单
    # （与 exam_affairs 的考场位置/备注同防线）。这些字段会进监考表 Excel/PDF，
    # 且前端有 innerHTML 拼接路径，只 .strip() 不构成纵深防御。
    name = sanitize_label(request.form.get('name'), 30)
    if not name:
        return jsonify({'ok': False, 'msg': '请填写姓名'})
    exist = AffairInvigilator.query.filter_by(affair_id=affair.id, name=name).first()
    if exist is not None:
        return jsonify({'ok': False, 'msg': '该监考员已存在'})
    db.session.add(AffairInvigilator(
        affair_id=affair.id, name=name,
        subject=sanitize_label(request.form.get('subject'), 20),
        note=sanitize_label(request.form.get('note'), 100)))
    db.session.commit()
    # 2026-10-12 数据可信度审计（§3.8）：其他监考员（含校外无账号老师）增删原先无业务日志
    log_operation(current_user, '新增', '其他监考员', aid, f'{name}', module='grades')
    return jsonify({'ok': True})


@bp.route('/affairs/<int:aid>/invig/teacher-candidates', methods=['GET'])
@login_required
@perm_required('grades.edit')
def affair_invig_teacher_candidates(aid):
    """2026-10-14 用户口径：加监考员要**从教师名单里勾选**（尤其是其他学科 / 其他年级的老师），
    不该再用 prompt 一个个手输姓名和学科。校外无账号老师仍走「手工添加 / 导入名单」。

    返回全校在职教师（姓名 + 学科 + 关联账号）+ 已在本批次教师池里的姓名，供前端置灰。
    """
    affair = _affair(aid)
    from app.models.academic import Teacher       # 教师库（academic 库）
    # 学科口径：**以任课教师映射 TeacherSubjectLink 为唯一依据**（项目约定；Teacher.subject
    # 多为空）。一位老师可能教多科，故回传 subjects 数组，前端按学科分组展开。
    subs_by_uid = {}
    for lk in TeacherSubjectLink.query.filter_by(active=True).all():
        s = (lk.subject or '').strip()
        if s:
            subs_by_uid.setdefault(lk.user_id, set()).add(s)
    rows = (Teacher.query.filter(Teacher.status == 'active')
            .order_by(Teacher.name).all())
    teachers, seen = [], set()
    for t in rows:
        nm = (t.name or '').strip()
        if not nm or nm in seen:
            continue
        seen.add(nm)
        subs = sorted(subs_by_uid.get(t.user_id, set()))
        teachers.append({'name': nm, 'subjects': subs,
                         'subject': '、'.join(subs),   # 展示 / 去重串（与教师池 subject 同格式）
                         'user_id': t.user_id})
    return jsonify({'success': True, 'data': {
        'teachers': _with_pinyin(teachers),
        'in_pool': sorted({p['name'] for p in invig_pool(affair)}),
        'others': sorted(o.name for o in
                         AffairInvigilator.query.filter_by(affair_id=affair.id).all()),
    }})


@bp.route('/affairs/<int:aid>/invig/others/add-batch', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_invig_others_add_batch(aid):
    """从教师名单**批量**加入监考员（弹窗勾选后一次提交）。

    body：JSON `[{'name': ..., 'subject': ...}, ...]`；按姓名去重（已存在的计入 dup）。
    字段仍走 sanitize_label 白名单（与单条新增同一防线）。
    """
    affair = _affair(aid)
    items = request.get_json(silent=True)
    if not isinstance(items, list):
        return jsonify({'ok': False, 'msg': '参数格式不正确'})
    exist = {o.name for o in AffairInvigilator.query.filter_by(affair_id=affair.id).all()}
    added = dup = 0
    for it in items[:500]:                        # 上限：防一次塞几千行
        if not isinstance(it, dict):
            continue
        name = sanitize_label(it.get('name'), 30)
        if not name or name in exist:
            dup += 1
            continue
        db.session.add(AffairInvigilator(
            affair_id=affair.id, name=name,
            subject=sanitize_label(it.get('subject'), 20),
            note=sanitize_label(it.get('note'), 100)))
        exist.add(name)
        added += 1
    db.session.commit()
    if added:
        log_operation(current_user, '新增', '其他监考员', aid,
                      f'勾选加入 {added} 人', module='grades')
    return jsonify({'ok': True, 'added': added, 'dup': dup})


@bp.route('/affairs/<int:aid>/invig/others/<int:oid>/delete', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_invig_other_delete(aid, oid):
    affair = _affair(aid)
    row = AffairInvigilator.query.get_or_404(oid)
    if row.affair_id != affair.id:
        return jsonify({'ok': False, 'msg': '不属于本批次'})
    _nm = row.name
    db.session.delete(row)
    db.session.commit()
    log_operation(current_user, '删除', '其他监考员', aid, f'{_nm}', module='grades')
    return jsonify({'ok': True})


@bp.route('/affairs/<int:aid>/invig/others/import', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_invig_others_import(aid):
    """导入其他监考员名单（Excel：姓名、任教学科、备注）"""
    affair = _affair(aid)
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'ok': False, 'msg': '请选择 Excel 文件'})
    ok, err = validate_upload(f.filename, allowed_exts=['xlsx', 'xls'], stream=f.stream)
    if not ok:
        return jsonify({'ok': False, 'msg': err})
    try:
        wb = openpyxl.load_workbook(f.stream, data_only=True)
    except Exception as e:  # noqa: BLE001
        return jsonify({'ok': False, 'msg': f'无法读取 Excel：{e}'})
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return jsonify({'ok': False, 'msg': '文件为空'})
    head = [str(x or '').strip() for x in rows[0]]

    def _col(*names):
        for nm in names:
            if nm in head:
                return head.index(nm)
        return -1

    ci_name = _col('姓名', '监考员', '监考老师')
    ci_subj = _col('任教学科', '学科', '科目')
    ci_note = _col('备注')
    if ci_name < 0:
        return jsonify({'ok': False, 'msg': '未找到「姓名」列'})
    exist = {o.name for o in AffairInvigilator.query.filter_by(affair_id=affair.id).all()}
    teacher_names = {t.teacher_name for t in
                     AffairTeacher.query.filter_by(affair_id=affair.id).all()
                     if t.teacher_name}
    added, dup = 0, 0
    for r in rows[1:]:
        def cell(i):
            return r[i] if (0 <= i < len(r)) else None
        # 2026-10-12 安全审计 M-1：Excel 导入同样走服务端白名单（导入是写入面最宽的口子）
        nm = sanitize_label(cell(ci_name), 30)
        if not nm:
            continue
        if nm in exist or nm in teacher_names:
            dup += 1
            continue
        db.session.add(AffairInvigilator(
            affair_id=affair.id, name=nm,
            subject=sanitize_label(cell(ci_subj) if ci_subj >= 0 else '', 20),
            note=sanitize_label(cell(ci_note) if ci_note >= 0 else '', 100)))
        exist.add(nm)
        added += 1
    db.session.commit()
    log_operation(current_user, '导入', '其他监考员', aid, f'新增 {added} 人', module='grades')
    return jsonify({'ok': True, 'added': added, 'dup': dup})


@bp.route('/affairs/<int:aid>/invig/copy-sources', methods=['GET'])
@login_required
@perm_required('grades.edit')
def affair_invig_copy_sources(aid):
    """可复用的监考配置来源：同年级历次考务批次（按考试日期倒序）+ 各自配置摘要。

    2026-10-14 用户口径：「要能复用上次考试的配置，上一次是哪一场**可以选择**」。
    只列出确实配过监考的批次（有其他监考员 / 排除名单 / 已排监考表 / 人数 > 1）。
    """
    affair = _affair(aid)
    rows = (ExamAffair.query
            .filter(ExamAffair.grade == affair.grade, ExamAffair.id != affair.id)
            .order_by(ExamAffair.exam_date.desc(), ExamAffair.id.desc()).all())
    out = []
    for a in rows:
        others = AffairInvigilator.query.filter_by(affair_id=a.id).count()
        excludes = AffairInvigExclude.query.filter_by(affair_id=a.id).count()
        whole = AffairInvigExclude.query.filter_by(affair_id=a.id,
                                                   schedule_id=None).count()
        invig = AffairInvigilation.query.filter_by(affair_id=a.id).count()
        slots = a.invig_slots or 1
        if not (others or excludes or invig or slots > 1):
            continue
        out.append({'id': a.id, 'name': a.name,
                    'date': a.exam_date.strftime('%Y-%m-%d') if a.exam_date else '',
                    'slots': slots, 'others': others, 'excludes': excludes,
                    'whole': whole, 'invig': invig})
    return jsonify({'success': True, 'data': {'sources': out,
                                              'cur_slots': affair.invig_slots or 1}})


@bp.route('/affairs/<int:aid>/invig/copy-from', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_invig_copy_from(aid):
    """复用指定批次的监考配置（人口径见下），2026-10-14 用户需求。

    · 每考场监考人数：**覆盖**为源批次的值；
    · 其他监考员：按姓名去重**追加**；
    · 排除名单：整批排除（schedule_id 为空）直接搬；按场次排除按「科目 + 同科第几场」
      映射到本批次场次，映射不上（本批次没这门科 / 同科场次不够）或已存在则跳过并报数。
    · **不搬**已编排的监考表格子 —— 本批次考场与场次一般与源批次不同，搬过来无意义。
    """
    affair = _affair(aid)
    body = request.get_json(silent=True) or {}
    src_id = request.form.get('src_aid', type=int) or body.get('src_aid')
    try:
        src = ExamAffair.query.get(int(src_id)) if src_id else None
    except (TypeError, ValueError):
        src = None
    if src is None or src.grade != affair.grade or src.id == affair.id:
        return jsonify({'ok': False, 'msg': '源批次不存在、跨年级或就是本批次'})

    # ① 每考场监考人数（覆盖）
    old_slots = affair.invig_slots or 1
    affair.invig_slots = src.invig_slots or 1

    # ② 其他监考员（去重追加）
    exist_o = {o.name for o in AffairInvigilator.query.filter_by(affair_id=affair.id).all()}
    o_added = o_dup = 0
    for r in AffairInvigilator.query.filter_by(affair_id=src.id).all():
        if r.name in exist_o:
            o_dup += 1
            continue
        db.session.add(AffairInvigilator(affair_id=affair.id, name=r.name,
                                         subject=r.subject, note=r.note))
        exist_o.add(r.name)
        o_added += 1

    # ③ 排除名单
    src_scheds = (AffairSchedule.query.filter_by(affair_id=src.id)
                  .order_by(AffairSchedule.id).all())
    dst_scheds = (AffairSchedule.query.filter_by(affair_id=affair.id)
                  .order_by(AffairSchedule.id).all())
    src_ki, dst_pool = {}, {}
    for s in src_scheds:                    # 源场次 id → (科目, 同科第几场)
        k = (s.subject or '').strip()
        src_ki[s.id] = (k, sum(1 for x in src_scheds
                               if (x.subject or '').strip() == k and x.id <= s.id))
    for t in dst_scheds:                    # 本批次：科目 → [场次 id...]
        dst_pool.setdefault((t.subject or '').strip(), []).append(t.id)
    src2dst = {}
    for sid, (k, idx) in src_ki.items():
        lst = dst_pool.get(k) or []
        src2dst[sid] = lst[idx - 1] if len(lst) >= idx else None

    exist_e = {(e.name, e.schedule_id) for e in
               AffairInvigExclude.query.filter_by(affair_id=affair.id).all()}
    e_added = e_dup = e_skip = 0
    for e in AffairInvigExclude.query.filter_by(affair_id=src.id).all():
        sid = e.schedule_id
        if sid is not None:
            sid = src2dst.get(sid)
            if sid is None:
                e_skip += 1                  # 本批次没有对应场次（科目对不上）
                continue
        if (e.name, sid) in exist_e:
            e_dup += 1
            continue
        db.session.add(AffairInvigExclude(affair_id=affair.id, name=e.name,
                                          user_id=e.user_id, schedule_id=sid))
        exist_e.add((e.name, sid))
        e_added += 1
    db.session.commit()
    # 2026-10-14：复用配置会改变后续自动编排的结果，按纪律记业务日志
    log_operation(current_user, '复用', '监考配置', aid,
                  f'来自批次{src.id}：人数 {old_slots}→{affair.invig_slots}'
                  f'，其他监考员 +{o_added}，排除 +{e_added}', module='grades')
    return jsonify({'ok': True, 'src': src.name, 'slots': affair.invig_slots,
                    'old_slots': old_slots, 'others_added': o_added, 'others_dup': o_dup,
                    'excl_added': e_added, 'excl_dup': e_dup, 'excl_skipped': e_skip})


@bp.route('/affairs/<int:aid>/invig/exclude', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_invig_exclude_add(aid):
    """加入排除名单：form name（必填）、schedule_id（空=整批次不监考）、note"""
    from app.models.grades import AffairInvigExclude

    affair = _affair(aid)
    name = (request.form.get('name') or '').strip()
    sid = request.form.get('schedule_id', type=int)
    if not name:
        return jsonify({'ok': False, 'msg': '请选择老师'})
    pool = {p['name']: p for p in invig_pool(affair)}
    p = pool.get(name)
    if p is None:
        return jsonify({'ok': False, 'msg': '该老师不在监考池中'})
    if sid:
        sch = AffairSchedule.query.get(sid)
        if sch is None or sch.affair_id != affair.id:
            return jsonify({'ok': False, 'msg': '场次无效'})
    q = AffairInvigExclude.query.filter_by(affair_id=affair.id, name=name,
                                           schedule_id=sid)
    if q.first() is not None:
        return jsonify({'ok': False, 'msg': '该排除项已存在'})
    db.session.add(AffairInvigExclude(
        affair_id=affair.id, name=name, user_id=p['user_id'], schedule_id=sid,
        note=(request.form.get('note') or '').strip()[:100] or None))
    db.session.commit()
    scope = '整批次不监考' if not sid else '不监考该场次'
    log_operation(current_user, '修改', '监考排除名单', aid, f'{name} {scope}',
                  module='grades')
    return jsonify({'ok': True, 'msg': f'{name}：{scope}'})


@bp.route('/affairs/<int:aid>/invig/exclude/batch', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_invig_exclude_batch(aid):
    """批量加入排除名单：names（可多选老师）× sids（可多选场次，0 = 整批次）。

    2026-10-13 用户口径：原先「一个人 × 一场」逐个加，一个老师要排掉 6 场得点 6 次；
    现在横竖都能多选，一次落多条。规则：
      - 老师必须来自监考池（与单个添加同一道校验）；
      - 场次必须属于本批次；勾了「整批次不监考」时该老师只落一条整批排除
        （具体场次的勾选忽略，避免「整批 + 每场」的冗余行）；
      - 同一组合已存在则跳过计数，不算失败（重复点「加」不会报错）。
    """
    from app.models.grades import AffairInvigExclude

    affair = _affair(aid)
    names = []
    for raw in request.form.getlist('names'):
        nm = (raw or '').strip()
        if nm and nm not in names:
            names.append(nm)
    if not names:
        return jsonify({'ok': False, 'msg': '请选择老师'})
    pool = {p['name']: p for p in invig_pool(affair)}
    bad = [n for n in names if n not in pool]
    if bad:
        return jsonify({'ok': False, 'msg': '不在监考池中：' + '、'.join(bad[:5])})
    vals = []
    for raw in request.form.getlist('sids'):
        if raw is None or str(raw).strip() == '':
            continue
        try:
            vals.append(int(raw))
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'msg': '场次参数无效'})
    whole = (0 in vals) or not vals            # 没勾场次 = 按整批次（与旧单选默认一致）
    sids = [v for v in dict.fromkeys(vals) if v]
    if sids:
        owned = {s.id for s in AffairSchedule.query.filter_by(affair_id=affair.id).all()}
        bad_s = [str(v) for v in sids if v not in owned]
        if bad_s:
            return jsonify({'ok': False, 'msg': '场次不属于本批次：' + '、'.join(bad_s[:5])})
    targets = [0] if whole else sids
    if len(names) * len(targets) > 500:
        return jsonify({'ok': False,
                        'msg': f'一次最多 500 条（本次 {len(names) * len(targets)} 条），请分批'})
    exist = {(e.name, e.schedule_id or 0) for e in _excl_rows(affair)}
    added = skipped = 0
    for nm in names:
        for sid in targets:
            if (nm, sid) in exist:
                skipped += 1
                continue
            db.session.add(AffairInvigExclude(
                affair_id=affair.id, name=nm, user_id=pool[nm]['user_id'],
                schedule_id=sid or None))
            exist.add((nm, sid))
            added += 1
    if added:
        db.session.commit()
        scope = '整批次不监考' if whole else f'{len(sids)} 个场次不监考'
        log_operation(current_user, '修改', '监考排除名单', aid,
                      f'批量：{len(names)} 人 × {scope} → 新增 {added} 条'
                      + (f'（跳过已存在 {skipped} 条）' if skipped else ''),
                      module='grades')
    return jsonify({'ok': True, 'added': added, 'skipped': skipped,
                    'names': len(names), 'schedules': 0 if whole else len(sids),
                    'whole': whole})


@bp.route('/affairs/<int:aid>/invig/exclude/delete', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_invig_exclude_del(aid):
    """恢复某人监考（删除排除项）：form id"""
    from app.models.grades import AffairInvigExclude

    affair = _affair(aid)
    eid = request.form.get('id', type=int)
    row = db.session.get(AffairInvigExclude, eid) if eid else None
    if row is None or row.affair_id != affair.id:
        return jsonify({'ok': False, 'msg': '排除项不存在'})
    _n = row.name
    db.session.delete(row)
    db.session.commit()
    log_operation(current_user, '修改', '监考排除名单', aid, f'恢复 {_n} 监考',
                  module='grades')
    return jsonify({'ok': True, 'msg': f'已恢复 {_n}'})


@bp.route('/affairs/<int:aid>/invig/auto', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_invig_auto(aid):
    """自动编排监考：场次 × 考场 × 监考位，尽量均衡轮转。

    2026-10-19 用户口径：两个规则交给页面勾选（缺省都开）——
      avoid_same=0 时不回避本场科目教师；head_first=1 时每天首场优先排班主任。
    """
    affair = _affair(aid)
    avoid_same = _flag('avoid_same', True)
    head_first = _flag('head_first', True)
    # 2026-10-12（§3.5）：容量不足等前置条件不满足时给 400 + 明确原因，
    # 不要让 ValueError 冒成 500（原实现未捕获，前端只看到"请求失败"）。
    try:
        rows, info = _auto_arrange(affair, avoid_same=avoid_same,
                                   head_first=head_first)
    except ValueError as e:
        return jsonify({'ok': False, 'msg': str(e)}), 400
    db.session.commit()
    gaps = info.get('gap_count') or 0
    log_operation(current_user, '编排', '监考表', aid,
                  f'{info["schedules"]}场×{info["rooms"]}考场×{info["slots"]}人 → '
                  f'{rows}/{info.get("total", rows)}格'
                  + ('，不回避同科' if not avoid_same else '')
                  + ('，首场优先班主任' if head_first else '')
                  + (f'，缺口 {gaps}' if gaps else ''),
                  module='grades')
    return jsonify({'ok': True, 'rows': rows, **info})


def _flag(name, default=True):
    """表单布尔开关：缺省取 default，'0/off/false/no/空' 视为关。"""
    raw = request.form.get(name)
    if raw is None:
        return default
    return (raw or '').strip().lower() not in ('0', 'off', 'false', 'no', '')


def _excl_rows(affair):
    """排除名单明细（整批在前）；表尚未迁移时返回空列表。"""
    from app.models.grades import AffairInvigExclude

    try:
        return (AffairInvigExclude.query.filter_by(affair_id=affair.id)
                .order_by(AffairInvigExclude.schedule_id.is_(None).desc(),
                          AffairInvigExclude.name).all())
    except Exception:                       # noqa: BLE001
        db.session.rollback()
        return []


def _excludes(affair):
    """监考排除名单 → (整批不监考 set(name), {场次id: set(name)} 不监考该场次)"""
    excl_all, excl_sch = set(), {}
    rows = _excl_rows(affair)
    for e in rows:
        nm = (e.name or '').strip()
        if not nm:
            continue
        if e.schedule_id:
            excl_sch.setdefault(e.schedule_id, set()).add(nm)
        else:
            excl_all.add(nm)
    return excl_all, excl_sch


def _time_overlap(s1, s2):
    """两个场次时间是否重叠（同一人被排进重叠场次 = 分身乏术）。

    只有**同一天且两端时间齐全**才判定；时间缺失时不误判（保守放行）。
    """
    if not s1.exam_date or s1.exam_date != s2.exam_date:
        return False
    a1, b1 = (s1.start_time or ''), (s1.end_time or '')
    a2, b2 = (s2.start_time or ''), (s2.end_time or '')
    if not (a1 and b1 and a2 and b2):
        return False
    return a1 < b2 and a2 < b1


def _schedule_duration(sch, default=120):
    """场次时长（分钟）：由 start_time/end_time 计算；缺失或异常时用 default。

    2026-10-19：均衡口径从"格数"改成"累计时长"的前提 —— 各场次时长不同
    （语文 150 分钟、英语 90 分钟），只按次数均衡会出现"都是 5 场，
    但一个 750 分钟、一个 450 分钟"的明显不公平。
    """
    a, b = (sch.start_time or '').strip(), (sch.end_time or '').strip()
    try:
        ah, am = a.split(':')
        bh, bm = b.split(':')
        mins = (int(bh) * 60 + int(bm)) - (int(ah) * 60 + int(am))
        if 0 < mins <= 12 * 60:
            return mins
    except Exception:                   # noqa: BLE001  时间格式异常 → 用默认时长
        pass
    return default


def _default_duration(schedules):
    """同批场次时长的中位数（给时间缺失的场次兜底）；全缺失时按 120 分钟。"""
    vals = sorted(_schedule_duration(s, 0) for s in schedules)
    vals = [v for v in vals if v > 0]
    return vals[len(vals) // 2] if vals else 120


def _first_slot_sids(schedules):
    """每天「第一场」的场次 id 集合（同日同一起始时间的并行场次都算）。

    2026-10-19 用户口径「第一场次优先编排班主任」：班主任早上本来就要到校
    （早读 / 班级管理），先排首场最顺；非班主任只在自己有场次时才用到校。
    没有开始时间的场次按 `_schedule_key` 的先后兜底取第一条。
    """
    by_day = {}
    for s in schedules:
        by_day.setdefault(s.exam_date, []).append(s)
    out = set()
    for group in by_day.values():
        timed = [s for s in group if (s.start_time or '').strip()]
        if timed:
            first = min(s.start_time.strip() for s in timed)
            out |= {s.id for s in timed if s.start_time.strip() == first}
        else:
            out.add(min(group, key=_schedule_key).id)
    return out


def _head_teacher_names(pool):
    """监考池里的班主任姓名集合。

    口径（2026-10-19，2026-10-21 扩充）：满足任一即算班主任 ——
      ① 环节④ 班主任三列里出现的人（`invig_pool` 打的 is_head 标记，**没有账号也算**）；
      ② `User.role == 'homeroom_teacher'`；
      ③ 在 `UserClassLink` 里挂了班（多身份账号常见：role 是普通教师却兼班主任）。
    其他监考员（无账号 / user_id 为空且不在班主任列）不参与优先。
    """
    from app.models import UserClassLink
    names = {p['name'] for p in pool if p.get('is_head')}
    ids = {p['user_id'] for p in pool if p.get('user_id')}
    if not ids:
        return names
    heads = {u.id for u in User.query.filter(
        User.id.in_(ids), User.role == 'homeroom_teacher').all()}
    heads |= {l.user_id for l in UserClassLink.query.filter(
        UserClassLink.user_id.in_(ids)).all()}
    return names | {p['name'] for p in pool if p.get('user_id') in heads}


def _auto_arrange(affair, avoid_same=True, head_first=True):
    """自动编排监考（2026-10-13 增强，2026-10-19 均衡口径改为**累计时长**）：

    约束（硬）：
      H1 同科回避：任教该场科目的老师不排（一场多科时每科都回避）
      H2 一人一格：同一场次内一人只监一个考场
      H3 排除名单：整批不监考 / 不监考指定场次
      H4 时间冲突：同一人被排的两个场次时间不能重叠

    算法：`services/invig_balance.best_assign` —— 多起点 LPT 贪心 +
    缺口补位 + 移动/互换降差 + 等长互换收次数，目标是「每人累计监考分钟
    尽量接近」。只按格数均衡会出现"都是 5 场，但一个 495 分钟、一个
    345 分钟"（用户 2026-10-19 反馈的正是这种：场次多的人时间也更多）。

    返回 (rows, stat)；stat 含 filled/total/gaps/load（每人格数）/
    load_minutes（每人分钟）/ balance（max/min/mean/spread）。

    参数（2026-10-19 用户口径：回避与首场偏好都做成页面勾选，不再写死）：
      avoid_same : True 时同科回避（任教本场科目者不排本场）
      head_first : True 时**每天第一场**的格优先排班主任（首选，不硬约束）
    """
    pool = invig_pool(affair)
    excl_all, excl_sch = _excludes(affair)
    schedules = sorted(AffairSchedule.query.filter_by(affair_id=affair.id).all(),
                       key=_schedule_key)
    rooms = AffairRoom.query.filter_by(affair_id=affair.id).order_by(AffairRoom.room_no).all()
    slots = max(1, min(_MAX_SLOTS, affair.invig_slots or 1))
    if not pool:
        raise ValueError('监考教师池为空（请先在环节④ 核对任课教师）')
    if not schedules:
        raise ValueError('尚未导入考试时间表（环节⑤）')
    if not rooms:
        raise ValueError('尚未设置考场（环节②）')
    # 2026-10-12 数据可信度审计（§3.5）：原先池内人手不够时 `if not cands: break`
    # 直接留空，该考场剩余监考位**无声无息**——20 考场×2 人=40 格、池里只有 25 人时，
    # 15 个空格既不报错也不计数，导出的监考表就是空的。
    # 这里先做容量预检：总格数 vs 池内可用教师数（按场次剔除同科回避后取最小值）。
    def _static_cands(sch):
        return [p for p in pool
                if p['name'] not in excl_all
                and p['name'] not in excl_sch.get(sch.id, set())
                and (not avoid_same
                     or not (p['subjects'] & _split_subjects(sch.subject)))]

    total_cells = len(schedules) * len(rooms) * slots
    # 2026-10-13：容量预检改为**各场次可排上限之和**（原先用"单场最小可用人数 × 场次数"，
    # 但各场次可用人数本就不同 —— 回避/排除往往只影响个别场次，那样算会误判"人不够"）。
    per_sch = {s.id: len(_static_cands(s)) for s in schedules}
    capacity = sum(min(n, len(rooms) * slots) for n in per_sch.values())
    avail = min(per_sch.values()) if per_sch else 0
    if capacity < total_cells:
        raise ValueError(
            '监考教师不足：共需 %d 格（%d 场 × %d 考场 × %d 人），'
            '而各场次可排教师合计最多 %d 格（已剔除%s排除名单，'
            '最少的一场只有 %d 人可用）→ 缺 %d 格。'
            '请增加监考员（环节⑥「其他监考员」）、取消部分排除，'
            '或取消「同科回避」、减少每考场监考人数。'
            % (total_cells, len(schedules), len(rooms), slots,
               capacity, '同科回避与' if avoid_same else '', avail,
               total_cells - capacity))
    # 2026-10-18：自动重排会整批清空旧安排，删前先快照（回收站可恢复）；
    # 归档失败就中止重排，避免"排了一次，旧安排再也找不回来"。
    # 2026-10-19：**首次编排没有旧安排可存**，此时 archive_snapshot 返回 None
    # 属正常（不是失败）—— 原来一律当失败，导致第一次点「自动编排」永远报
    # 「删除前归档失败」，整批排不出来。
    from app.utils.trash import archive_snapshot
    _old_inv = AffairInvigilation.query.filter_by(affair_id=affair.id).all()
    if _old_inv:
        _snap = archive_snapshot(
            'grades', 'affair_invig_bundle', '监考安排(重排前)',
            tables=[(AffairInvigilation, _old_inv)],
            summary=f'{affair.name}：重排前 {len(_old_inv)} 格监考',
            operator=current_user)
        if _snap is None:
            raise ValueError('删除前归档失败，已中止重排（避免监考安排不可恢复）')
        db.session.commit()  # 归档先落库，再清空旧安排
    AffairInvigilation.query.filter_by(affair_id=affair.id).delete()
    # 2026-10-19：编排交给 services/invig_balance（均衡口径 = **累计时长**）。
    # 组装"格"（场次 × 考场 × 监考位），静态候选已剔除同科回避与排除名单；
    # 时间冲突由 overlap 回调判定，算法内部保证一人一场次一格。
    dur_of = {s.id: _schedule_duration(s, _default_duration(schedules))
              for s in schedules}
    sch_by_id = {s.id: s for s in schedules}
    by_name = {p['name']: p for p in pool}
    # 2026-10-19：「每天首场优先排班主任」——只对首场格给优先名单，
    # 其余场次不带偏好，均衡目标不变。
    head_names = _head_teacher_names(pool) if head_first else set()
    first_sids = _first_slot_sids(schedules) if head_names else set()
    cells = []
    for sch in schedules:
        cands = frozenset(p['name'] for p in _static_cands(sch))
        pref = frozenset(head_names & cands) if sch.id in first_sids else frozenset()
        for room in rooms:
            for slot in range(1, slots + 1):
                cells.append(Cell(key=(sch.id, room.room_no, slot), sid=sch.id,
                                  duration=dur_of[sch.id], cands=cands, pref=pref))

    def _overlap(a, b):
        return _time_overlap(sch_by_id[a], sch_by_id[b])

    plan, cell_gaps, stat = best_assign(cells, [p['name'] for p in pool], _overlap)
    gaps = [{'schedule': f'{sch_by_id[c.sid].exam_date or ""} '
                         f'{sch_by_id[c.sid].subject}',
             'room': c.key[1], 'slot': c.key[2],
             'reason': '无可用教师（同科回避 / 排除 / 时间冲突）'}
            for c in cell_gaps]
    rows = 0
    for cell, name in plan:
        p = by_name[name]
        sch = sch_by_id[cell.sid]
        db.session.add(AffairInvigilation(
            affair_id=affair.id, schedule_id=sch.id, room_no=cell.key[1],
            slot=cell.key[2], name=name, user_id=p['user_id'],
            subject='、'.join(sorted(p['subjects']))))
        rows += 1
    return rows, {'slots': slots, 'schedules': len(schedules), 'rooms': len(rooms),
                  'total': total_cells, 'filled': rows, 'gap_count': len(gaps),
                  # 2026-10-19：本次编排用的两个勾选 + 优先人数量，供前端提示
                  'opts': {'avoid_same': bool(avoid_same),
                           'head_first': bool(head_first),
                           'head_count': len(head_names),
                           'first_schedules': len(first_sids)},
                  'gaps': gaps[:8],
                  'load': {k: v for k, v in sorted(stat['count'].items(),
                                                   key=lambda x: -x[1])},
                  # 2026-10-19：每人累计监考时长（分钟），供页面核对是否均衡
                  'load_minutes': {k: v for k, v in sorted(stat['load'].items(),
                                                           key=lambda x: -x[1])},
                  # 时长均衡指标：极差 / 人均，前端直接展示，不用自己算
                  'balance': {'max': stat['max_load'], 'min': stat['min_load'],
                              'mean': stat['mean_load'], 'spread': stat['spread']}}


@bp.route('/affairs/<int:aid>/invig/set', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_invig_set(aid):
    """手动指定某一格监考老师（一人一格）。按姓名设置，姓名不在池中则拒绝。

    form: schedule_id, room_no, slot, name
    """
    affair = _affair(aid)
    sid = request.form.get('schedule_id', type=int)
    room_no = (request.form.get('room_no') or '').strip()
    slot = request.form.get('slot', type=int) or 1
    name = (request.form.get('name') or '').strip()
    sch = AffairSchedule.query.get(sid)
    if sch is None or sch.affair_id != affair.id:
        return jsonify({'ok': False, 'msg': '场次无效'})
    if not AffairRoom.query.filter_by(affair_id=affair.id, room_no=room_no).first():
        return jsonify({'ok': False, 'msg': '考场无效'})
    row = AffairInvigilation.query.filter_by(
        affair_id=affair.id, schedule_id=sid, room_no=room_no, slot=slot).first()
    if not name:
        if row is not None:
            db.session.delete(row)
            db.session.commit()
        return jsonify({'ok': True, 'name': ''})
    pool = {p['name']: p for p in invig_pool(affair)}
    p = pool.get(name)
    if p is None:
        return jsonify({'ok': False, 'msg': '该老师不在监考池中'})
    if sch.subject and (p['subjects'] & _split_subjects(sch.subject)):
        return jsonify({'ok': False, 'msg': f'{name} 任教{sch.subject}，本场需回避'})
    # 2026-10-13：手动指派与自动编排同一套硬约束（原先手动可绕过）
    excl_all, excl_sch = _excludes(affair)
    if name in excl_all:
        return jsonify({'ok': False, 'msg': f'{name} 已设为本次考试不监考'})
    if name in excl_sch.get(sid, set()):
        return jsonify({'ok': False, 'msg': f'{name} 已设为不监考这一场'})
    dup = AffairInvigilation.query.filter_by(
        affair_id=affair.id, schedule_id=sid, name=name).filter(
        db.or_(AffairInvigilation.room_no != room_no,
               AffairInvigilation.slot != slot)).first()
    if dup is not None:
        return jsonify({'ok': False,
                        'msg': f'{name} 本场已排在考场 {dup.room_no}，不能同时监考两个考场'})
    for other in AffairInvigilation.query.filter_by(
            affair_id=affair.id, name=name).all():
        if other.schedule_id == sid:
            continue
        s2 = AffairSchedule.query.get(other.schedule_id)
        if s2 is not None and _time_overlap(sch, s2):
            return jsonify({'ok': False,
                            'msg': f'{name} 在 {s2.exam_date} {s2.start_time}-{s2.end_time} '
                                   f'已排监考，与本场时间重叠'})
    if row is None:
        row = AffairInvigilation(affair_id=affair.id, schedule_id=sid,
                                 room_no=room_no, slot=slot)
        db.session.add(row)
    row.name = name
    row.user_id = p['user_id']
    row.subject = '、'.join(sorted(p['subjects']))
    db.session.commit()
    # 2026-10-12 数据可信度审计（§3.8）：手动改监考表原先无业务日志，
    # 事后无法回答"这一格是谁改的、从谁改成谁"。
    log_operation(current_user, '修改', '监考表', aid,
                  f'{sch.exam_date or ""} {sch.subject}｜考场 {room_no} 第 {slot} 位 → {name}',
                  module='grades')
    return jsonify({'ok': True, 'name': name})


# ==================== 环节⑦ 导出监考表（学校张贴格式矩阵 + 工作量统计） ====================
# 2026-10-13 用户口径：监考表**只保留一个导出**——行=考场（含容纳人数/位置）、
# 列=日期 × 上午/下午 × 科目的「教师安排」矩阵（对齐学校下发的张贴表），
# 并在**同一张表**下方直接带「监考工作量统计」（每人 格数 × 时长 + 合计）。
# 原先的「长表 Excel / 监考表 PDF / 矩阵 / 工作量」四个入口按用户要求合并为一个。
_WEEKS_CN = ('周一', '周二', '周三', '周四', '周五', '周六', '周日')

_SRC_CN = {'teacher': '任课教师', 'other': '其他监考员', 'unknown': ''}


def _room_subject_label(room):
    """考场科目的显示文案（监考表页面与导出共用）

    2026-10-16 用户口径「监考表缺个科目」。内部约定：``默认``/``通用``/``全部``/空
    都表示"不限科目"，页面上统一显示「全科」（与环节②考场设置的下拉口径一致），
    避免把内部词「默认」直接贴到学校张贴表上。
    """
    s = (getattr(room, 'subject', '') or '').strip()
    if s in ('', '默认', '通用', '全部'):
        return '全科'
    return s


def _matrix_days(schedules):
    """场次按日期分组（组内保持 _schedule_key 顺序）→ [{'date': date, 'items': [s]}]"""
    days = []
    for s in sorted(schedules, key=_schedule_key):
        if not days or days[-1]['date'] != s.exam_date:
            days.append({'date': s.exam_date, 'items': []})
        days[-1]['items'].append(s)
    return days


def _period_of(t):
    """'08:20' → 上午 / '14:30' → 下午；没写时间返回 ''（表头该格留空）"""
    m = re.match(r'^\s*(\d{1,2})\s*[:：]\s*(\d{1,2})', t or '')
    if not m:
        return ''
    return '上午' if int(m.group(1)) < 12 else '下午'


def _cn_day(d):
    return f'{d.month}月{d.day}日（{_WEEKS_CN[d.weekday()]}）' if d else '日期待定'


@bp.route('/affairs/<int:aid>/export/invig')
@login_required
@perm_required('grades.edit')
def affair_export_invig(aid):
    """导出监考表（一个文件、一张表）：上段 = 教师安排矩阵（行=考场，列=日期×时段×科目，
    一格一位老师），下段 = 监考工作量统计（每人 格数 × 时长 + 合计）——2026-10-13 用户口径。"""
    affair = _affair(aid)
    schedules = AffairSchedule.query.filter_by(affair_id=affair.id).all()
    rooms = (AffairRoom.query.filter_by(affair_id=affair.id)
             .order_by(AffairRoom.room_no).all())
    if not schedules or not rooms:
        return jsonify({'ok': False, 'msg': '尚未编排监考表'}), 400
    invigs = AffairInvigilation.query.filter_by(affair_id=affair.id).all()
    slots = max(1, min(_MAX_SLOTS, affair.invig_slots or 1))
    for iv in invigs:
        if iv.slot and iv.slot > slots:
            slots = min(_MAX_SLOTS, iv.slot)
    at = {}
    for iv in invigs:
        at.setdefault((iv.schedule_id, iv.room_no), {})[iv.slot] = iv.name or ''

    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    days = _matrix_days(schedules)
    ordered = [s for d in days for s in d['items']]
    lead = 4                       # 左侧固定列：考场号 / 容纳人数 / 考场位置 / 科目（2026-10-16 加）
    ncol = lead + len(ordered)

    thin = Side(style='thin', color='9AA5B1')
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    center = Alignment(horizontal='center', vertical='center', wrap_text=True)
    head_fill = PatternFill('solid', fgColor='DCE6F1')
    gap_fill = PatternFill('solid', fgColor='FCE4E4')
    title_font = Font(bold=True, size=14)
    head_font = Font(bold=True)
    data_font = Font()
    gap_font = Font(color='B02A2A')

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '监考表'

    def paint(r1, c1, r2, c2, value=None, font=None, fill=None):
        """区域刷边框（合并前逐格刷，合并后 openpyxl 只保留左上角），左上角写值"""
        for rr in range(r1, r2 + 1):
            for cc in range(c1, c2 + 1):
                cell = ws.cell(row=rr, column=cc)
                cell.border = box
                if fill:
                    cell.fill = fill
        top = ws.cell(row=r1, column=c1)
        if value is not None:
            top.value = xl_safe(value)
        top.font = font or data_font
        top.alignment = center

    # ① 标题
    paint(1, 1, 1, ncol, f'{affair.name}　监考表', font=title_font, fill=head_fill)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncol)
    ws.row_dimensions[1].height = 26

    # ② 日期层 / ③ 上午下午层 / ④ 科目层
    col = lead + 1          # 1..lead 是左侧固定列，场次从 lead+1 列开始
    for d in days:
        span = len(d['items'])
        paint(2, col, 2, col + span - 1, _cn_day(d['date']), font=head_font, fill=head_fill)
        if span > 1:
            ws.merge_cells(start_row=2, start_column=col, end_row=2, end_column=col + span - 1)
        # 上午/下午：只有该天场次都写了时间才可信，否则整天留空
        per_list = [_period_of(s.start_time) for s in d['items']]
        if all(per_list):
            i = 0
            while i < len(per_list):
                j = i
                while j + 1 < len(per_list) and per_list[j + 1] == per_list[i]:
                    j += 1
                paint(3, col + i, 3, col + j, per_list[i], font=head_font, fill=head_fill)
                if j > i:
                    ws.merge_cells(start_row=3, start_column=col + i,
                                   end_row=3, end_column=col + j)
                i = j + 1
        else:
            paint(3, col, 3, col + span - 1, '', fill=head_fill)
        for i, s in enumerate(d['items']):
            t = f'{s.start_time}-{s.end_time}' if (s.start_time or s.end_time) else ''
            paint(4, col + i, 4, col + i, (s.subject or '') + (f'\n{t}' if t else ''),
                  font=head_font, fill=head_fill)
        col += span

    # ⑤ 左侧列头（2026-10-16 用户口径「监考表缺个科目」：末列补「科目」，
    #    与页面监考表矩阵同源显示考场科目/适用选科）
    paint(5, 1, 5, 1, '考场号', font=head_font, fill=head_fill)
    paint(5, 2, 5, 2, '容纳人数', font=head_font, fill=head_fill)
    paint(5, 3, 5, 3, '考场位置', font=head_font, fill=head_fill)
    paint(5, 4, 5, 4, '科目', font=head_font, fill=head_fill)

    # ⑥ 数据行（一格一位老师；多监考位用「、」并列；空格标「待排」）
    row = 6
    for rm in rooms:
        paint(row, 1, row, 1, pad2(rm.room_no))
        paint(row, 2, row, 2, rm.capacity if rm.capacity else '')
        paint(row, 3, row, 3, rm.location or '')
        paint(row, 4, row, 4, _room_subject_label(rm))
        for i, s in enumerate(ordered):
            names = [n for n in (at.get((s.id, rm.room_no), {}).get(k, '')
                                 for k in range(1, slots + 1)) if n]
            c1 = lead + 1 + i
            if names:
                paint(row, c1, row, c1, '、'.join(names))
            else:
                paint(row, c1, row, c1, '待排', font=gap_font, fill=gap_fill)
        ws.row_dimensions[row].height = 22
        row += 1

    # ---- 下段：监考工作量统计（与矩阵同一张表；2026-10-13 用户口径：一个导出里自带） ----
    wl = _invig_workload(affair)
    row += 1
    paint(row, 1, row, 6, f'{affair.name}　监考工作量统计', font=head_font, fill=head_fill)
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
    row += 1
    if wl['total_missing']:
        paint(row, 1, row, 6,
              f'（有 {wl["total_missing"]} 格场次未填开始/结束时间，未计入时长 —— 请到环节⑤ 补时间）',
              font=gap_font)
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
        row += 1
    for ci, h in enumerate(['教师', '身份', '监考格数', '监考时长（分钟）',
                            '监考时长（小时）', '缺时间格数'], 1):
        paint(row, ci, row, ci, h, font=head_font, fill=head_fill)
    row += 1
    if wl['items']:
        for it in wl['items']:
            paint(row, 1, row, 1, it['name'])
            # 2026-10-21：班主任身份单独标（班主任也多任教某科，写「任课教师」看不出）
            paint(row, 2, row, 2,
                  '班主任' if it.get('is_head') else _SRC_CN.get(it['source'], ''))
            paint(row, 3, row, 3, it['cells'])
            paint(row, 4, row, 4, it['minutes'])
            paint(row, 5, row, 5, round(it['minutes'] / 60, 2))
            paint(row, 6, row, 6, it['missing'])
            row += 1
        for ci, v in enumerate(['合计', '', wl['total_cells'], wl['total_minutes'],
                                round(wl['total_minutes'] / 60, 2), wl['total_missing']], 1):
            paint(row, ci, row, ci, v, font=head_font)
        row += 1
    else:
        paint(row, 1, row, 6, '（尚未编排监考表）')
        row += 1

    ws.column_dimensions['A'].width = 13
    ws.column_dimensions['B'].width = 12
    ws.column_dimensions['C'].width = 16
    ws.column_dimensions['D'].width = 12      # 科目（2026-10-16 新增列）
    for i in range(lead + 1, ncol + 1):
        ws.column_dimensions[get_column_letter(i)].width = 12
    ws.freeze_panes = 'E6'                   # 左侧 4 列固定（新增科目列后右移一格）
    ws.print_title_rows = '1:5'          # 打印时每页重复表头
    ws.page_setup.orientation = 'landscape'

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    log_operation(current_user, '导出', '监考表', aid,
                  f'{len(ordered)} 场 × {len(rooms)} 考场 / {len(wl["items"])} 人工作量',
                  module='grades')
    return send_file(out, as_attachment=True,
                     download_name=safe_download_name(f'监考表_{affair.name}') + '.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')



