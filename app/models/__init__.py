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
    AiKey, AiGlobalKey, AiReport

__all__ = ['User', 'Student', 'Room', 'BedAssignment', 'StudentAccommodation',
           'DictCategory', 'DictItem', 'OperationLog', 'AssignmentHistory',
           'ClassProfile', 'ClassSubject',
           'PermissionGroup', 'UserClassLink', 'GradeSetting',
           'Exam', 'ExamScore', 'ExamBand', 'TeacherSubjectLink',
           'AiKey', 'AiGlobalKey', 'AiReport']

