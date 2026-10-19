# -*- coding: utf-8 -*-
"""临界生页面 / Excel 导出共用的口径解析（2026-10-19）。

为什么单独一份：临界生名单含**逐生总分 + 各科明细**，页面与导出的数据范围
必须完全一致（班主任只见本班、纯任课教师不含总分口径 → 空名单）。若导出
另写一遍 scope，很容易一边收一边漏，因此两边都走本函数。
"""
from flask import abort
from flask_login import current_user

from app.models.grades import ExamBand, TOTAL_SUBJECT
from app.modules.grades.services import report_service as rs
from app.modules.grades.services import report_warning_service as rws
from app.modules.grades.services import score_guard
from app.modules.grades.services.exam_guard import assert_exam_visible


def _int_arg(raw, default):
    """查询参数取整数：非法/缺省一律用默认值（与 request.args type=int 同义）。"""
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _clamp(raw, default, cfg_max):
    return max(0, min(_int_arg(raw, default), cfg_max))


def _by_class(data):
    """班级对比：按临界生人数降序（与页面表格同一顺序）。"""
    agg = {}
    for row in data['rows']:
        bucket = agg.setdefault(row['class_name'], {
            'class_name': row['class_name'],
            'headteacher': row.get('headteacher') or '',
            'above': 0, 'below': 0,
        })
        bucket['above' if row['status'] == '线上' else 'below'] += 1
        if not bucket['headteacher'] and row.get('headteacher'):
            bucket['headteacher'] = row['headteacher']
    return sorted(agg.values(),
                  key=lambda x: (-(x['above'] + x['below']), x['class_name']))


def _scope_tip(allowed, dropped):
    """数据范围徽标文案（与页面一致；空串＝不限范围，不显示徽标）。"""
    if allowed is None:
        return '', ''
    if not allowed:
        tip = ('临界生按总分划线统计，只对总分口径已授权的班级开放'
               + ('（你只有任教班级：' + '、'.join(dropped) + '）'
                  if dropped else '（任课教师只授予单科，不含总分口径）'))
        return '无本班数据', tip
    if len(allowed) <= 3:
        label = '仅 ' + '、'.join(sorted(allowed))
    else:
        label = f'仅你的 {len(allowed)} 个班'
    tip = '临界生名单含逐生总分与各科成绩，只显示你数据范围内的班级'
    if dropped:
        tip += ('；任教班级 ' + '、'.join(dropped)
                + ' 仅授予单科，不含总分口径，未计入临界生')
    return label, tip


def load(exam_id, args):
    """解析参数 + 数据范围，返回页面与导出共用的模板上下文。"""
    exam = assert_exam_visible(exam_id)
    try:                       # fail-closed：解析不出范围（无授权/毕业年级）一律不放行
        scope = score_guard.resolve(current_user)
    except PermissionError as exc:
        abort(403, description=str(exc))
    # 2026-10-15：临界生含逐生总分与各科明细，必须按「总分口径」收班级；
    # 纯任课教师（只有单科授权）得到空集 → 空名单，不能借临界生绕过总分口径。
    allowed_classes = scope.total_visible_classes(exam.grade)
    visible_now = scope.visible_classes(exam.grade)
    dropped_classes = (sorted(visible_now - (allowed_classes or set()))
                       if visible_now is not None and allowed_classes is not None
                       else [])

    cfg_max = rs.NEAR_LINE_MAX
    direction = (args.get('direction') or '').strip()
    layer = (args.get('layer') or '').strip()
    above = _clamp(args.get('above'), rs.NEAR_LINE_ABOVE, cfg_max)
    below = _clamp(args.get('below'), rs.NEAR_LINE_BELOW, cfg_max)
    cls = (args.get('cls') or '').strip()   # 班级筛选（服务内校验，非法＝全部）

    # 可选层级（本场总分划线）——直接查分数线表，避免额外重算
    lq = ExamBand.query.filter_by(exam_id=exam_id, subject=TOTAL_SUBJECT)
    if direction:
        lq = lq.filter_by(direction=direction)
    layers = []
    for row in lq.order_by(ExamBand.seq).all():
        if row.name and row.name not in layers:
            layers.append(row.name)

    data = rws.near_line_report(exam_id, direction, layer, above, below, cls,
                               allowed_classes=allowed_classes)
    error = data.get('error') if isinstance(data, dict) else '计算失败'
    scope_label, scope_tip = _scope_tip(allowed_classes, dropped_classes)
    return {
        'exam': exam, 'data': data, 'error': error, 'layers': layers,
        'direction': direction, 'layer': layer, 'above': above, 'below': below,
        'cfg_max': cfg_max, 'by_class': _by_class(data) if not error else [],
        'scope_label': scope_label, 'scope_tip': scope_tip,
        'allowed_classes': allowed_classes,
    }
