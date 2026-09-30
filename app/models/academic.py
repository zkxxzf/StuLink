# StuLink v1.18.7.0 2026-09-30
# 教务模块模型：教师名单 / 课表 / 查课记录 / 教师业绩（独立库 academic.db）
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


class Timetable(db.Model):
    """课表档案（按学期/年级），明细见 TimetableEntry"""
    __bind_key__ = 'academic'
    __tablename__ = 'timetables'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)        # 如 2026-2027学年第一学期
    grade = db.Column(db.String(10))                       # 适用年级（可空=全校）
    created_by = db.Column(db.Integer)                     # 创建人 users.id
    created_at = db.Column(db.DateTime, default=datetime.now)

    def __repr__(self):
        return f'<Timetable {self.name}>'


class TimetableEntry(db.Model):
    """课表明细：某位教师一周内的一节课"""
    __bind_key__ = 'academic'
    __tablename__ = 'timetable_entries'

    id = db.Column(db.Integer, primary_key=True)
    timetable_id = db.Column(db.Integer, nullable=False, index=True)
    teacher_uid = db.Column(db.String(16), index=True)
    teacher_name = db.Column(db.String(50))
    subject = db.Column(db.String(20))
    class_name = db.Column(db.String(10))
    weekday = db.Column(db.Integer)                        # 1=周一 ... 7=周日
    period = db.Column(db.Integer)                         # 节次 1..10
    week_range = db.Column(db.String(20))                  # 周次范围，如 "1-20"
    room = db.Column(db.String(30))                        # 教室
    note = db.Column(db.String(100))

    __table_args__ = (
        db.Index('idx_tt_entry_teacher_slot', 'teacher_uid', 'weekday', 'period'),
    )


class InspectionRecord(db.Model):
    """教务查课记录（巡课检查教师上课情况）"""
    __bind_key__ = 'academic'
    __tablename__ = 'inspection_records'

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
    __bind_key__ = 'academic'
    __tablename__ = 'teacher_achievements'

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
    created_at = db.Column(db.DateTime, default=datetime.now)

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
    __bind_key__ = 'academic'
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
    created_at = db.Column(db.DateTime, default=datetime.now)

    def to_dict(self):
        return {
            'id': self.id,
            'achievement_id': self.achievement_id,
            'file_name': self.file_name,
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
INSPECTION_RESULTS = [
    ('normal', '正常'), ('late', '迟到'), ('absent', '缺课'),
    ('swap', '调课'), ('other', '其他'),
]


class CourseSwap(db.Model):
    """调课记录"""
    __tablename__ = 'course_swaps'
    __bind_key__ = 'academic'

    id = db.Column(db.Integer, primary_key=True)
    swap_type = db.Column(db.String(10), nullable=False)        # personal / bulk
    applicant_uid = db.Column(db.String(16), index=True)        # 教师编号
    applicant_name = db.Column(db.String(50))
    original_timetable_entry_id = db.Column(db.Integer)         # 原课表条目ID
    original_date = db.Column(db.Date)
    original_period = db.Column(db.Integer)
    original_class = db.Column(db.String(10))
    original_subject = db.Column(db.String(20))
    new_date = db.Column(db.Date)
    new_period = db.Column(db.Integer)
    new_room = db.Column(db.String(30))
    reason = db.Column(db.String(200))
    scope_grade = db.Column(db.String(10))                      # 统一调课影响年级
    scope_note = db.Column(db.String(100))                      # 影响说明
    status = db.Column(db.String(10), default='pending')        # pending/approved/rejected
    reviewed_by = db.Column(db.Integer)
    reviewed_at = db.Column(db.DateTime)
    reject_reason = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.now)

    def __repr__(self):
        return f'<CourseSwap {self.id} {self.swap_type} {self.status}>'


SWAP_STATUS = {'pending': '待审核', 'approved': '已通过', 'rejected': '已驳回'}
SWAP_TYPES = {'personal': '个人调课', 'bulk': '统一调课'}


# ── 表单收集系统 ──────────────────────────────────────────────

FORM_STATUS = {'draft': '草稿', 'open': '进行中', 'closed': '已关闭', 'archived': '已归档'}
FORM_TARGET_TYPES = {'all': '全体', 'teachers': '教师', 'students': '学生', 'grade': '指定年级'}
QUESTION_TYPES = [
    ('text', '单行文本'), ('textarea', '多行文本'), ('single_choice', '单选'),
    ('multi_choice', '多选'), ('file', '文件上传'), ('date', '日期'), ('number', '数字'),
]
SUBMISSION_STATUS = {'submitted': '已提交', 'approved': '已通过', 'rejected': '已驳回'}


class FormCategory(db.Model):
    """表单分类"""
    __tablename__ = 'form_categories'
    __bind_key__ = 'academic'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(30), nullable=False, unique=True)
    sort_order = db.Column(db.Integer, default=0)

    def __repr__(self):
        return f'<FormCategory {self.name}>'


class FormTemplate(db.Model):
    """表单模板定义"""
    __tablename__ = 'form_templates'
    __bind_key__ = 'academic'

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

    questions = db.relationship('FormQuestion', backref='template',
                                cascade='all, delete-orphan',
                                order_by='FormQuestion.sort_order')

    def __repr__(self):
        return f'<FormTemplate {self.title}>'


class FormQuestion(db.Model):
    """表单题目"""
    __tablename__ = 'form_questions'
    __bind_key__ = 'academic'

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
    __bind_key__ = 'academic'

    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(db.Integer, db.ForeignKey('form_templates.id'), nullable=False)
    submitter_type = db.Column(db.String(10))                 # teacher/student
    submitter_id = db.Column(db.Integer)
    submitter_name = db.Column(db.String(50))
    submitter_uid = db.Column(db.String(30))
    submitter_grade = db.Column(db.String(10))
    submitter_class = db.Column(db.String(10))
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
    __bind_key__ = 'academic'

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


# ── 任课安排 / 备课组长（2026-09-25）─────────────────────────────────────────
# 任课安排表本身不落库：它由课表条目（timetable.db 的 schedule_entries）聚合而来，
# 保证"课表一改，任课表立刻跟着变"，避免两处数据打架。这里只落库无法自动推导的
# 备课组长信息。

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
