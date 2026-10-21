# StuLink v1.19.50.62 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序成绩管理：只读数据视图（分数修改记录 / 证明台账 / 汇总打印）。

统计口径全部调用网页同一份实现：
- 修改记录 → `ScoreChangeLog`（改分/删分留痕，只增不改）；
- 证明台账 → `Certificate`（管理员看全部，其他人只看本人开具）；
- 汇总打印 → `stats_service.print_summary`（与网页 /grades/print-summary 同源，
  共用 `grades_report_ps_*` 缓存，改分/划线后一并失效）。
"""
from datetime import datetime

from flask import g, request
from sqlalchemy import or_

from app.models.grades import Certificate, ScoreChangeLog
from app.models.user import User
from app.modules.grades.services import stats_service
from app.modules.wxapi import bp
from app.modules.wxapi.core.guards import wx_perm_required
from app.modules.wxapi.core.response import wx_ok
from app.modules.wxapi.grades.routes_grades import PERM
from app.modules.wxapi.grades.routes_grades_stats import _visible_exam

MAX_ROWS = 200
ACTION_LABEL = {'update': '修改', 'delete': '缺考'}


def _dt(value):
    """时间 → MM-DD HH:MM（空值 —）。"""
    return value.strftime('%m-%d %H:%M') if value else '—'


def _num(value):
    """分数去掉多余的 .0，保留一位。"""
    if value is None:
        return '—'
    return ('%g' % value)


@bp.route('/grades/manage/score-changes')
@wx_perm_required('grades.edit')
def wx_manage_score_changes():
    """单场考试的分数修改流水（与网页 /grades/score-changes?exam= 同权限）。"""
    exam, err = _visible_exam(request.args.get('exam_id', type=int))
    if err:
        return err
    logs = (ScoreChangeLog.query.filter_by(exam_id=exam.id)
            .order_by(ScoreChangeLog.operated_at.desc(),
                      ScoreChangeLog.id.desc())
            .limit(MAX_ROWS).all())
    rows = [{
        'time': _dt(x.operated_at),
        'student': (x.student_name or '') + '（' + (x.student_no or '') + '）',
        'class_name': x.class_name or '—',
        'subject': x.subject or '—',
        'action': ACTION_LABEL.get(x.action, x.action or '—'),
        'before': _num(x.old_score),
        'after': _num(x.new_score),
        'operator': x.operator_name or '—',
        'note': x.note or '',
    } for x in logs]
    return wx_ok({
        'view': 'score_changes',
        'title': '分数修改记录',
        'subtitle': '%s · %d 条' % (exam.name, len(rows)),
        'columns': [
            {'key': 'time', 'label': '时间', 'w': 150},
            {'key': 'student', 'label': '学生', 'w': 260},
            {'key': 'class_name', 'label': '班级', 'w': 110},
            {'key': 'subject', 'label': '科目', 'w': 100},
            {'key': 'action', 'label': '动作', 'w': 100},
            {'key': 'before', 'label': '改前', 'w': 100},
            {'key': 'after', 'label': '改后', 'w': 100},
            {'key': 'operator', 'label': '操作人', 'w': 140},
            {'key': 'note', 'label': '备注', 'w': 240},
        ],
        'rows': rows,
        'total': len(rows),
    })


@bp.route('/grades/manage/cert-records')
@wx_perm_required(PERM)
def wx_manage_cert_records():
    """成绩证明台账（query: status=valid|invalid / kw=）。"""
    user = g.wx_user
    q = Certificate.query
    if user.role != 'admin':
        q = q.filter(Certificate.generated_by == user.id)
    status = (request.args.get('status') or '').strip()
    if status == 'invalid':
        q = q.filter(Certificate.invalid.is_(True))
    elif status == 'valid':
        q = q.filter(Certificate.invalid.isnot(True))
    kw = (request.args.get('kw') or '').strip()
    if kw:
        like = '%' + kw + '%'
        q = q.filter(or_(Certificate.student_name.like(like),
                         Certificate.student_no.like(like),
                         Certificate.code.like(like)))
    certs = q.order_by(Certificate.generated_at.desc()).limit(MAX_ROWS).all()
    uids = {c.generated_by for c in certs if c.generated_by}
    names = {}
    if uids:
        names = {u.id: (u.real_name or u.username) for u in
                 User.query.filter(User.id.in_(uids)).all()}
    rows = [{
        'code': c.code or '',
        'student': (c.student_name or '') + '（' + (c.student_no or '') + '）',
        'class_name': (c.grade or '') + (c.class_name or ''),
        'scope': c.scope_desc or c.term or '—',
        'operator': names.get(c.generated_by, '—'),
        'time': _dt(c.generated_at),
        'status': '已作废' if c.invalid else '有效',
    } for c in certs]
    return wx_ok({
        'view': 'cert_records',
        'title': '成绩证明台账',
        'subtitle': '共 %d 份（最多显示 %d 份）' % (len(rows), MAX_ROWS),
        'columns': [
            {'key': 'code', 'label': '防伪码', 'w': 250},
            {'key': 'student', 'label': '学生', 'w': 260},
            {'key': 'class_name', 'label': '年级班级', 'w': 150},
            {'key': 'scope', 'label': '证明范围', 'w': 220},
            {'key': 'operator', 'label': '生成人', 'w': 140},
            {'key': 'time', 'label': '开具时间', 'w': 150},
            {'key': 'status', 'label': '状态', 'w': 100},
        ],
        'rows': rows,
        'total': len(rows),
        'filters': ['status'],
    })


@bp.route('/grades/manage/print-summary')
@wx_perm_required(PERM)
def wx_manage_print_summary():
    """汇总打印版数据（与网页 /grades/print-summary 同一份计算与缓存）。"""
    exam, err = _visible_exam(request.args.get('exam_id', type=int))
    if err:
        return err
    data = stats_service.cached_exam_data(exam.id)
    prev = data.prev_exam
    key = 'grades_report_ps_%d_%d' % (
        exam.id, prev.id if prev is not None else 0)
    from app.modules.grades.routes.report import _cached
    ps = _cached(key, lambda: stats_service.print_summary(exam.id))
    return wx_ok({
        'view': 'print_summary',
        'title': '汇总打印',
        'exam': ps.get('exam'),
        'prev_name': ps.get('prev_name') or '',
        'blocks': ps.get('blocks') or [],
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
    })
