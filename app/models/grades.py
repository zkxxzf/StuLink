# StuLink v1.9.0 2026-09-03
# 成绩管理与可视化分析系统：数据模型（独立库 grades.db）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import json
from datetime import datetime
from app.extensions import db

# ============ 科目常量（与字典 exam_subject 预置值保持一致） ============
SUBJECTS = ['语文', '数学', '外语', '物理', '历史', '化学', '生物', '政治', '地理']
TOTAL_SUBJECT = '总分'
# 默认满分：语数外 150，其余 100（河南 3+1+2）
DEFAULT_FULL_MARKS = {s: (150 if s in ('语文', '数学', '外语') else 100) for s in SUBJECTS}

# 选科组合字符 → 科目全名（用于由选科组合推断应考科目）
_SEL_CHAR_MAP = {'物': '物理', '化': '化学', '生': '生物', '政': '政治', '史': '历史', '地': '地理'}


def subjects_of_selection(selection):
    """由选科组合（如 物化生/史政地）推断应考科目集合（6 科）：
    语数外 + 方向科 + 2 门再选科；无法推断返回 None
    """
    if not selection:
        return None
    sel = str(selection).strip()
    if len(sel) < 3:
        return None
    names = [_SEL_CHAR_MAP.get(ch) for ch in sel]
    if any(n is None for n in names):
        return None
    direction_subject = names[0]
    if direction_subject not in ('物理', '历史'):
        return None
    return ['语文', '数学', '外语', direction_subject] + names[1:]


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
    )


class ExamBand(db.Model):
    """分层配置：考试 × 方向 × 有序层（seq=1 最高层，无上界）"""
    __bind_key__ = 'grades'
    __tablename__ = 'exam_bands'

    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, nullable=False)
    direction = db.Column(db.String(4), default='')   # 空串=双向套用
    seq = db.Column(db.Integer, nullable=False)       # 1=最高层
    name = db.Column(db.String(20), nullable=False)   # 层名（优秀/良好/及格/待提升…）
    lower_mode = db.Column(db.String(8), nullable=False, default='score')  # score/ratio
    lower_value = db.Column(db.Float, nullable=False)  # 下界分数或比例(0~100)

    __table_args__ = (
        db.UniqueConstraint('exam_id', 'direction', 'seq', name='uq_band_seq'),
    )


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
    created_at = db.Column(db.DateTime, default=datetime.now)

    __table_args__ = (
        db.Index('idx_ai_report_user', 'user_id'),
        db.Index('idx_ai_report_exam', 'exam_id'),
    )
