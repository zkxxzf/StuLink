# StuLink v1.19.67.64 2026-10-10
# 成绩管理与可视化分析系统：数据模型（独立库 grades.db）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import json
import logging
import re
from datetime import date, datetime
from app.extensions import db
# 综合组六科（体育/音乐/美术/信息技术/通用技术/心理）的**唯一真值**在教务模型；
# 成绩侧只在「教师-学科映射可维护科目」（LINK_SUBJECTS）里引用它。
# 依赖方向：models.grades → models.academic 单向（academic 不反向 import grades 模型）。
from app.models.academic import COMPREHENSIVE_SUBJECTS

# 2026-10-10 架构审计：本模块解析 JSON 配置时原有静默 except，配置损坏时
# 会悄悄回落到默认值（页面照常显示，但满分/配置其实没生效）。用 debug 级留痕
# —— 属低频数据问题，不宜 warning 刷屏。
logger = logging.getLogger(__name__)

# ============ 科目常量（与字典 exam_subject 预置值保持一致） ============
SUBJECTS = ['语文', '数学', '外语', '物理', '历史', '化学', '生物', '政治', '地理']
TOTAL_SUBJECT = '总分'
# 2026-10-13 科目别名自动映射（**唯一维护点**，用户口径「英语→外语、日语→外语」等）。
# 用户粘贴 / 导入 Excel / 手填时习惯写「英语」「日语」等，实际都指系统的标准科「外语」；
# 此前各模块各写一份 {'英语': '外语'}，日语/俄语等小语种一律被判非法（整行丢弃/列被忽略）。
# 口径：先归一、再过白名单；**未命中别名时原样返回** —— 不认识的值仍会被白名单挡下并给
# 用户明确报错，绝不把未知值悄悄变成合法值。
SUBJECT_ALIAS = {
    # 外语系：小语种统一归到系统标准科「外语」
    '英语': '外语', '日语': '外语', '俄语': '外语', '德语': '外语', '法语': '外语',
    '西班牙语': '外语', '韩语': '外语', '朝鲜语': '外语', '意大利语': '外语',
    '葡萄牙语': '外语', '阿拉伯语': '外语',
    # 政治系
    '思想政治': '政治', '思想品德': '政治', '思品': '政治',
    '道法': '政治', '道德与法治': '政治',
    # 常见单字简称（教务 / 粘贴表格里很常见）
    '语': '语文', '数': '数学', '英': '外语', '物': '物理', '化': '化学',
    '生': '生物', '政': '政治', '史': '历史', '地': '地理',
}


def normalize_subject(name):
    """科目名归一：去首尾空白（含全角空格）→ 查别名表 → 返回标准科名。

    2026-10-13：各导入 / 手填 / 粘贴入口统一调用它，再各自做白名单校验。
    未命中别名时**原样返回**；None 返回 ''（同样会被白名单挡下，不抛异常）。
    """
    if name is None:
        return ''
    t = str(name).replace('\u3000', ' ').strip()
    return SUBJECT_ALIAS.get(t, t)
# 默认满分：语数外 150，其余 100（河南 3+1+2）
DEFAULT_FULL_MARKS = {s: (150 if s in ('语文', '数学', '外语') else 100) for s in SUBJECTS}

# 教师-学科映射（TeacherSubjectLink）可维护的科目集合 —— 2026-10-15 用户口径
# 「教师映射扩容，增加综合组」：成绩科目 9 科 + 综合组 6 科（体育/音乐/美术/信息技术/
# 通用技术/心理，各占一列，页面上归到「综合组」分组带，与学科组长页同一份口径）。
# ⚠ 成绩链路仍以 SUBJECTS（9 科）为准：成绩导入白名单 / 分析报表 / 分数线 /
#   DEFAULT_FULL_MARKS / AI 成绩口径**一律不扩** —— 六科只在「任课映射维护 +
#   课表导入核对」里流动（成绩表里本就没有这些科目）。
LINK_SUBJECTS = SUBJECTS + list(COMPREHENSIVE_SUBJECTS)

# 考务④「核对任课教师」的两组口径（2026-10-21 用户口径；唯一真值放模型层，
# 供考务路由与分析侧的 teacher_snapshot_service 共用，避免两边各写一份）：
#   · AFFAIR_HEAD_SUBJECTS：班主任三列＝**伪科目**，只存在 affair_teachers 表，
#     绝不写回 TeacherSubjectLink、也不进成绩科目字典；
#     **2026-10-21 合并 master 后的新口径是「正班 / 副班1 / 副班2」**
#     （见 teacher_snapshot_service.HOMEROOM_POSITIONS）；本常量保留为**历史别名**，
#     仅用于读取旧库数据与迁移改名，新代码不要再用它写库；
#   · AFFAIR_EXCLUDED_SUBJECTS：考务侧不看的综合组科目（本校不带「通用技术」；
#     任课映射页 / 导入模板仍保留它，故只排除考务列）。
AFFAIR_HEAD_SUBJECTS = ('正班主任', '副班主任1', '副班主任2')
AFFAIR_EXCLUDED_SUBJECTS = ('通用技术',)

# 选科组合字符 → 科目全名（用于由选科组合推断应考科目）
_SEL_CHAR_MAP = {'物': '物理', '化': '化学', '生': '生物', '政': '政治', '史': '历史', '地': '地理'}
_SUBJECT_FULL = {'语文', '数学', '外语', '物理', '历史', '化学', '生物', '政治', '地理'}


def _normalize_subjects(names):
    """把一组科目名（简称/全称混用）规范成系统科目列表：语数外打底，去重保序。"""
    out = []
    for n in names:
        n = (n or '').strip()
        if not n:
            continue
        if n in _SUBJECT_FULL:
            out.append(n)
        elif n in _SEL_CHAR_MAP:
            out.append(_SEL_CHAR_MAP[n])
    seen = set()
    result = []
    for s in (['语文', '数学', '外语'] + out):
        if s not in seen:
            seen.add(s)
            result.append(s)
    return result or None


def subjects_of_selection(selection):
    """由选科组合推断应考科目集合（语数外 + 方向科 + 再选科）。

    支持的写法：
    - 3+1+2 简称拼接：物化生 / 史政地 / 物化地 …（首字为 物理/历史）
    - 科目全称分隔：物理、化学、生物（顿号/逗号/空格）
    - 文理：理科/理 → 语数外+物化生；文科/文 → 语数外+政史地
    - 空 / 不分科 / 全科 / 统一 → 全部 9 科（默认口径，统一考试）
    无法识别返回 None（调用方按「实际有成绩」兜底）
    """
    if not selection:
        return list(SUBJECTS)   # 未填选科 = 不分科，默认全部 9 科
    sel = str(selection).strip()
    if not sel:
        return list(SUBJECTS)
    if sel in ('理科', '理', '理科生'):
        return ['语文', '数学', '外语', '物理', '化学', '生物']
    if sel in ('文科', '文', '文科生'):
        return ['语文', '数学', '外语', '历史', '政治', '地理']
    if sel in ('不分科', '全科', '全', '全部', '统一', '通用'):
        return list(SUBJECTS)
    # 科目全称分隔（顿号/逗号/空格）
    if any(sep in sel for sep in ('、', '，', ',', ' ')):
        return _normalize_subjects(re.split(r'[、，,\s]+', sel))
    # 3+1+2 简称拼接
    names = [_SEL_CHAR_MAP.get(ch) for ch in sel]
    if not names or any(n is None for n in names):
        return None
    direction_subject = names[0]
    if direction_subject not in ('物理', '历史'):
        return None
    return ['语文', '数学', '外语', direction_subject] + names[1:]


# ============ 成绩认定证明（纸质模板）排版口径 ============
# 模板科目顺序：语数英 / 物化生 / 政史地（与 SUBJECTS 的分析顺序不同，仅用于证明排版）
CERT_SUBJECT_ORDER = ['语文', '数学', '外语', '物理', '化学', '生物', '政治', '历史', '地理']
# 对外文书展示名：系统科目名 外语，纸质证明按校方模板写作 英语
SUBJECT_DISPLAY = {'外语': '英语'}


def subject_display(subject):
    """成绩证明等对外文书使用（外语→英语），其余沿用系统科目名"""
    return SUBJECT_DISPLAY.get(subject, subject)


# ============ 年级学期（高一上 … 高三下）口径 ============
# 2026-10-20 用户口径（考试列表把「（高三上）」做成标签、创建考试可选）：
#   ① 学年以 8 月 1 日为界：2026 级在 2026-08-01 ~ 2027-07-31 都算高一，
#      2027-08-01 起才是高二（term 学年同步从 8/1 起算）；
#   ② 学期以春节为界：春节（正月初一）前都是上学期，春节起算下学期
#      —— 所以 2024级 2026-02-06 的期末考仍属「高二上」（2026 春节是 2/17），
#      旧口径「2 月起=下学期」会把它误判成高二下。
GRADE_SEMESTERS = ('高一上', '高一下', '高二上', '高二下', '高三上', '高三下')

# 春节（农历正月初一）的公历日期。只用于判定「春节前 = 上学期」，
# 内置表避免引入农历库；表外年份回落到 2 月 1 日（考试数据都在近些年）。
SPRING_FESTIVAL = {
    2015: (2, 19), 2016: (2, 8), 2017: (1, 28), 2018: (2, 16), 2019: (2, 5),
    2020: (1, 25), 2021: (2, 12), 2022: (2, 1), 2023: (1, 22), 2024: (2, 10),
    2025: (1, 29), 2026: (2, 17), 2027: (2, 6), 2028: (1, 26), 2029: (2, 13),
    2030: (2, 3), 2031: (1, 23), 2032: (2, 11), 2033: (1, 31), 2034: (2, 19),
    2035: (2, 8), 2036: (1, 28), 2037: (2, 15), 2038: (2, 4), 2039: (1, 24),
    2040: (2, 12), 2041: (2, 1), 2042: (1, 22), 2043: (2, 10), 2044: (1, 30),
    2045: (2, 17),
}


def spring_festival(year):
    """year 年春节（正月初一）的公历日期；表外年份回落 2 月 1 日"""
    try:
        month, day = SPRING_FESTIVAL.get(int(year), (2, 1))
        return date(int(year), month, day)
    except (TypeError, ValueError):
        return date(2000, 2, 1)


def term_of_date(d):
    """由日期推导学年，如 2025-09-01 → '2025-2026'（8 月 1 日起算新学年）"""
    if not d:
        return ''
    if d.month >= 8:
        return f'{d.year}-{d.year + 1}'
    return f'{d.year - 1}-{d.year}'


def semester_of(exam_date):
    """考试日期 → 学期：春节前=上学期，春节起~7 月底=下学期"""
    if not exam_date:
        return ''
    if exam_date.month >= 8:        # 8 月起是本学年上半段，必定上学期
        return '上学期'
    return '上学期' if exam_date < spring_festival(exam_date.year) else '下学期'


def grade_level_label(grade, term):
    """学年起始年 - 入学年 + 1 → 高一/高二/高三；无法推算返回空串"""
    try:
        enroll = int(''.join(ch for ch in str(grade) if ch.isdigit())[:4])
        start = int(str(term).split('-')[0])
        level = start - enroll + 1
    except (AttributeError, IndexError, TypeError, ValueError):
        return ''
    return {1: '高一', 2: '高二', 3: '高三'}.get(level, '')


def exam_semester(grade, exam_date):
    """年级 + 考试日期 → 「高三上」这类学期标签；推算不出返回空串"""
    level = grade_level_label(grade, term_of_date(exam_date))
    half = {'上学期': '上', '下学期': '下'}.get(semester_of(exam_date), '')
    return f'{level}{half}' if (level and half) else ''


def _default_config():
    return json.dumps({
        'band_width': 30,
        'rank_bands': [10, 20, 30, 50, 100, 200, 300],
        'subject_lines': {},   # {科目: {excellent: , pass: , low: }} 覆盖，缺省按满分 90%/60%/40%
    }, ensure_ascii=False)


# ============ 考试两个维度（字典 code ）============
# 性质：谁组织的、比对范围多大 —— 决定有没有外部（全市/多校）数据
EXAM_KINDS = ('联考', '本校考', '小测验')
# 阶段：教学进度节点 —— 用于“上一场同阶段考试”的定位（分层依据）
EXAM_STAGES = ('期末考', '期中考', '月考', '限时练', '高三一测', '高三二测')
# 分数口径（v1.19.67.64）：**仅作标记**，不参与导入解析/排名/划线口径判断
# （口径在导入时按数据确定：有赋分进主分，只给原始分时原始分顶上主分）。
# 允许留空 = 未指定，由导入时的文件决定。
SCORE_MODES = ('converted', 'raw')
SCORE_MODE_LABELS = {'converted': '赋分', 'raw': '原始分'}
SCORE_MODE_AUTO_LABEL = '按导入确定'      # 未指定（score_mode 为空）时的展示名


class Exam(db.Model):
    """考试主档：一场考试 = 一个年级 × 一次

    v1.19.0 四段式重构：新增两个维度
      exam_kind  考试性质（联考 / 本校考 / 小测验）
      exam_type  考试阶段（期末考 / 期中考 / 月考 / 限时练 / 高三一测 / 高三二测）
    以及 score_mode（分数口径**标记**——v1.19.67.64 起允许不选；见字段注释）。
    """
    __bind_key__ = 'grades'
    __tablename__ = 'exams'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    exam_type = db.Column(db.String(20))            # 字典 exam_type（阶段：月考/期中/期末/限时练…）
    # v1.19.0 考试性质（字典 exam_kind：联考/本校考/小测验）
    exam_kind = db.Column(db.String(20))
    # 分数口径标记：converted=赋分 / raw=原始分 / 空=未指定（默认，导入时按文件确定）
    # v1.19.67.64 起允许留空。注意它只是"标记"：导入解析、排名、划线都不读它——
    # exam_scores.score 在导入时已按「有赋分进主分、只给原始分则原始分顶上」定型。
    score_mode = db.Column(db.String(10), default='')
    grade = db.Column(db.String(10), nullable=False)  # 2024级/2025级…（与字典一致）
    exam_date = db.Column(db.Date, nullable=False)
    term = db.Column(db.String(20))                  # 学年 2025-2026，由日期推导或登记选择
    # 年级学期标签（高一上…高三下）：空 = 按 grade + exam_date 自动推算，
    # 非空 = 手工指定（跨年级/复读等例外），见 semester_label()。
    # 2026-10-20 新增：此前这个信息只写在 name 文本的末尾括号里。
    grade_semester = db.Column(db.String(10), default='')
    config_json = db.Column(db.Text, default=_default_config)  # 分段宽/名次段/科目线覆盖
    status = db.Column(db.String(10), default='draft', nullable=False)  # draft/imported/dirty
    import_draft = db.Column(db.Text)                # 上传预检结果快照（JSON，确认导入用）
    import_token = db.Column(db.String(36))          # 预检批次号（防串批）
    import_info = db.Column(db.Text)                 # 最近导入批次摘要 JSON
    operator_id = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    def kind_label(self):
        """性质展示名（未填返回空）"""
        return (self.exam_kind or '').strip()

    def stage_label(self):
        """阶段展示名（回落到旧的 exam_type）"""
        return (self.exam_type or '').strip()

    def score_mode_label(self):
        """分数口径展示名：赋分 / 原始分 / 按导入确定（未指定）

        v1.19.67.64：未指定显示「按导入确定」，不再回落成「赋分」——
        否则老师没选却被显示成"赋分"，与本场实际口径不符。
        """
        return SCORE_MODE_LABELS.get((self.score_mode or '').strip(), SCORE_MODE_AUTO_LABEL)

    def semester_label(self):
        """年级学期标签（如「高三上」）：优先手工值，否则按年级+日期推算

        推算不出（年级/日期异常，或届数超出高一~高三）返回空串。
        """
        fixed = (self.grade_semester or '').strip()
        if fixed in GRADE_SEMESTERS:
            return fixed
        return exam_semester(self.grade, self.exam_date)

    def display_name(self):
        """去掉名称末尾与学期标签重复的「（高三上）」

        历史考试的年级学期都写在名称末尾括号里；列表把学期单独做成标签后，
        名称列再带一遍就重复了。只删与标签**完全一致**的那个后缀，
        括号里是别的说明（如「（补考）」）时原样保留。
        """
        sem = self.semester_label()
        if sem:
            suffix = f'（{sem}）'
            if (self.name or '').endswith(suffix):
                return self.name[:-len(suffix)]
        return self.name

    def title_with_dimensions(self):
        """“2024级·联考·月考·2026-10-10”式完整标识（缺项自动跳过）"""
        parts = [self.grade, self.kind_label(), self.stage_label()]
        if self.exam_date:
            parts.append(self.exam_date.strftime('%Y-%m-%d'))
        return '·'.join(p for p in parts if p)

    def get_config(self):
        try:
            cfg = json.loads(self.config_json or '{}')
        except (json.JSONDecodeError, TypeError) as _e:
            # 2026-10-10：此处返回空 dict 会让**该考试的 band_width / full_marks /
            # rank_bands 等全部配置静默失效**（页面照常渲染，数值却不是用户设的）。
            # 属数据损坏，用 warning 留痕（降级行为不变）。
            logger.warning('考试 %s 的 config_json 解析失败，本次全部配置回落默认值: %s',
                           self.id, _e)
            cfg = {}
        return cfg

    def set_config(self, cfg):
        self.config_json = json.dumps(cfg, ensure_ascii=False)

    def band_width(self):
        cfg = self.get_config()
        try:
            return int(cfg.get('band_width', 30))
        except (TypeError, ValueError) as _e:
            logger.debug('考试 %s 的 band_width 配置非法（%r），沿用默认 30: %s',
                         self.id, cfg.get('band_width'), _e)
            return 30

    def rank_bands(self):
        cfg = self.get_config()
        rb = cfg.get('rank_bands')
        if isinstance(rb, list):
            return [int(x) for x in rb]
        return [10, 20, 30, 50, 100, 200, 300]

    def subject_lines(self, subject):
        """返回该科目三线 dict {excellent, pass, low}（缺省 90%/60%/40% 满分）"""
        fm = self.full_marks().get(subject, 100)
        try:
            sl = (self.get_config().get('subject_lines') or {}).get(subject) or {}
        except AttributeError as _e:
            # 配置结构异常（subject_lines 不是 dict 类型）→ 回落按满分比例（行为不变）
            logger.debug('考试 %s 的 subject_lines 配置结构异常（科目 %s）: %s',
                         self.id, subject, _e)
            sl = {}
        return {
            'excellent': sl.get('excellent', fm * 0.9),
            'pass': sl.get('pass', fm * 0.6),
            'low': sl.get('low', fm * 0.4),
        }

    def class_trim(self):
        """本场考试的「去差人数」按班覆盖：{班级: 去差人数}

        2026-10-21 用户口径：数据导入页可按班级调整去差人数（默认卓越班 2、强基班 0）。
        存在 config_json，避免加列迁移；键为班级名（01班…），值允许为 0（显式不去差）。
        """
        raw = self.get_config().get('class_trim') or {}
        out = {}
        if isinstance(raw, dict):
            for k, v in raw.items():
                try:
                    n = int(v)
                except (TypeError, ValueError):
                    continue
                if n >= 0:
                    out[str(k)] = n
        return out

    def set_class_trim(self, mapping):
        """写入按班去差人数（整份覆盖；只保留非负整数）"""
        clean = {}
        for k, v in (mapping or {}).items():
            try:
                n = int(v)
            except (TypeError, ValueError):
                continue
            if n >= 0:
                clean[str(k)] = n
        cfg = self.get_config()
        cfg['class_trim'] = clean
        self.set_config(cfg)

    def band_template_id(self):
        """本场考试绑定的分层模板 id（未绑定返回 None）"""
        return self.get_config().get('band_template_id')

    def band_template_name(self):
        return self.get_config().get('band_template_name') or ''

    def set_band_template(self, template_id, template_name=''):
        """绑定分层模板（存 config_json，避免加列迁移）"""
        cfg = self.get_config()
        if template_id:
            cfg['band_template_id'] = template_id
            cfg['band_template_name'] = template_name or ''
        else:
            cfg.pop('band_template_id', None)
            cfg.pop('band_template_name', None)
        self.set_config(cfg)

    def full_marks(self):
        """科目满分 dict（默认常量，可按考试覆盖）"""
        cfg = self.get_config()
        fm = dict(DEFAULT_FULL_MARKS)
        over = cfg.get('full_marks') or {}
        for k, v in over.items():
            if k in fm and v:
                try:
                    fm[k] = float(v)
                except (TypeError, ValueError) as _e:
                    # 配置值非法 → 保留默认满分（行为不变），仅 debug 留痕
                    logger.debug('考试 %s 的科目满分配置非法（%s=%r），沿用默认值: %s',
                                 self.id, k, v, _e)
        return fm

    __table_args__ = (
        db.Index('idx_exam_grade_date', 'grade', 'exam_date'),
    )


class ExamScore(db.Model):
    """成绩明细：一学生一科目一行（含伪科目 总分 行），快照冗余学生属性"""
    __bind_key__ = 'grades'
    __tablename__ = 'exam_scores'

    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, nullable=False)
    student_no = db.Column(db.String(20), nullable=False)   # 学号快照（主库匹配键）
    student_name = db.Column(db.String(50))                 # 姓名快照
    grade = db.Column(db.String(10))
    class_name = db.Column(db.String(10))                   # 班级快照（换班不影响历史）
    # v1.19.0 考号快照（联考等第三方编排的号，第三方未给时为空）
    exam_no = db.Column(db.String(20))
    # v1.19.67.64 班型快照（强基班/卓越班…）：导入时取自班型设置，使去差均分等分析
    # 永远用「考试当时」的班型；此前实时读 ClassProfile，班型一改历史全部失真
    class_type = db.Column(db.String(20))
    direction = db.Column(db.String(4))                     # 物理/历史 快照
    subject_selection = db.Column(db.String(10))            # 选科组合快照
    subject = db.Column(db.String(10), nullable=False)      # 9 科之一 或 总分
    # v1.19.0 score=本场口径分（赋分优先；无赋分时就是原始分），划线/排名/分析均用它
    score = db.Column(db.Float)                             # NULL=无成绩
    # v1.19.0 原始分：仅在“赋分与原始两者都给”时填入，供查看原始分，不参与排名
    raw_score = db.Column(db.Float)
    rank_class = db.Column(db.Integer)                      # 班排名（同方向）
    rank_dir = db.Column(db.Integer)                        # 方向排名
    move_rank = db.Column(db.Integer)                       # 仅总分行：进退步
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        db.UniqueConstraint('exam_id', 'student_no', 'subject', name='uq_exam_stu_subject'),
        db.Index('idx_scores_exam_subject', 'exam_id', 'subject'),
        db.Index('idx_scores_exam_subject_class', 'exam_id', 'subject', 'class_name'),
        db.Index('idx_scores_exam_class_subject', 'exam_id', 'class_name', 'subject'),
        db.Index('idx_scores_exam_stu', 'exam_id', 'student_no'),
        # v1.19.0 考号查询（准考证/考场表回溯）
        db.Index('idx_scores_exam_no', 'exam_id', 'exam_no'),
        # v1.13.2 性能：学生维度查询（查该生全部总分考试，不带 exam_id）此前全表扫描
        # 31 万行；覆盖索引 (student_no, subject, exam_id) 让查询只走索引
        db.Index('idx_scores_stu_subject_exam', 'student_no', 'subject', 'exam_id'),
    )


class ExamBand(db.Model):
    """分层配置：考试 × 方向 × [选科组合] × 学科 × 有序层（seq=1 最高层，无上界）

    subject='总分' 为总分线（原行为）；subject=学科名 为该科单科线，
    两者独立配置，故同一学科在物理/历史方向下可有不同分数线。
    2026-10-15 组合维度：selection 非空 = 该选科组合（如 物化生）自己的线
    （组合总分线挂在所属方向下，direction 必填）；空 = 按方向/全体（原口径）。
    判层（band_of_score / band_list）按学生自身组合取线，组合线优先，
    无组合线回落方向线（stats_service）。
    """
    __bind_key__ = 'grades'
    __tablename__ = 'exam_bands'

    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, nullable=False)
    direction = db.Column(db.String(4), default='')   # 空串=双向套用
    selection = db.Column(db.String(10), nullable=False, default='')  # 选科组合（物化生…）；空=按方向
    subject = db.Column(db.String(10), nullable=False,
                        default=TOTAL_SUBJECT)        # 总分 或 9 科之一
    seq = db.Column(db.Integer, nullable=False)       # 1=最高层
    name = db.Column(db.String(20), nullable=False)   # 层名（优秀/良好/及格/待提升…）
    lower_mode = db.Column(db.String(8), nullable=False, default='score')  # score/ratio
    lower_value = db.Column(db.Float, nullable=False)  # 下界分数或比例(0~100)

    __table_args__ = (
        db.UniqueConstraint('exam_id', 'direction', 'selection', 'subject', 'seq',
                            name='uq_band_seq'),
    )


class BandTemplate(db.Model):
    """分层模板：可复用的层名序列（含默认下界方式与比例）

    考试与模板绑定（存在 Exam.config_json.band_template_id）：
    一场考试选定模板后，它的分层与后续成绩分析都按该模板的层来算；
    换一场考试可以选另一个模板（如期末用「高考线」、限时练用「四层」）。
    未绑定模板 / 未划线的考试，分析只做基础数据展示，不出分层与上线类指标。
    """
    __bind_key__ = 'grades'
    __tablename__ = 'band_templates'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False, unique=True)
    lower_mode = db.Column(db.String(8), nullable=False, default='score')   # score/ratio
    layers_json = db.Column(db.Text, nullable=False)   # [{"name":"一本","ratio":20}] seq 1 最高层
    # 适用考试类型（如 期末/月考/限时练）：为空=通用。用于按考试类型自动默认匹配模板
    exam_types_json = db.Column(db.Text)
    remark = db.Column(db.String(100))
    is_builtin = db.Column(db.Boolean, default=False)  # 内置模板：不允许删除
    sort_order = db.Column(db.Integer, default=0)
    created_by = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    def get_layers(self):
        try:
            val = json.loads(self.layers_json or '[]')
        except (json.JSONDecodeError, TypeError):
            return []
        return val if isinstance(val, list) else []

    def set_layers(self, layers):
        self.layers_json = json.dumps(layers, ensure_ascii=False)

    def names(self):
        """有序层名（seq 1 最高层）"""
        return [str(x.get('name', '')).strip() for x in self.get_layers()
                if str(x.get('name', '')).strip()]

    def get_exam_types(self):
        """适用考试类型列表；空列表=通用（匹配任意考试）"""
        try:
            val = json.loads(self.exam_types_json or '[]')
        except (json.JSONDecodeError, TypeError):
            return []
        return [str(x).strip() for x in val if str(x).strip()] if isinstance(val, list) else []

    def set_exam_types(self, types):
        self.exam_types_json = json.dumps(types or [], ensure_ascii=False)


class TeacherSubjectLink(db.Model):
    """任课教师-班级-科目映射（按年级分设，一科一班一师）"""
    __bind_key__ = 'grades'
    __tablename__ = 'teacher_subject_links'

    id = db.Column(db.Integer, primary_key=True)
    grade = db.Column(db.String(10), nullable=False)
    class_name = db.Column(db.String(10), nullable=False)
    subject = db.Column(db.String(10), nullable=False)
    user_id = db.Column(db.Integer, nullable=False)
    active = db.Column(db.Boolean, default=True)
    created_by = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        db.UniqueConstraint('grade', 'class_name', 'subject', name='uq_link_class_subject'),
        db.Index('idx_link_user', 'user_id'),
        db.Index('idx_link_grade', 'grade'),
    )


# ── 任课映射方案（2026-10-14）────────────────────────────────────────────────
# 为什么需要：高中每学期任课安排都会变（分班 / 换老师 / 学科组调整），而
# TeacherSubjectLink 只存「当前」一套，改完就没法回看上一学期谁教哪个班。
# 「方案」＝某个学期（或某次调整）的整套任课映射留档 + 一个明确的「当前生效」。
#
# 关键取舍（重要，勿改）：
# - **teacher_subject_links 仍是"当前生效映射"的唯一运行真值**，语义与列都不动 ——
#   成绩 / 教务 / 考务 / 工作台 / MCP 等 20+ 处读取点零改动（它们只认 active=True）；
# - 每个方案（含当前方案）在 teacher_link_plan_rows 里各存一份完整快照，
#   「使用某方案」＝把该方案快照物化回主表（服务层 link_plan_service 负责）；
# - is_current=True 全局至多一个（服务层保证），页面据此显示/编辑"当前方案"。

class TeacherLinkPlan(db.Model):
    """任课映射方案（学年 × 学期 / 自定义名称，可任意多个）"""
    __bind_key__ = 'grades'
    __tablename__ = 'teacher_link_plans'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)      # 方案名（如「2026-2027学年 第一学期」）
    school_year = db.Column(db.String(20), default='')   # 如 2026-2027（可空＝未标注）
    term = db.Column(db.String(10), default='')          # ''=整学年 / 第一学期 / 第二学期
    note = db.Column(db.String(200), default='')         # 说明（可空）
    is_current = db.Column(db.Boolean, default=False, index=True)  # 当前生效方案（至多一个）
    created_by = db.Column(db.Integer)                   # users.id
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    # 2026-10-14 第四轮审计（DATA3-01）：至多一个「当前方案」由 DB 级**部分唯一索引**兜底
    # （服务层循环翻转只保正常路径；手工改库 / 并发提交仍可能留下两个 is_current=1）
    __table_args__ = (
        db.Index('uq_link_plan_current', 'is_current', unique=True,
                 sqlite_where=db.text('is_current = 1')),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'school_year': self.school_year or '',
            'term': self.term or '',
            'term_text': self.term or '整学年',
            'note': self.note or '',
            'is_current': bool(self.is_current),
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M') if self.created_at else '',
            'updated_at': self.updated_at.strftime('%Y-%m-%d %H:%M') if self.updated_at else '',
        }


class TeacherLinkPlanRow(db.Model):
    """任课映射方案明细（方案 × 年级 × 班级 × 科目 → 教师账号快照）"""
    __bind_key__ = 'grades'
    __tablename__ = 'teacher_link_plan_rows'

    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(db.Integer, nullable=False, index=True)
    grade = db.Column(db.String(10), nullable=False)
    class_name = db.Column(db.String(10), nullable=False)
    subject = db.Column(db.String(10), nullable=False)
    user_id = db.Column(db.Integer, nullable=False)

    __table_args__ = (
        db.UniqueConstraint('plan_id', 'grade', 'class_name', 'subject',
                            name='uq_plan_link_cell'),
    )


class ExamTeacherLink(db.Model):
    """v1.19.67.64 考试任课快照：考试 × 班级 × 科目的**当时**任课教师

    为什么需要：TeacherSubjectLink 只存"当前"映射，教师一换（尤其分班后）
    历史考试的教师维度分析会全部错位。本表把每场考试的任课教师映射定格，
    分析读快照而非当前映射，保证「不重新分班时才可比」的口径可落地。

    source 可信度：import=成绩导入时快照（可信）｜excel=从旧成绩 Excel 任课表提取
                 ｜backfill=用当前映射回填（推测）｜manual=人工修订
    """
    __bind_key__ = 'grades'
    __tablename__ = 'exam_teacher_links'

    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, nullable=False)
    grade = db.Column(db.String(10), nullable=False)
    class_name = db.Column(db.String(10), nullable=False)
    subject = db.Column(db.String(10), nullable=False)
    user_id = db.Column(db.Integer)                 # 关联 users.id（可空：历史教师已离职/未建号）
    teacher_name = db.Column(db.String(50))         # 姓名快照（防改名/删号后显示为空）
    source = db.Column(db.String(10), default='import')
    snapshot_at = db.Column(db.DateTime, default=datetime.now)

    __table_args__ = (
        db.UniqueConstraint('exam_id', 'class_name', 'subject', name='uq_exam_cls_subj'),
        db.Index('idx_exam_teacher_exam', 'exam_id'),
    )


class AiKey(db.Model):
    """个人 AI API Key（仅本人可见；AES 随机 IV 加密存储）"""
    __bind_key__ = 'grades'
    __tablename__ = 'ai_keys'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False, unique=True)
    provider = db.Column(db.String(20), default='deepseek')   # deepseek / custom
    base_url = db.Column(db.String(200), default='')          # 自定义 OpenAI 兼容地址（空=官方）
    model = db.Column(db.String(50), default='deepseek-chat')
    api_key_enc = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)


class AiGlobalKey(db.Model):
    """全局公共 AI Key（管理员配置，个人未配置时兜底；固定单行 id=1）"""
    __bind_key__ = 'grades'
    __tablename__ = 'ai_global_keys'

    id = db.Column(db.Integer, primary_key=True)
    provider = db.Column(db.String(20), default='deepseek')
    base_url = db.Column(db.String(200), default='')
    model = db.Column(db.String(50), default='deepseek-chat')
    api_key_enc = db.Column(db.Text)
    operator_id = db.Column(db.Integer)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)


class AiReport(db.Model):
    """AI 报告历史（生成者本人可见，管理员可见全部）"""
    __bind_key__ = 'grades'
    __tablename__ = 'ai_reports'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)
    exam_id = db.Column(db.Integer, nullable=False)
    prev_exam_id = db.Column(db.Integer)
    grade = db.Column(db.String(10))
    scope_desc = db.Column(db.String(100))     # 发送范围说明（全校/本班/本人任课…）
    scope_students = db.Column(db.Integer)     # 发送学生数
    provider = db.Column(db.String(20))
    model = db.Column(db.String(50))
    content = db.Column(db.Text)               # 报告原文
    charts = db.Column(db.Text)                # 图表数据（JSON，本地统计，随报告固化）
    # 2026-10-13「AI 一句话分析」：kind=md（旧口径 Markdown 报告）/ html（工具调用后
    # 直接产出的 HTML 片段，已由 app/utils/html_guard.py 白名单消毒，前端直插）；
    # trace 记录这轮的工具调用轨迹（JSON：[{tool,args,ok,summary,ms}]），便于回溯数据来源。
    kind = db.Column(db.String(10), default='md')
    trace = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.now)

    __table_args__ = (
        db.Index('idx_ai_report_user', 'user_id'),
        db.Index('idx_ai_report_exam', 'exam_id'),
    )


class AiChatMessage(db.Model):
    """AI 多轮对话消息（按 用户+考试 组织上下文）"""
    __bind_key__ = 'grades'
    __tablename__ = 'ai_chat_messages'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)
    exam_id = db.Column(db.Integer, nullable=False)
    role = db.Column(db.String(10), nullable=False)      # user / assistant
    content = db.Column(db.Text)
    provider = db.Column(db.String(20))
    model = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.now)

    __table_args__ = (
        db.Index('idx_ai_chat_user_exam', 'user_id', 'exam_id'),
    )


class AiTask(db.Model):
    """AI 一句话分析任务（服务端持久化）。

    为什么落库：生成一次报告要跑多轮工具调用（几十秒~几分钟），
    若只靠"一次 SSE 请求直通"，用户切页/刷新就断了、看不到进度也找不到结果。
    现在改成：**提问即建任务**（本表）→ 后台线程执行 → 进度事件进进程内缓冲
    并可随时（重新）订阅 → 完成后写 `AiReport` 并回填 `report_id`。

    status:
      running     进行中（`heartbeat` 心跳，进程内 `_RUNNING` 集合用于识别僵尸）
      done        已完成（`report_id` 指向 ai_reports）
      error       失败（`error` 是给用户看的原因）
      interrupted 服务重启/进程丢失导致的中断（可重新提问）
    """
    __bind_key__ = 'grades'
    __tablename__ = 'ai_tasks'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)
    exam_id = db.Column(db.Integer, nullable=False)
    # 2026-10-14 提示词生成器：question 就是最终提示词（可能由生成器拼装、可手动编辑），
    # 500 字太短（生成器模板本身约 200~400 字 + 附加要求），放宽到 2000。
    question = db.Column(db.String(2000), nullable=False)
    # gen = 提示词生成器的**结构化选项** JSON（{grades,classes,subjects,focus,output,extra,prompt}）。
    # 为什么与 question 分开存：question 是"最终发出去的那段话"（可能被手动改过），
    # gen 是"当时选了哪些维度" —— 中断/失败后「重新编辑提示词」靠它无损回填表单。
    gen = db.Column(db.Text, default='')
    direction = db.Column(db.String(10), default='')
    layer = db.Column(db.String(30), default='')
    status = db.Column(db.String(12), default='running')
    stage = db.Column(db.String(200), default='')        # 最近一条阶段文案（列表里直接显示）
    error = db.Column(db.String(500), default='')
    tool_count = db.Column(db.Integer, default=0)
    report_id = db.Column(db.Integer)                    # 完成后指向 ai_reports.id
    trace = db.Column(db.Text)                           # 工具调用轨迹（JSON，与报告同一份）
    heartbeat = db.Column(db.DateTime, default=datetime.now)   # 心跳：识别僵尸任务
    created_at = db.Column(db.DateTime, default=datetime.now)
    finished_at = db.Column(db.DateTime)

    __table_args__ = (
        db.Index('idx_ai_task_user', 'user_id'),
        db.Index('idx_ai_task_exam', 'exam_id'),
        db.Index('idx_ai_task_status', 'status'),
    )


class AiTrash(db.Model):
    """AI 分析记录的删除归档（2026-10-18）。

    背景：用户误点"清空"把全部 AI 分析一次删光，而报告正文只存在 ai_reports 里
    （重新生成要几十秒~几分钟，结论也不会完全一致；数据库备份是按天/小时的，当天的
    记录覆盖不到）。所以删除前把**整行**序列化存到这里，几 KB 换一次可恢复。

    kind: report / task；payload: 原表整行 JSON（datetime 已转 isoformat）；
    restored_at 非空表示已被恢复过（避免重复恢复）。
    """
    __bind_key__ = 'grades'
    __tablename__ = 'ai_trash'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)
    kind = db.Column(db.String(10), nullable=False)
    ref_id = db.Column(db.Integer)
    payload = db.Column(db.Text)
    deleted_at = db.Column(db.DateTime, default=datetime.now)
    restored_at = db.Column(db.DateTime)

    __table_args__ = (
        db.Index('idx_ai_trash_user', 'user_id'),
        db.Index('idx_ai_trash_kind_ref', 'kind', 'ref_id'),
    )


class AiPromptTemplate(db.Model):
    """AI 成绩分析「提示词生成器」模板（2026-10-14）。

    老师用下拉选项（分析范围＝年级/班级/学科 → 分析侧重 → 输出格式 → 附加要求）
    拼出一次分析的提示词；本表把**这套选项**存成命名模板，下次直接套用、再改再跑。

    存的是 `payload`（JSON）= 结构化选项 + 当时生成的 prompt 文本：
      · 结构化选项 → 套用后表单可无损回填（选项仍可改、可重新生成）；
      · prompt 文本   → 「当时是这么拼的 / 我手动改成了这样」的留档。
    仅本人可见（列表/删除都按 user_id 过滤）。
    """
    __bind_key__ = 'grades'
    __tablename__ = 'ai_prompt_templates'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)
    name = db.Column(db.String(50), nullable=False)
    payload = db.Column(db.Text)                 # JSON：生成器选项 + prompt 文本
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now)

    __table_args__ = (
        db.Index('idx_ai_prompt_tpl_user', 'user_id'),
    )


class CertSigningKey(db.Model):
    """v1.12.2 成绩证明签名密钥（固定单行 id=1）：
    防伪码 = SL + 日期 + 随机码 + HMAC(密钥, 学号|随机码|内容哈希) 的前 16 位。
    密钥随机生成后 AES 加密存库（与 AI Key 同套 crypto），不落明文配置文件。"""
    __bind_key__ = 'grades'
    __tablename__ = 'cert_signing_keys'

    id = db.Column(db.Integer, primary_key=True)
    key_enc = db.Column(db.Text)                     # AES 随机 IV 加密后的签名密钥（hex 原文）
    created_at = db.Column(db.DateTime, default=datetime.now)


class Certificate(db.Model):
    """成绩证明台账（教职工代生成；加密防伪码 + 公开核验页，无电子签章/无二维码）"""
    __bind_key__ = 'grades'
    __tablename__ = 'certificates'

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(40), nullable=False, unique=True)  # 防伪验证码（公开核验用）
    nonce = db.Column(db.String(16))                 # v1.12.2 随机码（防伪码的随机段，落库存档）
    sig = db.Column(db.String(40))                   # v1.12.2 HMAC 签名段（密钥+学号+随机码+内容哈希）
    student_no = db.Column(db.String(20), nullable=False)         # = Student.student_number
    student_name = db.Column(db.String(50))
    grade = db.Column(db.String(10))
    class_name = db.Column(db.String(10))
    term = db.Column(db.String(20))                 # 学期范围说明（单场/全部学期）
    scope_desc = db.Column(db.String(100))          # 证明范围摘要（如某场考试名）
    exam_ids = db.Column(db.Text)                    # 关联考试 id 列表（JSON）
    generated_by = db.Column(db.Integer)             # 生成人 user_id
    generated_at = db.Column(db.DateTime, default=datetime.now)
    invalid = db.Column(db.Boolean, default=False)   # 作废标记
    content_json = db.Column(db.Text)                # 成绩快照（生成时固化，保证可验真）

    __table_args__ = (
        db.Index('idx_cert_code', 'code'),
        db.Index('idx_cert_stu', 'student_no'),
        # 2026-10-14 第四轮审计修复（PERF-118①）：按生成人追溯/统计（谁开了多少份
        # 证明）此前无索引；带 generated_at 支持时间范围过滤。
        db.Index('idx_cert_generated_by', 'generated_by', 'generated_at'),
    )


# ==================== 考务管理（考场编排 + 考号生成，对应 Excel 宏工作簿） ====================
# 完整考务流程：学生名单 → 考场设置 → 编排与考号生成（三种模式） → 导出 → 成绩关联
# 数据与 exams 同库（grades.db），便于按 学号(student_no) 关联成绩。

class ExamAffair(db.Model):
    """考务批次：一次考试的考场编排 + 考号生成任务（对应 Excel 一个工作簿）"""
    __bind_key__ = 'grades'
    __tablename__ = 'exam_affairs'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)        # 批次名（如 2026级 期中 考场编排）
    grade = db.Column(db.String(10), nullable=False)        # 年级（2025级）
    exam_date = db.Column(db.Date)                          # 考试日期（参考）
    exam_id = db.Column(db.Integer)                         # 关联考试（成绩关联用，可空）
    mode = db.Column(db.Integer, default=0)                 # 考号模式：0 前缀+考场+座号 / 1 学号即考号 / 2 自定义考号
    default_prefix = db.Column(db.String(10), default='1701')  # 模式0默认考号前缀（对应 Excel 考号前缀编写区域「默认」）
    # v1.12.1 编排增强：分组模式 / 非参考学生处理 / 选科考号前后缀（每选科各设一个前缀）
    selection_mode = db.Column(db.String(10), default='selected')  # 编排分组模式：selected 按选科分组 / plain 不选科（全体一组，考场全通用）
    non_attend_mode = db.Column(db.String(10), default='skip')     # 非参考学生处理：skip 不安排考场 / tail 安排到同选科考场尾场
    subject_prefixes = db.Column(db.Text)                          # 选科考号前后缀 JSON：{"物化生":{"prefix":"1701","suffix":""}}
    # v1.19.67.64 监考编排：每考场默认监考人数（1/2/3，用户口径默认 1 人）
    invig_slots = db.Column(db.Integer, default=1)
    status = db.Column(db.String(10), default='draft')     # draft / arranged / linked
    note = db.Column(db.Text)
    operator_id = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    def rooms(self):
        return AffairRoom.query.filter_by(affair_id=self.id).order_by(AffairRoom.room_no).all()

    def students(self):
        return AffairStudent.query.filter_by(affair_id=self.id).all()

    def arranged_count(self):
        return AffairStudent.query.filter_by(affair_id=self.id).filter(
            AffairStudent.room_no.isnot(None)).count()

    # v1.12.1 读取选科考号前后缀配置（JSON，异常兜底为空 dict）
    def get_subject_prefixes(self):
        try:
            val = json.loads(self.subject_prefixes) if self.subject_prefixes else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return val if isinstance(val, dict) else {}


class AffairRoom(db.Model):
    """考场（对应 Excel「考场信息表」一行）"""
    __bind_key__ = 'grades'
    __tablename__ = 'affair_rooms'

    id = db.Column(db.Integer, primary_key=True)
    affair_id = db.Column(db.Integer, nullable=False)
    room_no = db.Column(db.String(10), nullable=False)      # 考场编号
    location = db.Column(db.String(100))                    # 考场位置
    subject = db.Column(db.String(20), default='默认')       # 考场科目/适用选科（默认/通用/空=不限）
    capacity = db.Column(db.Integer, default=30)            # 容量
    prefix = db.Column(db.String(10))                       # 模式0考号前缀（按考场，覆盖批次默认前缀）
    note = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.now)

    __table_args__ = (
        db.UniqueConstraint('affair_id', 'room_no', name='uq_affair_room'),
        db.Index('idx_affair_room_affair', 'affair_id'),
    )

    @property
    def is_universal(self):
        """默认/通用/空 考场对所有选科开放（对应 Excel e.subject==='默认' 的兜底逻辑）"""
        return (self.subject or '默认') in ('默认', '通用', '', '全部')


class AffairStudent(db.Model):
    """考务学生（对应 Excel「学生信息表」一行）"""
    __bind_key__ = 'grades'
    __tablename__ = 'affair_students'

    id = db.Column(db.Integer, primary_key=True)
    affair_id = db.Column(db.Integer, nullable=False)
    student_no = db.Column(db.String(20), nullable=False)   # 学号（成绩匹配键）
    name = db.Column(db.String(50))
    class_name = db.Column(db.String(10))
    subject_selection = db.Column(db.String(20))            # 选科组合（如 史政地）
    subject = db.Column(db.String(20), default='默认')       # 考试科目（编排分组用，对应 Excel「考试科目」列）
    is_attend = db.Column(db.Boolean, default=True)         # 是否参与考试
    fixed_room = db.Column(db.String(10))                   # 固定考场
    fixed_seat = db.Column(db.Integer)                      # 固定座号
    custom_number = db.Column(db.String(20))                # 自定义考号（模式2）
    room_no = db.Column(db.String(10))                      # 编排结果：考场号
    seat_no = db.Column(db.Integer)                         # 编排结果：座号
    exam_number = db.Column(db.String(30))                  # 编排结果：考号

    __table_args__ = (
        db.UniqueConstraint('affair_id', 'student_no', name='uq_affair_stu'),
        db.Index('idx_affair_stu_affair', 'affair_id'),
    )


class AffairRoomLib(db.Model):
    """v1.12.1 考务改造：考场房间库（跨批次共享的全校可用考场房间）
    考务批次编排时从库中选取房间生成考场，自动带出位置与默认容量。
    """
    __bind_key__ = 'grades'
    __tablename__ = 'affair_room_libs'

    id = db.Column(db.Integer, primary_key=True)
    location = db.Column(db.String(100), nullable=False)    # 房间位置（如 教学楼A-101）
    capacity = db.Column(db.Integer, default=30)            # 默认容量
    note = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.now)

    __table_args__ = (
        db.UniqueConstraint('location', name='uq_affair_room_lib_loc'),
    )


class ExamRoomGroup(db.Model):
    """v1.19.0 考场分组：把考场房间按年级区域归类（默认 高一/高二/高三考场）。

    不同年级的考试时间通常错开，同一房间可同时归属多个分组；分组只影响
    「考场维护」页面的归集与后续建场时的筛选，*不会*改动任何历史场次的考场
    （`affair_rooms` 属于各自考务批次，彼此独立）。
    """
    __bind_key__ = 'grades'
    __tablename__ = 'exam_room_groups'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.now)

    __table_args__ = (
        db.UniqueConstraint('name', name='uq_exam_room_group_name'),
    )


class ExamRoomGroupMap(db.Model):
    """v1.19.0 考场房间 ↔ 分组 关联（多对多）

    2026-10-14 用户口径：分组里的房间**顺序**也要能调（上移 / 下移），而且要能「带着顺序复用」——
    考务步骤② 从某个分组「一键带入」时按本字段排出来的先后生成 01、02… 考场编号
    （见 ``exam_affairs.affair_rooms_from_lib_batch``；迁移脚本
    ``scripts/migrate/migrate_room_group_map_sort_20261014.py``）。
    """
    __bind_key__ = 'grades'
    __tablename__ = 'exam_room_group_map'

    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, nullable=False)
    lib_id = db.Column(db.Integer, nullable=False)     # → affair_room_libs.id
    sort_order = db.Column(db.Integer, default=0)      # 组内顺序（10、20、30… 步进）

    __table_args__ = (
        db.UniqueConstraint('group_id', 'lib_id', name='uq_room_group_map'),
        db.Index('idx_room_group_map_lib', 'lib_id'),
    )


class ScoreChangeLog(db.Model):
    """v1.19.67.64 分数修改记录（改分/删分留痕）

    每次在成绩单改分（含赋分/原始分、标记缺考）都写一条，供全校查询：
    谁、什么时候、改了哪场考试哪个学生的哪一科、改前改后各是多少、什么动作。
    只增不改不删（查询页只读）。
    """
    __bind_key__ = 'grades'
    __tablename__ = 'score_change_logs'

    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, nullable=False)
    exam_name = db.Column(db.String(100))                  # 快照，考试改名也不影响留痕
    score_id = db.Column(db.Integer)                       # ExamScore.id（删分后可能已不存在）
    student_no = db.Column(db.String(20), nullable=False)
    student_name = db.Column(db.String(50))
    class_name = db.Column(db.String(10))
    subject = db.Column(db.String(10), nullable=False)
    action = db.Column(db.String(10), nullable=False)      # update 修改 / delete 缺考
    old_score = db.Column(db.Float)
    new_score = db.Column(db.Float)
    old_raw = db.Column(db.Float)
    new_raw = db.Column(db.Float)
    operator_id = db.Column(db.Integer)
    operator_name = db.Column(db.String(50))
    operated_at = db.Column(db.DateTime, default=datetime.now)
    note = db.Column(db.String(200))

    __table_args__ = (
        db.Index('idx_scorelog_exam_time', 'exam_id', 'operated_at'),
        db.Index('idx_scorelog_student', 'exam_id', 'student_no'),
    )


class ScoreReviewRequest(db.Model):
    """v1.19.67.64 成绩复核申请（教师申请调分 → 有改分权限者审批）

    2026-10-15 用户口径：任课教师 / 班主任发现分数有误时可提交复核申请，写清
    「学生 + 学科 + 题号 + 增减分数」；审批通过后可选**直接应用到成绩单** ——
    走与手动改分同一条链路（重算总分 → 重算排名 → 写 ScoreChangeLog 留痕）。

    · 题号是说明性字段（成绩表 exam_scores 的粒度是「考试 × 学生 × 学科」，无题号列），
      允许 "12" / "12-15" 这类写法，留空表示整卷；
    · status：pending 待审核 / approved 已批准 / rejected 已驳回 / withdrawn 已撤回；
    · applied=True 表示批准时已把增减分写入成绩单（否则只是批准记录，由管理员另行处理）。
    """
    __bind_key__ = 'grades'
    __tablename__ = 'score_review_requests'

    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, nullable=False)
    exam_name = db.Column(db.String(100))                  # 快照，考试改名不影响留痕
    grade = db.Column(db.String(10))                       # 考试当时班级口径（exam_scores 快照）
    class_name = db.Column(db.String(10))
    student_no = db.Column(db.String(20), nullable=False)
    student_name = db.Column(db.String(50))
    subject = db.Column(db.String(10), nullable=False)     # 9 科之一（不含总分）
    question_no = db.Column(db.String(40))                 # 题号 / 题号段；空 = 整卷
    delta = db.Column(db.Float, nullable=False)            # 增减分数（可正可负）
    reason = db.Column(db.String(500))
    status = db.Column(db.String(12), nullable=False, default='pending')
    applicant_id = db.Column(db.Integer)
    applicant_name = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.now)
    reviewer_id = db.Column(db.Integer)
    reviewer_name = db.Column(db.String(50))
    reviewed_at = db.Column(db.DateTime)
    review_note = db.Column(db.String(200))
    applied = db.Column(db.Boolean, default=False)         # 已应用到成绩单
    applied_at = db.Column(db.DateTime)

    __table_args__ = (
        db.Index('idx_score_review_exam_status', 'exam_id', 'status'),
        db.Index('idx_score_review_student', 'exam_id', 'student_no'),
        db.Index('idx_score_review_applicant', 'applicant_id', 'status'),
    )


# ============ v1.19.67.64 考务环节④⑤⑥：任课核对 / 考试时间表 / 监考编排 ============
#
# 设计口径（用户 2026-10-12 定案）：
#   · 三者都是**本批次（本次考试）**的数据，不回写全局任课映射；
#   · 环节④ 只核对「本次考试」的年级任课安排，供环节⑥ 的教师池与回避使用；
#   · 环节⑤ 考试时间表承载「时间段 → 考试科目」，是回避教师的依据；
#   · 环节⑥ 监考表一人一格；每考场监考人数 1/2/3（默认 1，存 ExamAffair.invig_slots）；
#     回避规则：某场次科目 = 化学 → 所有任教化学的老师不排该场次。


class AffairTeacher(db.Model):
    """环节④ 本批次任课教师核对（仅本次考试）

    默认从当前 TeacherSubjectLink（该年级 active）带入，可在考务向导内临时调整
    （如某老师临时代课一两个月）。仅作用于本考务批次，不改全局任课映射。
    """
    __bind_key__ = 'grades'
    __tablename__ = 'affair_teachers'

    id = db.Column(db.Integer, primary_key=True)
    affair_id = db.Column(db.Integer, nullable=False)
    grade = db.Column(db.String(10))
    class_name = db.Column(db.String(10), nullable=False)
    subject = db.Column(db.String(10), nullable=False)
    user_id = db.Column(db.Integer)                  # 关联 users.id（可空：校外/离职教师）
    teacher_name = db.Column(db.String(50))
    source = db.Column(db.String(10), default='sync')   # sync 带入 / manual 人工修订
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        db.UniqueConstraint('affair_id', 'class_name', 'subject', name='uq_affair_teacher'),
        db.Index('idx_affair_teacher_affair', 'affair_id'),
    )


class AffairSchedule(db.Model):
    """环节⑤ 考试时间表：本批次每个场次（时间段 → 考试科目）

    一行 = 一个场次。subject 既是「这场考什么」的展示，也是环节⑥ 回避教师的依据
    （考化学 → 化学老师不监考本场）。selection 可空（如按选科分场时标注）。
    """
    __bind_key__ = 'grades'
    __tablename__ = 'affair_schedules'

    id = db.Column(db.Integer, primary_key=True)
    affair_id = db.Column(db.Integer, nullable=False)
    exam_date = db.Column(db.Date)                   # 考试日期
    start_time = db.Column(db.String(10))            # 开始时间（HH:MM）
    end_time = db.Column(db.String(10))              # 结束时间（HH:MM）
    subject = db.Column(db.String(20), nullable=False)   # 考试科目（回避依据）
    selection = db.Column(db.String(20))             # 选科/分场标注（可空）
    note = db.Column(db.String(100))
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.now)

    __table_args__ = (
        db.Index('idx_affair_schedule_affair', 'affair_id'),
    )


class AffairInvigilator(db.Model):
    """环节⑥ 其他监考员（本批次；可为**无系统账号**的校外老师）

    仅用于本次考试的监考编排，不创建系统账号、不参与任何权限。
    教师池 = 环节④ 任课教师（去重） + 本表的其他监考员。
    """
    __bind_key__ = 'grades'
    __tablename__ = 'affair_invigilators'

    id = db.Column(db.Integer, primary_key=True)
    affair_id = db.Column(db.Integer, nullable=False)
    name = db.Column(db.String(50), nullable=False)
    subject = db.Column(db.String(50))               # 任教学科（可空/多科顿号分隔，用于回避）
    note = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.now)

    __table_args__ = (
        db.UniqueConstraint('affair_id', 'name', name='uq_affair_invigilator'),
        db.Index('idx_affair_invigilator_affair', 'affair_id'),
    )


class AffairInvigilation(db.Model):
    """环节⑥ 监考安排结果：本批次「场次 × 考场 × 第 N 个监考位 → 监考老师」

    一人一格：slot=1/2/3 对应监考老师1/2/3。name/subject 为快照，教师池变更不影响已排结果。
    """
    __bind_key__ = 'grades'
    __tablename__ = 'affair_invigilations'

    id = db.Column(db.Integer, primary_key=True)
    affair_id = db.Column(db.Integer, nullable=False)
    schedule_id = db.Column(db.Integer, nullable=False)   # → affair_schedules.id
    room_no = db.Column(db.String(10), nullable=False)
    slot = db.Column(db.Integer, default=1)               # 监考位 1/2/3
    name = db.Column(db.String(50))                       # 监考老师（快照）
    user_id = db.Column(db.Integer)                       # 关联 users.id（可空）
    subject = db.Column(db.String(50))                    # 该老师任教学科（快照，留痕用）
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        db.UniqueConstraint('affair_id', 'schedule_id', 'room_no', 'slot',
                            name='uq_affair_invigilation'),
        db.Index('idx_affair_invig_affair', 'affair_id'),
    )


class AffairInvigExclude(db.Model):
    """环节⑥ 监考**排除名单**（2026-10-13 新增）

    两类：
      - `schedule_id IS NULL` → 这个老师**整批次不监考**（产假/外出/身体原因等）
      - `schedule_id = 某场次` → 这个老师**不监考指定的这一场**（如当天另有工作、
        或有该科学生需回避之外的个人原因）

    排除对**自动编排**与**手动指派**同时生效；被排除的人仍留在教师池里显示（划掉），
    便于随时恢复。校外/无账号监考员没有 user_id，故以 `name` 为准（批次内姓名唯一）。
    """
    __bind_key__ = 'grades'
    __tablename__ = 'affair_invig_excludes'

    id = db.Column(db.Integer, primary_key=True)
    affair_id = db.Column(db.Integer, nullable=False)
    name = db.Column(db.String(50), nullable=False)
    user_id = db.Column(db.Integer)                       # 可空（无账号者）
    schedule_id = db.Column(db.Integer)                   # 空 = 整批次不监考
    note = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.now)

    __table_args__ = (
        db.Index('idx_affair_invig_ex_affair', 'affair_id'),
        db.Index('idx_affair_invig_ex_sch', 'affair_id', 'schedule_id'),
    )
