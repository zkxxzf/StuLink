# StuLink v1.19.49.42 2026-10-13
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""德育奖惩模型（morality.db）

设计口径（用户 2026-10-13）：
  「德育里先出一个奖惩信息。奖励很多、惩罚也很多，先做考试作弊这种惩罚，
   剩下的补齐；该有的信息都要有，还要能上传附件。」

数据：
  - 与积分（points.db）分开独立成库：奖惩是**长期档案**（含附件、处分期限、撤销记录），
    与每日积分流水不是一个生命周期，拆库后互不影响。
  - 学生信息按**快照**存（学号/姓名/年级/班级），跨库不设外键 —— 与 PointRecord 同口径：
    学生毕业/转班后历史奖惩记录仍可查、不被改。
"""
from datetime import date, datetime

from app.extensions import db

# ── 类型 ──────────────────────────────────────────────────────────────────
KIND_REWARD = 'reward'
KIND_PUNISH = 'punish'
KINDS = {
    KIND_REWARD: '奖励',
    KIND_PUNISH: '惩罚',
}

# ── 奖励类别（用户：奖励那可多了）──────────────────────────────────────────
REWARD_CATEGORIES = [
    ('study', '学业优秀'),
    ('competition', '竞赛获奖'),
    ('moral', '品德表彰'),
    ('progress', '进步显著'),
    ('sports_art', '文体活动'),
    ('volunteer', '志愿服务'),
    ('cadre', '学生干部'),
    ('attendance', '全勤守纪'),
    ('other', '其他奖励'),
]

# ── 惩罚类别（首个落地：考试作弊；其余补齐常用项）──────────────────────────
PUNISH_CATEGORIES = [
    ('exam_cheating', '考试作弊'),
    ('late_absence', '迟到旷课'),
    ('class_discipline', '课堂违纪'),
    ('bullying', '校园欺凌'),
    ('property_damage', '破坏公物'),
    ('smoking_drinking', '抽烟饮酒'),
    ('phone_violation', '违规使用手机'),
    ('dorm_violation', '宿舍违纪'),
    ('violence', '打架斗殴'),
    ('other', '其他违纪'),
]

CATEGORY_LABELS = dict(REWARD_CATEGORIES + [('r_' + k, v) for k, v in REWARD_CATEGORIES]
                       + [('p_' + k, v) for k, v in PUNISH_CATEGORIES])


def category_options(kind):
    """某类型的类别选项 [(key, label)]"""
    return REWARD_CATEGORIES if kind == KIND_REWARD else PUNISH_CATEGORIES


def category_label(kind, key):
    for k, v in (category_options(kind) if kind else []):
        if k == key:
            return v
    return key or '—'


# ── 级别 ──────────────────────────────────────────────────────────────────
# 奖励：表彰层级；惩罚：处分档次
REWARD_LEVELS = [
    ('national', '国家级'),
    ('province', '省级'),
    ('city', '市级'),
    ('school', '校级'),
    ('grade', '年级级'),
    ('class', '班级级'),
]

PUNISH_LEVELS = [
    ('criticism', '通报批评'),
    ('warning', '警告'),
    ('serious_warning', '严重警告'),
    ('demerit', '记过'),
    ('major_demerit', '记大过'),
    ('probation', '留校察看'),
]

LEVEL_LABELS = dict(REWARD_LEVELS + PUNISH_LEVELS)


def level_options(kind):
    return REWARD_LEVELS if kind == KIND_REWARD else PUNISH_LEVELS


def level_label(level):
    return LEVEL_LABELS.get(level or '', level or '—')


# ── 状态 ──────────────────────────────────────────────────────────────────
STATUS_DRAFT = 'draft'          # 待确认（登记后需德育处确认）
STATUS_CONFIRMED = 'confirmed'  # 已生效
STATUS_REVOKED = 'revoked'      # 已撤销（申诉成功/期满解除）
STATUSES = {
    STATUS_DRAFT: '待确认',
    STATUS_CONFIRMED: '已生效',
    STATUS_REVOKED: '已撤销',
}

# ── 附件材料分类 ──────────────────────────────────────────────────────────
DOC_TYPES = [
    ('evidence', '佐证材料'),
    ('exam_record', '考场记录'),
    ('statement', '情况说明'),
    ('commitment', '承诺书'),
    ('certificate', '证书扫描件'),
    ('notice', '通知/通报'),
    ('other', '其他'),
]
DOC_TYPE_LABELS = dict(DOC_TYPES)


class RewardPunishment(db.Model):
    """奖惩记录（一条 = 一次表彰或一次处分）"""
    __bind_key__ = 'morality'
    __tablename__ = 'reward_punishments'

    id = db.Column(db.Integer, primary_key=True)

    # ── 学生快照（跨库不设外键，与 PointRecord 同口径）──
    student_no = db.Column(db.String(20), nullable=False, index=True)
    student_name = db.Column(db.String(50))
    grade = db.Column(db.String(10))
    class_name = db.Column(db.String(10))

    # ── 基本分类 ──
    kind = db.Column(db.String(10), nullable=False, default=KIND_REWARD)  # reward/punish
    category = db.Column(db.String(30))       # 见 REWARD_CATEGORIES / PUNISH_CATEGORIES
    level = db.Column(db.String(20))          # 表彰层级 / 处分档次

    # ── 内容 ──
    title = db.Column(db.String(120), nullable=False)   # 事由（一句话）
    description = db.Column(db.Text)                    # 详细情况
    happen_date = db.Column(db.Date, index=True)        # 发生日期
    # 奖励：颁发单位；惩罚：认定/作出处理决定的部门
    source = db.Column(db.String(60))
    award_name = db.Column(db.String(120))              # 奖励：奖项名称

    # ── 惩罚专属（考试作弊等）──
    related_exam = db.Column(db.String(120))            # 涉及考试（如「高三上期中考」）
    subject = db.Column(db.String(30))                  # 涉及科目
    # 处理/影响：取消本次成绩、记入档案、取消评优资格…
    effect = db.Column(db.String(200))
    period_months = db.Column(db.Integer)               # 处分期限（月）；0/空=无期限
    expire_date = db.Column(db.Date)                    # 到期日（可手填，未填按期限算）

    # ── 流程 ──
    status = db.Column(db.String(10), default=STATUS_DRAFT)
    confirmed_by = db.Column(db.String(50))
    confirmed_at = db.Column(db.DateTime)
    revoked_at = db.Column(db.DateTime)
    revoke_reason = db.Column(db.String(200))
    notify_parent = db.Column(db.Boolean, default=False)  # 是否已通知家长

    # ── 记录人 ──
    operator_id = db.Column(db.Integer)
    operator_name = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        db.Index('idx_rp_grade_class', 'grade', 'class_name'),
        db.Index('idx_rp_kind', 'kind'),
        db.Index('idx_rp_date', 'happen_date'),
    )

    def kind_text(self):
        return KINDS.get(self.kind, self.kind or '—')

    def category_text(self):
        return category_label(self.kind, self.category)

    def level_text(self):
        return level_label(self.level)

    def status_text(self):
        return STATUSES.get(self.status, self.status or '—')

    def effect_text(self):
        """展示用：影响 + 期限"""
        parts = []
        if self.effect:
            parts.append(self.effect)
        if self.period_months:
            parts.append(f'期限 {self.period_months} 个月')
        if self.expire_date:
            parts.append(f'到期 {self.expire_date}')
        return '；'.join(parts) or '—'

    def to_dict(self, attachments=None):
        return {
            'id': self.id,
            'student_no': self.student_no,
            'student_name': self.student_name,
            'grade': self.grade,
            'class_name': self.class_name,
            'kind': self.kind,
            'kind_text': self.kind_text(),
            'category': self.category,
            'category_text': self.category_text(),
            'level': self.level,
            'level_text': self.level_text(),
            'title': self.title,
            'description': self.description or '',
            'happen_date': (self.happen_date.strftime('%Y-%m-%d')
                            if self.happen_date else ''),
            'source': self.source or '',
            'award_name': self.award_name or '',
            'related_exam': self.related_exam or '',
            'subject': self.subject or '',
            'effect': self.effect or '',
            'effect_text': self.effect_text(),
            'period_months': self.period_months,
            'expire_date': (self.expire_date.strftime('%Y-%m-%d')
                            if self.expire_date else ''),
            'status': self.status,
            'status_text': self.status_text(),
            'confirmed_by': self.confirmed_by or '',
            'confirmed_at': (self.confirmed_at.strftime('%Y-%m-%d %H:%M')
                             if self.confirmed_at else ''),
            'revoked_at': (self.revoked_at.strftime('%Y-%m-%d %H:%M')
                           if self.revoked_at else ''),
            'revoke_reason': self.revoke_reason or '',
            'notify_parent': bool(self.notify_parent),
            'operator_name': self.operator_name or '',
            'created_at': (self.created_at.strftime('%Y-%m-%d %H:%M')
                           if self.created_at else ''),
            'attachments': [a.to_dict() for a in (attachments or [])],
        }


class RewardPunishmentAttachment(db.Model):
    """奖惩附件（考场记录、情况说明、承诺书、证书扫描件…）

    文件落在 `app/static/uploads/morality/<record_id>/`，落盘名 uuid.ext；
    `/static/uploads` 直链已被全局 before_request 封禁 —— 必须走带鉴权的
    `morality.rp_file_view` / `rp_file_download` 访问。
    """
    __bind_key__ = 'morality'
    __tablename__ = 'reward_punishment_attachments'

    id = db.Column(db.Integer, primary_key=True)
    record_id = db.Column(db.Integer, index=True, nullable=False)
    file_name = db.Column(db.String(200))     # 原始文件名
    stored_name = db.Column(db.String(80))    # 落盘名 uuid.ext
    ext = db.Column(db.String(10))
    mime = db.Column(db.String(60))
    size = db.Column(db.Integer)
    doc_type = db.Column(db.String(20))       # 材料分类，见 DOC_TYPES
    uploaded_by = db.Column(db.Integer)
    uploaded_name = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.now)

    def size_text(self):
        if not self.size:
            return '—'
        if self.size < 1024:
            return f'{self.size}B'
        if self.size < 1024 * 1024:
            return f'{self.size / 1024:.0f}KB'
        return f'{self.size / 1024 / 1024:.1f}MB'

    def doc_type_text(self):
        return DOC_TYPE_LABELS.get(self.doc_type or '', '其他')

    def to_dict(self):
        return {
            'id': self.id,
            'record_id': self.record_id,
            'file_name': self.file_name,
            'ext': self.ext,
            'mime': self.mime,
            'size': self.size,
            'size_text': self.size_text(),
            'doc_type': self.doc_type or '',
            'doc_type_text': self.doc_type_text(),
            'uploaded_name': self.uploaded_name or '',
            'created_at': (self.created_at.strftime('%Y-%m-%d %H:%M')
                           if self.created_at else ''),
        }
