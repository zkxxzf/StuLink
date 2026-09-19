# StuLink v1.16.0 2026-09-18
# 课表服务层：学期管理 / 节次配置 / 视图查询 / 条目CRUD / 冲突检测 / Excel导入导出 / 版本快照
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""课表核心服务（独立库 timetable.db）。

所有查询均过滤 is_deleted == False；跨库只用逻辑键不 JOIN。
写操作均记录 ScheduleVersion 快照（snapshot_json 为修改前数据）。
"""
import io
import json
import re
from datetime import datetime, date

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter

from app.extensions import db
from app.models.timetable import (
    TermSchedule, PeriodDef, ScheduleEntry, ScheduleVersion,
    get_default_periods, SCHEDULE_STATUS, WEEKDAY_NAMES, MAX_PERIOD,
    PERIOD_TYPES, ENTRY_TYPES,
)

# ─── 内部工具 ──────────────────────────────────────────────────────────────

_WEEKDAY_MAP = {'周一': 1, '周二': 2, '周三': 3, '周四': 4, '周五': 5, '周六': 6, '周日': 7}


def _snapshot(entry):
    """生成条目快照 JSON 字符串（委托 schedule_common）"""
    from app.modules.academic.services.schedule_common import snapshot_entry
    return snapshot_entry(entry)


def _record_version(schedule_id, entry_id, action, snapshot_str, operator=None, remark=None):
    """写入版本记录（委托 schedule_common，保持原签名兼容）"""
    from app.modules.academic.services.schedule_common import record_version
    record_version(
        term_schedule_id=schedule_id,
        entry_id=entry_id,
        action=action,
        operator_id=getattr(operator, 'id', None),
        operator_name=getattr(operator, 'real_name', None) or getattr(operator, 'username', None),
        remark=remark or '',
        snapshot_json=snapshot_str,
    )


def _base_entry_query(schedule_id):
    """基础条目查询（过滤软删除）"""
    return ScheduleEntry.query.filter_by(
        term_schedule_id=schedule_id, is_deleted=False)


def _week_range_covers(week_range_str, week):
    """判断周次范围字符串是否覆盖指定周（预留钩子，供学期周期任务扩展）。

    支持写法："1-18"、"1-9,11-18"、"5"（单周）、"单周"、"双周"、"全周"、
    "1-18(单)"、空值/None（视为全周覆盖）；解析失败时保守地视为覆盖。
    """
    if week is None:
        return True
    try:
        week = int(week)
    except (ValueError, TypeError):
        return True
    s = (week_range_str or '').strip()
    if not s or s in ('全周', '全部', '每周'):
        return True
    # 单/双周标记（兼容 "单周"、"1-18(单)"、"1-18（双）" 等写法）
    parity = None
    if '单' in s:
        parity = 'odd'
    elif '双' in s:
        parity = 'even'
    if parity:
        if s in ('单周', '双周'):
            return (week % 2 == 1) if parity == 'odd' else (week % 2 == 0)
        # 带范围+奇偶标记：先继续解析范围，最后叠加奇偶过滤
    # 去括号内容后按逗号/顿号分段解析范围
    body = re.sub(r'[（(][^）)]*[）)]', '', s)
    covered = False
    parsed_any = False
    for seg in re.split(r'[,，、;；]', body):
        seg = seg.strip()
        if not seg:
            continue
        m = re.match(r'^(\d{1,2})\s*[-–—~～]\s*(\d{1,2})$', seg)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            if lo > hi:
                lo, hi = hi, lo
            parsed_any = True
            if lo <= week <= hi:
                covered = True
            continue
        m = re.match(r'^(\d{1,2})$', seg)
        if m:
            parsed_any = True
            if int(m.group(1)) == week:
                covered = True
            continue
        # 无法解析的片段：保守视为覆盖
        return True
    if not parsed_any:
        return True  # 整串都解析失败，保守视为覆盖
    if parity and covered:
        return (week % 2 == 1) if parity == 'odd' else (week % 2 == 0)
    return covered


def _filter_by_week(entries, week):
    """按周次过滤条目列表（week=None 时原样返回）"""
    if week is None:
        return list(entries)
    return [e for e in entries if _week_range_covers(e.week_range, week)]


# ═══════════════════════════════════════════════════════════════════════════════
# 学期管理
# ═══════════════════════════════════════════════════════════════════════════════

# get_active_schedule 和 get_periods 已由 schedule_common 提供（见文件顶部导入）


def list_schedules():
    """全部学期，按 created_at 倒序"""
    return TermSchedule.query.order_by(TermSchedule.created_at.desc()).all()


def create_schedule(name, school_year, term, description=None,
                    created_by=None, with_default_periods=True,
                    start_date=None, end_date=None, total_weeks=None,
                    week_start_offset=0, is_current=False):
    """创建学期，with_default_periods 为真时写入 13 条默认节次。

    Task#27 增量追加可选关键字参数（默认值不改变原有行为）：
    start_date / end_date / total_weeks / week_start_offset / is_current。
    """
    ts = TermSchedule(
        name=name, school_year=school_year, term=term,
        status='draft', description=description,
        created_by=getattr(created_by, 'id', None) if created_by else None,
        start_date=start_date, end_date=end_date,
        total_weeks=total_weeks if total_weeks is not None else 20,
        week_start_offset=week_start_offset or 0,
        is_current=bool(is_current),
    )
    db.session.add(ts)
    db.session.flush()  # 获取 id
    if with_default_periods:
        for p in get_default_periods():
            db.session.add(PeriodDef(term_schedule_id=ts.id, **p))
    db.session.commit()
    return ts


def activate_schedule(schedule_id):
    """把该学期设为 active，同时把其他所有 active 改为 archived"""
    target = db.session.get(TermSchedule, schedule_id)
    if not target:
        raise ValueError('学期不存在')
    TermSchedule.query.filter_by(status='active').update({'status': 'archived'})
    target.status = 'active'
    db.session.commit()
    return target


def update_schedule(schedule_id, return_warnings=False, **fields):
    """更新学期字段。

    支持 name/school_year/term/description/status 及 Task#27 新增的
    start_date/end_date/total_weeks/week_start_offset/is_current。
    含日期字段时调用 term_service.validate_term_dates 做校验。
    return_warnings=False（默认）保持向后兼容，返回 ts；
    return_warnings=True 时返回 (ts, warnings)。
    """
    ts = db.session.get(TermSchedule, schedule_id)
    if not ts:
        raise ValueError('学期不存在')
    allowed = {'name', 'school_year', 'term', 'description', 'status',
               'start_date', 'end_date', 'total_weeks', 'week_start_offset',
               'is_current'}
    for k, v in fields.items():
        if k in allowed:
            setattr(ts, k, v)
    warnings = []
    if any(k in fields for k in ('start_date', 'end_date', 'total_weeks')):
        from app.modules.academic.services import term_service
        warnings = term_service.validate_term_dates(
            ts.start_date, ts.end_date, ts.total_weeks)
    db.session.commit()
    if return_warnings:
        return ts, warnings
    return ts


def set_current_term(schedule_id):
    """把指定学期设为 is_current=True 并清除其他学期的 is_current（互斥）。

    若该学期 status='draft' 则自动转 'active' 并把原 active 转 archived
    （复用 activate_schedule 的互斥逻辑）。返回该学期对象。
    """
    target = db.session.get(TermSchedule, schedule_id)
    if not target:
        raise ValueError('学期不存在')
    TermSchedule.query.filter(TermSchedule.id != schedule_id)\
        .update({'is_current': False}, synchronize_session=False)
    target.is_current = True
    db.session.commit()
    if target.status == 'draft':
        activate_schedule(schedule_id)
    return target


def delete_schedule(schedule_id):
    """删除学期（仅限 draft 状态，cascade 清掉节次和条目）"""
    ts = db.session.get(TermSchedule, schedule_id)
    if not ts:
        raise ValueError('学期不存在')
    if ts.status != 'draft':
        raise ValueError('只能删除草稿状态的学期，请先归档')
    # 物理删除条目（含已软删除的）
    ScheduleEntry.query.filter_by(term_schedule_id=schedule_id).delete()
    ScheduleVersion.query.filter_by(term_schedule_id=schedule_id).delete()
    db.session.delete(ts)
    db.session.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# 节次管理
# ═══════════════════════════════════════════════════════════════════════════════

# get_periods 已由 schedule_common 提供（见文件顶部导入）


def save_periods(schedule_id, periods_data):
    """批量保存节次配置（按 period_number upsert）"""
    existing = {p.period_number: p for p in get_periods(schedule_id)}
    for item in periods_data:
        pn = item.get('period_number')
        if not pn or not isinstance(pn, int) or pn < 1 or pn > MAX_PERIOD:
            continue
        if pn in existing:
            p = existing[pn]
            p.period_name = item.get('period_name', p.period_name)
            p.start_time = item.get('start_time', p.start_time)
            p.end_time = item.get('end_time', p.end_time)
            p.period_type = item.get('period_type', p.period_type)
            p.sort_order = item.get('sort_order', p.sort_order)
        else:
            p = PeriodDef(term_schedule_id=schedule_id, **{
                k: item[k] for k in ('period_number', 'period_name', 'start_time',
                                     'end_time', 'period_type', 'sort_order')
                if k in item
            })
            db.session.add(p)
    db.session.commit()


def reset_default_periods(schedule_id):
    """重置为 13 节默认模板"""
    PeriodDef.query.filter_by(term_schedule_id=schedule_id).delete()
    for p in get_default_periods():
        db.session.add(PeriodDef(term_schedule_id=schedule_id, **p))
    db.session.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# 视图查询
# ═══════════════════════════════════════════════════════════════════════════════

def _build_grid(entries, periods):
    """构建 {period_number: {weekday: entry_dict}} 网格"""
    grid = {}
    for p in periods:
        grid[p.period_number] = {wd: None for wd in range(1, 8)}
    for e in entries:
        if e.period_number in grid:
            grid[e.period_number][e.weekday] = e.to_dict()
    return grid


def get_class_view(schedule_id, grade, class_name, weekday=None, week=None):
    """班级课表视图：返回网格 + 节次列表 + 学科课时统计（week 为可选周次过滤）"""
    periods = get_periods(schedule_id)
    q = _base_entry_query(schedule_id).filter_by(grade=grade, class_name=class_name)
    if weekday:
        q = q.filter_by(weekday=weekday)
    entries = _filter_by_week(q.all(), week)
    grid = _build_grid(entries, periods)
    # 学科课时统计
    stats = {}
    for e in entries:
        stats[e.subject] = stats.get(e.subject, 0) + 1
    return {
        'grid': grid,
        'periods': [p.to_dict() for p in periods],
        'entries': [e.to_dict() for e in entries],
        'stats': stats,
        'total': len(entries),
    }


def get_grade_view(schedule_id, grade, weekday=None, week=None):
    """年级视图：该年级所有班级列表 + 每个班的网格"""
    periods = get_periods(schedule_id)
    q = _base_entry_query(schedule_id).filter_by(grade=grade)
    if weekday:
        q = q.filter_by(weekday=weekday)
    entries = _filter_by_week(q.all(), week)
    classes = sorted(set(e.class_name for e in entries))
    result = {}
    for cn in classes:
        cls_entries = [e for e in entries if e.class_name == cn]
        result[cn] = _build_grid(cls_entries, periods)
    return {
        'classes': classes,
        'grids': result,
        'periods': [p.to_dict() for p in periods],
        'total': len(entries),
    }


def get_master_view(schedule_id, weekday=None, week=None):
    """大课表（全校）：按年级分组"""
    periods = get_periods(schedule_id)
    q = _base_entry_query(schedule_id)
    if weekday:
        q = q.filter_by(weekday=weekday)
    entries = _filter_by_week(q.all(), week)
    grade_classes = {}
    for e in entries:
        grade_classes.setdefault(e.grade, set()).add(e.class_name)
    grade_classes = {g: sorted(cs) for g, cs in sorted(grade_classes.items())}
    grids = {}
    for g, cls_list in grade_classes.items():
        for cn in cls_list:
            cls_entries = [e for e in entries if e.grade == g and e.class_name == cn]
            grids[f'{g}_{cn}'] = _build_grid(cls_entries, periods)
    return {
        'grade_classes': grade_classes,
        'grids': grids,
        'periods': [p.to_dict() for p in periods],
        'total': len(entries),
    }


def get_teacher_view(schedule_id, teacher_uid, weekday=None, week=None):
    """教师个人课表 + 学科课时统计"""
    periods = get_periods(schedule_id)
    q = _base_entry_query(schedule_id).filter_by(teacher_uid=teacher_uid)
    if weekday:
        q = q.filter_by(weekday=weekday)
    entries = _filter_by_week(q.all(), week)
    grid = {}
    for p in periods:
        grid[p.period_number] = {wd: None for wd in range(1, 8)}
    for e in entries:
        if e.period_number in grid:
            grid[e.period_number][e.weekday] = e.to_dict()
    stats = {}
    for e in entries:
        stats[e.subject] = stats.get(e.subject, 0) + 1
    return {
        'grid': grid,
        'periods': [p.to_dict() for p in periods],
        'entries': [e.to_dict() for e in entries],
        'stats': stats,
        'total': len(entries),
    }


def get_today_schedule(schedule_id=None, grade=None, class_name=None, week=None):
    """今日全校课表：返回当天全部安排 + 当前节次标记（schedule_id 可为空=按日期自动定位）。

    Task#27：未显式传 schedule_id 时，通过 term_service.resolve_schedule_by_date() 定位学期，
    并自动计算当天所属教学周传给 week 过滤（单双周课表不会串）；若学期未配置起止
    日期则退回 week=None（不过滤），保证向后兼容不报错。
    """
    if schedule_id is None:
        from app.modules.academic.services import term_service
        sched, resolved_week = term_service.resolve_schedule_by_date()
        schedule_id = sched.id if sched else None
        if week is None:
            week = resolved_week
    if not schedule_id:
        return {'entries': [], 'current_period_number': None, 'weekday': 0,
                'periods': [], 'date': date.today().isoformat(),
                'schedule_id': None, 'week': None}

    now = datetime.now()
    wd = now.isoweekday()  # 1=周一 ... 7=周日
    periods = get_periods(schedule_id)
    q = _base_entry_query(schedule_id).filter_by(weekday=wd)
    if grade:
        q = q.filter_by(grade=grade)
    if class_name:
        q = q.filter_by(class_name=class_name)
    entries = _filter_by_week(
        q.order_by(ScheduleEntry.grade, ScheduleEntry.class_name,
                   ScheduleEntry.period_number).all(), week)

    # 确定当前节次
    current_period = None
    cur_minutes = now.hour * 60 + now.minute
    for p in periods:
        if p.start_time and p.end_time:
            try:
                sh, sm = map(int, p.start_time.split(':'))
                eh, em = map(int, p.end_time.split(':'))
                if sh * 60 + sm <= cur_minutes <= eh * 60 + em:
                    current_period = p.period_number
                    break
            except (ValueError, AttributeError):
                pass

    return {
        'entries': [e.to_dict() for e in entries],
        'current_period_number': current_period,
        'weekday': wd,
        'weekday_text': WEEKDAY_NAMES.get(wd, ''),
        'periods': [p.to_dict() for p in periods],
        'date': now.strftime('%Y-%m-%d'),
        'total': len(entries),
        'schedule_id': schedule_id,
        'week': week,
    }


def get_period_schedule(period_number, schedule_id=None, date_str=None, grade=None,
                        week=None):
    """指定节次全校课表：{grade: [{class_name, subject, teacher_name, room, entry_type}]}

    Task#27：未显式传 schedule_id 时按日期自动定位学期与教学周（同 get_today_schedule）。
    """
    if schedule_id is None:
        from app.modules.academic.services import term_service
        sched, resolved_week = term_service.resolve_schedule_by_date()
        schedule_id = sched.id if sched else None
        if week is None:
            week = resolved_week
    if not schedule_id:
        return {}

    q = _base_entry_query(schedule_id).filter_by(period_number=period_number)
    if grade:
        q = q.filter_by(grade=grade)
    entries = _filter_by_week(
        q.order_by(ScheduleEntry.grade, ScheduleEntry.class_name).all(), week)

    result = {}
    for e in entries:
        result.setdefault(e.grade, []).append({
            'class_name': e.class_name,
            'subject': e.subject,
            'teacher_name': e.teacher_name,
            'teacher_uid': e.teacher_uid,
            'room': e.room,
            'entry_type': e.entry_type,
            'entry_type_text': ENTRY_TYPES.get(e.entry_type, ''),
        })
    return result


def get_entry_detail(entry_id):
    """单条目详情"""
    e = db.session.get(ScheduleEntry, entry_id)
    if not e or e.is_deleted:
        return None
    return e.to_dict()


# ═══════════════════════════════════════════════════════════════════════════════
# 条目 CRUD
# ═══════════════════════════════════════════════════════════════════════════════

def check_class_conflict(schedule_id, grade, class_name, weekday, period_number,
                         exclude_entry_id=None):
    """检测同班同时段冲突，返回冲突 entry 或 None"""
    q = _base_entry_query(schedule_id).filter_by(
        grade=grade, class_name=class_name,
        weekday=weekday, period_number=period_number)
    if exclude_entry_id:
        q = q.filter(ScheduleEntry.id != exclude_entry_id)
    return q.first()


def check_teacher_conflict(schedule_id, teacher_uid, weekday, period_number,
                           exclude_entry_id=None):
    """检测同教师同时段冲突，返回冲突 entry 或 None"""
    if not teacher_uid:
        return None
    q = _base_entry_query(schedule_id).filter_by(
        teacher_uid=teacher_uid, weekday=weekday, period_number=period_number)
    if exclude_entry_id:
        q = q.filter(ScheduleEntry.id != exclude_entry_id)
    return q.first()


def add_entry(schedule_id, grade, class_name, weekday, period_number, subject,
              teacher_uid=None, teacher_name=None, room=None,
              week_range='1-18', note=None, operator=None):
    """添加课条目 → (success, message_or_entry)"""
    # 校验节次
    if not (1 <= period_number <= MAX_PERIOD):
        return False, f'节次超出范围（1-{MAX_PERIOD}）'
    pd = PeriodDef.query.filter_by(term_schedule_id=schedule_id,
                                   period_number=period_number).first()
    if not pd:
        return False, f'该学期未定义第 {period_number} 节'
    # 班级冲突
    conflict = check_class_conflict(schedule_id, grade, class_name, weekday, period_number)
    if conflict:
        return False, f'班级冲突：{grade}{class_name} {WEEKDAY_NAMES.get(weekday,"")}第{period_number}节 已有「{conflict.subject}」'
    # 教师冲突
    if teacher_uid:
        tc = check_teacher_conflict(schedule_id, teacher_uid, weekday, period_number)
        if tc:
            return False, f'教师冲突：{teacher_name or teacher_uid} {WEEKDAY_NAMES.get(weekday,"")}第{period_number}节 已有「{tc.subject}」({tc.grade}{tc.class_name})'

    entry = ScheduleEntry(
        term_schedule_id=schedule_id, grade=grade, class_name=class_name,
        weekday=weekday, period_number=period_number, subject=subject,
        teacher_uid=teacher_uid, teacher_name=teacher_name,
        room=room, week_range=week_range or '1-18', note=note,
        entry_type='normal', is_deleted=False,
    )
    db.session.add(entry)
    db.session.flush()
    _record_version(schedule_id, entry.id, 'create', _snapshot(entry),
                    operator, f'新增：{grade}{class_name} {WEEKDAY_NAMES.get(weekday,"")}第{period_number}节 {subject}')
    db.session.commit()
    return True, entry


def edit_entry(entry_id, operator=None, **fields):
    """编辑课条目（冲突校验排除自身）→ (success, message_or_entry)"""
    entry = db.session.get(ScheduleEntry, entry_id)
    if not entry or entry.is_deleted:
        return False, '条目不存在'

    new_weekday = fields.get('weekday', entry.weekday)
    new_period = fields.get('period_number', entry.period_number)
    new_grade = fields.get('grade', entry.grade)
    new_class = fields.get('class_name', entry.class_name)
    new_teacher_uid = fields.get('teacher_uid', entry.teacher_uid)
    new_teacher_name = fields.get('teacher_name', entry.teacher_name)

    # 冲突校验
    conflict = check_class_conflict(entry.term_schedule_id, new_grade, new_class,
                                    new_weekday, new_period, exclude_entry_id=entry_id)
    if conflict:
        return False, f'班级冲突：{new_grade}{new_class} 该时段已有「{conflict.subject}」'
    if new_teacher_uid:
        tc = check_teacher_conflict(entry.term_schedule_id, new_teacher_uid,
                                    new_weekday, new_period, exclude_entry_id=entry_id)
        if tc:
            return False, f'教师冲突：{new_teacher_name or new_teacher_uid} 该时段已有课({tc.grade}{tc.class_name})'

    # 快照旧数据
    old_snapshot = _snapshot(entry)
    allowed = {'grade', 'class_name', 'weekday', 'period_number', 'subject',
               'teacher_uid', 'teacher_name', 'room', 'week_range', 'note'}
    for k, v in fields.items():
        if k in allowed:
            setattr(entry, k, v)
    entry.updated_at = datetime.now()
    _record_version(entry.term_schedule_id, entry_id, 'update', old_snapshot,
                    operator, f'编辑：{entry.grade}{entry.class_name} {entry.subject}')
    db.session.commit()
    return True, entry


def delete_entry(entry_id, operator=None):
    """软删除 → (success, message)"""
    entry = db.session.get(ScheduleEntry, entry_id)
    if not entry or entry.is_deleted:
        return False, '条目不存在'
    old_snapshot = _snapshot(entry)
    entry.is_deleted = True
    entry.updated_at = datetime.now()
    _record_version(entry.term_schedule_id, entry_id, 'delete', old_snapshot,
                    operator, f'删除：{entry.grade}{entry.class_name} {WEEKDAY_NAMES.get(entry.weekday,"")}第{entry.period_number}节 {entry.subject}')
    db.session.commit()
    return True, '已删除'


def batch_add_entries(schedule_id, entries_list, operator=None):
    """批量添加（跳过冲突项）→ {success: n, failed: n, errors: [...]}"""
    success = 0
    failed = 0
    errors = []
    for idx, item in enumerate(entries_list):
        ok, result = add_entry(
            schedule_id=schedule_id,
            grade=item.get('grade', ''),
            class_name=item.get('class_name', ''),
            weekday=item.get('weekday', 1),
            period_number=item.get('period_number', 1),
            subject=item.get('subject', ''),
            teacher_uid=item.get('teacher_uid'),
            teacher_name=item.get('teacher_name'),
            room=item.get('room'),
            week_range=item.get('week_range', '1-18'),
            note=item.get('note'),
            operator=operator,
        )
        if ok:
            success += 1
        else:
            failed += 1
            errors.append({'row': idx + 1, 'message': result})
    return {'success': success, 'failed': failed, 'errors': errors}


# ═══════════════════════════════════════════════════════════════════════════════
# 版本查询
# ═══════════════════════════════════════════════════════════════════════════════

def get_entry_versions(entry_id, limit=50):
    """变更历史"""
    return ScheduleVersion.query.filter_by(entry_id=entry_id)\
        .order_by(ScheduleVersion.operated_at.desc()).limit(limit).all()


def get_schedule_versions(schedule_id, page=1, per_page=50):
    """学期变更历史分页"""
    return ScheduleVersion.query.filter_by(term_schedule_id=schedule_id)\
        .order_by(ScheduleVersion.operated_at.desc())\
        .paginate(page=page, per_page=per_page, error_out=False)


# ═══════════════════════════════════════════════════════════════════════════════
# Excel 导入导出
# ═══════════════════════════════════════════════════════════════════════════════

_IMPORT_HEADERS = ['年级', '班级', '星期', '节次', '学科', '教师姓名', '教师编号', '教室', '周次', '备注']


def _parse_weekday(raw):
    """解析星期 → 1-7 整数，失败返回 None"""
    if not raw:
        return None
    raw = str(raw).strip()
    if raw in _WEEKDAY_MAP:
        return _WEEKDAY_MAP[raw]
    try:
        v = int(raw)
        return v if 1 <= v <= 7 else None
    except (ValueError, TypeError):
        return None


def _parse_period(raw, periods_map):
    """解析节次：支持数字/名称（如 '第1节'/'早读'/'晚自习1'）→ int"""
    if not raw:
        return None
    raw = str(raw).strip()
    # 纯数字
    try:
        v = int(raw)
        if 1 <= v <= MAX_PERIOD:
            return v
    except (ValueError, TypeError):
        pass
    # "第N节" 格式
    m = re.match(r'第(\d+)节', raw)
    if m:
        v = int(m.group(1))
        if 1 <= v <= MAX_PERIOD:
            return v
    # 按名称匹配
    if raw in periods_map:
        return periods_map[raw]
    return None


def import_from_excel(schedule_id, file_storage, operator=None):
    """解析 Excel 导入课表 → {success, failed, errors}"""
    periods = get_periods(schedule_id)
    periods_map = {p.period_name: p.period_number for p in periods}

    stream = io.BytesIO(file_storage.read())
    file_storage.seek(0)
    try:
        wb = load_workbook(stream, data_only=True)
    except Exception:
        return {'success': 0, 'failed': 0, 'errors': [{'row': 0, 'message': '无法解析 Excel 文件'}]}

    ws = wb.active
    iter_rows = ws.iter_rows(values_only=True)
    try:
        header = next(iter_rows)
    except StopIteration:
        return {'success': 0, 'failed': 0, 'errors': [{'row': 0, 'message': '文件为空'}]}

    # 列索引映射
    col = {}
    for idx, cell in enumerate(header):
        h = str(cell or '').strip()
        if h in _IMPORT_HEADERS:
            col[h] = idx

    if '班级' not in col or '学科' not in col:
        return {'success': 0, 'failed': 0,
                'errors': [{'row': 0, 'message': '缺少必要列（班级、学科），请使用标准模板'}]}

    entries_list = []
    parse_errors = []
    for rn, raw in enumerate(iter_rows, start=2):
        def cell(key):
            i = col.get(key)
            if i is None or i >= len(raw) or raw[i] is None:
                return ''
            return str(raw[i]).strip()

        grade_val = cell('年级')
        class_name = cell('班级')
        weekday_raw = cell('星期')
        period_raw = cell('节次')
        subject = cell('学科')
        teacher_name = cell('教师姓名')
        teacher_uid = cell('教师编号')
        room = cell('教室')
        week_range = cell('周次')
        note = cell('备注')

        if not any((grade_val, class_name, subject, weekday_raw, period_raw)):
            continue  # 跳过空行

        errors = []
        weekday = _parse_weekday(weekday_raw)
        if not weekday:
            errors.append(f'星期格式无效：{weekday_raw}')
        period_number = _parse_period(period_raw, periods_map)
        if not period_number:
            errors.append(f'节次格式无效：{period_raw}')
        if not class_name:
            errors.append('班级为空')
        if not subject:
            errors.append('学科为空')
        if not grade_val:
            # 尝试从 class_name 推断年级
            grade_val = class_name[:2] if len(class_name) >= 2 else ''

        if errors:
            parse_errors.append({'row': rn, 'message': '；'.join(errors)})
        else:
            entries_list.append({
                'grade': grade_val, 'class_name': class_name,
                'weekday': weekday, 'period_number': period_number,
                'subject': subject, 'teacher_uid': teacher_uid or None,
                'teacher_name': teacher_name or None,
                'room': room or None, 'week_range': week_range or '1-18',
                'note': note or None,
            })

    # 批量添加
    result = batch_add_entries(schedule_id, entries_list, operator)
    # 合并解析错误（调整行号偏移）
    all_errors = parse_errors + [{'row': e['row'], 'message': e['message']} for e in result['errors']]
    return {'success': result['success'], 'failed': result['failed'] + len(parse_errors),
            'errors': all_errors}


def export_schedule(schedule_id, view_type='class', grade=None, class_name=None,
                    teacher_uid=None, week=None):
    """导出 Excel → BytesIO（week 为可选周次过滤，与视图口径一致）"""
    periods = get_periods(schedule_id)
    ts = db.session.get(TermSchedule, schedule_id)
    title = ts.name if ts else '课表'

    wb = Workbook()
    wb.remove(wb.active)

    if view_type == 'class' and grade and class_name:
        _export_class_sheet(wb, schedule_id, grade, class_name, periods, title, week)
    elif view_type == 'grade' and grade:
        data = get_grade_view(schedule_id, grade, week=week)
        for cn in data['classes']:
            _export_class_sheet(wb, schedule_id, grade, cn, periods,
                                f'{title} {grade}{cn}', week)
    elif view_type == 'teacher' and teacher_uid:
        _export_teacher_sheet(wb, schedule_id, teacher_uid, periods, title, week)
    else:
        # master：按班级分 sheet
        data = get_master_view(schedule_id, week=week)
        for g, cls_list in data['grade_classes'].items():
            for cn in cls_list:
                _export_class_sheet(wb, schedule_id, g, cn, periods, f'{g}{cn}', week)

    if not wb.sheetnames:
        ws = wb.create_sheet('空')
        ws.append(['无数据'])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _export_class_sheet(wb, schedule_id, grade, class_name, periods, sheet_title,
                        week=None):
    """导出单个班级课表工作表"""
    ws = wb.create_sheet(sheet_title[:31])  # Excel sheet名最长31字符
    view = get_class_view(schedule_id, grade, class_name, week=week)
    grid = view['grid']

    # 表头样式
    hf = Font(bold=True, color='FFFFFF', size=11)
    hfl = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
    tb = Border(left=Side('thin'), right=Side('thin'),
                top=Side('thin'), bottom=Side('thin'))
    center = Alignment(horizontal='center', vertical='center', wrap_text=True)

    # 表头行
    headers = ['节次'] + [WEEKDAY_NAMES.get(wd, f'周{wd}') for wd in range(1, 8)]
    for ci, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=ci, value=h)
        c.font = hf
        c.fill = hfl
        c.alignment = center
        c.border = tb

    # 数据行
    for ri, p in enumerate(periods, 2):
        time_label = f'{p.period_name}\n{p.start_time}-{p.end_time}' if p.start_time else p.period_name
        c = ws.cell(row=ri, column=1, value=time_label)
        c.alignment = center
        c.border = tb
        c.font = Font(bold=True, size=10)
        for wd in range(1, 8):
            entry = grid.get(p.period_number, {}).get(wd)
            val = ''
            if entry:
                val = f"{entry['subject']}\n{entry.get('teacher_name') or ''}"
            c = ws.cell(row=ri, column=wd + 1, value=val)
            c.alignment = center
            c.border = tb

    ws.column_dimensions['A'].width = 16
    for i in range(2, 9):
        ws.column_dimensions[get_column_letter(i)].width = 14


def _export_teacher_sheet(wb, schedule_id, teacher_uid, periods, sheet_title,
                          week=None):
    """导出教师个人课表工作表"""
    ws = wb.create_sheet(sheet_title[:31])
    view = get_teacher_view(schedule_id, teacher_uid, week=week)
    grid = view['grid']

    hf = Font(bold=True, color='FFFFFF', size=11)
    hfl = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
    tb = Border(left=Side('thin'), right=Side('thin'),
                top=Side('thin'), bottom=Side('thin'))
    center = Alignment(horizontal='center', vertical='center', wrap_text=True)

    headers = ['节次'] + [WEEKDAY_NAMES.get(wd, '') for wd in range(1, 8)]
    for ci, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=ci, value=h)
        c.font = hf
        c.fill = hfl
        c.alignment = center
        c.border = tb

    for ri, p in enumerate(periods, 2):
        time_label = f'{p.period_name}\n{p.start_time}-{p.end_time}' if p.start_time else p.period_name
        c = ws.cell(row=ri, column=1, value=time_label)
        c.alignment = center
        c.border = tb
        c.font = Font(bold=True, size=10)
        for wd in range(1, 8):
            entry = grid.get(p.period_number, {}).get(wd)
            val = ''
            if entry:
                val = f"{entry['subject']}\n{entry.get('grade','')}{entry.get('class_name','')}"
            c = ws.cell(row=ri, column=wd + 1, value=val)
            c.alignment = center
            c.border = tb

    ws.column_dimensions['A'].width = 16
    for i in range(2, 9):
        ws.column_dimensions[get_column_letter(i)].width = 14


def generate_import_template(schedule_id):
    """生成导入模板 BytesIO（含表头 + 示例 + 节次说明 sheet）"""
    wb = Workbook()
    ws = wb.active
    ws.title = '课表数据'

    hf = Font(bold=True, color='FFFFFF')
    hfl = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
    for ci, h in enumerate(_IMPORT_HEADERS, 1):
        c = ws.cell(row=1, column=ci, value=h)
        c.font = hf
        c.fill = hfl
        c.alignment = Alignment(horizontal='center')

    # 示例行
    sample = ['高一', '01班', '周一', '第1节', '语文', '张老师', 'T20260001', 'A101', '1-18', '']
    for ci, v in enumerate(sample, 1):
        ws.cell(row=2, column=ci, value=v)

    widths = [8, 8, 8, 10, 10, 10, 14, 10, 8, 12]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = 'A2'

    # 节次说明 sheet
    ps = wb.create_sheet('节次说明')
    ps.append(['节次编号', '节次名称', '开始时间', '结束时间', '类型'])
    for c in ps[1]:
        c.font = Font(bold=True)
    periods = get_periods(schedule_id)
    for p in periods:
        ps.append([p.period_number, p.period_name, p.start_time, p.end_time,
                   PERIOD_TYPES.get(p.period_type, p.period_type)])
    ps.column_dimensions['A'].width = 10
    ps.column_dimensions['B'].width = 12
    ps.column_dimensions['C'].width = 10
    ps.column_dimensions['D'].width = 10
    ps.column_dimensions['E'].width = 12

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


# ═══════════════════════════════════════════════════════════════════════════════
# 辅助查询（供路由/API 使用）
# ═══════════════════════════════════════════════════════════════════════════════

def get_grade_class_list(schedule_id=None):
    """获取年级+班级列表（从课表条目中提取 distinct，供级联下拉）"""
    if schedule_id:
        q = _base_entry_query(schedule_id)
    else:
        ts = get_active_schedule()
        if not ts:
            return {}
        q = _base_entry_query(ts.id)
    rows = q.with_entities(ScheduleEntry.grade, ScheduleEntry.class_name).distinct().all()
    result = {}
    for g, cn in rows:
        result.setdefault(g, set()).add(cn)
    return {g: sorted(cs) for g, cs in sorted(result.items())}


def get_all_teachers():
    """获取教师列表（从 academic.db Teacher 表，跨库逻辑键查询）"""
    from app.models.academic import Teacher
    teachers = Teacher.query.filter_by(status='active')\
        .order_by(Teacher.teacher_uid).all()
    return [{'uid': t.teacher_uid, 'name': t.name, 'subject': t.subject or ''}
            for t in teachers]
