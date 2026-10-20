# StuLink v1.19.49.41 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序调课申请：我的课程 / 可选目标时段 / 提交。

只支持「调到本班 + 本人教师都空闲的格子」（空档直接调）。
与同事换时间（对调）需要在网页端挑对方课程并等对方确认，小程序暂不开放。
"""
from datetime import date

from flask import g, request

from app.extensions import db
from app.models.timetable import ScheduleEntry
from app.modules.academic.services import swap_service
from app.modules.wxapi import bp
from app.modules.wxapi.academic import swap_common as common
from app.modules.wxapi.core.guards import wx_perm_required
from app.modules.wxapi.core.response import wx_err, wx_ok
from app.utils.helpers import log_operation

PERM = 'academic.swap'
MAX_ITEMS = 10


def _parse_date(value):
    """'YYYY-MM-DD' → date；空 / 非法返回 None。"""
    try:
        return date.fromisoformat(str(value or '').strip())
    except (ValueError, AttributeError):
        return None


def _my_entry(uid, entry_id):
    """取本人任教的课表条目；不存在 / 已删 / 越权返回 None。"""
    if not entry_id:
        return None
    entry = db.session.get(ScheduleEntry, int(entry_id))
    if not entry or entry.is_deleted:
        return None
    if entry.teacher_uid != uid:
        return None
    return entry


def _slot_item(slot):
    """目标时段 → 小程序字段（只保留渲染需要的）。"""
    return {
        'weekday': slot.get('weekday'),
        'period': slot.get('period_number'),
        'period_name': slot.get('period_name') or '',
        'start_time': slot.get('start_time') or '',
        'end_time': slot.get('end_time') or '',
        'available': bool(slot.get('available')),
        'swappable': bool(slot.get('swappable')),
        'conflict_desc': slot.get('conflict_desc') or '',
    }


@bp.route('/swap/entries')
@wx_perm_required(PERM)
def wx_swap_entries():
    """我的本学期课程（申请调课第一步）。"""
    uid, _name = common.applicant(g.wx_user)
    sched = common.active_schedule()
    if sched is None:
        return wx_ok({'has_schedule': False, 'entries': []})
    rows = common.my_entries(uid)
    return wx_ok({
        'has_schedule': True,
        'entries': [common.entry_item(e) for e in rows],
    })


@bp.route('/swap/slots')
@wx_perm_required(PERM)
def wx_swap_slots():
    """某节课可调的目标时段（query: entry_id / swap_date / is_permanent）。"""
    uid, _name = common.applicant(g.wx_user)
    entry = _my_entry(uid, request.args.get('entry_id', type=int))
    if entry is None:
        return wx_err('只能为本人任教的课程申请调课', 403)
    permanent = (request.args.get('is_permanent') or '') in ('1', 'true')
    swap_date = _parse_date(request.args.get('swap_date'))
    week_range = entry.week_range if permanent else None
    week = None
    if not permanent and swap_date:
        week = swap_service._week_of_date(entry.term_schedule_id, swap_date)
    slots = swap_service.get_available_slots(
        entry.term_schedule_id, entry.grade, entry.class_name,
        entry.teacher_uid, exclude_entry_id=entry.id, include_all=True,
        week_range=week_range, week=week,
        teaching_class=entry.teaching_class)
    return wx_ok({
        'entry': common.entry_item(entry),
        'slots': [_slot_item(s) for s in slots],
        'week_text': swap_service.week_badge(week) if week else '',
        'is_permanent': permanent,
    })


def _clean_items(raw):
    """整理前端提交的调课明细（字段强类型 + 去掉明显非法项）。"""
    out = []
    for it in (raw or [])[:MAX_ITEMS]:
        if not isinstance(it, dict):
            continue
        try:
            eid = int(it.get('entry_id'))
            twd = int(it.get('new_weekday'))
            tpd = int(it.get('new_period'))
        except (TypeError, ValueError):
            continue
        out.append({
            'entry_id': eid,
            'new_weekday': twd,
            'new_period': tpd,
            'swap_date': _parse_date(it.get('swap_date')),
        })
    return out


def _conflict_text(msg, result):
    """整批冲突时把第一条原因带出来（比只给"提交失败"有用）。"""
    conflicts = (result or {}).get('conflicts') or []
    if not conflicts:
        return msg
    first = conflicts[0]
    return '%s：%s' % (msg, first.get('desc') or '目标时段不可用')


@bp.route('/swap/apply', methods=['POST'])
@wx_perm_required(PERM)
def wx_swap_apply():
    """提交调课申请（body: items / reason / is_permanent / source_date）。"""
    data = request.get_json(silent=True) or {}
    items = _clean_items(data.get('items'))
    reason = (data.get('reason') or '').strip()[:200]
    if not items:
        return wx_err('请选择要调走的课')
    if not reason:
        return wx_err('请填写调课理由')
    uid, name = common.applicant(g.wx_user)
    for it in items:
        if _my_entry(uid, it['entry_id']) is None:
            return wx_err('只能为本人任教的课程申请调课', 403)
    ok, msg, result = swap_service.apply_swap_batch(
        applicant_uid=uid, applicant_name=name, items=items,
        is_permanent=bool(data.get('is_permanent')),
        reason=reason,
        source_date=_parse_date(data.get('source_date')),
        source_weekday=data.get('source_weekday') or None)
    if not ok:
        return wx_err(_conflict_text(msg, result))
    # 2026-10-20 第十六轮：小程序调课写操作补审计（网页端每条都有，小程序原为 0）
    log_operation(g.wx_user, '新增', '调课记录', result.get('rep_id'),
                  '%d 条（小程序）：%s' % (len(items), reason[:40]),
                  module='academic')
    return wx_ok({'id': result.get('rep_id'),
                  'created': result.get('created') or 0}, msg)
