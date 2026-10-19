# StuLink 2026-10-18
# 通用删除归档（回收站）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""通用删除归档（回收站）模型。

为什么需要它（2026-10-18 审计）：
系统里大量"删除 / 清空"是**硬删除**（`db.session.delete` / `query.delete()`），
删完只剩一条操作日志"删了 X 条"，**内容本身无法找回** —— 出了事只能翻数据库备份，
而备份通常是按天/小时的，当天的操作覆盖不到。用户口径："删除后怎么去看审计日志？
难道除了备份就只能傻愣着吗"。

于是统一引入回收站：
- 删除前把整行序列化进 `trash_records`（JSON），保留 30 天；
- 管理端「回收站」可查看、按单行或整批**恢复**，也可手动彻底清除；
- 归档失败**不阻塞删除**（只记日志），保证业务可用性。
"""
from datetime import datetime, timedelta

from app.extensions import db

# 回收站保留天数（过期后可一键清理；不自动删，避免"悄悄消失"）
TRASH_RETENTION_DAYS = 30


class TrashRecord(db.Model):
    __bind_key__ = 'system'
    __tablename__ = 'trash_records'

    id = db.Column(db.Integer, primary_key=True)
    module = db.Column(db.String(20), nullable=False)   # grades / academic / dormitory / system
    kind = db.Column(db.String(40), nullable=False)     # 恢复用的模型键，见 utils/trash._MODELS
    kind_label = db.Column(db.String(60))               # 中文名（列表展示）
    ref_id = db.Column(db.Integer)                      # 原记录 id（整批时为空）
    summary = db.Column(db.String(255))                 # 人话摘要（哪场考试 / 哪个班级…）
    payload = db.Column(db.Text)                        # 单行 dict 或整批 list 的 JSON
    item_count = db.Column(db.Integer, default=1)
    operator_id = db.Column(db.Integer)
    operator_name = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.now)
    expires_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now() + timedelta(days=TRASH_RETENTION_DAYS))
    restored_at = db.Column(db.DateTime)                # 非空 = 已恢复过

    __table_args__ = (
        db.Index('idx_trash_kind', 'kind'),
        db.Index('idx_trash_created', 'created_at'),
        db.Index('idx_trash_restored', 'restored_at'),
    )

    def preview_rows(self, limit=3):
        """取前几行用于页面展开预览（不暴露敏感字段由调用方决定）。"""
        import json
        try:
            data = json.loads(self.payload or 'null')
        except (TypeError, ValueError):
            return []
        if isinstance(data, list):
            return data[:limit]
        return [data] if isinstance(data, dict) else []
