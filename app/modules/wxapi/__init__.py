# StuLink v1.19.49.35 2026-10-19
# 微信小程序 API 模块（/api/wx）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""微信小程序 API 模块。

网页端走 cookie session，小程序无法可靠回传 cookie，故本模块统一用
`X-Wx-Token` 请求头鉴权；业务数据一律复用既有服务层（成绩 / 课表 /
宿舍），**不复制任何计算逻辑**。

目录按领域拆分：
- core：鉴权、响应、序列化、令牌
- auth：登录/启动/当前用户
- workbench：首页工作台
- grades：成绩、考试分析、学生档案、画像列表
- academic：课表、查课、教师、业绩
- dorm：宿舍概览/房间/查询/统计
- notice：通知公告
- exam：考务批次/考场/监考
"""
from flask import Blueprint

bp = Blueprint('wxapi', __name__, url_prefix='/api/wx')

# 必须放在 bp 定义之后：各 routes_* 会反向导入本模块的 bp
from app.modules.wxapi.core import (  # noqa: E402,F401
    guards,
    response,
    serializers,
    tokens,
)
from app.modules.wxapi.auth import routes_auth  # noqa: E402,F401
from app.modules.wxapi.workbench import routes_workbench  # noqa: E402,F401
from app.modules.wxapi.grades import (  # noqa: E402,F401
    routes_analysis,
    routes_grades,
    routes_grades_compare,
    routes_grades_honor,
    routes_grades_stats,
    routes_portrait_list,
    routes_student_profile,
)
from app.modules.wxapi.academic import (  # noqa: E402,F401
    routes_achievements,
    routes_inspection,
    routes_schedule,
    routes_schedule_overview,
    routes_schedule_views,
    routes_teachers,
)
from app.modules.wxapi.dorm import (  # noqa: E402,F401
    routes_dorm,
    routes_dorm_search,
    routes_dorm_stats,
)
from app.modules.wxapi.notice import routes_notice  # noqa: E402,F401
from app.modules.wxapi.exam import (  # noqa: E402,F401
    routes_exam,
    routes_exam_rooms,
)
