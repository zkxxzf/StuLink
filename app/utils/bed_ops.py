# StuLink v1.19.47.4 2026-10-10
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""床位联动操作（学籍 / 住宿状态变更时复用）

背景（用户口径 2026-10-12）：
  学生被置为「离校」状态时（学籍情况=学籍已转出，或住校/走读=离校），
  系统应**提示确认后自动清除其床位** —— 否则离校学生仍占用床位，
  宿舍入住率、床位分配算法、宿舍统计都会把空位算错。

口径与系统既有「清除床位」一致：**保留床位行、只清空占用者**
（`BedAssignment.student_id = None`），不删行 —— 行本身承载「房间-床号」结构，
删了会让房间床位结构缺号。
"""
from app.extensions import db


def release_student_bed(student_id):
    """释放某学生占用的床位；返回 True 表示原先确实占用着床位（本次已释放）。"""
    if not student_id:
        return False
    from app.models import BedAssignment
    bed = BedAssignment.query.filter_by(student_id=student_id).first()
    if bed is None:
        return False
    bed.student_id = None
    bed.assigned_by = None
    db.session.add(bed)
    return True


def mark_boarding_left_school(student_id):
    """把住宿状态置为「离校」（无住宿记录则新建一条）；返回住宿记录对象。"""
    if not student_id:
        return None
    from app.models import StudentAccommodation
    acc = StudentAccommodation.query.filter_by(student_id=student_id).first()
    if acc is None:
        acc = StudentAccommodation(student_id=student_id)
        db.session.add(acc)
    acc.boarding_type = '离校'
    return acc


def student_has_bed(student_id):
    """该学生当前是否占用床位（前端据此决定是否弹「清除床位」确认提示）"""
    if not student_id:
        return False
    from app.models import BedAssignment
    return BedAssignment.query.filter_by(student_id=student_id).first() is not None
