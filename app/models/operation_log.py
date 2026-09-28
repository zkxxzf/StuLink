# StuLink v1.7.0 2026-08-02
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from datetime import datetime
from app.extensions import db


class OperationLog(db.Model):
    __tablename__ = 'operation_logs'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    action = db.Column(db.String(20), nullable=False)  # 创建/更新/删除/导入/导出/登录
    target_type = db.Column(db.String(30))  # 学生/宿舍/床位分配/用户
    target_id = db.Column(db.Integer)
    detail = db.Column(db.Text)  # JSON 格式数据
    ip_address = db.Column(db.String(45))
    module = db.Column(db.String(30), default='system')   # dormitory/system/points/grades
    severity = db.Column(db.String(10), default='INFO')  # INFO/WARNING/ERROR
    # ── HTTP 审计字段（请求级兜底网，2026-09-25 迁移 migrate_oplog_http_fields.py）──
    endpoint = db.Column(db.String(120))     # Flask endpoint，如 system.students.create
    method = db.Column(db.String(10))        # POST/PUT/PATCH/DELETE
    status_code = db.Column(db.Integer)      # 响应状态码（用于区分成功/被拒/报错）
    request_id = db.Column(db.String(36))    # 同一次请求的多条日志串联
    created_at = db.Column(db.DateTime, default=datetime.now)

    user = db.relationship('User', foreign_keys=[user_id])


