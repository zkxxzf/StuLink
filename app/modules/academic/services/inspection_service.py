# -*- coding: utf-8 -*-
"""查课写服务（2026-10-11 抽取）：录入 / 批量录入 / 编辑 / 删除 / 矩阵标记。

背景：查课此前**没有 service 层**，全部写逻辑内联在 routes/inspection.py 的 5 个
路由里。为了让 MCP 写工具（mcp_server/tools/inspection.py）与网页端共用同一套
校验与落库逻辑，这里按原路由语义 1:1 抽出，路由改为调用本模块。

抽取原则：
  1. **行为不变**——校验顺序、降级策略、日志文案与原先一致；
  2. **参数错误统一 raise ValueError**（原路由是 flash + redirect），由调用方
     （网页端路由 / MCP toolkit）转成各自的呈现方式；toolkit 会把 ValueError
     转成"参数错误：..."返回给模型；
  3. **越权统一 raise PermissionError**——复用 access_scope 的年级/班级/教师
     三重判定，与网页端完全同口径；
  4. 调用方负责 commit（本模块只 add/delete，不提交），便于批量操作单事务。

数据范围三重校验（与网页端一致，缺一即越权）：
  ① 年级：visible_academic_grades(user) 非 None 时必须命中；
  ② 班级：academic_class_is_visible / academic_class_authorizer；
  ③ 教师：academic_has_class_restrictions(user) 为真时，teacher_uid 必须在
     _visible_active_teachers(user) 集合内。
"""
from datetime import date

from app.extensions import db
from app.models.academic import INSPECTION_RESULTS, InspectionRecord, Teacher
from app.modules.academic.services.access_scope import (
    academic_class_authorizer, academic_class_is_visible,
    academic_has_class_restrictions, apply_academic_scope,
    visible_academic_grades,
)
from app.utils.helpers import log_operation

_RESULT_KEYS = {k for k, _ in INSPECTION_RESULTS}

# 单次标记/批量录入上限（与网页端 inspection.py:748 一致，挡异常/恶意大批量提交）
_MAX_CELLS = 500
_MAX_BATCH = 500


def _parse_date(value):
    """YYYY-MM-DD → date；非法返回 None（调用方决定报错还是忽略）。"""
    try:
        return date.fromisoformat((value or '').strip())
    except (ValueError, AttributeError):
        return None


def _visible_active_teachers(user):
    """可见的在职教师（班级受限账号按课表条目范围收窄）。与路由内同名函数同口径。"""
    from app.models.timetable import ScheduleEntry
    if not academic_has_class_restrictions(user):
        return Teacher.query.filter_by(status='active').order_by(
            Teacher.teacher_uid).all()
    rows = apply_academic_scope(
        ScheduleEntry.query.filter_by(is_deleted=False), user,
        ScheduleEntry).with_entities(
            ScheduleEntry.teacher_uid).distinct().all()
    uids = {r[0] for r in rows if r[0]}
    return Teacher.query.filter(Teacher.status == 'active',
                                Teacher.teacher_uid.in_(uids)).all()


def _check_scope(user, grade, class_name, teacher_uid=None):
    """三重范围校验：年级 → 班级 → 教师（越权抛 PermissionError）。"""
    grades = visible_academic_grades(user)
    if grades is not None and grade not in grades:
        raise PermissionError(f'年级 {grade} 不在你的可见范围内')
    if not academic_class_is_visible(user, grade, class_name):
        raise PermissionError(f'班级 {grade}{class_name or ""} 不在你的可见范围内')
    if teacher_uid and academic_has_class_restrictions(user):
        allowed = {t.teacher_uid for t in _visible_active_teachers(user)}
        if teacher_uid not in allowed:
            raise PermissionError(f'教师 {teacher_uid} 不在你负责的范围内')
    return True


def _norm_result(value, default='normal'):
    """结果值归一：非法值回落默认（与网页端一致，不报错）。"""
    result = (value or '').strip()
    return result if result in _RESULT_KEYS else default


def add_record(user, inspect_date, grade, teacher_uid, class_name=None,
               period=None, subject='', result='normal', note=''):
    """录入一条查课记录。返回新建的 InspectionRecord（未 commit）。"""
    if not isinstance(inspect_date, date):
        parsed = _parse_date(inspect_date)
        if not parsed:
            raise ValueError('请选择正确的查课日期（YYYY-MM-DD）')
        inspect_date = parsed
    grade = (grade or '').strip()
    if not grade:
        raise ValueError('请选择年级')
    teacher_uid = (teacher_uid or '').strip()
    if not teacher_uid:
        raise ValueError('请选择被查课的教师')
    _check_scope(user, grade, class_name, teacher_uid)

    teacher = Teacher.query.filter_by(teacher_uid=teacher_uid).first()
    if not teacher:
        raise ValueError(f'教师不存在：{teacher_uid}')

    rec = InspectionRecord(
        inspect_date=inspect_date,
        grade=grade,
        period=period,
        teacher_uid=teacher.teacher_uid,
        teacher_name=teacher.name,
        class_name=(class_name or '').strip() or None,
        subject=(subject or '').strip() or teacher.subject,
        result=_norm_result(result),
        inspector_id=user.id,
        note=(note or '').strip() or None,
    )
    db.session.add(rec)
    return rec


def add_records_batch(user, inspect_date, rows):
    """批量录入查课记录（整批，跳过越权/无效行，返回新增条数）。

    rows: [{teacher_uid, grade, period, class_name, subject, result, note}, ...]
    与网页端 inspection_batch 同口径：越权与无效行**静默跳过**（不是整批拒绝），
    因为巡课场景常见"填了一屏但有空行"。全部无效时抛 ValueError。
    """
    if not isinstance(inspect_date, date):
        parsed = _parse_date(inspect_date)
        if not parsed:
            raise ValueError('请选择正确的查课日期（YYYY-MM-DD）')
        inspect_date = parsed
    if not isinstance(rows, list) or not rows:
        raise ValueError('没有要录入的记录')
    if len(rows) > _MAX_BATCH:
        raise ValueError(f'单次最多录入 {_MAX_BATCH} 条，请分批提交')

    grades_scope = visible_academic_grades(user)
    class_visible = academic_class_authorizer(user)
    restricted = academic_has_class_restrictions(user)
    allowed_uids = ({t.teacher_uid for t in _visible_active_teachers(user)}
                    if restricted else None)

    added = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        t_uid = (row.get('teacher_uid') or '').strip()
        grade = (row.get('grade') or '').strip()
        if not t_uid or not grade:
            continue
        if grades_scope is not None and grade not in grades_scope:
            continue
        class_name = (row.get('class_name') or '').strip() or None
        if not class_visible(grade, class_name):
            continue
        if allowed_uids is not None and t_uid not in allowed_uids:
            continue
        teacher = Teacher.query.filter_by(teacher_uid=t_uid).first()
        if not teacher:
            continue
        period = row.get('period')
        if period not in (None, ''):
            try:
                period = int(period)
            except (TypeError, ValueError):
                period = None
        db.session.add(InspectionRecord(
            inspect_date=inspect_date, grade=grade, period=period,
            teacher_uid=teacher.teacher_uid, teacher_name=teacher.name,
            class_name=class_name,
            subject=(row.get('subject') or '').strip() or teacher.subject,
            result=_norm_result(row.get('result')),
            inspector_id=user.id,
            note=(row.get('note') or '').strip() or None,
        ))
        added += 1
    if added == 0:
        raise ValueError('没有有效的记录被添加（请检查年级/班级/教师是否在权限范围内）')
    return added


def edit_record(user, rid, result=None, class_name=None, subject=None,
                period=None, note=None):
    """编辑查课记录（只改传入的字段）。返回更新后的记录（未 commit）。"""
    rec = db.session.get(InspectionRecord, rid)
    if rec is None:
        raise ValueError(f'查课记录不存在：{rid}')
    if not academic_class_is_visible(user, rec.grade, rec.class_name):
        raise PermissionError('该记录不在你的可见范围内')
    if class_name is not None:
        new_class = (class_name or '').strip() or None
        if not academic_class_is_visible(user, rec.grade, new_class):
            raise PermissionError('所选班级不在你的可见范围内')
        rec.class_name = new_class
    if result is not None and result in _RESULT_KEYS:
        rec.result = result
    if subject:
        rec.subject = subject
    if period is not None:
        rec.period = period
    if note is not None:
        rec.note = (note or '').strip() or None
    return rec


def delete_record(user, rid):
    """删除查课记录（未 commit）。"""
    rec = db.session.get(InspectionRecord, rid)
    if rec is None:
        raise ValueError(f'查课记录不存在：{rid}')
    if not academic_class_is_visible(user, rec.grade, rec.class_name):
        raise PermissionError('该记录不在你的可见范围内')
    db.session.delete(rec)
    return rec


def mark_cells(user, inspect_date, cells):
    """矩阵标记：给「某天+某班+某节次」写查课结果，同格覆盖更新；result 空=撤销。

    返回 (updated, cleared, skipped, touched)。越权/无效格**跳过**而非整批拒绝
    （巡课常一次标几十格，个别格越权不应让整屏白干）。
    """
    if not isinstance(inspect_date, date):
        parsed = _parse_date(inspect_date)
        if not parsed:
            raise ValueError('日期不正确（YYYY-MM-DD）')
        inspect_date = parsed
    if not isinstance(cells, list) or not cells:
        raise ValueError('没有要标记的格子')
    if len(cells) > _MAX_CELLS:
        raise ValueError(f'单次最多标记 {_MAX_CELLS} 格')

    grades_scope = visible_academic_grades(user)
    class_visible = academic_class_authorizer(user)
    updated = cleared = skipped = 0
    touched = []

    for cell in cells:
        if not isinstance(cell, dict):
            skipped += 1
            continue
        grade = (cell.get('grade') or '').strip()
        class_name = (cell.get('class_name') or '').strip() or None
        try:
            period = int(cell.get('period_number'))
        except (TypeError, ValueError):
            period = None
        if not grade or not period:
            skipped += 1
            continue
        if grades_scope is not None and grade not in grades_scope:
            skipped += 1
            continue
        if not class_visible(grade, class_name):
            skipped += 1
            continue
        result = (cell.get('result') or '').strip()
        if result and result not in _RESULT_KEYS:
            skipped += 1
            continue

        teacher_uid = (cell.get('teacher_uid') or '').strip()
        teacher_name = (cell.get('teacher_name') or '').strip()
        subject = (cell.get('subject') or '').strip()
        entry_id = str(cell.get('entry_id') or '').strip()
        if entry_id.isdigit():
            # 教师/学科以课表条目为准（前端只传 entry_id），避免标记与课表对不上
            from app.models.timetable import ScheduleEntry
            be = db.session.get(ScheduleEntry, int(entry_id))
            if be:
                teacher_uid = be.teacher_uid or teacher_uid
                teacher_name = be.teacher_name or teacher_name
                subject = be.subject or subject
                class_name = be.class_name or class_name
                period = be.period_number or period
                # 2026-10-13 审计修复：entry_id 会连带覆盖 grade/class_name/period，
                # 而上面第 250-255 行的年级与班级可见性校验是对**入参**做的。
                # 若不复检，受限账号传一个越界班级的 entry_id 就能把查课记录写到
                # 非授权班级。这里对覆盖后的班级重新过一遍可见性。
                if grades_scope is not None and be.grade not in grades_scope:
                    skipped += 1
                    continue
                if not class_visible(be.grade, be.class_name):
                    skipped += 1
                    continue
                grade = be.grade or grade

        rec = (InspectionRecord.query
               .filter_by(inspect_date=inspect_date, grade=grade,
                          class_name=class_name, period=period)
               .order_by(InspectionRecord.id.desc()).first())
        if not result:
            if rec:
                db.session.delete(rec)
                cleared += 1
                touched.append({'grade': grade, 'class_name': class_name,
                                'period_number': period, 'result': ''})
            continue
        if rec:
            rec.result = result
            rec.teacher_uid = teacher_uid or rec.teacher_uid
            rec.teacher_name = teacher_name or rec.teacher_name
            rec.subject = subject or rec.subject
            rec.inspector_id = user.id
            if 'note' in cell:
                rec.note = (cell.get('note') or '').strip() or None
        else:
            db.session.add(InspectionRecord(
                inspect_date=inspect_date, grade=grade, period=period,
                teacher_uid=teacher_uid or None,
                teacher_name=teacher_name or None,
                class_name=class_name, subject=subject or None,
                result=result, inspector_id=user.id,
                note=(cell.get('note') or '').strip() or None))
        updated += 1
        touched.append({'grade': grade, 'class_name': class_name,
                        'period_number': period, 'result': result,
                        'label': dict(INSPECTION_RESULTS).get(result, result)})
    return updated, cleared, skipped, touched


def notify_leaders(user, inspect_date, period, grade='', expected=0,
                   checked=0, unchecked=None):
    """把某节次查课情况推送给课表管理员（领导）。返回 (ok, message, data)。"""
    from app.extensions import db as _db
    from app.models import User
    from app.models.permission_group import PermissionGroup
    from app.modules.notifications.services import notification_service

    if not isinstance(inspect_date, date):
        parsed = _parse_date(inspect_date)
        if not parsed:
            return False, '日期不正确（YYYY-MM-DD）', {}
        inspect_date = parsed
    if not period:
        return False, '请指定节次', {}

    # 与网页端 _timetable_leaders 同口径：课表管理权限持有者 + 管理员（按 username 推送）
    groups = PermissionGroup.query.filter(
        PermissionGroup.menu_keys.like('%"academic.timetable"%')).all()
    gids = [g.id for g in groups if g.id]
    conds = [User.role == 'admin']
    if gids:
        conds.append(User.permission_group_id.in_(gids))
    users = User.query.filter(User.is_active.is_(True), _db.or_(*conds)).all()
    uids = [u.username for u in users if getattr(u, 'username', None)]
    if not uids:
        return False, '没有可推送的课表管理员', {}

    # 文案与网页端 inspection_notify 保持一致（含未查班级清单，最多列 30 个）
    names = [f"{c.get('grade') or ''}{c.get('class_name') or ''}".strip()
             for c in (unchecked or []) if isinstance(c, dict)]
    names = [n for n in names if n]
    grade_text = f'（{grade}）' if grade else ''
    miss_text = ('；未查：' + '、'.join(names[:30])
                 + ('等' if len(names) > 30 else '')) if names else ''
    unchecked_cnt = len(names) if names else max(0, (expected or 0) - (checked or 0))
    title = (f'查课提醒：{inspect_date} 第{period}节{grade_text}'
             f'有 {unchecked_cnt} 个班未查')
    content = (f'{user.real_name} 提交查课：{inspect_date} 第{period}节{grade_text}，'
               f'应查 {expected or 0} 个班，已查 {checked or 0} 个，'
               f'未查 {unchecked_cnt} 个{miss_text}。')
    ok, msg, data = notification_service.notify_users(
        uids, title, content, category='academic', biz_type='inspection',
        creator_id=user.id, creator_name=user.real_name, priority='high')
    if not ok:
        return False, msg, data or {}
    return True, msg, data


def log_inspection(user, action, target_id, detail):
    """查课操作留痕（module='academic'）。"""
    log_operation(user, action, '查课记录', target_id, detail, module='academic')
