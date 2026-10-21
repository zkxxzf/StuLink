# StuLink v1.19.55.64 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序「班级成绩单」：逐生全科成绩 + 班排 / 方向排，服务端分页。

口径与网页 `/grades/exams/<id>`（成绩单）完全一致：
- 班级范围走 `scoresheet.visible`（含总分口径收敛，任课教师单科授权不放总分）；
- 取数 / 排序 / 分页直接复用 `exams._exam_page_rows`，不复制任何统计；
- 班级＝考试当时班级（总分行快照），不按当前名册。
"""
from flask import g, request

from app.extensions import db
from app.models.grades import SUBJECTS, TOTAL_SUBJECT, ExamScore
from app.modules.grades.routes.exams import _exam_page_rows, _sort_args
from app.modules.grades.services import scoresheet
from app.modules.wxapi import bp
from app.modules.wxapi.core.guards import wx_perm_required
from app.modules.wxapi.core.response import wx_err, wx_ok
from app.modules.wxapi.grades.routes_grades_stats import _visible_exam

DEFAULT_SIZE = 30
MAX_SIZE = 100
DIRECTIONS = ('物理', '历史')


def _exam_subjects(exam_id):
    """本场出现的科目，按系统标准顺序（语数外 + 选科）。"""
    present = {
        r[0] for r in db.session.query(ExamScore.subject)
        .filter(ExamScore.exam_id == exam_id,
                ExamScore.subject != TOTAL_SUBJECT)
        .distinct().all() if r[0]
    }
    return [s for s in SUBJECTS if s in present]


def _exam_directions(exam_id):
    """本场出现的方向（物理 / 历史），无方向（全科场）返回空列表。"""
    present = {
        r[0] for r in db.session.query(ExamScore.direction)
        .filter(ExamScore.exam_id == exam_id).distinct().all()
        if r[0] in DIRECTIONS
    }
    return [d for d in DIRECTIONS if d in present]


def _score_row(row):
    """裁剪为成绩单需要的字段（保留科目 rank 供表内展示）。"""
    subjects = {}
    for name, item in (row.get('subjects') or {}).items():
        subjects[name] = {
            'score': item.get('score'),
            'raw': item.get('raw'),
            'rank': item.get('rank'),
            'rank_class': item.get('rank_class'),
        }
    return {
        'no': row.get('no') or '',
        'name': row.get('name') or '',
        'class_name': row.get('class_name') or '',
        'exam_no': row.get('exam_no') or '',
        'direction': row.get('direction') or '',
        'selection': row.get('selection') or '',
        'total': row.get('total'),
        'total_raw': row.get('total_raw'),
        'rank': row.get('rank'),
        'rank_class': row.get('rank_class'),
        'move': row.get('move'),
        'subjects': subjects,
        'unselected': sorted(row.get('unselected') or []),
    }


@bp.route('/grades/scores')
@wx_perm_required('grades.view')   # 2026-10-19 第十四轮：成绩单=grades.view
def wx_grades_scores():
    """班级成绩单（query: exam_id / class_name / direction / sort / order /
    page / size）。

    page 从 1 开始；size 默认 30、上限 100。sort 取 no/name/exam_no/cls/
    total/rank_dir/rank_class/move 或 `s:科目名`，与网页表头排序同一白名单。
    """
    exam, err = _visible_exam(request.args.get('exam_id', type=int))
    if err:
        return err
    try:
        allowed, classes = scoresheet.visible(g.wx_user, exam)
    except PermissionError:
        return wx_err('当前账号没有本场成绩单的查看权限', 403)
    if allowed is not None and not allowed:
        return wx_err('当前账号没有本场成绩单的查看权限', 403)

    class_name = (request.args.get('class_name') or '').strip()
    if class_name and class_name not in classes:
        return wx_err('该班级不在你的可见范围内', 403)
    direction = (request.args.get('direction') or '').strip()
    if direction not in DIRECTIONS:
        direction = ''

    sort_key, order = _sort_args(request.args)
    page = max(1, request.args.get('page', type=int) or 1)
    size = request.args.get('size', type=int) or DEFAULT_SIZE
    size = max(1, min(size, MAX_SIZE))

    rows, page, total_pages, total = _exam_page_rows(
        exam.id, page, size,
        class_name=class_name or None,
        direction=direction or None,
        sort_key=sort_key, order=order,
        allowed_classes=allowed,
    )
    return wx_ok({
        'exam': {
            'id': exam.id,
            'name': exam.name,
            'grade': exam.grade or '',
            'date': exam.exam_date.strftime('%Y-%m-%d') if exam.exam_date else '',
        },
        'classes': classes,
        'class_name': class_name,
        'directions': _exam_directions(exam.id),
        'direction': direction,
        'sort': sort_key,
        'order': order,
        'page': page,
        'size': size,
        'total_pages': total_pages,
        'total': total,
        'subjects': _exam_subjects(exam.id),
        'rows': [_score_row(r) for r in rows],
    })
