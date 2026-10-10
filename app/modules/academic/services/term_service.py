# StuLink v1.18.9.1 2026-10-10
# 学期周期服务（Task#27）：日期→学期/教学周定位、学期交接、归档、跨学期对比、使用报告
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""学期周期维度服务层（独立库 timetable.db）。

独立文件，避免与 schedule_service.py 大面积冲突。职责：
- 把「某个日期」映射到「所属学期 + 教学周」，供今日课表/查课/实时课表统一使用
- 学期校历与周次日历数据
- 学期交接（复制上学期课表作为新学期草稿底版）
- 学期归档（含快照记录）
- 跨学期课表对比 / 同学期变更时间线
- 学期课表使用情况报告

所有查询均过滤 is_deleted == False；跨库只用逻辑键不 JOIN；统计用 GROUP BY 一次聚合。
"""
import json
from datetime import datetime, date, timedelta

from sqlalchemy import func

from app.extensions import db
from app.models.timetable import (
    TermSchedule, PeriodDef, ScheduleEntry, ScheduleSwap, ScheduleVersion,
    WEEKDAY_NAMES,
)

_ACTION_TEXT = {'create': '新增', 'update': '编辑', 'delete': '删除', 'swap': '调课'}


# ═══════════════════════════════════════════════════════════════════════════════
# 日期 → 学期 / 教学周 定位
# ═══════════════════════════════════════════════════════════════════════════════

def resolve_schedule_by_date(target_date=None):
    """返回该日期所属的 (TermSchedule, week_number)。

    定位优先级（2026-10-10 修正）：
    1. **当前生效**：status='active' 的学期（业务上唯一）；这是"现在用的是哪张课表"
       唯一权威口径，查课 / 调课 / 工作台都必须以它为依据；
    2. 退一步用 is_current=True 的学期（只标了"当前"但没激活的情况）；
    3. 完全没有生效中学期时才按起止日期区间回看命中；
    4. 再兜底最新创建的学期。
    week_number 由学期的 get_week_number 计算，未配置起止日期时为 None。

    **Why**：老实现把"日期区间命中"排在 status 之前，于是**已归档**的学期只要填了
    起止日期、又恰好覆盖今天，就会抢走"当前学期"——查课/调课/工作台全部在操作旧学期的
    课表（用户实测：调课页显示的是历史学期的课，导入的新课表怎么都看不到）。
    """
    d = target_date or date.today()

    # 1) 当前生效中的学期 —— 唯一权威口径
    s = (TermSchedule.query.filter_by(status='active')
         .order_by(TermSchedule.id.desc()).first())
    if s:
        return s, s.get_week_number(d)

    # 2) 只标了「当前」但未激活
    s = (TermSchedule.query.filter_by(is_current=True)
         .order_by(TermSchedule.id.desc()).first())
    if s:
        return s, s.get_week_number(d)

    # 3) 没有生效中学期（纯历史回看）：按起止日期区间匹配
    q = TermSchedule.query.filter(
        TermSchedule.start_date.isnot(None),
        TermSchedule.start_date <= d,
    ).filter(
        db.or_(TermSchedule.end_date.is_(None), TermSchedule.end_date >= d)
    ).order_by(TermSchedule.start_date.desc())
    s = q.first()
    if s:
        return s, s.get_week_number(d)

    # 4) 兜底：最新创建的学期
    s = TermSchedule.query.order_by(
        TermSchedule.created_at.desc(), TermSchedule.id.desc()).first()
    return s, (s.get_week_number(d) if s else None)


def get_current_term_context(target_date=None):
    """统一学期上下文，供今日课表/实时课表/查课使用。

    返回 {'schedule', 'schedule_id', 'week', 'weekday', 'date', 'period_text'}。
    """
    d = target_date or date.today()
    schedule, week = resolve_schedule_by_date(d)
    return {
        'schedule': schedule,
        'schedule_id': schedule.id if schedule else None,
        'week': week,
        'weekday': d.isoweekday(),
        'date': d,
        'period_text': schedule.period_text() if schedule else '',
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 校历 / 周次日历
# ═══════════════════════════════════════════════════════════════════════════════

def get_week_calendar(schedule_id):
    """返回该学期 total_weeks 周的列表 [{week, start_date, end_date, is_current}]。

    供前端周次选择器与校历展示；未配置起止日期时 start/end 为 None 但仍列出周次。
    """
    ts = db.session.get(TermSchedule, schedule_id)
    if not ts:
        return []
    tw = ts.total_weeks or 20
    cur = ts.get_current_week()
    result = []
    for w in range(1, tw + 1):
        rng = ts.get_week_date_range(w)
        result.append({
            'week': w,
            'start_date': rng[0].strftime('%Y-%m-%d') if rng else None,
            'end_date': rng[1].strftime('%Y-%m-%d') if rng else None,
            'is_current': (w == cur),
        })
    return result


def validate_term_dates(start_date, end_date, total_weeks):
    """校验学期日期配置，返回警告文案列表（不抛异常）。

    - end <= start：结束日期必须晚于开始日期
    - total_weeks <= 0：教学周总数必须大于 0
    - start_date 非周一：给出对齐提示
    - 跨度周数与 total_weeks 不吻合（>1 周差）：给出核对提示（可能含假期，非硬错误）
    """
    warnings = []
    if start_date and end_date and end_date <= start_date:
        warnings.append('结束日期必须晚于开始日期')
    if total_weeks is not None:
        try:
            if int(total_weeks) <= 0:
                warnings.append('教学周总数必须大于 0')
        except (ValueError, TypeError):
            warnings.append('教学周总数格式无效')
    if start_date and start_date.weekday() != 0:
        # 2026-10-10：周次改为「自然周（周一~周日）」口径后，开学非周一不再造成偏移，
        # 只在第 1 周体现为不完整的一周，这里改成说明性提示（不是警告）。
        first_sunday = start_date + timedelta(days=7 - start_date.isoweekday())
        cn = '一二三四五六日'[start_date.isoweekday() - 1]
        warnings.append(
            f'开学第一天是周{cn}，第 1 周按自然周计算：'
            f'{start_date:%m-%d} ~ {first_sunday:%m-%d}（周日）结束，'
            f'第 2 周从 {first_sunday + timedelta(days=1):%m-%d}（周一）开始')
    if (start_date and end_date and end_date > start_date
            and total_weeks and int(total_weeks) > 0):
        span_weeks = ((end_date - start_date).days // 7) + 1
        if abs(span_weeks - int(total_weeks)) > 1:
            warnings.append(
                f'起止日期跨度约 {span_weeks} 周，与教学周总数 {int(total_weeks)} 不吻合'
                f'（可能含假期，请核对）')
    return warnings


# ═══════════════════════════════════════════════════════════════════════════════
# 学期列表 + 统计（GROUP BY 一次聚合，不在循环里逐学期 COUNT）
# ═══════════════════════════════════════════════════════════════════════════════

def list_terms_with_stats():
    """学期列表 + 每个学期的 条目数/节次数/调课数/日期区间/当前周/状态。"""
    terms = TermSchedule.query.order_by(
        TermSchedule.school_year.desc(), TermSchedule.id.desc()).all()

    entry_map = dict(
        db.session.query(ScheduleEntry.term_schedule_id, func.count(ScheduleEntry.id))
        .filter(ScheduleEntry.is_deleted == False)  # noqa: E712
        .group_by(ScheduleEntry.term_schedule_id).all())
    period_map = dict(
        db.session.query(PeriodDef.term_schedule_id, func.count(PeriodDef.id))
        .group_by(PeriodDef.term_schedule_id).all())
    swap_map = dict(
        db.session.query(ScheduleSwap.term_schedule_id, func.count(ScheduleSwap.id))
        .group_by(ScheduleSwap.term_schedule_id).all())

    result = []
    for t in terms:
        d = t.to_dict()
        d['entry_count'] = entry_map.get(t.id, 0)
        d['period_count'] = period_map.get(t.id, 0)
        d['swap_count'] = swap_map.get(t.id, 0)
        if t.start_date and t.end_date:
            d['date_range'] = (f'{t.start_date.strftime("%Y-%m-%d")} ~ '
                               f'{t.end_date.strftime("%Y-%m-%d")}')
        elif t.start_date:
            d['date_range'] = f'{t.start_date.strftime("%Y-%m-%d")} 起'
        else:
            d['date_range'] = '未配置'
        d['current_week'] = t.get_current_week()
        result.append(d)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 学期交接（复制上学期课表作为新学期草稿底版）
# ═══════════════════════════════════════════════════════════════════════════════

def copy_term_as_new_draft(source_schedule_id, new_name, new_school_year, new_term,
                           start_date=None, end_date=None, total_weeks=None,
                           copy_entries=True, copy_periods=True, created_by=None):
    """复制源学期作为新学期草稿底版。

    copy_periods=True：复制全部节次定义（沿用源学期自定义作息）。
    copy_entries=True：复制全部未删除条目，强制 entry_type='normal'、
        original_entry_id=None、note 追加「由{源学期名}复制」。
    返回 (success, message, new_schedule, stats{periods, entries})。
    """
    src = db.session.get(TermSchedule, source_schedule_id)
    if not src:
        return False, '源学期不存在', None, {'periods': 0, 'entries': 0}

    new = TermSchedule(
        name=new_name, school_year=new_school_year, term=new_term,
        status='draft', description=f'由「{src.name}」复制创建',
        created_by=getattr(created_by, 'id', None) if created_by else None,
        start_date=start_date, end_date=end_date,
        total_weeks=total_weeks or src.total_weeks or 20,
        week_start_offset=src.week_start_offset or 0,
        is_current=False,
    )
    db.session.add(new)
    db.session.flush()

    stats = {'periods': 0, 'entries': 0}
    if copy_periods:
        for p in src.periods.all():
            db.session.add(PeriodDef(
                term_schedule_id=new.id, period_number=p.period_number,
                period_name=p.period_name, start_time=p.start_time,
                end_time=p.end_time, period_type=p.period_type,
                sort_order=p.sort_order))
            stats['periods'] += 1
    if copy_entries:
        entries = ScheduleEntry.query.filter_by(
            term_schedule_id=source_schedule_id, is_deleted=False).all()
        suffix = f'由{src.name}复制'
        for e in entries:
            note = f'{e.note}；{suffix}' if e.note else suffix
            db.session.add(ScheduleEntry(
                term_schedule_id=new.id, grade=e.grade, class_name=e.class_name,
                weekday=e.weekday, period_number=e.period_number,
                week_range=e.week_range, subject=e.subject,
                teacher_uid=e.teacher_uid, teacher_name=e.teacher_name,
                room=e.room, entry_type='normal', original_entry_id=None,
                note=note[:100], is_deleted=False))
            stats['entries'] += 1
    db.session.commit()
    return True, f'已复制创建未启用学期「{new_name}」', new, stats


def archive_term(schedule_id, operator=None):
    """归档学期：status→archived、is_current→False，并写一条学期归档快照版本记录。

    **不要求先启用**（2026-10-10）：归档只是"收进历史课表只读"，与有没有启用无关。
    老 UI 只在 status='active' 时给归档入口，导致想归档一张没启用的课表必须先「激活」，
    而激活会把当前启用中的课表顶掉归档 —— 属于误伤，已在页面拆开。

    返回 (success, message)。
    """
    ts = db.session.get(TermSchedule, schedule_id)
    if not ts:
        return False, '学期不存在'
    entries = ScheduleEntry.query.filter_by(
        term_schedule_id=schedule_id, is_deleted=False).all()
    summary = {
        'archive_snapshot': True,
        'archived_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'name': ts.name,
        'start_date': ts.start_date.strftime('%Y-%m-%d') if ts.start_date else None,
        'end_date': ts.end_date.strftime('%Y-%m-%d') if ts.end_date else None,
        'total_weeks': ts.total_weeks,
        'total_entries': len(entries),
        'entries': [e.to_dict() for e in entries],
    }
    db.session.add(ScheduleVersion(
        term_schedule_id=schedule_id, entry_id=None, action='update',
        snapshot_json=json.dumps(summary, ensure_ascii=False),
        operator_id=getattr(operator, 'id', None),
        operator_name=getattr(operator, 'real_name', None) or getattr(operator, 'username', None),
        remark='学期归档', operated_at=datetime.now()))
    ts.status = 'archived'
    ts.is_current = False
    db.session.commit()
    return True, f'学期「{ts.name}」已归档'


# ═══════════════════════════════════════════════════════════════════════════════
# 跨学期对比 / 同学期变更时间线
# ═══════════════════════════════════════════════════════════════════════════════

def compare_terms(schedule_id_a, schedule_id_b, grade=None, class_name=None):
    """跨学期课表对比。对齐键 (grade, class_name, weekday, period_number)，只比较未删除条目。

    返回 {term_a, term_b, added[], removed[], changed[], unchanged_count, summary{}}。
    - added：B 有 A 无（slot 级）
    - removed：A 有 B 无（slot 级）
    - changed：同时段但 学科/教师/教室/周次 不同（field 级，一个差异字段一行）
    - summary.added/removed/changed 为 slot 级计数，changed_fields 为 field 级行数
    """
    ta = db.session.get(TermSchedule, schedule_id_a)
    tb = db.session.get(TermSchedule, schedule_id_b)

    def fetch(sid):
        q = ScheduleEntry.query.filter_by(term_schedule_id=sid, is_deleted=False)
        if grade:
            q = q.filter_by(grade=grade)
        if class_name:
            q = q.filter_by(class_name=class_name)
        return q.all()

    def key(e):
        return (e.grade, e.class_name, e.weekday, e.period_number)

    ma, mb = {}, {}
    for e in fetch(schedule_id_a):
        ma.setdefault(key(e), e)
    for e in fetch(schedule_id_b):
        mb.setdefault(key(e), e)

    def brief(e):
        return {'grade': e.grade, 'class_name': e.class_name, 'weekday': e.weekday,
                'weekday_text': WEEKDAY_NAMES.get(e.weekday, ''),
                'period_number': e.period_number, 'subject': e.subject,
                'teacher_name': e.teacher_name, 'room': e.room,
                'week_range': e.week_range}

    added, removed, changed = [], [], []
    unchanged = 0
    changed_slots = 0
    compare_fields = ('subject', 'teacher_name', 'room', 'week_range')

    for k, b in mb.items():
        if k not in ma:
            added.append(brief(b))
            continue
        a = ma[k]
        diffs = [(f, getattr(a, f), getattr(b, f)) for f in compare_fields
                 if (getattr(a, f) or '') != (getattr(b, f) or '')]
        if diffs:
            changed_slots += 1
            for f, old, new in diffs:
                row = brief(b)
                row.update({'field': f, 'old': old, 'new': new})
                changed.append(row)
        else:
            unchanged += 1
    for k, a in ma.items():
        if k not in mb:
            removed.append(brief(a))

    added.sort(key=lambda r: (r['grade'], r['class_name'], r['weekday'], r['period_number']))
    removed.sort(key=lambda r: (r['grade'], r['class_name'], r['weekday'], r['period_number']))
    changed.sort(key=lambda r: (r['grade'], r['class_name'], r['weekday'], r['period_number']))

    return {
        'term_a': ta.to_dict() if ta else None,
        'term_b': tb.to_dict() if tb else None,
        'added': added,
        'removed': removed,
        'changed': changed,
        'unchanged_count': unchanged,
        'summary': {
            'added': len(added),
            'removed': len(removed),
            'changed': changed_slots,
            'changed_fields': len(changed),
            'unchanged': unchanged,
            'total_a': len(ma),
            'total_b': len(mb),
        },
    }


def compare_term_versions(schedule_id, entry_id=None, limit=100):
    """同一学期内的课表变更历史时间线（基于 ScheduleVersion），按时间倒序。

    返回 [{operated_at, operator_name, action, action_text, entry_summary,
    remark, snapshot}]，entry_summary 从 snapshot_json 解析出 学科/班级/星期节次。
    """
    q = ScheduleVersion.query.filter_by(term_schedule_id=schedule_id)
    if entry_id:
        q = q.filter_by(entry_id=entry_id)
    rows = q.order_by(ScheduleVersion.operated_at.desc()).limit(limit).all()

    result = []
    for v in rows:
        snap = None
        if v.snapshot_json:
            try:
                snap = json.loads(v.snapshot_json)
            except (ValueError, TypeError):
                snap = None
        summary = {}
        if isinstance(snap, dict):
            if snap.get('archive_snapshot'):
                summary = {'archive': True, 'name': snap.get('name'),
                           'total_entries': snap.get('total_entries')}
            elif 'subject' in snap:
                summary = {
                    'subject': snap.get('subject'),
                    'class': f"{snap.get('grade', '')}{snap.get('class_name', '')}",
                    'slot': f"{WEEKDAY_NAMES.get(snap.get('weekday'), '')}第{snap.get('period_number')}节",
                }
        result.append({
            'id': v.id,
            'entry_id': v.entry_id,
            'operated_at': v.operated_at.strftime('%Y-%m-%d %H:%M:%S') if v.operated_at else None,
            'operator_name': v.operator_name,
            'action': v.action,
            'action_text': _ACTION_TEXT.get(v.action, v.action or ''),
            'entry_summary': summary,
            'remark': v.remark,
            'snapshot': snap,
        })
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 学期课表使用情况报告
# ═══════════════════════════════════════════════════════════════════════════════
