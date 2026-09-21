# StuLink v1.7.0 2026-08-02
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from app.models.user import User
from app.models.student import Student
from app.models.room import Room, BedAssignment, StudentAccommodation
from app.models.dictionary import DictCategory, DictItem
from app.models.operation_log import OperationLog
from app.models.assignment_history import AssignmentHistory
from app.models.class_profile import ClassProfile, ClassSubject
from app.models.permission_group import PermissionGroup
from app.models.user_class_link import UserClassLink
from app.models.grade_setting import GradeSetting
from app.models.grades import Exam, ExamScore, ExamBand, TeacherSubjectLink, \
    AiKey, AiGlobalKey, AiReport, AiChatMessage, AffairRoomLib  # v1.12.1 考场房间库
from app.models.points import PointRecord, PointRuleTemplate
from app.models.system_setting import SystemSetting
from app.models.academic import Teacher, Timetable, TimetableEntry, \
    InspectionRecord, TeacherAchievement, CourseSwap, \
    FormCategory, FormTemplate, FormQuestion, FormSubmission, FormAnswer, \
    WorkRecord, AttendanceRecord, RECORD_TYPES, ATTENDANCE_STATUS
from app.models.notification import Notification, NotificationRead, \
    NotificationRecipient, CATEGORY_LABELS
from app.models.portrait import StudentPortrait, PortraitComment, PortraitEvent
from app.models.user_data_scope import UserDataScope
from app.models.timetable import (
    TermSchedule, PeriodDef, ScheduleEntry, ScheduleSwap, ScheduleVersion,
    get_default_periods,
    SCHEDULE_STATUS, ENTRY_TYPES, SWAP_STATUS as TT_SWAP_STATUS,
    SWAP_TYPES as TT_SWAP_TYPES, PERIOD_TYPES, WEEKDAY_NAMES, MAX_PERIOD,
)

__all__ = ['User', 'Student', 'Room', 'BedAssignment', 'StudentAccommodation',
           'DictCategory', 'DictItem', 'OperationLog', 'AssignmentHistory',
           'ClassProfile', 'ClassSubject',
           'PermissionGroup', 'UserClassLink', 'GradeSetting',
           'Exam', 'ExamScore', 'ExamBand', 'TeacherSubjectLink',
           'AiKey', 'AiGlobalKey', 'AiReport', 'AiChatMessage',
           'AffairRoomLib', 'PointRecord', 'PointRuleTemplate',
           'SystemSetting',
           'Teacher', 'Timetable', 'TimetableEntry',
           'InspectionRecord', 'TeacherAchievement', 'CourseSwap',
           'FormCategory', 'FormTemplate', 'FormQuestion', 'FormSubmission', 'FormAnswer',
           'WorkRecord', 'AttendanceRecord', 'RECORD_TYPES', 'ATTENDANCE_STATUS',
           'Notification', 'NotificationRead', 'NotificationRecipient',
           'CATEGORY_LABELS',
           'StudentPortrait', 'PortraitComment', 'PortraitEvent', 'UserDataScope',
           # 课表模块（timetable.db）
           'TermSchedule', 'PeriodDef', 'ScheduleEntry', 'ScheduleSwap', 'ScheduleVersion',
           'get_default_periods',
           'SCHEDULE_STATUS', 'ENTRY_TYPES', 'TT_SWAP_STATUS', 'TT_SWAP_TYPES',
           'PERIOD_TYPES', 'WEEKDAY_NAMES', 'MAX_PERIOD']

