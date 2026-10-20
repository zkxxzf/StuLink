# StuLink v1.19.49.48 2026-10-20
# 教育事业统计模块入口
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""教育事业综合统计（2026-10-20 新增）。

页面挂系统管理下（url_prefix=/system）：
  /system/edu-stat    统计页：学生 4 张表 + 教师 3 张表预览与导出
  /system/edu-assets  校舍资产：按学年录入 5170/5176/1102 参数
"""
from flask import Blueprint

bp = Blueprint('edustat', __name__, url_prefix='/system')

from app.modules.edustat import routes  # noqa: E402,F401
