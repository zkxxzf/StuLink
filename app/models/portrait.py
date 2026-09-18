# StuLink v1.9.2 2026-09-16
# 学生画像模块模型（占位骨架）：独立库 portrait.db
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""学生画像（规划中，当前为占位骨架）

本模块当前仅建立独立数据库与最小占位表，业务功能后续迭代。

规划方向：
- 学习画像：成绩趋势、学科均衡度、进退步分析（数据来源 grades.db，按学号读取）
- 行为画像：积分、宿舍表现、出勤记录等综合表现（数据来源 points.db / dormitory.db）
- 成长档案：荣誉、评语、关键事件记录

数据关联：经学号快照关联 system.db 学生基础信息（跨库只读读取，
任一来源模块异常不影响画像模块自身与系统管理基础数据）。
"""
from datetime import datetime

from app.extensions import db


class StudentProfile(db.Model):
    """学生画像占位表：预留学号关联与画像摘要字段"""
    __bind_key__ = 'portrait'
    __tablename__ = 'student_profiles'

    id = db.Column(db.Integer, primary_key=True)
    student_no = db.Column(db.String(20), unique=True, index=True)  # 学号快照（关联 system.db students）
    summary = db.Column(db.Text)                # 画像摘要（JSON 预留）
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    def __repr__(self):
        return f'<StudentProfile {self.student_no}>'
