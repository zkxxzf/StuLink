# StuLink v1.19.50.54 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序划线分层（总分线）。

网页 `/grades/bands` 是「层 × 方向 × 学科」的大矩阵；小程序只做**总分线**的
查看与编辑（教师最常用的一屏），单科线仍在电脑端。写入复用网页
`bands._save_bands`（比例换算、无成绩拒绝、缓存失效、操作日志全一致）。
"""
from flask import g, request

from app.extensions import db
from app.models.grades import TOTAL_SUBJECT
from app.modules.grades.routes.bands import _bands_payload, _save_bands
from app.modules.grades.services import stats_service
from app.modules.grades.utils import invalidate_exam_cache
from app.modules.wxapi import bp
from app.modules.wxapi.core.guards import wx_perm_required
from app.modules.wxapi.core.response import wx_err, wx_ok
from app.modules.wxapi.grades.routes_grades import PERM
from app.modules.wxapi.grades.routes_grades_stats import _visible_exam
from app.utils.helpers import log_operation


def _directions(exam_id):
    data = stats_service.cached_exam_data(exam_id)
    return [d for d in (data.directions or ['']) if d] or ['']


@bp.route('/grades/bands')
@wx_perm_required(PERM)
def wx_bands_get():
    """本场考试的划线（按方向，只含总分线）。"""
    exam, err = _visible_exam(request.args.get('exam_id', type=int))
    if err:
        return err
    directions = _directions(exam.id)
    out = {}
    for d in directions:
        out[d or '全体'] = _bands_payload(
            exam.id, d, TOTAL_SUBJECT) if d else _bands_payload(
            exam.id, '', TOTAL_SUBJECT)
    return wx_ok({
        'exam': {'id': exam.id, 'name': exam.name, 'grade': exam.grade or ''},
        'directions': [d or '全体' for d in directions],
        'bands': out,
        'can_edit': g.wx_user.has_perm('grades.settings'),
        'subject': TOTAL_SUBJECT,
    })


@bp.route('/grades/bands', methods=['POST'])
@wx_perm_required('grades.settings')
def wx_bands_save():
    """保存某方向的总分线：body {exam_id, direction, bands:[{name,lower_mode,lower_value}]}。"""
    data = request.get_json(silent=True) or {}
    exam, err = _visible_exam(data.get('exam_id'))
    if err:
        return err
    direction = (data.get('direction') or '').strip()
    directions = _directions(exam.id)
    if direction == '全体':
        direction = ''
    if direction not in directions:
        return wx_err('方向无效', 400)
    raw = data.get('bands') or []
    if not isinstance(raw, list) or not raw:
        return wx_err('请至少保留一层')
    bands = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        name = (item.get('name') or '').strip()[:20]
        if not name:
            return wx_err('第 %d 层缺少名称' % (i + 1))
        try:
            value = float(item.get('lower_value'))
        except (TypeError, ValueError):
            return wx_err('第 %d 层下界不是有效数字' % (i + 1))
        if value < 0:
            return wx_err('第 %d 层下界不能为负数' % (i + 1))
        bands.append({
            'seq': i + 1,
            'name': name,
            'lower_mode': 'score',
            'lower_value': value,
        })
    items, save_err = _save_bands(
        exam.id, direction, TOTAL_SUBJECT, bands)
    if save_err:
        return wx_err(save_err, 400)
    db.session.commit()
    invalidate_exam_cache(exam.id)
    log_operation(g.wx_user, '划线', '考试', exam.id,
                  '%s 总分线 %d 层（小程序）' % (direction or '全体', len(items)),
                  module='grades')
    return wx_ok({
        'direction': direction or '全体',
        'bands': _bands_payload(exam.id, direction, TOTAL_SUBJECT),
    }, '分层已保存')
