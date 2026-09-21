# StuLink v1.17.0 2026-09-21
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
    status = db.Column(db.String(10), default='approved')  # pending/approved/rejected
    submitted_by = db.Column(db.Integer)                   # 提交人 users.id
    reviewed_by = db.Column(db.Integer)                    # 审核人 users.id
    reviewed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.now)

    def __repr__(self):
        return f'<Achievement {self.teacher_name} {self.title}>'


# 业绩类别与查课结果的统一文案（模板与页面渲染共用）
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
