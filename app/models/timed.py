# StuLink v1.19.67.64 2026-10-20
# 限时练：与普通考试完全隔离的独立数据表
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""限时练数据模型（2026-10-20 用户口径）。

设计要点（与普通考试零耦合）：

1. **独立表**：场次 / 成绩 / 分层模板各一张表，**不写** ``exams`` /
   ``exam_scores``。因此历次考试的查询、筛选、分页、导出完全不受影响。
2. **类型锁定**：场次属于限时练是"落在哪张表"决定的，没有可切换的模式列，
   与普通考试之间不存在互相转换的入口。
3. **追加式单科**：``timed_scores`` 以（场次, 学生, 科目）唯一；
   一次导入一科，重复导入同科 = 该科整科覆盖（缺考学生自动消失）。
4. **名次区间**：``timed_band_templates.layers_json`` 存名次区间，
   分析时直接按 ``timed_scores.rank_grade`` 落区间统计人数。
"""
from datetime import datetime

from app.extensions import db
from app.models.grades import exam_semester

# 场次状态：未导入 / 已导入（追加导入多科后仍是 imported）
TIMED_STATUS_LABEL = {'draft': '未导入', 'imported': '已导入'}

# 内置分层模板：与 ordinary 考试 `band_templates` 里「限时练名次分层」的 12 层
# 保持一致（2026-10-20 用户口径：层要跟现有模板一样多）。
# 口径 = **累积门槛**：层「前N」统计的是"全年级该科名次 ≤ N"的人数
# （不是互斥区间），与普通考试 rank 模板的"前N名上线人数"同一算盘。
DEFAULT_TIMED_LAYERS = (
    {'name': '前10', 'from_rank': 1, 'to_rank': 10},
    {'name': '前20', 'from_rank': 1, 'to_rank': 20},
    {'name': '前50', 'from_rank': 1, 'to_rank': 50},
    {'name': '前80', 'from_rank': 1, 'to_rank': 80},
    {'name': '前100', 'from_rank': 1, 'to_rank': 100},
    {'name': '前150', 'from_rank': 1, 'to_rank': 150},
    {'name': '前200', 'from_rank': 1, 'to_rank': 200},
    {'name': '前250', 'from_rank': 1, 'to_rank': 250},
    {'name': '前300', 'from_rank': 1, 'to_rank': 300},
    {'name': '前400', 'from_rank': 1, 'to_rank': 400},
    {'name': '前500', 'from_rank': 1, 'to_rank': 500},
    {'name': '前550', 'from_rank': 1, 'to_rank': 550},
)

BUILTIN_TIMED_TEMPLATE = '限时练名次分层'


class TimedExam(db.Model):
    """限时练场次：一个年级 × 一次限时练（科目不固定，随导入追加）"""
    __bind_key__ = 'timed'                                # 独立库 timed.db
    __tablename__ = 'timed_exams'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    grade = db.Column(db.String(10), nullable=False)      # 2024级 / 2025级
    exam_date = db.Column(db.Date, nullable=False)
    term = db.Column(db.String(20))                       # 学年 2025-2026
    grade_semester = db.Column(db.String(10), default='')  # 空 = 按年级+日期推算
    template_id = db.Column(db.Integer)                   # 限时练分层模板 id
    status = db.Column(db.String(10), default='draft', nullable=False)
    remark = db.Column(db.String(200))
    # 导入预检草稿（JSON）+ 一次性批次号（防串批，与普通考试同一模式）
    import_draft = db.Column(db.Text)
    import_token = db.Column(db.String(36))
    operator_id = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now,
                           onupdate=datetime.now)

    def semester_label(self):
        """年级学期标签（如「高三上」）：优先手工值，否则按年级+日期推算"""
        from app.models.grades import GRADE_SEMESTERS
        fixed = (self.grade_semester or '').strip()
        if fixed in GRADE_SEMESTERS:
            return fixed
        return exam_semester(self.grade, self.exam_date)

    def display_name(self):
        """名称末尾与学期标签重复的「（高三上）」自动去掉"""
        sem = self.semester_label()
        if sem:
            suffix = f'（{sem}）'
            if (self.name or '').endswith(suffix):
                return self.name[:-len(suffix)]
        return self.name

    def natural_semester(self):
        """自然学期键（学年 + 上/下），如 `2026-2027上`。

        2026-10-20 用户口径：列表的学期筛选按**自然学期**分，一个自然学期
        里三个年级（高一/高二/高三）的场次一起展示；过了春节自动变下学期。
        所以不能再用「高三上/高二上/高一上」这种年级学期当筛选值。
        """
        from app.models.grades import semester_of, term_of_date
        term = (self.term or '').strip() or term_of_date(self.exam_date)
        if not term:
            return ''
        half = '上' if semester_of(self.exam_date) == '上学期' else '下'
        return f'{term}{half}'

    def natural_semester_label(self):
        """自然学期的展示名，如「2026-2027 上学期」"""
        key = self.natural_semester()
        if not key:
            return ''
        return f'{key[:9]} {"上学期" if key.endswith("上") else "下学期"}'

    def status_label(self):
        """状态展示名（未导入 / 已导入）"""
        return TIMED_STATUS_LABEL.get(self.status or 'draft', self.status or '')

    __table_args__ = (
        db.Index('idx_timed_exam_grade_date', 'grade', 'exam_date'),
    )


class TimedScore(db.Model):
    """限时练成绩：一学生一科目一行（缺考不落库，故 score 非空）"""
    __bind_key__ = 'timed'                                # 独立库 timed.db
    __tablename__ = 'timed_scores'

    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, nullable=False)        # TimedExam.id
    student_no = db.Column(db.String(20), nullable=False)  # 学号快照
    student_name = db.Column(db.String(50))
    zhixue_no = db.Column(db.String(20))                   # 智学网考号快照
    grade = db.Column(db.String(10))
    class_name = db.Column(db.String(10))
    subject = db.Column(db.String(10), nullable=False)     # 单科（不含总分）
    score = db.Column(db.Float, nullable=False)
    rank_grade = db.Column(db.Integer)                     # 全年级该科名次
    updated_at = db.Column(db.DateTime, default=datetime.now,
                           onupdate=datetime.now)

    __table_args__ = (
        db.UniqueConstraint('exam_id', 'student_no', 'subject',
                            name='uq_timed_stu_subject'),
        db.Index('idx_timed_scores_exam_subject', 'exam_id', 'subject'),
        db.Index('idx_timed_scores_rank', 'exam_id', 'subject', 'rank_grade'),
    )


class TimedBandTemplate(db.Model):
    """限时练专属分层模板：只存名次区间，不掺分数线和选科组合

    layers_json 结构：[{"name": "前10名", "from_rank": 1, "to_rank": 10}, …]
    """
    __bind_key__ = 'timed'                                # 独立库 timed.db
    __tablename__ = 'timed_band_templates'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    layers_json = db.Column(db.Text, default='[]')
    is_builtin = db.Column(db.Boolean, default=False)
    sort_order = db.Column(db.Integer, default=0)
    created_by = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now,
                           onupdate=datetime.now)

    def get_layers(self):
        """层列表（结构异常时回落内置区间，避免分析页整块空白）"""
        import json
        try:
            rows = json.loads(self.layers_json or '[]')
        except (TypeError, ValueError):
            rows = []
        out = []
        for r in rows if isinstance(rows, list) else []:
            if not isinstance(r, dict) or not (r.get('name') or '').strip():
                continue
            out.append({
                'name': str(r.get('name')).strip()[:20],
                'from_rank': _to_int(r.get('from_rank'), 1),
                'to_rank': _to_int(r.get('to_rank'), None),
            })
        return out or [dict(x) for x in DEFAULT_TIMED_LAYERS]

    def set_layers(self, layers):
        """保存层列表（只保留 name / from_rank / to_rank 三个字段）"""
        import json
        clean = []
        for r in layers or []:
            name = str((r or {}).get('name') or '').strip()[:20]
            if not name:
                continue
            clean.append({
                'name': name,
                'from_rank': _to_int(r.get('from_rank'), 1),
                'to_rank': _to_int(r.get('to_rank'), None),
            })
        self.layers_json = json.dumps(clean, ensure_ascii=False)

    def layer_names(self):
        """层名列表（划线页下拉 / 图例用）"""
        return [x['name'] for x in self.get_layers()]


def _to_int(value, default):
    """安全的整数转换：空值/非法值返回 default"""
    if value in (None, ''):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
