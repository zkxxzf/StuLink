"""寝室分配历史记录服务 — 静默失败，不阻塞主业务"""
import json
from datetime import datetime

from app.models.assignment_history import AssignmentHistory
from app.extensions import db


def _current_semester():
    """根据当前月份推导学期，如 '2025-2026-2'"""
# StuLink v1.5.0 2026-07-01
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0
    now = datetime.now()
    year = now.year
    month = now.month
    if 2 <= month <= 7:
        return f"{year-1}-{year}-2"    # 春季学期
    else:
        return f"{year}-{year+1}-1"    # 秋季学期


def record_assignment(action_type, student, room, bed_number,
                      operator=None, detail=None):
    """宿舍分配历史（向后兼容）"""
    record_history('dormitory', action_type, student, operator,
                   room=room, bed_number=bed_number, detail=detail)


def record_history(target_type, action_type, student, operator=None,
                   room=None, bed_number=None, detail=None):
    """统一历史记录入口，支持所有模块"""
    try:
        record = AssignmentHistory(
            target_type=target_type,
            student_id=student.id if student else 0,
            student_name=student.name if student else '(未知)',
            grade=student.grade if student else '',
            class_name=student.class_name if student else '',
            gender=student.gender if student else '',
            action_type=action_type,
            semester=_current_semester(),
            operator_id=operator.id if operator else None,
            operator_name=operator.real_name if operator else '',
            room_id=room.id if room else None,
            room_display=room.display_name if room else None,
            bed_number=bed_number,
            detail=json.dumps(detail, ensure_ascii=False) if detail else None,
        )
        db.session.add(record)
    except Exception:
        pass  # 静默失败，绝不阻塞主业务


def record_batch(records):
    """批量记录（auto_assign / clear_class 场景）"""
    for r in records:
        record_assignment(**r)
