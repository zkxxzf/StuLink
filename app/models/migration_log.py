# StuLink v1.19.41.2 2026-10-12
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""迁移登记表：记录哪些迁移脚本已在当前库上成功应用过。

背景（2026-10-12 全项目审计）：scripts/ 下 97 个迁移脚本无统一执行记录，
"现网跑到第几个"只能靠各脚本自称幂等，无法审计（毕业归档 SQL 引用已删列
正因此成为隐患）。

- 迁移脚本执行成功后由 scripts/_mig_log.py 调 record(script) 登记一行；
- 脚本开头 is_applied(script) 为真则整体跳过 → 幂等；
- bind_key='system'（主库）：所有迁移脚本可无脑共用，不随模块分库漂移。
"""
from datetime import datetime

from app.extensions import db


class MigrationLog(db.Model):
    """迁移执行登记（一行 = 一个迁移脚本的一次成功应用）"""
    __bind_key__ = 'system'
    __tablename__ = 'migration_log'

    id = db.Column(db.Integer, primary_key=True)
    script = db.Column(db.String(200), unique=True, nullable=False, index=True)
    applied_at = db.Column(db.DateTime, default=datetime.now, nullable=False)
    note = db.Column(db.String(200))

    def __repr__(self):
        return f'<MigrationLog {self.script} @ {self.applied_at}>'
