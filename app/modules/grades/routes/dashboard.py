# StuLink v1.19.47.29 2026-10-13
# 成绩工作台：首页仪表盘（模块落地页）+ 实时刷新接口
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""成绩工作台首页仪表盘。

2026-10-13 用户口径：
- 成绩模块落地页由「历次考试」改为**首页仪表盘**（侧边栏第一项）；
- 仪表盘展示：考试总次数、各年级考试次数、限时练/期中/期末/月考数量、
  已开具证明条数、各类别参与人数、每个年级参与人数、当前动态；
- 支持**实时更新**：页面每 30s 轮询 `/grades/api/dashboard`（页面隐藏时暂停），
  也可点「刷新」手动拉一次；两端共用 dashboard_service.build_overview，口径一致。
"""
import logging

from flask import jsonify, render_template
from flask_login import login_required, current_user

from app.modules.grades import bp
from app.modules.grades.services import dashboard_service
from app.utils.cache import cache
from app.utils.decorators import perm_required

logger = logging.getLogger(__name__)

# PERF-15：仪表盘是成绩模块的默认落地页且前端每 30s 轮询一次，未缓存时
# 100 人在线 = 每分钟约 200 次请求，每次 3 条跨数十万行的 COUNT(DISTINCT)，
# 是 grades.db 读压力最大的单一来源。数据本身是准实时看板（考试数/证明数/参与人数），
# 60s TTL 完全够用，命中后 DB 压力归零。
_DASHBOARD_TTL = 60


def _overview_for(user):
    """取仪表盘数据（按用户缓存 60s；含数据范围，key 必须带 user.id）"""
    key = f'grades_dashboard_v1_{user.id}'
    data = cache.get(key)
    if data is None:
        data = dashboard_service.build_overview(user)
        cache.set(key, data, timeout=_DASHBOARD_TTL)
    return data


@bp.route('/')
@login_required
@perm_required('grades.view')
def dashboard():
    """首页仪表盘（成绩模块默认落地页）"""
    data = _overview_for(current_user)
    return render_template('grades/dashboard.html', d=data)


@bp.route('/api/dashboard')
@login_required
@perm_required('grades.view')
def dashboard_api():
    """仪表盘数据接口（前端定时轮询，实现实时更新；只读）"""
    return jsonify(_overview_for(current_user))
