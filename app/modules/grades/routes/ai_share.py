# StuLink v1.19.62.65 2026-10-22
# AI 报告分享：生成 / 查询 / 撤销（仅报告所有者）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""AI 报告分享（2026-10-22 用户要求：做成链接，别人登录即可查看）。

三条硬约束：
  1. **只有报告所有者**能生成/查看/撤销链接（`report.user_id == current_user.id`）；
     管理员也不能替别人分享（与「AI 分析仅本人可见」一致）。
  2. 一个报告同时最多一条有效链接（服务层保证），页面不会出现多个地址。
  3. 生成/撤销都写操作日志（谁在何时把哪份报告对外分享），便于事后追溯。
"""
from flask import abort, jsonify, request
from flask_login import current_user, login_required

from app.models.grades import AiReport
from app.modules.grades import bp
from app.modules.grades.services import ai_share_service as share_svc
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation

DAY_CHOICES = (0, 7, 30, 90)
DEFAULT_DAYS = 30


def _own_report(rid):
    """取报告并校验归属（非本人一律 403，连"这报告存在吗"都不告诉外人）。"""
    report = AiReport.query.get_or_404(rid)
    if report.user_id != current_user.id:
        abort(403)
    return report


def _read_days():
    """读有效期参数（天）；非法值回落到默认 30 天。0 = 永久。"""
    raw = request.form.get('days')
    if raw is None:
        body = request.get_json(silent=True) or {}
        raw = body.get('days')
    try:
        days = int(raw)
    except (TypeError, ValueError):
        days = DEFAULT_DAYS
    return days if days in DAY_CHOICES or days == 0 else DEFAULT_DAYS


@bp.route('/ai/reports/<int:rid>/share', methods=['GET'])
@login_required
@perm_required('grades.view')
def ai_share_state(rid):
    """当前分享状态（供报告区渲染"分享"按钮的浮层）。"""
    report = _own_report(rid)
    share = share_svc.active_for(report.id)
    return jsonify(success=True, data={
        'report_id': report.id,
        'share': share_svc.share_state(share),
    })


@bp.route('/ai/reports/<int:rid>/share', methods=['POST'])
@login_required
@perm_required('grades.view')
def ai_share_create(rid):
    """生成（或复用）分享链接。"""
    report = _own_report(rid)
    days = _read_days()
    share, created = share_svc.create_or_reuse(report, days, current_user.id)
    log_operation(current_user, '分享', 'AI 报告', report.id,
                  f'有效期 {"永久" if not days else str(days) + " 天"}'
                  f'{"（新建）" if created else "（复用）"}', module='grades')
    return jsonify(success=True, data={
        'share': share_svc.share_state(share),
        'created': created,
    })


@bp.route('/ai/reports/<int:rid>/share/revoke', methods=['POST'])
@login_required
@perm_required('grades.view')
def ai_share_revoke(rid):
    """撤销分享：链接立即失效（已打开过的页面刷新即提示失效）。"""
    report = _own_report(rid)
    share = share_svc.active_for(report.id)
    changed = share_svc.revoke(share)
    if changed:
        log_operation(current_user, '撤销分享', 'AI 报告', report.id, '', module='grades')
    return jsonify(success=True, message='链接已失效' if changed else '当前没有有效的分享链接')
