# StuLink v1.19.70.64 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序考务接口：我的监考 / 我参与的考务批次时间表（只读）。

数据源是考务环节⑤⑥的落库结果（`affair_schedules` /
`affair_invigilations`）。可见范围按「我本人的监考记录」收敛：
普通教师无需 grades.edit 权限也能看自己的监考，但看不到别人的排班。
"""
from flask import g, request
from sqlalchemy import and_, or_

from app.extensions import db
from app.models import (
    AffairInvigilation,
    AffairSchedule,
    Exam,
    ExamAffair,
)
from app.modules.wxapi import bp
from app.modules.wxapi.core.guards import wx_login_required
from app.modules.wxapi.core.response import wx_err, wx_ok


def _date_str(value):
    """Date → 'YYYY-MM-DD'（空值返回空串）。"""
    return value.strftime('%Y-%m-%d') if value else ''


def _exam_names(ids):
    """批量取考试名，返回 {exam_id: name}。"""
    ids = [i for i in ids if i]
    if not ids:
        return {}
    rows = Exam.query.filter(Exam.id.in_(ids)).all()
    return {e.id: (e.name or '') for e in rows}


def _invig_rows(user_id, affair_id=None, real_name=''):
    """取某人的监考记录：[(监考, 场次, 批次)]，同库 JOIN。

    2026-10-19：监考表的 `user_id` 可能是空（排班时只落了姓名快照），
    因此除 user_id 外再按「姓名完全一致且 user_id 为空」兜底，
    否则这部分教师在小程序里看不到自己的监考。
    """
    cond = AffairInvigilation.user_id == user_id
    if real_name:
        # 2026-10-19 第十三轮审计：同名学生会让两人互相看到对方的考场与学生
        # 名单（姓名/学号/座号），因此仅当全校只有这一个姓名时才启用姓名兜底
        from app.models import User as _U
        if _U.query.filter(_U.real_name == real_name).count() == 1:
            cond = or_(cond, and_(AffairInvigilation.user_id.is_(None),
                                  AffairInvigilation.name == real_name))
    q = (db.session.query(AffairInvigilation, AffairSchedule, ExamAffair)
         .join(AffairSchedule,
               AffairInvigilation.schedule_id == AffairSchedule.id)
         .join(ExamAffair, AffairInvigilation.affair_id == ExamAffair.id)
         .filter(cond))
    if affair_id:
        q = q.filter(AffairInvigilation.affair_id == affair_id)
    return q.all()


def _scope_filter(items, scope):
    """按日期过滤：upcoming=今天及以后，past=今天之前，all=不过滤。"""
    if scope not in ('upcoming', 'past'):
        return items
    from datetime import date
    today = date.today().strftime('%Y-%m-%d')
    if scope == 'upcoming':
        return [x for x in items
                if x.get('date') and x['date'] >= today]
    return [x for x in items if x.get('date') and x['date'] < today]


def _schedule_item(sch):
    """场次条目：日期 / 起止时间 / 科目 / 选科标注。"""
    return {
        'id': sch.id,
        'date': _date_str(sch.exam_date),
        'start': sch.start_time or '',
        'end': sch.end_time or '',
        'subject': sch.subject or '',
        'selection': sch.selection or '',
        'note': sch.note or '',
    }


def _invig_item(inv, sch, affair, exam_name):
    """监考条目：批次 / 场次 / 考场 / 监考位。"""
    item = _schedule_item(sch)
    item.update({
        'affair_id': affair.id,
        'affair_name': affair.name or '',
        'exam_name': exam_name,
        'grade': affair.grade or '',
        'room_no': inv.room_no or '',
        'slot': inv.slot or 1,
    })
    return item


@bp.route('/exam/my-invigilation')
@wx_login_required
def wx_my_invigilation():
    """我的监考安排（query: scope=upcoming|past|all，默认全部）。

    按时间升序返回，含批次名、考场号与监考位。
    """
    rows = _invig_rows(g.wx_user.id, real_name=g.wx_user.real_name or '')
    names = _exam_names({a.exam_id for _i, _s, a in rows})
    items = [_invig_item(inv, sch, affair, names.get(affair.exam_id, ''))
             for inv, sch, affair in rows]
    items.sort(key=lambda x: (x['date'], x['start'], x['room_no']))
    items = _scope_filter(items, (request.args.get('scope') or 'all').strip())
    return wx_ok({'items': items, 'total': len(items)})


@bp.route('/exam/affair/<int:aid>')
@wx_login_required
def wx_exam_affair(aid):
    """单个考务批次详情：完整考试时间表 + 我的监考明细。

    可见性按「本人在该批次有监考记录」收敛；没有记录即 404，
    避免用批次 id 遍历别人参与的考务。
    """
    rows = _invig_rows(g.wx_user.id, aid, real_name=g.wx_user.real_name or '')
    if not rows:
        return wx_err('考务批次不存在或你没有参与', 404)
    affair = rows[0][2]
    names = _exam_names({affair.exam_id})
    schedules = (AffairSchedule.query
                 .filter(AffairSchedule.affair_id == aid)
                 .order_by(AffairSchedule.exam_date,
                           AffairSchedule.start_time).all())
    mine = [_invig_item(inv, sch, affair, names.get(affair.exam_id, ''))
            for inv, sch, _a in rows]
    mine.sort(key=lambda x: (x['date'], x['start'], x['room_no']))
    return wx_ok({
        'id': affair.id,
        'name': affair.name or '',
        'grade': affair.grade or '',
        'exam_date': _date_str(affair.exam_date),
        'exam_name': names.get(affair.exam_id, ''),
        'status': affair.status or '',
        'my_count': len(mine),
        'schedules': [_schedule_item(s) for s in schedules],
        'mine': mine,
    })


@bp.route('/exam/affairs')
@wx_login_required
def wx_exam_affairs():
    """我参与监考的考务批次（含完整考试时间表）。"""
    rows = _invig_rows(g.wx_user.id, real_name=g.wx_user.real_name or '')
    affair_ids = sorted({affair.id for _i, _s, affair in rows})
    if not affair_ids:
        return wx_ok([])
    affairs = {a.id: a for a in ExamAffair.query.filter(
        ExamAffair.id.in_(affair_ids)).all()}
    schedules = {}
    for sch in (AffairSchedule.query
                .filter(AffairSchedule.affair_id.in_(affair_ids))
                .order_by(AffairSchedule.exam_date,
                          AffairSchedule.start_time).all()):
        schedules.setdefault(sch.affair_id, []).append(sch)
    names = _exam_names({a.exam_id for a in affairs.values()})
    out = []
    for aid in affair_ids:
        affair = affairs.get(aid)
        if affair is None:
            continue
        mine = [1 for _i, _s, x in rows if x.id == aid]
        out.append({
            'id': affair.id,
            'name': affair.name or '',
            'grade': affair.grade or '',
            'exam_date': _date_str(affair.exam_date),
            'exam_name': names.get(affair.exam_id, ''),
            'status': affair.status or '',
            'my_count': len(mine),
            'schedules': [_schedule_item(s)
                          for s in schedules.get(aid, [])],
        })
    return wx_ok(out)
