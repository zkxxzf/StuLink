# StuLink v1.18.0.0 2026-09-23
# 成绩管理与可视化分析系统：数据模型（独立库 grades.db）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import json
import re
from datetime import datetime
from app.extensions import db

# ============ 科目常量（与字典 exam_subject 预置值保持一致） ============
SUBJECTS = ['语文', '数学', '外语', '物理', '历史', '化学', '生物', '政治', '地理']
TOTAL_SUBJECT = '总分'
# 默认满分：语数外 150，其余 100（河南 3+1+2）
DEFAULT_FULL_MARKS = {s: (150 if s in ('语文', '数学', '外语') else 100) for s in SUBJECTS}

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


def semester_of(exam_date):
    """考试日期 → 学期：9月~次年1月=上学期，2月~8月=下学期"""
    if not exam_date:
        return ''
    return '上学期' if (exam_date.month >= 9 or exam_date.month == 1) else '下学期'


def grade_level_label(grade, term):
    """学年起始年 - 入学年 + 1 → 高一/高二/高三；无法推算返回空串"""
    try:
        enroll = int(''.join(ch for ch in str(grade) if ch.isdigit())[:4])
        start = int(str(term).split('-')[0])
        level = start - enroll + 1
    except (AttributeError, IndexError, TypeError, ValueError):
        return ''
    return {1: '高一', 2: '高二', 3: '高三'}.get(level, '')


def _default_config():
    return json.dumps({
        'band_width': 30,
        'rank_bands': [10, 20, 30, 50, 100, 200, 300],
        'subject_lines': {},   # {科目: {excellent: , pass: , low: }} 覆盖，缺省按满分 90%/60%/40%
    }, ensure_ascii=False)


class Exam(db.Model):
    """考试主档：一场考试 = 一个年级 × 一次"""
    __bind_key__ = 'grades'
    __tablename__ = 'exams'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    exam_type = db.Column(db.String(20))            # 字典 exam_type（月考/期中/期末…）
    grade = db.Column(db.String(10), nullable=False)  # 2024级/2025级…（与字典一致）
    exam_date = db.Column(db.Date, nullable=False)
    term = db.Column(db.String(20))                  # 学年 2025-2026，由日期推导或登记选择
    config_json = db.Column(db.Text, default=_default_config)  # 分段宽/名次段/科目线覆盖
    status = db.Column(db.String(10), default='draft', nullable=False)  # draft/imported/dirty
    import_draft = db.Column(db.Text)                # 上传预检结果快照（JSON，确认导入用）
    import_token = db.Column(db.String(36))          # 预检批次号（防串批）
    import_info = db.Column(db.Text)                 # 最近导入批次摘要 JSON
    operator_id = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    def get_config(self):
        try:
            cfg = json.loads(self.config_json or '{}')
        except (json.JSONDecodeError, TypeError):
            cfg = {}
        return cfg

    def set_config(self, cfg):
        self.config_json = json.dumps(cfg, ensure_ascii=False)

    def band_width(self):
        cfg = self.get_config()
        try:
            return int(cfg.get('band_width', 30))
        except (TypeError, ValueError):
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
        except AttributeError:
            sl = {}
        return {
            'excellent': sl.get('excellent', fm * 0.9),
            'pass': sl.get('pass', fm * 0.6),
            'low': sl.get('low', fm * 0.4),
        }

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
                except (TypeError, ValueError):
                    pass
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
    direction = db.Column(db.String(4))                     # 物理/历史 快照
    subject_selection = db.Column(db.String(10))            # 选科组合快照
    subject = db.Column(db.String(10), nullable=False)      # 9 科之一 或 总分
    score = db.Column(db.Float)                             # NULL=无成绩
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
        # v1.13.2 性能：学生维度查询（查该生全部总分考试，不带 exam_id）此前全表扫描
        # 31 万行；覆盖索引 (student_no, subject, exam_id) 让查询只走索引
        db.Index('idx_scores_stu_subject_exam', 'student_no', 'subject', 'exam_id'),
    )


class ExamBand(db.Model):
    """分层配置：考试 × 方向 × 学科 × 有序层（seq=1 最高层，无上界）

    subject='总分' 为总分线（原行为）；subject=学科名 为该科单科线，
    两者独立配置，故同一学科在物理/历史方向下可有不同分数线。
    """
    __bind_key__ = 'grades'
    __tablename__ = 'exam_bands'

    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, nullable=False)
    direction = db.Column(db.String(4), default='')   # 空串=双向套用
    subject = db.Column(db.String(10), nullable=False,
                        default=TOTAL_SUBJECT)        # 总分 或 9 科之一
    seq = db.Column(db.Integer, nullable=False)       # 1=最高层
    name = db.Column(db.String(20), nullable=False)   # 层名（优秀/良好/及格/待提升…）
    lower_mode = db.Column(db.String(8), nullable=False, default='score')  # score/ratio
    lower_value = db.Column(db.Float, nullable=False)  # 下界分数或比例(0~100)

    __table_args__ = (
        db.UniqueConstraint('exam_id', 'direction', 'subject', 'seq', name='uq_band_seq'),
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
