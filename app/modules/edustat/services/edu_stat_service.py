# StuLink v1.19.58.64 2026-10-20
# 教育事业统计（基表 2108/3118/3119/3045 + 4149/4153/4156）计算服务
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""教育事业综合统计基表的口径计算（只读）。

设计原则：
- **不新增第二套口径**：学生/教师全部读既有表（students / teachers /
  student_accommodation），不复制数据、不做快照；
- 年级按「学年」映射：学年 2026 → 一年级=2026级、二年级=2025级、
  三年级=2024级、毕业生=2023级（不依赖是否已执行「毕业」操作）；
- 年龄统一按**学年 9 月 1 日**计算（教育统计口径），身份证解析；
- 未填写的属性一律返回 None（页面显示「—」），不把"没填"当成 0 报上去。

对应的基表：
  教基2108 普通高中班额班数情况
  教基3118 普通高中分类型学生数
  教基3119 高中阶段分年龄学生数
  教基3045 在校生中其他情况
  教基4149 中小学教职工情况
  教基4153 基础教育专任教师分年龄情况
  教基4156 普通高中专任教师分课程、分学历情况
"""
import re
from collections import Counter, OrderedDict
from datetime import date

from app.extensions import db
from app.models import Student, Teacher
from app.models.room import StudentAccommodation

# ── 基表固定的分类（顺序与基表一致，导出时直接按此排列）──────────────────
CLASS_SIZE_BANDS = [
    ('25人及以下', 0, 25), ('26-30人', 26, 30), ('31-35人', 31, 35),
    ('36-40人', 36, 40), ('41-45人', 41, 45), ('46-50人', 46, 50),
    ('51-55人', 51, 55), ('56-60人', 56, 60), ('61-65人', 61, 65),
    ('66人及以上', 66, 10 ** 6),
]

STUDENT_AGE_BANDS = [
    ('14岁及以下', 0, 14), ('15岁', 15, 15), ('16岁', 16, 16),
    ('17岁', 17, 17), ('18岁', 18, 18), ('19岁', 19, 19),
    ('20岁', 20, 20), ('21岁', 21, 21), ('22岁', 22, 22),
    ('23岁及以上', 23, 10 ** 6),
]

TEACHER_AGE_BANDS = [
    ('24岁及以下', 0, 24), ('25-29岁', 25, 29), ('30-34岁', 30, 34),
    ('35-39岁', 35, 39), ('40-44岁', 40, 44), ('45-49岁', 45, 49),
    ('50-54岁', 50, 54), ('55-59岁', 55, 59), ('60岁及以上', 60, 10 ** 6),
]

EDU_LEVELS = ['博士研究生', '硕士研究生', '本科', '专科',
              '高中阶段', '高中阶段以下']
TITLE_LEVELS = ['正高级', '副高级', '中级', '助理级', '员级', '未定职级']
STAFF_TYPES = ['专任教师', '行政人员', '教辅人员', '工勤人员', '其他']

# 教基4156 的课程列（基表列序）；键=系统 Teacher.subject 的归一值
SUBJECT_COLUMNS = ['思想政治', '语文', '数学', '外语', '历史', '地理',
                   '物理', '化学', '生物学', '技术', '体育与健康', '艺术',
                   '综合实践活动', '劳动', '其他']
# 系统里的学科名 → 基表课程列（未列出的归「其他」）
SUBJECT_MAP = {
    '思想政治': '思想政治', '政治': '思想政治', '道德与法治': '思想政治',
    '语文': '语文', '数学': '数学',
    '外语': '外语', '英语': '外语', '日语': '外语', '俄语': '外语',
    '历史': '历史', '地理': '地理', '物理': '物理', '化学': '化学',
    '生物': '生物学', '生物学': '生物学',
    '技术': '技术', '信息技术': '技术', '通用技术': '技术',
    '体育': '体育与健康', '体育与健康': '体育与健康',
    '音乐': '艺术', '美术': '艺术', '艺术': '艺术',
    '综合实践活动': '综合实践活动', '劳动': '劳动',
}


# ── 基础工具 ─────────────────────────────────────────────────────────
def normalize_year(text):
    """"2026-2027" / "2026" → 2026；非法返回当前年。"""
    m = re.search(r'(20\d{2})', str(text or ''))
    if m:
        return int(m.group(1))
    return date.today().year


def year_label(school_year):
    """统一显示成 "2026-2027"。"""
    y = normalize_year(school_year)
    return f'{y}-{y + 1}'


def ref_date(school_year):
    """统计时点：学年 9 月 1 日。"""
    return date(normalize_year(school_year), 9, 1)


def grade_of(school_year, offset):
    """学年 - offset 对应的年级名（系统约定「YYYY级」）。"""
    return f'{normalize_year(school_year) - offset}级'


def _age_on(id_card, ref):
    """从 18 位身份证算统计时点年龄；无效返回 None。"""
    if not id_card or len(id_card) != 18:
        return None
    try:
        y, m, d = int(id_card[6:10]), int(id_card[10:12]), int(id_card[12:14])
        return ref.year - y - ((ref.month, ref.day) < (m, d))
    except (TypeError, ValueError):
        return None


def _gender_from_id(id_card):
    """身份证第 17 位：奇男偶女。"""
    if not id_card or len(id_card) != 18 or not id_card[16].isdigit():
        return None
    return '男' if int(id_card[16]) % 2 else '女'


def _band(age, bands):
    """年龄 → 所属分档名；None 返回 None。"""
    if age is None:
        return None
    for name, lo, hi in bands:
        if lo <= age <= hi:
            return name
    return None


def grade_scope(school_year):
    """本学年统计涉及的年级：一年级/二年级/三年级 + 毕业年级。"""
    return {
        'g1': grade_of(school_year, 0),
        'g2': grade_of(school_year, 1),
        'g3': grade_of(school_year, 2),
        'graduated': grade_of(school_year, 3),
    }


def _students(school_year, include_graduated=False):
    """取本学年学生（默认只取三个在校年级）。"""
    sc = grade_scope(school_year)
    grades = [sc['g1'], sc['g2'], sc['g3']]
    if include_graduated:
        grades.append(sc['graduated'])
    return Student.query.filter(Student.grade.in_(grades)).all()


def _boarding_map():
    """student_id → 是否住校（boarding_type='住校'）。"""
    rows = StudentAccommodation.query.all()
    return {r.student_id: (r.boarding_type == '住校') for r in rows}


# ── 教基2108 班额班数 ────────────────────────────────────────────────
def class_size(school_year):
    """返回 {'columns': [一年级, 二年级, 三年级], 'rows': [(档, {年级: 班数})], 'total': {...}}"""
    sc = grade_scope(school_year)
    cols = [sc['g1'], sc['g2'], sc['g3']]
    sts = _students(school_year)
    per_class = Counter((s.grade, s.class_name) for s in sts if s.class_name)
    table = OrderedDict((name, {c: 0 for c in cols}) for name, _lo, _hi in CLASS_SIZE_BANDS)
    total = {c: 0 for c in cols}
    for (grade, _cls), n in per_class.items():
        if grade not in total:
            continue
        total[grade] += 1
        for name, lo, hi in CLASS_SIZE_BANDS:
            if lo <= n <= hi:
                table[name][grade] += 1
                break
    return {
        'columns': cols,
        'grades': cols,
        'rows': [(name, table[name]) for name, _lo, _hi in CLASS_SIZE_BANDS],
        'total': total,
        'class_count': sum(total.values()),
        'student_count': len(sts),
    }


# ── 教基3118 分类型学生数 ────────────────────────────────────────────
def student_types(school_year):
    sc = grade_scope(school_year)
    sts = _students(school_year)
    boarding = _boarding_map()

    def _stat(rows):
        return {
            'total': len(rows),
            'female': sum(1 for s in rows if s.gender == '女'),
            'minority': sum(1 for s in rows if (s.ethnicity or '汉族') != '汉族'),
            'boarding': sum(1 for s in rows if boarding.get(s.id)),
        }

    by_grade = {g: [s for s in sts if s.grade == g] for g in (sc['g1'], sc['g2'], sc['g3'])}
    grads = Student.query.filter(Student.grade == sc['graduated']).all()
    return {
        'grades': sc,
        'enrolled': {k: _stat(v) for k, v in by_grade.items()},
        'enrolled_total': _stat(sts),
        'graduated': len(grads),
        'graduated_female': sum(1 for s in grads if s.gender == '女'),
        'graduates_expected': len(by_grade[sc['g3']]),
        'new_students': len(by_grade[sc['g1']]),
    }


# ── 教基3119 分年龄学生数 ────────────────────────────────────────────
def student_ages(school_year):
    ref = ref_date(school_year)
    sts = _students(school_year)
    rows = OrderedDict((name, {'new': 0, 'enrolled': 0, 'female': 0})
                       for name, _lo, _hi in STUDENT_AGE_BANDS)
    unknown = 0
    g1 = grade_of(school_year, 0)
    for s in sts:
        age = _age_on(s.id_card_number, ref)
        band = _band(age, STUDENT_AGE_BANDS)
        if band is None:
            unknown += 1
            continue
        rows[band]['enrolled'] += 1
        if s.gender == '女':
            rows[band]['female'] += 1
        if s.grade == g1:
            rows[band]['new'] += 1
    return {
        'rows': [(name, rows[name]) for name, _lo, _hi in STUDENT_AGE_BANDS],
        'totals': {
            'new': sum(c['new'] for c in rows.values()),
            'enrolled': sum(c['enrolled'] for c in rows.values()),
            'female': sum(c['female'] for c in rows.values()),
        },
        'unknown': unknown,
        'ref_date': ref,
    }


# ── 教基3045 在校生中其他情况 ────────────────────────────────────────
def student_other(school_year):
    sts = _students(school_year)
    minority = sum(1 for s in sts if (s.ethnicity or '汉族') != '汉族')
    female_minority = sum(1 for s in sts
                          if s.gender == '女' and (s.ethnicity or '汉族') != '汉族')
    return {
        'total': len(sts),
        'minority': minority,
        'female_total': sum(1 for s in sts if s.gender == '女'),
        'female_minority': female_minority,
        # 基表还要求党员/团员/民主党派/港澳台/华侨/残疾人 —— 系统无对应字段
        'unsupported': ['中共党员', '共青团员', '民主党派', '香港', '澳门',
                        '台湾', '华侨', '残疾人'],
    }


# ── 教师侧 ───────────────────────────────────────────────────────────
def _teachers():
    return Teacher.query.filter(Teacher.status == 'active').all()


def _t_gender(t):
    return t.gender or _gender_from_id(_t_id_card(t))


def _t_id_card(t):
    try:
        from app.utils.crypto import decrypt
        return decrypt(t.id_card_enc) if t.id_card_enc else None
    except Exception:  # noqa: BLE001
        return None


def _t_age(t, ref):
    """教师统计时点年龄：**出生日期优先**，没有才回退身份证解析。

    2026-10-20 用户口径（隐私）：教育统计只需要年龄，不必收身份证 ——
    只填生日即可满足 4153；身份证保留为选填（人事档案用途）。
    """
    bd = getattr(t, 'birth_date', None)
    if bd:
        try:
            return ref.year - bd.year - ((ref.month, ref.day) < (bd.month, bd.day))
        except (AttributeError, TypeError):
            pass
    return _age_on(_t_id_card(t), ref)


def _t_staff_type(t):
    """未填人员类别时按「是否有学科」推断：有学科=专任教师，否则=其他。"""
    if t.staff_type in STAFF_TYPES:
        return t.staff_type
    return '专任教师' if (t.subject or '').strip() else '其他'


def teacher_summary(school_year):
    ts = _teachers()
    by_type = Counter(_t_staff_type(t) for t in ts)
    return {
        'total': len(ts),
        'by_type': {k: by_type.get(k, 0) for k in STAFF_TYPES},
        'female': sum(1 for t in ts if _t_gender(t) == '女'),
        'minority': sum(1 for t in ts
                        if (t.ethnicity or '汉族') != '汉族'),
        'official': sum(1 for t in ts if t.is_official is True),
        'foreign': sum(1 for t in ts if t.is_foreign is True),
        'untagged_staff_type': sum(1 for t in ts if t.staff_type not in STAFF_TYPES),
    }


def teacher_ages(school_year):
    ref = ref_date(school_year)
    ts = _teachers()
    rows = OrderedDict(
        (name, {'total': 0, 'female': 0, 'minority': 0, 'titles': Counter()})
        for name, _lo, _hi in TEACHER_AGE_BANDS)
    title_totals = Counter()
    female_title_totals = Counter()
    unknown = 0
    for t in ts:
        age = _t_age(t, ref)
        band = _band(age, TEACHER_AGE_BANDS)
        if band is None:
            unknown += 1
            continue
        cell = rows[band]
        cell['total'] += 1
        if _t_gender(t) == '女':
            cell['female'] += 1
        if (t.ethnicity or '汉族') != '汉族':
            cell['minority'] += 1
        title = t.title or '未定职级'
        cell['titles'][title] += 1
        title_totals[title] += 1
        if _t_gender(t) == '女':
            female_title_totals[title] += 1
    return {
        'rows': [(name, rows[name]) for name, _lo, _hi in TEACHER_AGE_BANDS],
        'totals': {
            'total': sum(c['total'] for c in rows.values()),
            'female': sum(c['female'] for c in rows.values()),
            'minority': sum(c['minority'] for c in rows.values()),
        },
        # 各职称的人数（合计列）与其中女教师数（#女 列）—— 与样表的职称行一一对应
        'title_totals': {k: title_totals.get(k, 0) for k in TITLE_LEVELS},
        'female_title_totals': {k: female_title_totals.get(k, 0)
                                for k in TITLE_LEVELS},
        'unknown': unknown,
        'ref_date': ref,
    }


def teacher_by_subject(school_year):
    """按课程列 + 学历分档统计专任教师（与教基4156 的行列一一对应）。

    行：总计 / #女 / #少数民族 / 各学历；列：合计 / #女 / 各课程。
    说明：样表的「#女」「#少数民族」是**行**（各课程中的人数），
    与学历行的「#女」列不是同一口径，故这里单独算两份。
    """
    ts = [t for t in _teachers() if _t_staff_type(t) == '专任教师']
    cols = ['合计', '#女'] + SUBJECT_COLUMNS
    grid = OrderedDict((lv, {c: 0 for c in cols})
                       for lv in ['总计'] + EDU_LEVELS)
    female_row = {c: 0 for c in cols}
    minority_row = {c: 0 for c in cols}
    subject_fallback = 0
    for t in ts:
        raw = (t.subject or '').strip()
        col = SUBJECT_MAP.get(raw)
        if col is None:
            col = '其他'
            if raw:
                subject_fallback += 1
        lv = t.education if t.education in EDU_LEVELS else '未填学历'
        if lv not in grid:
            grid[lv] = {c: 0 for c in cols}
        for row_key in ('总计', lv):
            grid[row_key]['合计'] += 1
            if _t_gender(t) == '女':
                grid[row_key]['#女'] += 1
            grid[row_key][col] += 1
        if _t_gender(t) == '女':
            female_row['合计'] += 1
            female_row[col] += 1
        if (t.ethnicity or '汉族') != '汉族':
            minority_row['合计'] += 1
            minority_row[col] += 1
    rows = [('总计', grid['总计']), ('#女', female_row),
            ('#少数民族', minority_row)]
    rows += [(lv, grid[lv]) for lv in grid if lv != '总计']
    return {
        'columns': cols,
        'rows': rows,
        'unknown_education': sum(1 for t in ts
                                 if t.education not in EDU_LEVELS),
        'unmapped_subject': subject_fallback,
    }


# ── 数据质量提示（页面顶部用）────────────────────────────────────────
def data_quality(school_year):
    """返回各类"缺失属性"人数，供页面提示还需补录多少。"""
    sts = _students(school_year)
    ts = _teachers()
    ref = ref_date(school_year)
    return {
        'students_total': len(sts),
        'students_no_idcard': sum(1 for s in sts if _age_on(s.id_card_number, ref) is None),
        'students_no_ethnicity': sum(1 for s in sts if not (s.ethnicity or '').strip()),
        'teachers_total': len(ts),
        'teachers_no_birth': sum(1 for t in ts
                                 if _t_age(t, ref) is None),
        'teachers_no_idcard': sum(1 for t in ts if _age_on(_t_id_card(t), ref) is None),
        'teachers_no_subject': sum(1 for t in ts if not (t.subject or '').strip()),
        'teachers_no_education': sum(1 for t in ts
                                     if t.education not in EDU_LEVELS),
        'teachers_no_staff_type': sum(1 for t in ts
                                      if t.staff_type not in STAFF_TYPES),
        'teachers_no_gender': sum(1 for t in ts if not _t_gender(t)),
        'teachers_no_ethnicity': sum(1 for t in ts if not (t.ethnicity or '').strip()),
    }


def collect(school_year, asset_row=None):
    """一次算齐 6 张表 + 数据质量 + 校舍参数，供页面与导出一致使用。"""
    year = normalize_year(school_year)
    label = year_label(year)
    return {
        'school_year': label,
        'grades': grade_scope(year),
        'class_size': class_size(year),
        'student_types': student_types(year),
        'student_ages': student_ages(year),
        'student_other': student_other(year),
        'teacher_summary': teacher_summary(year),
        'teacher_ages': teacher_ages(year),
        'teacher_by_subject': teacher_by_subject(year),
        'quality': data_quality(year),
        'asset': asset_row,
    }
