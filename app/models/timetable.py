# StuLink v1.18.9.1 2026-10-10
# 课表模块模型：学期课表 / 节次定义 / 课表明细 / 调课记录 / 变更版本（独立库 timetable.db）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import re
from datetime import datetime, date, timedelta

from app.extensions import db


# ── 模块级常量（路由/服务/模板共用） ──────────────────────────────────────

# 2026-10-10：draft 的对外文案统一为「未启用」（原「草稿」容易被理解成"还没做完"，
# 而它的语义是"这张课表还没生效"，与是否排完课无关）。三处渲染点共用本表。
SCHEDULE_STATUS = {'draft': '未启用', 'active': '启用中', 'archived': '已归档'}
ENTRY_TYPES = {'normal': '正常', 'swap': '调课'}
SWAP_STATUS = {'pending': '待审核', 'approved': '已通过', 'rejected': '已驳回', 'executed': '已执行'}
SWAP_TYPES = {'personal': '个人调课', 'bulk': '统一调课'}
PERIOD_TYPES = {'morning': '上午', 'afternoon': '下午', 'evening': '晚自习', 'break': '课间/午休'}
WEEKDAY_NAMES = {1: '周一', 2: '周二', 3: '周三', 4: '周四', 5: '周五', 6: '周六', 7: '周日'}
# 2026-10-10：原 `MAX_PERIOD = 13`（"一天最多 13 节"）已删除 —— **一天几节由学校自己定，
# 系统不设业务上限**（用户报障：凭什么限制我）。下面这个是**编号合法性上界**，
# 只用来挡住手滑输入（如 999），正常排课永远碰不到；真实约束是"节次必须在本学期定义过"。
PERIOD_NUMBER_CEILING = 99


# ── 默认节次模板（高中作息：早读 + 上午5 + 下午4 + 晚自习3 = 13 节，仅新建学期的初始值，
#    不是上限；学校可自由增删，也可存成全局模板复用）
#
#    时长口径（2026-10-10 用户给定）：**早读 40 分钟 / 正课 45 分钟 / 晚自习 50 分钟**，
#    课间 10 分钟。一句话：每节课一般是 40、45 或 50 分钟，晚自习按 50 分钟排
#    （原来默认给的是 60 分钟，已改）。 ──────────────────────────────────

def get_default_periods():
    """返回 13 节默认节次配置列表（dict 形式），用于新建学期时批量写入 PeriodDef。

    结构：period_number / period_name / start_time / end_time / period_type / sort_order
    适配早读 + 上午 5 节 + 下午 4 节 + 晚自习 3 节；午休作为时间空档，
    不占用课表节次编号。早操和课间操是值守活动，由独立值班表维护。

    时长口径（2026-10-10 用户给定）：早读 40、正课 45、晚自习 50 分钟，课间 10 分钟。
    """
    return [
        {'period_number': 1,  'period_name': '早读',    'start_time': '07:00', 'end_time': '07:40', 'period_type': 'morning',   'sort_order': 1},
        {'period_number': 2,  'period_name': '第1节',   'start_time': '08:00', 'end_time': '08:45', 'period_type': 'morning',   'sort_order': 2},
        {'period_number': 3,  'period_name': '第2节',   'start_time': '08:55', 'end_time': '09:40', 'period_type': 'morning',   'sort_order': 3},
        {'period_number': 4,  'period_name': '第3节',   'start_time': '10:10', 'end_time': '10:55', 'period_type': 'morning',   'sort_order': 4},
        {'period_number': 5,  'period_name': '第4节',   'start_time': '11:05', 'end_time': '11:50', 'period_type': 'morning',   'sort_order': 5},
        {'period_number': 6,  'period_name': '第5节',   'start_time': '12:00', 'end_time': '12:45', 'period_type': 'morning',   'sort_order': 6},
        {'period_number': 7,  'period_name': '第6节',   'start_time': '14:00', 'end_time': '14:45', 'period_type': 'afternoon', 'sort_order': 7},
        {'period_number': 8,  'period_name': '第7节',   'start_time': '14:55', 'end_time': '15:40', 'period_type': 'afternoon', 'sort_order': 8},
        {'period_number': 9,  'period_name': '第8节',   'start_time': '16:00', 'end_time': '16:45', 'period_type': 'afternoon', 'sort_order': 9},
        {'period_number': 10, 'period_name': '第9节',   'start_time': '16:55', 'end_time': '17:40', 'period_type': 'afternoon', 'sort_order': 10},
        # 晚自习按 50 分钟排（2026-10-10 口径：早读 40 / 正课 45 / 晚自习 50；原来默认给的是 60 分钟）
        {'period_number': 11, 'period_name': '晚自习1', 'start_time': '19:00', 'end_time': '19:50', 'period_type': 'evening',   'sort_order': 11},
        {'period_number': 12, 'period_name': '晚自习2', 'start_time': '20:00', 'end_time': '20:50', 'period_type': 'evening',   'sort_order': 12},
        {'period_number': 13, 'period_name': '晚自习3', 'start_time': '21:00', 'end_time': '21:50', 'period_type': 'evening',   'sort_order': 13},
    ]


# ── 模型定义 ─────────────────────────────────────────────────────────────

class TermSchedule(db.Model):
    """学期课表档案

    一个学期对应一条记录，业务上同时只有一个 status='active' 的学期课表。
    periods: 该学期的节次定义（一对多，删除学期时级联删除节次）
    entries: 该学期的课表明细条目（一对多）
    """
    __bind_key__ = 'timetable'
    __tablename__ = 'term_schedules'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)         # 如 "2026-2027学年第一学期"
    school_year = db.Column(db.String(20), nullable=False)  # 如 "2026-2027"
    term = db.Column(db.String(10), nullable=False)         # 如 "第一学期"
    status = db.Column(db.String(10), default='draft')      # draft / active / archived
    description = db.Column(db.String(200))
    created_by = db.Column(db.Integer)                      # 创建人 users.id（跨库逻辑键）
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    # ── 学期周期维度（Task#27 增量新增，均为可空，不影响历史数据） ──────────
    start_date = db.Column(db.Date)                         # 学期第一天（应为周一）
    end_date = db.Column(db.Date)                           # 学期最后一天
    total_weeks = db.Column(db.Integer, default=20)         # 教学周总数
    week_start_offset = db.Column(db.Integer, default=0)    # 第1周相对 start_date 的偏移（周）
    is_current = db.Column(db.Boolean, default=False)       # 标记当前学期（配合 status='active'）

    # 同库关系：节次定义随学期删除而级联删除
    periods = db.relationship('PeriodDef', backref='term_schedule',
                              cascade='all, delete-orphan',
                              order_by='PeriodDef.sort_order',
                              lazy='dynamic')
    # 课表明细条目（数量大，使用 dynamic 避免全量加载）
    entries = db.relationship('ScheduleEntry', backref='term_schedule',
                              lazy='dynamic')

    # ── 学期周期计算方法（纯 Python 计算，不查库） ──────────────────────────

    def contains_date(self, target_date=None):
        """判断某日期是否落在本学期区间内（未配置 start_date 时返回 False）"""
        if not self.start_date:
            return False
        d = target_date or date.today()
        if d < self.start_date:
            return False
        if self.end_date and d > self.end_date:
            return False
        return True

    def week1_monday(self):
        """第 1 周所属自然周的周一（开学日往前退到本周一）。

        2026-10-10：周次一律按**自然周（周一~周日）**划分 —— 开学第一天不是周一时，
        第 1 周就是「开学日 ~ 该周周日」这个不完整周，**不从开学日往后数 7 天**。
        老口径（开学=周首、每周跨到下周同一天）会让周次整体"漂移"，周四开学就出现
        "第 1 周 = 09-03~09-09"这种既非自然周、也对不上校历的区间。
        """
        if not self.start_date:
            return None
        return self.start_date - timedelta(days=self.start_date.isoweekday() - 1)

    def get_week_number(self, target_date=None):
        """返回该日期属于第几周（int），或 None（不在学期范围/未配置起止日期）。

        计算方式（自然周口径）：((target_date - 第1周周一).days // 7) + 1
        - week_start_offset，并校验 1 <= week <= total_weeks。
        """
        if not self.start_date:
            return None
        d = target_date or date.today()
        if not self.contains_date(d):
            return None
        anchor = self.week1_monday()
        offset = self.week_start_offset or 0
        week = ((d - anchor).days // 7) + 1 - offset
        tw = self.total_weeks or 20
        if week < 1 or week > tw:
            return None
        return week

    def get_week_date_range(self, week):
        """返回该教学周的 (周一日期, 周日日期) 元组，或 None。

        第 1 周从**开学日**算起（开学不是周一时，前面的工作日还没开始）；
        最后一周不越过学期结束日。
        """
        anchor = self.week1_monday()
        if anchor is None or week is None:
            return None
        try:
            week = int(week)
        except (ValueError, TypeError):
            return None
        tw = self.total_weeks or 20
        if week < 1 or week > tw:
            return None
        offset = self.week_start_offset or 0
        monday = anchor + timedelta(days=(week - 1 + offset) * 7)
        start = monday
        if self.start_date and start < self.start_date:
            start = self.start_date          # 第 1 周：开学前那几天不算
        end = monday + timedelta(days=6)
        if self.end_date and end > self.end_date:
            end = self.end_date              # 最后一周：不越过学期结束日
        return (start, end)

    def get_current_week(self):
        """等价 get_week_number(date.today())"""
        return self.get_week_number(date.today())

    def period_text(self):
        """如 "2026-09-01 ~ 2027-01-15 · 共20周 · 第3周"；未配置日期时返回提示文案"""
        parts = []
        if self.start_date and self.end_date:
            parts.append(f'{self.start_date.strftime("%Y-%m-%d")} ~ '
                         f'{self.end_date.strftime("%Y-%m-%d")}')
        elif self.start_date:
            parts.append(f'{self.start_date.strftime("%Y-%m-%d")} 起')
        if self.total_weeks:
            parts.append(f'共{self.total_weeks}周')
        cw = self.get_current_week()
        if cw:
            parts.append(f'第{cw}周')
        return ' · '.join(parts) if parts else '未配置日期'

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'school_year': self.school_year,
            'term': self.term,
            'status': self.status,
            'status_text': SCHEDULE_STATUS.get(self.status, self.status),
            'description': self.description,
            'created_by': self.created_by,
            # 2026-10-10：同 ScheduleEntry.to_dict，isoformat 等价更快
            'created_at': (self.created_at.isoformat(sep=' ', timespec='seconds')
                           if self.created_at else None),
            'updated_at': (self.updated_at.isoformat(sep=' ', timespec='seconds')
                           if self.updated_at else None),
            # ── 学期周期维度（Task#27） ──
            'start_date': self.start_date.strftime('%Y-%m-%d') if self.start_date else None,
            'end_date': self.end_date.strftime('%Y-%m-%d') if self.end_date else None,
            'total_weeks': self.total_weeks,
            'week_start_offset': self.week_start_offset,
            'is_current': bool(self.is_current),
            'current_week': self.get_current_week(),
            'period_text': self.period_text(),
        }

    def __repr__(self):
        return f'<TermSchedule {self.id} {self.name} [{self.status}]>'


class PeriodDef(db.Model):
    """节次定义：每节含名称/时间/类型，**数量不限**（由学校自定义，见 save_periods）

    唯一约束 (term_schedule_id, period_number) 保证同一学期内节次号不重复。
    period_type: morning / afternoon / evening / break
    """
    __bind_key__ = 'timetable'
    __tablename__ = 'period_defs'

    id = db.Column(db.Integer, primary_key=True)
    term_schedule_id = db.Column(db.Integer, db.ForeignKey('term_schedules.id'), nullable=False)
    period_number = db.Column(db.Integer, nullable=False)   # 1..N（N 由学校自定义）
    period_name = db.Column(db.String(20), nullable=False)  # 如 "早读" / "第1节" / "晚自习1"
    start_time = db.Column(db.String(5))                    # "07:00"
    end_time = db.Column(db.String(5))                      # "07:40"
    period_type = db.Column(db.String(10))                  # morning/afternoon/evening/break
    sort_order = db.Column(db.Integer, default=0)

    __table_args__ = (
        db.UniqueConstraint('term_schedule_id', 'period_number', name='uq_period_ts_num'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'term_schedule_id': self.term_schedule_id,
            'period_number': self.period_number,
            'period_name': self.period_name,
            'start_time': self.start_time,
            'end_time': self.end_time,
            'period_type': self.period_type,
            'period_type_text': PERIOD_TYPES.get(self.period_type, self.period_type),
            'sort_order': self.sort_order,
        }

    def __repr__(self):
        return f'<PeriodDef {self.period_number} {self.period_name}>'


class ScheduleEntry(db.Model):
    """课表明细条目（核心表）

    每行代表某学期某班级某星期某节次的一节课。
    - teacher_uid: 逻辑键，关联 system.db 中的教师用户（跨库不建物理外键）
    - entry_type: normal=正常排课 / swap=调课后自动生成
    - original_entry_id: 若为调课产生，指向被替换的原条目 id（同库逻辑键）
    - is_deleted: 软删除标记，便于版本追溯而非物理删除
    索引:
    - idx_entry_class: 按学期+班级+星期+节次快速定位（班级课表查询）
    - idx_entry_teacher: 按学期+教师+星期+节次快速定位（教师课表/冲突检测）
    """
    __bind_key__ = 'timetable'
    __tablename__ = 'schedule_entries'

    id = db.Column(db.Integer, primary_key=True)
    term_schedule_id = db.Column(db.Integer, db.ForeignKey('term_schedules.id'), nullable=False)
    grade = db.Column(db.String(10), nullable=False)        # 如 "高一"
    class_name = db.Column(db.String(10), nullable=False)   # 如 "01班"
    weekday = db.Column(db.Integer, nullable=False)         # 1=周一 ... 7=周日
    period_number = db.Column(db.Integer, nullable=False)   # 1-13
    week_range = db.Column(db.String(20), default='1-18')   # 周次范围，如 "1-18"
    subject = db.Column(db.String(20), nullable=False)      # 学科
    teacher_uid = db.Column(db.String(16))                  # 教师编号（逻辑键）
    teacher_name = db.Column(db.String(50))                 # 教师姓名快照
    room = db.Column(db.String(30))                         # 教室
    # 走班教学班（新高考 3+1+2，2026-09-26 迁移 migrate_teaching_class.py）：
    # 空＝行政班课（全班在本班教室上）；非空＝走班课，只有属于该教学班的学生来上，
    # 如「物化生1」。对不走班的学校该列恒为空，所有既有逻辑不受影响。
    teaching_class = db.Column(db.String(30))
    entry_type = db.Column(db.String(10), default='normal') # normal / swap
    original_entry_id = db.Column(db.Integer)               # 调课来源条目 id
    note = db.Column(db.String(100))
    is_deleted = db.Column(db.Boolean, default=False)       # 软删除
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        db.Index('idx_entry_class', 'term_schedule_id', 'grade', 'class_name', 'weekday', 'period_number'),
        db.Index('idx_entry_teacher', 'term_schedule_id', 'teacher_uid', 'weekday', 'period_number'),
        db.Index('idx_entry_teaching', 'term_schedule_id', 'grade', 'teaching_class'),
    )

    def week_badge(self):
        """周次角标：只在"值得标注"时返回文字，否则空串（避免满屏角标）。

        - 空 / 全周 / 整学期范围（如 1-18、1-20）→ 不标注；
        - 单周 / 双周 / 含奇偶标记 → '单周' / '双周'；
        - 其它（跳周、部分周次、单周次）→ '周次 x'。
        """
        s = (self.week_range or '').strip()
        if not s or s in ('全周', '全部', '每周', '1-18'):
            return ''
        if s in ('单周', '双周'):
            return s
        if '单' in s:
            return '单周'
        if '双' in s:
            return '双周'
        # 1-N 且 N>=15 视为整学期（各校周数不同，18/20/22 都可能），不标注
        m = re.match(r'^1\s*[-–—~～]\s*(\d{1,2})$', s)
        if m and int(m.group(1)) >= 15:
            return ''
        return f'周次 {s}'

    def to_dict(self):
        return {
            'id': self.id,
            'term_schedule_id': self.term_schedule_id,
            'grade': self.grade,
            'class_name': self.class_name,
            'weekday': self.weekday,
            'weekday_text': WEEKDAY_NAMES.get(self.weekday, ''),
            'period_number': self.period_number,
            'week_range': self.week_range,
            'week_badge': self.week_badge(),
            'subject': self.subject,
            'teacher_uid': self.teacher_uid,
            'teacher_name': self.teacher_name,
            'room': self.room,
            'teaching_class': self.teaching_class,
            'entry_type': self.entry_type,
            'entry_type_text': ENTRY_TYPES.get(self.entry_type, self.entry_type),
            'original_entry_id': self.original_entry_id,
            'note': self.note,
            'is_deleted': self.is_deleted,
            # 2026-10-10：isoformat(timespec='seconds') 与 strftime('%Y-%m-%d %H:%M:%S')
            # 输出逐字符一致但更快；总课表等热路径每条 to_dict 要算 2 次时间戳
            'created_at': (self.created_at.isoformat(sep=' ', timespec='seconds')
                           if self.created_at else None),
            'updated_at': (self.updated_at.isoformat(sep=' ', timespec='seconds')
                           if self.updated_at else None),
        }

    def __repr__(self):
        return (f'<ScheduleEntry {self.id} {self.grade}{self.class_name} '
                f'{WEEKDAY_NAMES.get(self.weekday, "")}第{self.period_number}节 {self.subject}>')


class ScheduleSwap(db.Model):
    """调课记录

    - swap_type: personal=个人调课 / bulk=统一调课
    - is_permanent: True=永久修改课表 / False=仅当天临时调换
    - status: pending/approved/rejected/executed
    - original_entry_id / target_entry_id: 同库逻辑键，指向 ScheduleEntry.id
    - applicant_uid: 逻辑键，关联 system.db 教师用户
    索引:
    - idx_swap_status_created: 按状态+时间查询待审核列表
    - idx_swap_applicant: 按申请人查询个人调课历史
    """
    __bind_key__ = 'timetable'
    __tablename__ = 'schedule_swaps'

    id = db.Column(db.Integer, primary_key=True)
    term_schedule_id = db.Column(db.Integer, db.ForeignKey('term_schedules.id'))
    swap_type = db.Column(db.String(10), default='personal')  # personal / bulk
    original_entry_id = db.Column(db.Integer)                 # 原课表条目 id（逻辑键）
    target_entry_id = db.Column(db.Integer)                   # 审核后新生成的条目 id
    applicant_uid = db.Column(db.String(16), nullable=False)  # 教师编号
    applicant_name = db.Column(db.String(50))
    new_weekday = db.Column(db.Integer)                       # 目标星期（目标那天按周几的课表上课）
    new_period = db.Column(db.Integer)                        # 目标节次
    new_room = db.Column(db.String(30))                       # 目标教室
    swap_date = db.Column(db.Date)                            # 目标日期（调过去的这一天）
    # 2026-10-09：调休场景（如周六上周三的课）——原课所在日期 + 那天实际按周几的课表上课
    source_date = db.Column(db.Date)
    source_weekday = db.Column(db.Integer)
    is_permanent = db.Column(db.Boolean, default=False)       # True=永久改课表
    reason = db.Column(db.String(200))
    status = db.Column(db.String(10), default='pending')      # pending/approved/rejected/executed
    reviewed_by = db.Column(db.Integer)                       # 审核人 users.id（跨库逻辑键）
    review_note = db.Column(db.String(200))
    reviewed_at = db.Column(db.DateTime)
    # 分级审批：当前停在第几级（0 起）+ 每一级谁审的、什么意见（JSON 数组）
    approval_step = db.Column(db.Integer, default=0)
    approvals_json = db.Column(db.Text)
    # 2026-10-10：统一调课批次号 —— 同一次「统一调课」生成的多条记录共享同一 batch_id，
    # 列表按批次展示、整批一次性审批/执行，避免几十条一个个点。
    batch_id = db.Column(db.String(32), index=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    __table_args__ = (
        db.Index('idx_swap_status_created', 'status', 'created_at'),
        db.Index('idx_swap_applicant', 'applicant_uid'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'term_schedule_id': self.term_schedule_id,
            'swap_type': self.swap_type,
            'swap_type_text': SWAP_TYPES.get(self.swap_type, self.swap_type),
            'original_entry_id': self.original_entry_id,
            'target_entry_id': self.target_entry_id,
            'applicant_uid': self.applicant_uid,
            'applicant_name': self.applicant_name,
            'new_weekday': self.new_weekday,
            'new_weekday_text': WEEKDAY_NAMES.get(self.new_weekday, '') if self.new_weekday else '',
            'new_period': self.new_period,
            'new_room': self.new_room,
            'swap_date': self.swap_date.strftime('%Y-%m-%d') if self.swap_date else None,
            'source_date': self.source_date.strftime('%Y-%m-%d') if self.source_date else None,
            'source_weekday': self.source_weekday,
            'source_weekday_text': (WEEKDAY_NAMES.get(self.source_weekday, '')
                                    if self.source_weekday else ''),
            'approval_step': self.approval_step or 0,
            'approvals': self.approvals(),
            'batch_id': self.batch_id,
            'is_permanent': self.is_permanent,
            'reason': self.reason,
            'status': self.status,
            'status_text': SWAP_STATUS.get(self.status, self.status),
            'reviewed_by': self.reviewed_by,
            'review_note': self.review_note,
            'reviewed_at': self.reviewed_at.strftime('%Y-%m-%d %H:%M:%S') if self.reviewed_at else None,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S') if self.created_at else None,
        }

    # ── 分级审批轨迹 ──────────────────────────────────────────────────
    def approvals(self):
        """审批轨迹列表（每项 {step, role, role_text, user_id, user_name,
        action, action_text, note, at}），解析失败返回 []。"""
        import json
        raw = self.approvals_json
        if not raw:
            return []
        try:
            data = json.loads(raw)
            return data if isinstance(data, list) else []
        except (ValueError, TypeError):
            return []

    def add_approval(self, step, role, role_text, user_id, user_name, action,
                     action_text, note=''):
        """追加一条审批记录（step 从 0 起）并写回 JSON。"""
        import json
        from datetime import datetime as _dt
        rows = self.approvals()
        rows.append({
            'step': step or 0,
            'role': role or '',
            'role_text': role_text or '',
            'user_id': user_id,
            'user_name': user_name or '',
            'action': action,
            'action_text': action_text or action,
            'note': note or '',
            'at': _dt.now().strftime('%Y-%m-%d %H:%M'),
        })
        self.approvals_json = json.dumps(rows, ensure_ascii=False)

    def __repr__(self):
        return f'<ScheduleSwap {self.id} {self.applicant_name} {self.swap_type} [{self.status}]>'


class ScheduleVersion(db.Model):
    """课表变更版本快照

    每次对课表条目的增删改都记录一条版本，snapshot_json 保存修改前的数据快照，
    用于审计追溯和误操作回滚。
    action: create / update / delete / swap
    """
    __bind_key__ = 'timetable'
    __tablename__ = 'schedule_versions'

    id = db.Column(db.Integer, primary_key=True)
    term_schedule_id = db.Column(db.Integer, db.ForeignKey('term_schedules.id'))
    entry_id = db.Column(db.Integer)                    # 被修改的 ScheduleEntry.id（逻辑键）
    action = db.Column(db.String(10))                   # create/update/delete/swap
    snapshot_json = db.Column(db.Text)                  # 修改前数据快照（JSON 字符串）
    operator_id = db.Column(db.Integer)                 # 操作人 users.id（跨库逻辑键）
    operator_name = db.Column(db.String(50))
    remark = db.Column(db.String(200))
    operated_at = db.Column(db.DateTime, default=datetime.now)

    __table_args__ = (
        db.Index('idx_version_ts_entry', 'term_schedule_id', 'entry_id'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'term_schedule_id': self.term_schedule_id,
            'entry_id': self.entry_id,
            'action': self.action,
            'snapshot_json': self.snapshot_json,
            'operator_id': self.operator_id,
            'operator_name': self.operator_name,
            'remark': self.remark,
            'operated_at': self.operated_at.strftime('%Y-%m-%d %H:%M:%S') if self.operated_at else None,
        }

    def __repr__(self):
        return f'<ScheduleVersion {self.id} entry={self.entry_id} {self.action}>'
