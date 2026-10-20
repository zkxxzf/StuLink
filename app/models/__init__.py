# StuLink v1.7.0 2026-08-02
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from app.models.user import User
from app.models.student import Student
from app.models.room import Room, BedAssignment, StudentAccommodation
from app.models.dictionary import DictCategory, DictItem
from app.models.operation_log import OperationLog
from app.models.login_throttle import LoginThrottle
from app.models.ai_usage import AiUsage
from app.models.trash import TrashRecord
from app.models.assignment_history import AssignmentHistory
from app.models.class_profile import ClassProfile, ClassSubject
from app.models.permission_group import PermissionGroup
from app.models.user_class_link import UserClassLink
from app.models.grade_setting import GradeSetting
from app.models.grades import Exam, ExamScore, ExamBand, TeacherSubjectLink, \
    AiKey, AiGlobalKey, AiReport, AiChatMessage, AffairRoomLib, \
    ExamTeacherLink, ExamAffair, AffairRoom, AffairStudent  # v1.19.49.37 考试任课快照 / 考务
from app.models.grades import TeacherLinkPlan, TeacherLinkPlanRow  # 任课映射方案（2026-10-14）
from app.models.grades import AffairTeacher, AffairSchedule, AffairInvigilator, \
    AffairInvigilation  # v1.19.49.37 考务环节④⑤⑥：任课核对 / 考试时间表 / 监考编排
from app.models.grades import ScoreChangeLog, ScoreReviewRequest  # 分数修改留痕 / 成绩复核申请
from app.models.points import PointRecord, PointRuleTemplate
from app.models.morality import (RewardPunishment, RewardPunishmentAttachment,
                                 KINDS, REWARD_CATEGORIES, PUNISH_CATEGORIES,
                                 REWARD_LEVELS, PUNISH_LEVELS, STATUSES,
                                 DOC_TYPES, category_options, category_label,
                                 level_options, level_label)
from app.models.system_setting import SystemSetting
from app.models.academic import Teacher, \
    InspectionRecord, TeacherAchievement, \
    FormCategory, FormTemplate, FormQuestion, FormSubmission, FormAnswer, \
    WorkRecord, AttendanceRecord, RECORD_TYPES, ATTENDANCE_STATUS, \
    SubjectLeader, SUBJECT_ORDER, SubjectLeaderPlan, \
    WorkloadRule, ExamPeriod, WORKLOAD_KINDS
from app.models.notification import Notification, NotificationRead, \
    NotificationRecipient, CATEGORY_LABELS
from app.models.portrait import StudentPortrait, PortraitComment, PortraitEvent
from app.models.user_data_scope import UserDataScope
from app.models.user_permission_group import UserPermissionGroup
from app.models.wechat import (  # 微信绑定 / 扫码票据
    MAX_WECHAT_BINDINGS,
    WeChatBinding,
    WeChatTicket,
)
from app.models.timetable import (
    TermSchedule, PeriodDef, ScheduleEntry, ScheduleSwap, ScheduleVersion,
    get_default_periods,
    SCHEDULE_STATUS, ENTRY_TYPES, SWAP_STATUS as TT_SWAP_STATUS,
    SWAP_TYPES as TT_SWAP_TYPES, PERIOD_TYPES, WEEKDAY_NAMES,
    PERIOD_NUMBER_CEILING,
)

__all__ = ['User', 'Student', 'Room', 'BedAssignment', 'StudentAccommodation',
           'DictCategory', 'DictItem', 'OperationLog', 'LoginThrottle', 'AiUsage',
           'AssignmentHistory',
           'ClassProfile', 'ClassSubject',
           'PermissionGroup', 'UserClassLink', 'GradeSetting',
           'Exam', 'ExamScore', 'ExamBand', 'TeacherSubjectLink', 'ExamTeacherLink',
           'TeacherLinkPlan', 'TeacherLinkPlanRow',
           'ExamAffair', 'AffairRoom', 'AffairStudent',
           'AiKey', 'AiGlobalKey', 'AiReport', 'AiChatMessage',
           'AffairRoomLib', 'PointRecord', 'PointRuleTemplate',
           # 德育奖惩（morality.db，2026-10-13 v1.19.49.37）
           'RewardPunishment', 'RewardPunishmentAttachment',
           'REWARD_CATEGORIES', 'PUNISH_CATEGORIES', 'REWARD_LEVELS',
           'PUNISH_LEVELS', 'DOC_TYPES', 'category_options', 'category_label',
           'level_options', 'level_label',
           'SystemSetting',
           'Teacher',
           'InspectionRecord', 'TeacherAchievement',
           'FormCategory', 'FormTemplate', 'FormQuestion', 'FormSubmission', 'FormAnswer',
           'WorkRecord', 'AttendanceRecord', 'RECORD_TYPES', 'ATTENDANCE_STATUS',
           'WorkloadRule', 'ExamPeriod', 'WORKLOAD_KINDS',
           'Notification', 'NotificationRead', 'NotificationRecipient',
           'CATEGORY_LABELS',
           'StudentPortrait', 'PortraitComment', 'PortraitEvent', 'UserDataScope',
           # 多身份改造 P1：用户 ↔ 权限组 多对多
           'UserPermissionGroup',
           # 课表模块（timetable.db）
           'TermSchedule', 'PeriodDef', 'ScheduleEntry', 'ScheduleSwap', 'ScheduleVersion',
           'get_default_periods',
           'SCHEDULE_STATUS', 'ENTRY_TYPES', 'TT_SWAP_STATUS', 'TT_SWAP_TYPES',
           'PERIOD_TYPES', 'WEEKDAY_NAMES', 'PERIOD_NUMBER_CEILING',
           'WeChatBinding', 'WeChatTicket', 'MAX_WECHAT_BINDINGS']

