# StuLink v1.19.49.39 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序查课接口：我的查课记录（只读）。

只返回当前账号对应教师自己的记录，并叠加教务数据范围；
不提供录入 / 修改 / 删除（那些需要 academic.edit，仍走网页端）。
"""
from datetime import date

from flask import g, request

from app.models import InspectionRecord
from app.models.academic import INSPECTION_EXEMPT_KEYS, INSPECTION_RESULTS
from app.modules.wxapi import bp
from app.modules.wxapi.core.guards import wx_login_required
from app.modules.wxapi.core.response import wx_ok
from app.modules.wxapi.academic.routes_schedule import _teacher_uid

PAGE_SIZE = 30
MAX_SIZE = 50
RESULT_TEXT = dict(INSPECTION_RESULTS)


@bp.route('/inspection/mine')
@wx_login_required
def wx_inspection_mine():
    """我的查课记录（query: result / page / page_size）。"""
    uid = _teacher_uid(g.wx_user)
    if not uid:
        return wx_ok({
            'items': [], 'total': 0,
            'stats': _empty_stats(), 'results': _result_options(),
        })
    # 只按「本人教师编号」取数：自己的查课记录不因当前任课范围变化而消失
    rows = (InspectionRecord.query
            .filter(InspectionRecord.teacher_uid == uid)
            .order_by(InspectionRecord.inspect_date.desc(),
                      InspectionRecord.id.desc()).all())
    stats = _stats(rows)
    result = (request.args.get('result') or '').strip()
    if result in RESULT_TEXT:
        rows = [r for r in rows if (r.result or 'normal') == result]
    page = max(1, request.args.get('page', type=int) or 1)
    size = min(MAX_SIZE, max(1, request.args.get('page_size', type=int)
                             or PAGE_SIZE))
    start = (page - 1) * size
    return wx_ok({
        'items': [_row(r) for r in rows[start:start + size]],
        'total': len(rows),
        'page': page,
        'page_size': size,
        'stats': stats,
        'results': _result_options(),
    })


def _row(r):
    """查课记录行：日期 / 节次 / 班级 / 学科 / 结果 / 备注。"""
    return {
        'id': r.id,
        'date': r.inspect_date.strftime('%Y-%m-%d') if r.inspect_date else '',
        'period': r.period,
        'period_text': _period_text(r.period),
        'grade': r.grade or '',
        'class_name': r.class_name or '',
        'subject': r.subject or '',
        'result': r.result or 'normal',
        'result_text': RESULT_TEXT.get(r.result or 'normal', '其他'),
        'note': r.note or '',
    }


def _period_text(n):
    """节次文案（1=早读，2..6=第1..5节，其余按序号）。"""
    if not n:
        return ''
    if n == 1:
        return '早读'
    if 2 <= n <= 6:
        return '第%d节' % (n - 1)
    return '第%d节' % n


def _stats(rows):
    """结果计数 + 本月 / 异常数。"""
    stats = _empty_stats()
    month = date.today().strftime('%Y-%m')
    for r in rows:
        key = r.result or 'normal'
        stats[key] = stats.get(key, 0) + 1
        stats['total'] += 1
        if key not in INSPECTION_EXEMPT_KEYS:
            stats['abnormal'] += 1
        if r.inspect_date and r.inspect_date.strftime('%Y-%m') == month:
            stats['month'] += 1
    return stats


def _empty_stats():
    """统计初始值（保证前端字段永远存在）。"""
    out = {'total': 0, 'abnormal': 0, 'month': 0}
    for key, _label in INSPECTION_RESULTS:
        out[key] = 0
    return out


def _result_options():
    """结果筛选项。"""
    return [{'key': k, 'label': v} for k, v in INSPECTION_RESULTS]
