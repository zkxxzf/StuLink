<<<<<<< HEAD
# StuLink v1.18.8.0 2026-10-09
# 教务模块模型（2026-10-10 按「一个功能一个库」分库）：
#   academic.db    师资基础（教师名单 / 备课组长 / 班主任记录 / 考勤）
#   timetable.db   课表与晚自习值班
#   inspection.db  查课记录
#   achievement.db 教师业绩 + 附件
#   forms.db       问卷收集（模板/轮次/题目/提交/答案）
=======
# StuLink v1.18.9.1 2026-10-10
# 教务模块模型：教师名单 / 课表 / 查课记录 / 教师业绩（独立库 academic.db）
>>>>>>> origin/master
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from datetime import datetime

from app.extensions import db


class Teacher(db.Model):
    """教师名单（教务基础数据）

    - teacher_uid：系统生成的随机唯一编号（创建后不可更改），作为教师身份主标识；
      手机号可更换、工号老师不常用，均不作为唯一键。
    - id_card_enc：身份证号的确定性 AES 密文（crypto.encrypt，同一号码密文相同，
      支持唯一性等值查询）；首次导入可为空（后补），一经录入仅管理员可更新。
    - user_id：关联 system 库 users.id（快照式关联，跨库不建物理外键）。
    """
    __bind_key__ = 'academic'
    __tablename__ = 'teachers'

    id = db.Column(db.Integer, primary_key=True)
    teacher_uid = db.Column(db.String(16), unique=True, nullable=False, index=True)
    name = db.Column(db.String(50), nullable=False)
    id_card_enc = db.Column(db.String(128), unique=True)   # 身份证号密文（唯一，可空）
    phone = db.Column(db.String(20))
    subject = db.Column(db.String(20))                     # 任教学科
    status = db.Column(db.String(10), default='active')    # active=在职 / left=离职
    user_id = db.Column(db.Integer)                        # 关联 users.id（快照式）
    note = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    def __repr__(self):
        return f'<Teacher {self.teacher_uid} {self.name}>'


class InspectionRecord(db.Model):
    """教务查课记录（巡课检查教师上课情况）"""
    # 2026-10-10：查课独立成库（inspection.db）——「一个功能一个库」
    __bind_key__ = 'inspection'
    __tablename__ = 'inspection_records'
    # 2026-10-10：按检查人 / 班级维度统计的补充索引（数据量增长后统计接口不再全表扫）
    __table_args__ = (
        db.Index('ix_inspection_records_inspector', 'inspector_id'),
        db.Index('ix_inspection_records_class', 'grade', 'class_name'),
        # 2026-10-10：查课页按「日期+节次」查询；该索引原只写在补建脚本里、旧库从未建成，
        # 分库时自然也没搬过来。此处补模型声明，名字与 add_performance_indexes.py 保持同名对齐。
        db.Index('idx_inspect_date_period', 'inspect_date', 'period'),
    )

    id = db.Column(db.Integer, primary_key=True)
    inspect_date = db.Column(db.Date, nullable=False, index=True)
    grade = db.Column(db.String(10), index=True)           # 年级（v1.9.2 数据范围过滤用）
    period = db.Column(db.Integer)                         # 节次
    teacher_uid = db.Column(db.String(16), index=True)
    teacher_name = db.Column(db.String(50))
    class_name = db.Column(db.String(10))
    subject = db.Column(db.String(20))
    result = db.Column(db.String(10), default='normal')    # normal/late/absent/swap/other
    inspector_id = db.Column(db.Integer)                   # 检查人 users.id
    note = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.now)

    def __repr__(self):
        return f'<Inspection {self.inspect_date} {self.teacher_name}>'


class TeacherAchievement(db.Model):
    """教师业绩库（证书/课题/论文/荣誉/培训等）

    - 教务直接录入 → status=approved；教师工作台提交 → status=pending 待审核。
    """
    # 2026-10-10：教师业绩独立成库（achievement.db）
    __bind_key__ = 'achievement'
    __tablename__ = 'teacher_achievements'
    __table_args__ = (
        db.Index('ix_teacher_achievements_status', 'status'),
        db.Index('ix_teacher_achievements_created', 'created_at'),
    )

    id = db.Column(db.Integer, primary_key=True)
    teacher_uid = db.Column(db.String(16), index=True)
    teacher_name = db.Column(db.String(50))
    category = db.Column(db.String(20), nullable=False)    # certificate/course/paper/honor/training/other
    title = db.Column(db.String(100), nullable=False)      # 名称
    level = db.Column(db.String(20))                       # 国家级/省级/市级/区县级/校级/其他
    obtain_date = db.Column(db.Date)                       # 取得时间
    issuer = db.Column(db.String(100))                     # 颁发单位
    note = db.Column(db.String(200))
    # 标签（2026-09-26 新增）：逗号分隔，如「课题,省级,数学」。
    # 业绩库原来只有类别/级别两个维度，教务还需要按"做什么用的"打标签检索
    # （如 竞赛辅导/论文发表/继续教育），一库多用。
    tags = db.Column(db.String(200))
    status = db.Column(db.String(10), default='approved')  # pending/approved/rejected
    submitted_by = db.Column(db.Integer)                   # 提交人 users.id
    reviewed_by = db.Column(db.Integer)                    # 审核人 users.id
    reviewed_at = db.Column(db.DateTime)
    review_note = db.Column(db.String(200))                # 审核意见（驳回原因等，2026-09-26 新增）
    # 2026-10-09：按类别的动态字段（JSON）——课题有"立项/结题时间、课题编号、本人角色"，
    # 论文有"期刊、刊号、作者位次"，培训有"学时、主办单位"……不同类别要填的不一样。
    extra_json = db.Column(db.Text)
    # 2026-10-10：来源追溯（表单收集审核通过自动入账）
    # source_label 冗余存「XX收集·第2轮」，列表筛选/分组展示不必再 join 表单表。
    source_type = db.Column(db.String(12))        # manual / workbench / form
    source_id = db.Column(db.Integer)             # form_submissions.id（source_type=form 时）
    source_round_id = db.Column(db.Integer)       # form_rounds.id
    source_label = db.Column(db.String(120))
    created_at = db.Column(db.DateTime, default=datetime.now)

    def extra(self):
        """动态字段 dict（解析失败返回 {}）。"""
        import json
        if not self.extra_json:
            return {}
        try:
            data = json.loads(self.extra_json)
            return data if isinstance(data, dict) else {}
        except (ValueError, TypeError):
            return {}

    def __repr__(self):
        return f'<Achievement {self.teacher_name} {self.title}>'


# 业绩类别与查课结果的统一文案（模板与页面渲染共用）
class AchievementAttachment(db.Model):
    """业绩附件（证书扫描件、获奖照片、PDF/Word 材料等，2026-09-26 新增）。

    文件落在 `app/static/uploads/achievements/<achievement_id>/`，与表单材料同一套
    存放策略：`/static/uploads` 直链已被全局封禁，只能通过带鉴权的
    `academic.achievement_file_view` / `achievement_file_download` 访问。

    一条业绩可以有多个附件（证书正反面、红头文件 + 照片很常见）。
    """
    # 2026-10-10：随业绩一起独立成库（achievement.db）
    __bind_key__ = 'achievement'
    __tablename__ = 'achievement_attachments'

    id = db.Column(db.Integer, primary_key=True)
    achievement_id = db.Column(db.Integer, index=True, nullable=False)
    file_name = db.Column(db.String(200))       # 原始文件名（展示与下载名）
    stored_name = db.Column(db.String(80))      # 落盘名（uuid.ext，杜绝穿越/覆盖）
    ext = db.Column(db.String(10))
    mime = db.Column(db.String(60))
    size = db.Column(db.Integer)                # 字节
    uploaded_by = db.Column(db.Integer)         # users.id
    uploaded_name = db.Column(db.String(50))
    # 2026-10-09：材料分类（立项通知书 / 中期材料 / 结题证书 / 证书扫描件 …）
    doc_type = db.Column(db.String(20))
    created_at = db.Column(db.DateTime, default=datetime.now)

    def to_dict(self):
        return {
            'id': self.id,
            'achievement_id': self.achievement_id,
            'file_name': self.file_name,
            'doc_type': self.doc_type,
            'doc_type_text': DOC_TYPE_TEXT.get(self.doc_type, '') if self.doc_type else '',
            'ext': self.ext,
            'mime': self.mime,
            'size': self.size,
            'size_text': (f'{self.size / 1024 / 1024:.1f} MB' if (self.size or 0) >= 1024 * 1024
                          else f'{max(1, (self.size or 0) // 1024)} KB'),
            'uploaded_name': self.uploaded_name,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M') if self.created_at else '',
        }

    def __repr__(self):
        return f'<AchievementAttachment {self.achievement_id} {self.file_name}>'


ACHIEVEMENT_CATEGORIES = [
    ('certificate', '证书'), ('course', '课题'), ('paper', '论文'),
    ('honor', '荣誉'), ('training', '培训'), ('other', '其他'),
]
ACHIEVEMENT_LEVELS = ['国家级', '省级', '市级', '区县级', '校级', '其他']
ACHIEVEMENT_STATUS = {'pending': '待审核', 'approved': '已通过', 'rejected': '已驳回'}
# 2026-10-10：业绩来源（表单收集审核通过会自动入账，需可追溯回那一次提交/轮次）
ACHIEVEMENT_SOURCE = {'manual': '教务录入', 'workbench': '教师提交', 'form': '表单收集'}

# 2026-10-09：附件材料分类（统一 PDF，但材料种类很多，必须能分清是哪一份）
DOC_TYPES = [
    ('cert', '证书 / 奖状扫描件'),
    ('notice', '立项 / 表彰通知书'),
    ('opening', '开题报告'),
    ('midterm', '中期材料'),
    ('closing', '结题 / 结业材料'),
    ('paper', '论文正文 / 刊物页'),
    ('approval', '批文 / 红头文件'),
    ('other', '其它材料'),
]
DOC_TYPE_TEXT = dict(DOC_TYPES)

# 每个类别要补的字段（教务填表时按类别动态显示，存 extra_json）
ACHIEVEMENT_FIELDS = {
    'certificate': [('cert_no', '证书编号'), ('approve_no', '批准文号'),
                    ('valid_until', '有效期至', 'date')],
    'course': [('course_no', '课题编号'), ('start_date', '立项时间', 'date'),
               ('end_date', '结题时间', 'date'), ('role', '本人角色', 'select:主持,参与,成员'),
               ('fund', '经费（万元）')],
    'paper': [('journal', '发表期刊 / 论文集'), ('issn', '刊号（CN/ISSN）'),
              ('author_rank', '作者位次', 'select:独著,第一作者,第二作者,通讯作者,其它'),
              ('core', '是否核心', 'select:是,否')],
    'honor': [('honor_no', '证书编号'), ('approve_no', '批准文号'),
              ('grant_unit', '授予单位')],
    'training': [('hours', '学时'), ('host', '主办单位'),
                 ('start_date', '开始日期', 'date'), ('end_date', '结束日期', 'date')],
    'other': [('detail', '补充说明')],
}
INSPECTION_RESULTS = [
    ('normal', '正常'), ('late', '迟到'), ('absent', '缺课'),
    ('swap', '调课'), ('other', '其他'),
]


# 2026-10-10：CourseSwap（course_swaps 表，长期 0 行）已删除 ——
# 调课业务统一走 timetable.db 的 ScheduleSwap（带审批链/执行态），
# 工作台待审调课曾误读本表导致"永远没有待审"（见 workbench/routes 注释）。


# ── 表单收集系统 ──────────────────────────────────────────────

FORM_STATUS = {'draft': '草稿', 'open': '进行中', 'closed': '已关闭', 'archived': '已归档'}
FORM_TARGET_TYPES = {'all': '全体', 'teachers': '教师', 'students': '学生', 'grade': '指定年级'}
QUESTION_TYPES = [
    ('text', '单行文本'), ('textarea', '多行文本'), ('single_choice', '单选'),
    ('multi_choice', '多选'), ('file', '文件上传'), ('date', '日期'), ('number', '数字'),
]
SUBMISSION_STATUS = {'submitted': '已提交', 'approved': '已通过', 'rejected': '已驳回'}
# 2026-10-10：收集轮次状态（一次收集 = 一轮；模板本身只管题目与发布范围）
ROUND_STATUS = {'open': '进行中', 'closed': '已结束'}


class FormCategory(db.Model):
    """表单分类"""
    __tablename__ = 'form_categories'
    # 2026-10-10：表单收集独立成库（forms.db）
    __bind_key__ = 'forms'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(30), nullable=False, unique=True)
    sort_order = db.Column(db.Integer, default=0)

    def __repr__(self):
        return f'<FormCategory {self.name}>'


class FormTemplate(db.Model):
    """表单模板定义"""
    __tablename__ = 'form_templates'
    __bind_key__ = 'forms'
    __table_args__ = (db.Index('ix_form_templates_status', 'status'),)

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    category = db.Column(db.String(30))
    target_type = db.Column(db.String(20), default='all')   # all/teachers/students/grade_x
    target_scope = db.Column(db.Text)                        # JSON
    start_time = db.Column(db.DateTime)
    deadline = db.Column(db.DateTime)
    max_file_size_mb = db.Column(db.Integer, default=10)
    status = db.Column(db.String(10), default='draft')       # draft/open/closed/archived
    allow_multiple = db.Column(db.Boolean, default=False)
    created_by = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.now)
    # 2026-10-10：计入教师业绩库的映射规则（发起收集时指定，审核通过自动入账）
    # ach_title_mode=template 用模板标题当业绩名称；=question 取 ach_title_question_id 那题的答案。
    to_achievement = db.Column(db.Boolean, default=False)
    ach_category = db.Column(db.String(20))
    ach_level = db.Column(db.String(20))
    ach_title_mode = db.Column(db.String(10), default='template')
    ach_title_question_id = db.Column(db.Integer)
    ach_tags = db.Column(db.String(100))

    questions = db.relationship('FormQuestion', backref='template',
                                cascade='all, delete-orphan',
                                order_by='FormQuestion.sort_order')
    rounds = db.relationship('FormRound', backref='template',
                             order_by='FormRound.round_no')

    def __repr__(self):
        return f'<FormTemplate {self.title}>'


class FormRound(db.Model):
    """表单收集轮次（2026-10-10）：同一模板可发起多轮，业绩按「第几轮」沉淀。

    历史数据由迁移脚本为每个已有模板补一条 round_no=1 的轮次并回填
    form_submissions.round_id，保证「一次收集 = 一轮」的口径统一。
    """
    __tablename__ = 'form_rounds'
    __bind_key__ = 'forms'
    __table_args__ = (db.UniqueConstraint('template_id', 'round_no',
                                          name='uq_form_round_no'),
                      db.Index('ix_form_rounds_status', 'status'),)

    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(db.Integer, db.ForeignKey('form_templates.id'),
                            nullable=False, index=True)
    round_no = db.Column(db.Integer, nullable=False, default=1)
    name = db.Column(db.String(100))            # 如「2026-2027学年第一学期」
    term = db.Column(db.String(20))
    start_time = db.Column(db.DateTime)
    deadline = db.Column(db.DateTime)
    status = db.Column(db.String(10), default='open')     # open / closed
    created_by = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.now)

    def label(self):
        """「模板标题·第N轮（轮次名）」——业绩来源展示用。"""
        base = (self.template.title if self.template else '') or ''
        extra = f'（{self.name}）' if self.name else ''
        return f'{base}·第{self.round_no}轮{extra}'

    def __repr__(self):
        return f'<FormRound T{self.template_id} #{self.round_no}>'


class FormQuestion(db.Model):
    """表单题目"""
    __tablename__ = 'form_questions'
    __bind_key__ = 'forms'
    __table_args__ = (db.Index('ix_form_questions_template', 'template_id'),)

    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(db.Integer, db.ForeignKey('form_templates.id'), nullable=False)
    question_type = db.Column(db.String(20), nullable=False)  # text/textarea/single_choice/multi_choice/file/date/number
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.String(500))
    options_json = db.Column(db.Text)                         # JSON array for choices
    required = db.Column(db.Boolean, default=False)
    file_types = db.Column(db.String(100))                    # e.g. "pdf,doc,docx,xls"
    max_file_size_mb = db.Column(db.Integer)                  # overrides template level
    sort_order = db.Column(db.Integer, default=0)

    def __repr__(self):
        return f'<FormQuestion {self.title[:20]}>'


class FormSubmission(db.Model):
    """提交记录"""
    __tablename__ = 'form_submissions'
    __bind_key__ = 'forms'
    # 2026-10-10：提交表是问卷功能的主查询表（按模板统计 / 按人查重 / 按状态审），
    # 原来除 round_id 外一个索引都没有；数据量上去后这些条件是全表扫。
    __table_args__ = (
        db.Index('ix_form_submissions_template', 'template_id'),
        db.Index('ix_form_submissions_submitter', 'submitter_type', 'submitter_id'),
        db.Index('ix_form_submissions_uid', 'submitter_uid'),
        db.Index('ix_form_submissions_status', 'status'),
    )

    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(db.Integer, db.ForeignKey('form_templates.id'), nullable=False)
    submitter_type = db.Column(db.String(10))                 # teacher/student
    submitter_id = db.Column(db.Integer)
    submitter_name = db.Column(db.String(50))
    submitter_uid = db.Column(db.String(30))
    submitter_grade = db.Column(db.String(10))
    submitter_class = db.Column(db.String(10))
    # 2026-10-10：所属收集轮次（迁移脚本为历史提交回填 round_no=1 那条）
    round_id = db.Column(db.Integer, db.ForeignKey('form_rounds.id'), index=True)
    status = db.Column(db.String(10), default='submitted')    # submitted/approved/rejected
    reviewed_by = db.Column(db.Integer)
    reviewed_at = db.Column(db.DateTime)
    review_note = db.Column(db.String(200))
    submitted_at = db.Column(db.DateTime, default=datetime.now)

    answers = db.relationship('FormAnswer', backref='submission',
                              cascade='all, delete-orphan')

    def __repr__(self):
        return f'<FormSubmission {self.submitter_name} #{self.id}>'


class FormAnswer(db.Model):
    """答案数据"""
    __tablename__ = 'form_answers'
    __bind_key__ = 'forms'
    # 2026-10-10：答案是未来最大的表（一次提交 = 每题一行，全校数千人 × 多轮），
    # submission_id / question_id 是**外键却无索引** —— 必补，否则汇总按提交查答案是全表扫。
    __table_args__ = (
        db.Index('ix_form_answers_submission', 'submission_id'),
        db.Index('ix_form_answers_question', 'question_id'),
    )

    id = db.Column(db.Integer, primary_key=True)
    submission_id = db.Column(db.Integer, db.ForeignKey('form_submissions.id'), nullable=False)
    question_id = db.Column(db.Integer, db.ForeignKey('form_questions.id'), nullable=False)
    answer_text = db.Column(db.Text)
    answer_json = db.Column(db.Text)
    file_path = db.Column(db.String(200))
    file_name = db.Column(db.String(100))
    file_size = db.Column(db.Integer)

    def __repr__(self):
        return f'<FormAnswer Q{self.question_id}>'


# ── 班主任工作台 ──────────────────────────────────────────────

class WorkRecord(db.Model):
    """班主任工作记录（班会/家访/谈话）"""
    __tablename__ = 'work_records'
    __bind_key__ = 'academic'

    id = db.Column(db.Integer, primary_key=True)
    teacher_uid = db.Column(db.String(16), nullable=False, index=True)
    teacher_name = db.Column(db.String(50))
    record_type = db.Column(db.String(10), nullable=False)  # meeting/visit/talk
    student_no = db.Column(db.String(30), index=True)
    student_name = db.Column(db.String(50))
    class_name = db.Column(db.String(10), index=True)
    date = db.Column(db.Date, nullable=False)
    title = db.Column(db.String(100), nullable=False)
    content = db.Column(db.Text)
    follow_up = db.Column(db.Text)
    attachments_json = db.Column(db.Text)  # JSON list of file paths
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    def __repr__(self):
        return f'<WorkRecord {self.teacher_name} {self.record_type} {self.date}>'


RECORD_TYPES = [('meeting', '班会'), ('visit', '家访'), ('talk', '谈话')]


class AttendanceRecord(db.Model):
    """考勤记录"""
    __tablename__ = 'attendance_records'
    __bind_key__ = 'academic'
    # 2026-10-10：性能复合索引进模型声明（原先只写在 scripts/add_performance_indexes.py，
    # 新装环境 create_all 拿不到、会重演"脚本有、库里没有"的静默缺口）。
    # 索引名与现网完全一致，避免同表重复索引；脚本条目保留，作历史库补建载体（IF NOT EXISTS 幂等）。
    __table_args__ = (
        db.Index('idx_attend_class_date', 'class_name', 'attend_date'),
        db.Index('idx_attend_stuno_date', 'student_no', 'attend_date'),
    )

    id = db.Column(db.Integer, primary_key=True)
    student_no = db.Column(db.String(30), nullable=False, index=True)
    student_name = db.Column(db.String(50))
    grade = db.Column(db.String(10), index=True)
    class_name = db.Column(db.String(10), index=True)
    attend_date = db.Column(db.Date, nullable=False, index=True)
    period = db.Column(db.Integer)  # null = full day
    status = db.Column(db.String(10), nullable=False)  # present/absent/late/leave
    recorded_by = db.Column(db.Integer)
    remark = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.now)

    def __repr__(self):
        return f'<Attendance {self.student_no} {self.attend_date} {self.status}>'


ATTENDANCE_STATUS = [('present', '出勤'), ('absent', '缺勤'), ('late', '迟到'), ('leave', '请假')]


# ── 备课组长（2026-09-25）───────────────────────────────────────────────────
# 任课安排（原 /academic/duty）已于 2026-10-10 下线，教师-学科关系以任课教师
# 映射（TeacherSubjectLink）为唯一依据；此处保留备课组长（SubjectLeader）相关常量。
# 备课组长信息无法从课表自动推导，故落库（academic.db.subject_leaders）。

class SubjectLeader(db.Model):
    """备课组长登记（按 学年 × 学期 × 年级 × 学科 唯一）

    - `term=''` 表示"整学年"；`grade=''` 表示全校/综合组（如信息技术、心理）；
    - `leader_uid` 关联 academic.db 的 teachers.teacher_uid（快照式，跨库不建外键）；
    - `members` 备课组范围或成员；`duty` 主要职责（可套用 DUTY_TEMPLATES 预设）。
    """
    __bind_key__ = 'academic'
    __tablename__ = 'subject_leaders'

    id = db.Column(db.Integer, primary_key=True)
    school_year = db.Column(db.String(20), nullable=False, index=True)  # 如 2026-2027
    term = db.Column(db.String(10), default='')          # ''=整学年 / 第一学期 / 第二学期
    grade = db.Column(db.String(10), default='')         # ''=全校/综合组
    subject = db.Column(db.String(20), nullable=False, index=True)
    leader_uid = db.Column(db.String(16))                # Teacher.teacher_uid
    leader_name = db.Column(db.String(50), nullable=False)
    members = db.Column(db.String(300))                  # 备课组范围/成员
    duty = db.Column(db.String(500))                     # 主要职责
    sort_order = db.Column(db.Integer, default=0)
    updated_by = db.Column(db.Integer)                   # users.id
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        db.UniqueConstraint('school_year', 'term', 'grade', 'subject',
                            name='uq_subject_leader'),
        db.Index('idx_leader_year_subject', 'school_year', 'subject'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'school_year': self.school_year,
            'term': self.term or '',
            'term_text': self.term or '整学年',
            'grade': self.grade or '',
            'grade_text': self.grade or '全校',
            'subject': self.subject,
            'leader_uid': self.leader_uid or '',
            'leader_name': self.leader_name,
            'members': self.members or '',
            'duty': self.duty or '',
            'sort_order': self.sort_order or 0,
            'updated_at': self.updated_at.strftime('%Y-%m-%d %H:%M') if self.updated_at else '',
        }

    def __repr__(self):
        return f'<SubjectLeader {self.school_year} {self.grade}{self.subject} {self.leader_name}>'


# 学科固定展示顺序（任课表列序、备课组长分组顺序都用它；未列入的学科排在后面）
SUBJECT_ORDER = ['语文', '数学', '英语', '物理', '化学', '生物', '政治', '历史',
                 '地理', '体育', '音乐', '美术', '信息技术', '通用技术', '心理',
                 '劳动', '班会', '自习', '晚自习']

# 备课组长职责预设（前端「填入常用职责」按钮用；可按校情自行修改）
DUTY_TEMPLATES = {
    'default': ('① 统筹本年级本学科教学进度，组织每周集体备课并留存记录；'
                '② 组织单元主备、公开课与听评课，指导青年教师；'
                '③ 统一作业量、命题与阅卷标准，汇总月考质量分析；'
                '④ 建设与维护本学科教学资源库，及时传达教研通知。'),
    '毕业年级': ('① 牵头制定备考方案与复习进度表，组织命题与模拟考试；'
                 '② 组织考纲研读、专题突破与错题归因分析；'
                 '③ 关注临界生与学科短板，配合年级组制定补弱措施；'
                 '④ 汇总每次考试质量分析，动态调整复习策略。'),
    '综合组': ('① 统筹全校本学科课程开设与活动安排；'
               '② 组织跨年级教研与器材/场地管理；'
               '③ 负责校内外竞赛、展演与社团指导；'
               '④ 完成学校交办的其他教研任务。'),
}
