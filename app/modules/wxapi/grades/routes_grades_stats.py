# StuLink v1.19.67.64 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序班级成绩分析接口（只读）：各班汇总 + 分数段分布。

统计口径全部复用 `stats_service`（网页同款），范围判定沿用
`scope.check_exam_visible`；本文件只做裁剪，不重算任何指标。
"""
from flask import g, request

from app.extensions import db
from app.models import Exam
from app.modules.grades.services import scope as scope_service
from app.modules.grades.services import stats_service
from app.modules.wxapi import bp
from app.modules.wxapi.core.guards import wx_perm_required
from app.modules.wxapi.core.response import wx_err, wx_ok
from app.modules.wxapi.grades.routes_grades import PERM


def _visible_exam(exam_id):
    """取考试并校验可见性。

    注意：`scope.check_exam_visible` 的语义是「越界抛 PermissionError」，
    **没有布尔返回值**（可见时返回 None），不能写成 `if not ...`。
    返回 (exam, err_response)：exam 为 None 时 err_response 可直接返回。
    """
    if not exam_id:
        return None, wx_err('缺少 exam_id')
    exam = db.session.get(Exam, exam_id)
    if exam is None:
        return None, wx_err('考试不存在', 404)
    try:
        scope_service.check_exam_visible(g.wx_user, exam)
    except PermissionError:
        return None, wx_err('该考试不在你的可见范围内', 403)
    return exam, None


@bp.route('/grades/class-summary')
@wx_perm_required(PERM)
def wx_grades_class_summary():
    """年级各班总分汇总（query: exam_id）。"""
    exam, err = _visible_exam(request.args.get('exam_id', type=int))
    if err:
        return err
    data = stats_service.cached_exam_data(exam.id)
    rows = stats_service.class_summary_table(data) or []
    return wx_ok({
        'exam': {'id': exam.id, 'name': exam.name},
        'rows': [_summary_row(r) for r in rows],
    })


def _summary_row(row):
    """班级汇总行：班名 / 方向 / 参考数 / 均分 / 最高 / 最低 / 分层人数。"""
    return {
        'class_name': row.get('class_name') or '',
        'direction': row.get('direction') or '',
        'count': row.get('count') or 0,
        'avg': row.get('avg'),
        'max': row.get('max'),
        'min': row.get('min'),
        'layers': row.get('layers') or {},
    }


@bp.route('/grades/subjects')
@wx_perm_required(PERM)
def wx_grades_subjects():
    """年级各科总表（均分 / 最高最低 / 及格率 / 优秀率）。"""
    exam, err = _visible_exam(request.args.get('exam_id', type=int))
    if err:
        return err
    data = stats_service.cached_exam_data(exam.id)
    rows = stats_service.subject_table(data) or []
    return wx_ok({
        'exam': {'id': exam.id, 'name': exam.name},
        'rows': [_subject_row(r) for r in rows],
    })


def _subject_row(row):
    """科目行：科目 / 满分 / 参考数 / 均分 / 最高最低 / 两率。"""
    return {
        'subject': row.get('subject') or '',
        'full': row.get('full'),
        'count': row.get('count') or 0,
        'avg': row.get('avg'),
        'max': row.get('max'),
        'min': row.get('min'),
        'pass_rate': row.get('pass_rate') or '',
        'good_rate': row.get('good_rate') or '',
    }


@bp.route('/grades/segments')
@wx_perm_required(PERM)
def wx_grades_segments():
    """分数段 / 名次段分布（query: exam_id / class_name / by_rank）。

    by_rank=1 时按「方向名次段」统计（名次分层），否则按分数段。
    class_name 为空表示全年级。
    """
    exam, err = _visible_exam(request.args.get('exam_id', type=int))
    if err:
        return err
    class_name = (request.args.get('class_name') or '').strip()
    by_rank = request.args.get('by_rank') in ('1', 'true', 'yes')
    data = stats_service.cached_exam_data(exam.id)
    _segs, rows = stats_service.segment_table(
        data, class_name=class_name or None, by_rank=by_rank)
    return wx_ok({
        'exam': {'id': exam.id, 'name': exam.name},
        'class_name': class_name,
        'by_rank': by_rank,
        'segments': [_segment_row(r) for r in rows],
    })


def _segment_row(row):
    """分数段行：段名 / 人数。"""
    return {
        'label': row.get('label') or '',
        'total': row.get('total') or 0,
    }
