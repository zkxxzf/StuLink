# StuLink v1.19.70.64 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序考务安排：批次总览 + 批次详情（名单/考场/监考统计）+ 参加切换。

复用网页 `exam_affairs` 的同一批模型与 `_build_stats` 口径；创建批次复用
「考试 → 考务」既有规则（已有则复用，不重复建）。
"""
from flask import g, request

from app.extensions import db
from app.models.grades import (AffairRoom, AffairSchedule, AffairStudent,
                              AffairTeacher, AffairInvigilation, Exam,
                              ExamAffair)
from app.modules.grades.routes.exam_affairs import (
    _build_stats, _class_sort_key, _get_affair_checked, _inherit_rooms_from_prev,
    _student_sort_key)
from app.modules.grades.routes.affair_invig import ensure_teachers_synced
from app.modules.grades.services import scope as scope_service
from app.modules.wxapi import bp
from app.modules.wxapi.core.guards import wx_perm_required
from app.modules.wxapi.core.response import wx_err, wx_ok
from app.utils.helpers import log_operation

PERM = 'grades.edit'


@bp.route('/grades/affairs')
@wx_perm_required(PERM)
def wx_affairs():
    """考务批次列表（按可见年级收敛）+ 每批统计。

    2026-10-19 第十三轮审计：原用 `_all_active_grades()`（字典里全部未毕业
    年级），与注释「按可见年级收敛」不符 —— 年级长可看到外年级批次的
    名称/日期/人数/编排进度（详情接口有校验，泄露面仅元数据）。
    现与网页 `affairs_overview` 同源，改走 visible_grades(g.wx_user)。
    """
    grades = [x for x in (scope_service.visible_grades(g.wx_user) or []) if x]
    grade = (request.args.get('grade') or '').strip()
    if grade:
        if grade not in grades:
            return wx_err('该年级不在你的可见范围内', 403)
        q = ExamAffair.query.filter_by(grade=grade)
    else:
        q = ExamAffair.query.filter(ExamAffair.grade.in_(grades)) if grades \
            else ExamAffair.query.filter(False)
    affairs = q.order_by(ExamAffair.created_at.desc(),
                         ExamAffair.id.desc()).all()
    ids = [a.id for a in affairs]
    stus = {}
    if ids:
        for s in AffairStudent.query.filter(AffairStudent.affair_id.in_(ids)).all():
            stus.setdefault(s.affair_id, []).append(s)
    room_count = {}
    if ids:
        room_count = {row[0]: row[1] for row in db.session.query(
            AffairRoom.affair_id, db.func.count(AffairRoom.id)
        ).filter(AffairRoom.affair_id.in_(ids)
                 ).group_by(AffairRoom.affair_id).all()}
    exam_ids = [a.exam_id for a in affairs if a.exam_id]
    exams = {e.id: e for e in Exam.query.filter(Exam.id.in_(exam_ids)).all()} \
        if exam_ids else {}
    rows = []
    for a in affairs:
        st = stus.get(a.id, [])
        rows.append({
            'id': a.id,
            'name': a.name,
            'grade': a.grade,
            'exam_id': a.exam_id,
            'exam_name': exams[a.exam_id].name if a.exam_id in exams else '',
            'exam_date': a.exam_date.strftime('%Y-%m-%d') if a.exam_date else '',
            'selection_mode': a.selection_mode,
            'students': len(st),
            'attend': sum(1 for s in st if s.is_attend),
            'rooms': room_count.get(a.id, 0),
            'arranged': sum(1 for s in st if s.room_no),
            'linked': bool(a.exam_id),
        })
    return wx_ok({'affairs': rows, 'grades': grades, 'grade': grade})


@bp.route('/grades/affairs/<int:aid>')
@wx_perm_required(PERM)
def wx_affair_detail(aid):
    """批次详情：概览 + 名单（含考场/座号）+ 考场 + 监考统计。"""
    affair = _get_affair_checked(aid)
    students = (AffairStudent.query.filter_by(affair_id=aid)
                .order_by(AffairStudent.class_name,
                          AffairStudent.student_no).all())
    students.sort(key=_student_sort_key)
    rooms = (AffairRoom.query.filter_by(affair_id=aid)
             .order_by(AffairRoom.room_no).all())
    join = [s for s in students if s.is_attend]
    stats = _build_stats(join, rooms)
    per_class = {}
    for s in students:
        d = per_class.setdefault(s.class_name or '未分班',
                                 {'total': 0, 'join': 0, 'skip': 0})
        d['total'] += 1
        d['join' if s.is_attend else 'skip'] += 1
    class_stats = [dict(name=k, **v) for k, v in
                   sorted(per_class.items(), key=lambda kv: _class_sort_key(kv[0]))]
    ensure_teachers_synced(affair)
    scheds = (AffairSchedule.query.filter_by(affair_id=aid)
              .order_by(AffairSchedule.sort_order,
                        AffairSchedule.id).all())
    exam = db.session.get(Exam, affair.exam_id) if affair.exam_id else None
    return wx_ok({
        'affair': {
            'id': affair.id, 'name': affair.name, 'grade': affair.grade,
            'exam_id': affair.exam_id,
            'exam_name': exam.name if exam else '',
            'exam_date': affair.exam_date.strftime('%Y-%m-%d')
            if affair.exam_date else '',
            'selection_mode': affair.selection_mode,
        },
        'stats': stats,
        'class_stats': class_stats,
        'students': [{
            'id': s.id, 'no': s.student_no or '', 'name': s.name or '',
            'class_name': s.class_name or '', 'selection': s.subject_selection or '',
            'room_no': s.room_no or '', 'seat_no': s.seat_no,
            'exam_number': s.exam_number or '', 'attend': bool(s.is_attend),
        } for s in students],
        'rooms': [{
            'id': r.id, 'room_no': r.room_no or '', 'subject': r.subject or '',
            'capacity': r.capacity or 0, 'universal': bool(r.is_universal),
            'count': sum(1 for s in join if s.room_no == r.room_no),
        } for r in rooms],
        'invig': {
            'teachers': AffairTeacher.query.filter_by(affair_id=aid).count(),
            'schedules': len(scheds),
            'invigilations': AffairInvigilation.query.filter_by(
                affair_id=aid).count(),
            'schedule_list': [{
                'id': x.id, 'subject': x.subject or '', 'date': x.exam_date or '',
                'start': x.start_time or '', 'end': x.end_time or '',
            } for x in scheds],
        },
    })


@bp.route('/grades/affairs/<int:aid>/students/<int:sid>/attend', methods=['POST'])
@wx_perm_required(PERM)
def wx_affair_student_attend(aid, sid):
    """切换某学生「参加 / 不参加」（与网页步骤①同一字段）。"""
    affair = _get_affair_checked(aid)
    s = db.session.get(AffairStudent, sid)
    if s is None or s.affair_id != affair.id:
        return wx_err('学生不在该考务批次中', 404)
    data = request.get_json(silent=True) or {}
    attend = bool(data.get('attend'))
    s.is_attend = attend
    if not attend:
        s.room_no = ''
        s.seat_no = None
    db.session.commit()
    log_operation(g.wx_user, '编排', '考务学生', sid,
                  f'{affair.name}：{"参加" if attend else "不参加"}',
                  module='grades')
    return wx_ok({'id': sid, 'attend': attend},
                 '已设为参加' if attend else '已设为不参加')


@bp.route('/grades/affairs/from-exam', methods=['POST'])
@wx_perm_required(PERM)
def wx_affair_from_exam():
    """考试 → 考务：已有批次直接返回，无则按网页同一规则新建。"""
    from app.modules.grades.services.exam_guard import assert_exam_visible
    data = request.get_json(silent=True) or {}
    exam = assert_exam_visible(data.get('exam_id'))
    affair = (ExamAffair.query.filter_by(exam_id=exam.id)
              .order_by(ExamAffair.id.desc()).first())
    if affair is None:
        affair = ExamAffair(name=f'{exam.name} 考务', grade=exam.grade,
                            exam_date=exam.exam_date, exam_id=exam.id,
                            default_prefix='1701', selection_mode='selected',
                            operator_id=g.wx_user.id)
        db.session.add(affair)
        db.session.flush()
        n_rooms = _inherit_rooms_from_prev(affair)
        db.session.commit()
        log_operation(g.wx_user, '新建', '考务批次', affair.id,
                      f'{exam.grade} {affair.name}（自考试）'
                      + (f'（沿用上次考场{n_rooms}个）' if n_rooms else ''),
                      module='grades')
    return wx_ok({'id': affair.id, 'name': affair.name},
                 '已进入考务安排')
