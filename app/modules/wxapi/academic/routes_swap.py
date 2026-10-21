# StuLink v1.19.50.64 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序调课：我的申请（列表 / 详情 / 撤回）与「待我确认」的换课。

审批与执行（academic.timetable / academic.swap_approve）仍走网页端，
小程序只覆盖教师自己能做的三件事：看进度、撤回自己的申请、确认别人和我换课。
"""
from flask import g, request

from app.models.timetable import SWAP_STATUS
from app.modules.academic.services import swap_service
from app.modules.wxapi import bp
from app.modules.wxapi.academic import swap_common as common
from app.modules.wxapi.core.guards import wx_perm_required
from app.modules.wxapi.core.response import wx_err, wx_ok
from app.utils.helpers import log_operation

PERM = 'academic.swap'
PAGE_SIZE = 20
MAX_SIZE = 50
STATUS_ORDER = ('pending', 'approved', 'executed', 'rejected')


def _status_options():
    """筛选项：待审核 / 已通过 / 已执行 / 已驳回。"""
    return [{'key': k, 'label': SWAP_STATUS.get(k, k)} for k in STATUS_ORDER]


def _status():
    """读取 status 参数（非法值按"全部"处理）。"""
    val = (request.args.get('status') or '').strip()
    return val if val in SWAP_STATUS else ''


def _confirm_item(d):
    """待我确认的一条：对方申请里跟我相关的那节课。"""
    return {
        'id': d.get('id'),
        'applicant_name': d.get('applicant_name') or '',
        'exchange_kind_text': d.get('exchange_kind_text') or '换课',
        'original_text': d.get('original_text') or '',
        'partner_entry_text': d.get('partner_entry_text') or '',
        'reason': d.get('reason') or '',
        'swap_date': d.get('swap_date') or '',
        'created_at': d.get('created_at') or '',
    }


@bp.route('/swap/list')
@wx_perm_required(PERM)
def wx_swap_list():
    """我的调课申请（query: status / page / page_size）。"""
    uid, _name = common.applicant(g.wx_user)
    sched = common.active_schedule()
    # 2026-10-20 审计修复（越权）：无教师档案时 applicant() 返回 uid=None，而
    # swap_service.get_swap_list 的过滤条件是 `if not is_reviewer and applicant_uid:`
    # —— uid 为空就**不加申请人条件**，等于读到本学期全部人的调课单（含理由）。
    # 与 swap_common.applicant 的 fail-closed 约定一致：这里按空集返回。
    if not uid or sched is None:
        return wx_ok({'items': [], 'total': 0, 'page': 1, 'pages': 0,
                      'status': '', 'statuses': _status_options(),
                      'pending': 0, 'confirm_count': 0,
                      'has_schedule': sched is not None})
    status = _status()
    page = max(1, request.args.get('page', type=int) or 1)
    size = min(MAX_SIZE, max(1, request.args.get('page_size', type=int)
                             or PAGE_SIZE))
    items, pag = swap_service.get_swap_list(
        status=status or None, applicant_uid=uid, schedule_id=sched.id,
        page=page, per_page=size)
    confirms = swap_service.get_partner_pending(uid, limit=30)
    return wx_ok({
        'items': [common.swap_item(d, uid) for d in items],
        'total': pag.total, 'page': pag.page, 'pages': pag.pages,
        'status': status, 'statuses': _status_options(),
        'pending': swap_service.count_pending(sched.id, uid),
        'confirm_count': len(confirms),
        'has_schedule': True,
    })


@bp.route('/swap/confirm')
@wx_perm_required(PERM)
def wx_swap_confirm():
    """待我确认的换课 / 代课（逐条，不按批次折叠）。"""
    uid, _name = common.applicant(g.wx_user)
    rows = swap_service.get_partner_pending(uid, limit=30)
    return wx_ok({'items': [_confirm_item(d) for d in rows]})


@bp.route('/swap/<int:sid>')
@wx_perm_required(PERM)
def wx_swap_detail(sid):
    """调课详情：仅申请人本人或换课对方可见。"""
    uid, _name = common.applicant(g.wx_user)
    d = swap_service.get_swap_detail(sid)
    if not d:
        return wx_err('调课记录不存在', 404)
    if d.get('applicant_uid') != uid and d.get('partner_uid') != uid:
        return wx_err('无权查看该调课记录', 403)
    return wx_ok(common.swap_item(d, uid))


@bp.route('/swap/<int:sid>/cancel', methods=['POST'])
@wx_perm_required(PERM)
def wx_swap_cancel(sid):
    """撤回自己的待审核申请。"""
    uid, _name = common.applicant(g.wx_user)
    ok, msg, _sw = swap_service.cancel_swap(sid, uid)
    if not ok:
        return wx_err(msg)
    log_operation(g.wx_user, '撤销', '调课记录', sid, '（小程序）',
                  module='academic')
    return wx_ok({'id': sid}, msg)


@bp.route('/swap/<int:sid>/partner', methods=['POST'])
@wx_perm_required(PERM)
def wx_swap_partner(sid):
    """换课对方确认：body {action: agree|refuse, note}。"""
    data = request.get_json(silent=True) or {}
    action = (data.get('action') or '').strip()
    if action not in ('agree', 'refuse'):
        return wx_err('未知的确认动作')
    uid, name = common.applicant(g.wx_user)
    d = swap_service.get_swap_detail(sid)
    if not d:
        return wx_err('调课记录不存在', 404)
    # 2026-10-19 审计：只允许对方本人确认。服务层 docstring 的前提就是
    # 「路由层已校验操作者是 partner 本人」，把 admin 放进来会让对方课表
    # 在本人从未表态的情况下被改。管理员走网页端审批链路。
    if d.get('partner_uid') != uid:
        return wx_err('只有对方本人可以确认该申请', 403)
    ok, msg, _sw = swap_service.respond_exchange(
        sid, action, g.wx_user.id, name,
        (data.get('note') or '').strip()[:200])
    if not ok:
        return wx_err(msg)
    log_operation(g.wx_user, '换课确认', '调课记录', sid,
                  '%s（小程序）' % action, module='academic')
    return wx_ok({'id': sid}, msg)
