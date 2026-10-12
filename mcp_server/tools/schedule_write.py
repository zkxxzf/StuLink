# -*- coding: utf-8 -*-
"""学期课表写工具（2026-10-11 第 3 批）：学期管理 / 课表条目 / 节次配置。

权限统一 `academic.timetable`（与网页端 schedule.py 全部写路由一致）。

范围口径（**两类不同，不能混用**）：
  · 学期管理类（建/改/激活/设当前/归档/删除/日期/复制）与节次配置类：
    网页端**零范围校验**——学期与作息本就是**校级**概念，不该按年级收敛，
    MCP 保持一致（不擅自加严，否则教务无法管理跨年级学期）。
  · 课表条目 CRUD：网页端用 `academic_class_is_visible`（纯班级范围），
    MCP 用 `guards.schedule_class_scope` 复刻同一口径。
    ⚠️ 注意与调课的区别：调课走 `swap_entry_authorizer`（本人任教的课无条件放行），
    直接改课表走班级范围，两者不可混用。

必须自己补的一关：**归档学期只读**。
  服务层 `add_entry/edit_entry/delete_entry/save_periods` 都不检查学期状态，
  该限制只在网页端路由层（`_readonly_guard`）。MCP 直接调服务层，
  故用 `guards.schedule_writable(ts)` 补上。

刻意未开放：
  · smart-import（原样课表导入）——需上传 Excel 文件，MCP 无文件通道；
  · 分享短链签发（属 academic.view 的只读签名操作，非课表数据变更）。
"""
from ..core import guards, respond, toolkit

_TERM_FIELDS = ('id', 'name', 'school_year', 'term', 'status', 'is_current',
                'start_date', 'end_date', 'total_weeks', 'week_start_offset',
                'description', 'created_at')
_ENTRY_FIELDS = ('id', 'term_schedule_id', 'grade', 'class_name', 'weekday',
                 'period_number', 'subject', 'teacher_uid', 'teacher_name',
                 'room', 'week_range', 'note', 'teaching_class', 'entry_type')
_PERIOD_FIELDS = ('id', 'term_schedule_id', 'period_number', 'period_name',
                  'start_time', 'end_time', 'period_type', 'sort_order')


def _svc():
    from app.modules.academic.services import schedule_service
    return schedule_service


def _term_or_raise(sid):
    from app.models.timetable import TermSchedule
    ts = TermSchedule.query.get(int(sid)) if sid else None
    if ts is None:
        raise ValueError(f'学期不存在：{sid}（可用 list_schedules 查询）')
    return ts


def _entry_or_raise(sid, eid):
    from app.models.timetable import ScheduleEntry
    entry = ScheduleEntry.query.get(int(eid)) if eid else None
    if entry is None or entry.is_deleted:
        raise ValueError(f'课表条目不存在或已删除：{eid}')
    if entry.term_schedule_id != int(sid):
        raise ValueError(f'条目 #{eid} 不属于学期 {sid}')
    return entry


def _log(user, action, target_type, target_id, detail):
    from app.utils.helpers import log_operation
    log_operation(user, action, target_type, target_id, detail + '（MCP）',
                  module='academic')


# ══════════════════════════════════════════════════════════════════════
# 学期管理（academic.timetable，校级操作，无年级范围）
# ══════════════════════════════════════════════════════════════════════

def _create_term(user, name, school_year, term, description):
    svc = _svc()
    name, school_year, term = (name or '').strip(), (school_year or '').strip(), (term or '').strip()
    if not name or not school_year or not term:
        raise ValueError('名称、学年、学期为必填项')
    ts = svc.create_schedule(name, school_year, term,
                             description=(description or '').strip() or None,
                             created_by=user)
    _log(user, '新增', '学期课表', ts.id, ts.name)
    return {'created': True, 'term': respond.pick(ts, _TERM_FIELDS),
            'message': f'学期「{ts.name}」已创建（未启用，含默认节次）；'
                       f'如需使用请再调用 activate_term 激活'}


def _update_term(user, sid, name, school_year, term, description):
    from app.models.timetable import TermSchedule
    ts = _term_or_raise(sid)
    guards.schedule_writable(ts)
    name, school_year, term = (name or '').strip(), (school_year or '').strip(), (term or '').strip()
    if not name or not school_year or not term:
        raise ValueError('名称、学年、学期为必填项')
    dup = TermSchedule.query.filter(TermSchedule.id != ts.id,
                                    TermSchedule.name == name).first()
    if dup:
        raise ValueError(f'已存在同名学期「{name}」，请换一个名称')
    old_name = ts.name
    svc = _svc()
    svc.update_schedule(sid, name=name, school_year=school_year, term=term,
                        description=(description or '').strip() or None)
    _log(user, '更新', '学期课表', sid, f'{old_name} → {name}')
    return {'updated': True, 'term': respond.pick(ts, _TERM_FIELDS),
            'message': f'学期信息已更新：{old_name} → {name}'}


def _activate_term(user, sid):
    ts = _term_or_raise(sid)
    svc = _svc()
    svc.activate_schedule(sid)          # 其他 active 学期自动归档
    _log(user, '激活', '学期课表', sid, ts.name)
    return {'activated': True, 'term': respond.pick(ts, _TERM_FIELDS),
            'message': f'学期「{ts.name}」已激活；原启用学期已自动归档进「历史课表」'}


def _set_current_term(user, sid):
    ts = _term_or_raise(sid)
    svc = _svc()
    svc.set_current_term(sid)           # 仅切标记，draft/archived 会抛 ValueError
    _log(user, '更新', '当前学期', sid, ts.name)
    return {'updated': True, 'term': respond.pick(ts, _TERM_FIELDS),
            'message': f'已将「{ts.name}」设为当前学期'}


def _archive_term(user, sid):
    from app.modules.academic.services import term_service
    ts = _term_or_raise(sid)
    ok, msg = term_service.archive_term(sid, operator=user)
    if not ok:
        raise ValueError(msg)
    _log(user, '归档', '学期课表', sid, ts.name)
    return {'archived': True, 'term_id': sid,
            'message': msg or f'学期「{ts.name}」已归档进历史课表（只读）'}


def _delete_term(user, sid):
    ts = _term_or_raise(sid)
    name = ts.name
    svc = _svc()
    svc.delete_schedule(sid)            # 仅 draft 可删；物理删条目+版本
    _log(user, '删除', '学期课表', sid, name)
    return {'deleted': True, 'term_id': sid,
            'message': f'学期「{name}」已删除（含其全部课表条目，不可恢复）'}


def _update_term_dates(user, sid, start_date, end_date, total_weeks,
                       week_start_offset, is_current):
    from app.modules.academic.services import term_service as tsvc
    from app.extensions import db
    ts = _term_or_raise(sid)
    guards.schedule_writable(ts)
    sd = _parse_date(start_date)
    ed = _parse_date(end_date)
    try:
        weeks = int(total_weeks or 0)
    except (TypeError, ValueError):
        raise ValueError('total_weeks 必须是整数')
    try:
        offset = int(week_start_offset or 0)
    except (TypeError, ValueError):
        raise ValueError('week_start_offset 必须是整数')
    warnings = tsvc.validate_term_dates(sd, ed, weeks)
    # 与网页端一致：结束<=开始 或 周数<=0 属硬错误，不保存
    if (sd and ed and ed <= sd) or weeks <= 0:
        raise ValueError('保存失败：' + '；'.join(warnings))
    svc = _svc()
    if is_current:
        from app.models.timetable import TermSchedule
        TermSchedule.query.filter(TermSchedule.id != sid)\
            .update({'is_current': False}, synchronize_session=False)
    svc.update_schedule(sid, start_date=sd, end_date=ed, total_weeks=weeks,
                        week_start_offset=offset, is_current=bool(is_current))
    db.session.commit()
    _log(user, '更新', '学期日期配置', sid, ts.name)
    return {'updated': True, 'term': respond.pick(ts, _TERM_FIELDS),
            'warnings': warnings,
            'message': '学期日期与周次配置已保存'
                       + ('；提醒：' + '；'.join(warnings) if warnings else '')}


def _copy_term(user, sid, new_name, new_school_year, new_term, start_date,
               end_date, total_weeks, copy_entries, copy_periods):
    from app.modules.academic.services import term_service as tsvc
    from app.extensions import db
    src = _term_or_raise(sid)
    new_name, new_year, new_term = (new_name or '').strip(), \
        (new_school_year or '').strip(), (new_term or '').strip()
    if not new_name or not new_year or not new_term:
        raise ValueError('新学期名称、学年、学期为必填项')
    try:
        weeks = int(total_weeks) if total_weeks else None
    except (TypeError, ValueError):
        raise ValueError('total_weeks 必须是整数')
    try:
        ok, msg, new, stats = tsvc.copy_term_as_new_draft(
            sid, new_name, new_year, new_term,
            start_date=_parse_date(start_date), end_date=_parse_date(end_date),
            total_weeks=weeks, copy_entries=bool(copy_entries),
            copy_periods=bool(copy_periods), created_by=user)
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        raise ValueError(f'复制失败：{type(exc).__name__}: {exc}')
    if not ok:
        raise ValueError(msg)
    _log(user, '新增', '学期交接', new.id,
         f'{src.name} → {new_name}（节次 {stats["periods"]}，条目 {stats["entries"]}）')
    return {'created': True, 'term': respond.pick(new, _TERM_FIELDS),
            'stats': stats,
            'message': f'{msg}：复制节次 {stats["periods"]} 条、'
                       f'课表条目 {stats["entries"]} 条（新学期的状态为未启用）'}


def _parse_date(value):
    from datetime import date
    text = (value or '').strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        raise ValueError(f'日期格式应为 YYYY-MM-DD，收到：{text}')


# ══════════════════════════════════════════════════════════════════════
# 课表条目（academic.timetable + 班级范围 + 归档只读）
# ══════════════════════════════════════════════════════════════════════

def _add_entry(user, sid, grade, class_name, weekday, period_number, subject,
               teacher_uid, teacher_name, room, week_range, note,
               teaching_class):
    svc = _svc()
    ts = _term_or_raise(sid)
    guards.schedule_writable(ts)
    grade, class_name = (grade or '').strip(), (class_name or '').strip()
    subject = (subject or '').strip()
    if not grade or not class_name or not subject:
        raise ValueError('年级、班级、学科为必填项')
    guards.schedule_class_scope(user, grade, class_name)
    try:
        weekday = int(weekday or 0)
        period_number = int(period_number or 0)
    except (TypeError, ValueError):
        raise ValueError('星期/节次格式无效')
    if not (1 <= weekday <= 7):
        raise ValueError('星期超出范围（1-7）')
    ok, result = svc.add_entry(
        int(sid), grade, class_name, weekday, period_number, subject,
        teacher_uid=(teacher_uid or '').strip() or None,
        teacher_name=(teacher_name or '').strip() or None,
        room=(room or '').strip() or None,
        week_range=(week_range or '').strip() or '1-18',
        note=(note or '').strip() or None,
        teaching_class=(teaching_class or '').strip() or None,
        operator=user)
    if not ok:
        raise ValueError(result)
    _log(user, '新增', '课表条目', result.id,
         f'{ts.name}：{grade}{class_name} 第{period_number}节 {subject}')
    return {'created': True, 'entry': respond.pick(result, _ENTRY_FIELDS),
            'message': '课表条目已添加'}


def _edit_entry(user, sid, eid, grade, class_name, weekday, period_number,
                subject, teacher_uid, teacher_name, room, week_range, note,
                teaching_class):
    svc = _svc()
    ts = _term_or_raise(sid)
    guards.schedule_writable(ts)
    entry = _entry_or_raise(sid, eid)
    guards.schedule_class_scope(user, entry.grade, entry.class_name)

    fields = {}
    for key, val in (('grade', grade), ('class_name', class_name),
                     ('subject', subject), ('room', room),
                     ('week_range', week_range), ('note', note),
                     ('teaching_class', teaching_class)):
        if val is not None:
            fields[key] = (val or '').strip() or None
    for key, val in (('weekday', weekday), ('period_number', period_number)):
        if val:
            try:
                fields[key] = int(val)
            except (TypeError, ValueError):
                raise ValueError(f'{key} 格式无效')
    if teacher_uid is not None:
        fields['teacher_uid'] = (teacher_uid or '').strip() or None
        fields['teacher_name'] = (teacher_name or '').strip() or None
    # 目标班级也须在范围内（与网页端一致）
    guards.schedule_class_scope(user,
                                fields.get('grade', entry.grade),
                                fields.get('class_name', entry.class_name))
    if 'weekday' in fields and not (1 <= fields['weekday'] <= 7):
        raise ValueError('星期超出范围（1-7）')
    if not fields:
        raise ValueError('没有要修改的字段')
    ok, result = svc.edit_entry(eid, operator=user, **fields)
    if not ok:
        raise ValueError(result)
    _log(user, '更新', '课表条目', eid, ts.name)
    return {'updated': True, 'entry': respond.pick(result, _ENTRY_FIELDS),
            'message': '课表条目已更新'}


def _delete_entry(user, sid, eid):
    svc = _svc()
    ts = _term_or_raise(sid)
    guards.schedule_writable(ts)
    entry = _entry_or_raise(sid, eid)
    guards.schedule_class_scope(user, entry.grade, entry.class_name)
    detail = f'{entry.grade}{entry.class_name} {entry.subject}'
    ok, msg = svc.delete_entry(eid, operator=user)
    if not ok:
        raise ValueError(msg)
    _log(user, '删除', '课表条目', eid, f'{ts.name}：{detail}')
    return {'deleted': True, 'entry_id': eid,
            'message': f'课表条目已删除（软删除，可追溯）'}


# ══════════════════════════════════════════════════════════════════════
# 节次配置（academic.timetable，校级操作）
# ══════════════════════════════════════════════════════════════════════

def _save_periods(user, sid, periods, remove_missing):
    svc = _svc()
    ts = _term_or_raise(sid)
    guards.schedule_writable(ts)
    if not isinstance(periods, list) or not periods:
        raise ValueError('periods 必须是非空数组')
    from app.models.timetable import PERIOD_NUMBER_CEILING
    data, dropped = [], []
    for i, item in enumerate(periods, start=1):
        if not isinstance(item, dict):
            dropped.append(f'#{i}')
            continue
        try:
            pn = int(item.get('period_number'))
        except (TypeError, ValueError):
            dropped.append(str(item.get('period_number')))
            continue
        if not (1 <= pn <= PERIOD_NUMBER_CEILING):
            dropped.append(str(pn))
            continue
        data.append({
            'period_number': pn,
            'period_name': (item.get('period_name') or '').strip() or f'第{pn}节',
            'start_time': (item.get('start_time') or '').strip() or None,
            'end_time': (item.get('end_time') or '').strip() or None,
            'period_type': (item.get('period_type') or '').strip() or 'morning',
            'sort_order': i,
        })
    if not data:
        raise ValueError(f'没有合法的节次（编号需为 1~{PERIOD_NUMBER_CEILING} 的整数）')
    result = svc.save_periods(int(sid), data, remove_missing=bool(remove_missing))
    _log(user, '更新', '节次配置', sid, f'{ts.name}：{len(data)} 节')
    return {'saved': len(data), 'dropped': dropped,
            'removed': result.get('removed') or [],
            'kept_in_use': result.get('kept_in_use') or [],
            'message': f'已保存 {len(data)} 个节次'
                       + (f'；忽略 {len(dropped)} 个非法行' if dropped else '')
                       + (f'；移除第 {"、".join(map(str, result["removed"]))} 节'
                          if result.get('removed') else '')
                       + (f'；第 {"、".join(map(str, result["kept_in_use"]))} 节仍有课未移除'
                          if result.get('kept_in_use') else '')}


def _reset_periods(user, sid):
    svc = _svc()
    ts = _term_or_raise(sid)
    guards.schedule_writable(ts)
    svc.reset_default_periods(int(sid))   # 已有条目时抛 ValueError
    _log(user, '重置', '节次配置', sid, ts.name)
    return {'reset': True, 'term_id': sid,
            'message': '已重置为默认作息（早读、上午5节、下午4节、晚自习3节）'}


def _global_periods(user, sid, action):
    svc = _svc()
    ts = _term_or_raise(sid)
    guards.schedule_writable(ts)
    if action == 'save':
        ok, msg = svc.save_global_periods(int(sid), operator=user)
        if not ok:
            raise ValueError(msg)
        _log(user, '更新', '全局作息模板', sid, ts.name)
        return {'saved': True, 'message': msg}
    if action == 'apply':
        ok, msg, result = svc.apply_global_periods(int(sid))
        if not ok:
            raise ValueError(msg)
        _log(user, '更新', '节次配置', sid, f'{ts.name}：套用全局作息')
        kept = (result or {}).get('kept_in_use') or []
        return {'applied': True, 'kept_in_use': kept,
                'message': msg + (f'；第 {"、".join(map(str, kept))} 节仍有课未移除'
                                  if kept else '')}
    raise ValueError("action 只能是 save（存为全局模板）或 apply（套用全局模板）")


def register(server):
    """把本模块的工具注册到 MCP 服务。"""

    # ── 学期管理 ─────────────────────────────────────────────────────
    @server.tool(name='create_term',
                 description='【写操作】创建学期（未启用 draft 状态，自动带默认节次）。'
                             'name/school_year/term 必填，如 2026-2027 / 第一学期')
    async def create_term(name: str, school_year: str, term: str,
                          description: str = '') -> str:
        params = {'name': name, 'school_year': school_year, 'term': term}
        return await toolkit.db_write(
            lambda u: _create_term(u, name, school_year, term, description),
            tool='create_term', params=params, perm='academic.timetable')

    @server.tool(name='update_term',
                 description='【写操作】编辑学期基本信息（名称/学年/学期/说明）；'
                             '名称不可与其它学期重复。归档学期只读')
    async def update_term(sid: int, name: str, school_year: str, term: str,
                          description: str = '') -> str:
        params = {'sid': sid, 'name': name}
        return await toolkit.db_write(
            lambda u: _update_term(u, sid, name, school_year, term, description),
            tool='update_term', params=params, perm='academic.timetable')

    @server.tool(name='activate_term',
                 description='【写操作】激活学期（其他启用中的学期会自动归档进「历史课表」）')
    async def activate_term(sid: int) -> str:
        return await toolkit.db_write(lambda u: _activate_term(u, sid),
                                      tool='activate_term', params={'sid': sid},
                                      perm='academic.timetable')

    @server.tool(name='set_current_term',
                 description='【写操作】设为当前学期（仅切换「当前」标记，不影响激活/归档）；'
                             '未启用(draft)或已归档的学期不能设为当前')
    async def set_current_term(sid: int) -> str:
        return await toolkit.db_write(lambda u: _set_current_term(u, sid),
                                      tool='set_current_term',
                                      params={'sid': sid},
                                      perm='academic.timetable')

    @server.tool(name='archive_term',
                 description='【写操作】归档学期进「历史课表」（只读回看，留快照）；'
                             '归档后该学期课表不能再修改')
    async def archive_term(sid: int) -> str:
        return await toolkit.db_write(lambda u: _archive_term(u, sid),
                                      tool='archive_term', params={'sid': sid},
                                      perm='academic.timetable')

    @server.tool(name='delete_term',
                 description='【写操作】删除学期（**仅未启用 draft 可删**，会连同其全部课表'
                             '条目一并物理删除，不可恢复）；已启用/已归档请改用 archive_term')
    async def delete_term(sid: int) -> str:
        return await toolkit.db_write(lambda u: _delete_term(u, sid),
                                      tool='delete_term', params={'sid': sid},
                                      perm='academic.timetable')

    @server.tool(name='update_term_dates',
                 description='【写操作】设置学期起止日期与教学周数。start_date/end_date '
                             '格式 YYYY-MM-DD；total_weeks 须 >0；is_current=true 会'
                             '清除其它学期的「当前」标记')
    async def update_term_dates(sid: int, start_date: str, end_date: str,
                                total_weeks: int,
                                week_start_offset: int = 0,
                                is_current: bool = False) -> str:
        params = {'sid': sid, 'start_date': start_date, 'end_date': end_date,
                  'total_weeks': total_weeks}
        return await toolkit.db_write(
            lambda u: _update_term_dates(u, sid, start_date, end_date,
                                         total_weeks, week_start_offset,
                                         is_current),
            tool='update_term_dates', params=params, perm='academic.timetable')

    @server.tool(name='copy_term',
                 description='【写操作】以某学期为底版复制出新学期草稿（学期交接）；'
                             'copy_entries / copy_periods 控制是否复制课表条目与节次')
    async def copy_term(sid: int, new_name: str, new_school_year: str,
                        new_term: str, start_date: str = '',
                        end_date: str = '', total_weeks: int = 0,
                        copy_entries: bool = True,
                        copy_periods: bool = True) -> str:
        params = {'sid': sid, 'new_name': new_name, 'new_school_year': new_school_year}
        return await toolkit.db_write(
            lambda u: _copy_term(u, sid, new_name, new_school_year, new_term,
                                 start_date, end_date, total_weeks or None,
                                 copy_entries, copy_periods),
            tool='copy_term', params=params, perm='academic.timetable')

    # ── 课表条目 ─────────────────────────────────────────────────────
    @server.tool(name='add_schedule_entry',
                 description='【写操作】给学期添加一条课表条目。weekday 1-7、'
                             'period_number 须是该学期已定义的节次；会自动检测'
                             '同班同节次与同教师同节次冲突（按周次交集判定）')
    async def add_schedule_entry(sid: int, grade: str, class_name: str,
                                 weekday: int, period_number: int, subject: str,
                                 teacher_uid: str = '', teacher_name: str = '',
                                 room: str = '', week_range: str = '',
                                 note: str = '', teaching_class: str = '') -> str:
        params = {'sid': sid, 'grade': grade, 'class_name': class_name,
                  'weekday': weekday, 'period_number': period_number}
        return await toolkit.db_write(
            lambda u: _add_entry(u, sid, grade, class_name, weekday,
                                 period_number, subject, teacher_uid,
                                 teacher_name, room, week_range, note,
                                 teaching_class),
            tool='add_schedule_entry', params=params, perm='academic.timetable')

    @server.tool(name='update_schedule_entry',
                 description='【写操作】修改课表条目（只改传入的字段）；'
                             '原班级与目标班级都须在你的课表管理范围内')
    async def update_schedule_entry(sid: int, eid: int, grade: str = None,
                                    class_name: str = None, weekday: int = 0,
                                    period_number: int = 0, subject: str = None,
                                    teacher_uid: str = None,
                                    teacher_name: str = None, room: str = None,
                                    week_range: str = None, note: str = None,
                                    teaching_class: str = None) -> str:
        params = {'sid': sid, 'eid': eid}
        return await toolkit.db_write(
            lambda u: _edit_entry(u, sid, eid, grade, class_name, weekday,
                                  period_number, subject, teacher_uid,
                                  teacher_name, room, week_range, note,
                                  teaching_class),
            tool='update_schedule_entry', params=params,
            perm='academic.timetable')

    @server.tool(name='delete_schedule_entry',
                 description='【写操作】删除课表条目（软删除，可追溯）；'
                             '班级须在你的课表管理范围内')
    async def delete_schedule_entry(sid: int, eid: int) -> str:
        return await toolkit.db_write(
            lambda u: _delete_entry(u, sid, eid), tool='delete_schedule_entry',
            params={'sid': sid, 'eid': eid}, perm='academic.timetable')

    # ── 节次配置 ─────────────────────────────────────────────────────
    @server.tool(name='save_schedule_periods',
                 description='【写操作】保存学期节次配置。periods 元素含 '
                             'period_number（1~99）/ period_name / start_time / '
                             'end_time / period_type；remove_missing=true 会移除'
                             '未提交的节次（仍被课程引用的自动保留）')
    async def save_schedule_periods(sid: int, periods: list,
                                    remove_missing: bool = True) -> str:
        params = {'sid': sid, 'count': len(periods or [])}
        return await toolkit.db_write(
            lambda u: _save_periods(u, sid, periods, remove_missing),
            tool='save_schedule_periods', params=params,
            perm='academic.timetable')

    @server.tool(name='reset_schedule_periods',
                 description='【写操作】把节次重置为内置默认作息'
                             '（早读+上午5节+下午4节+晚自习3节）；**已有课表条目时会被拒绝**')
    async def reset_schedule_periods(sid: int) -> str:
        return await toolkit.db_write(lambda u: _reset_periods(u, sid),
                                      tool='reset_schedule_periods',
                                      params={'sid': sid},
                                      perm='academic.timetable')

    @server.tool(name='manage_global_periods',
                 description='【写操作】全局作息模板：action=save 把本学期作息存为'
                             '全局模板（新建学期默认套用）；action=apply 把全局模板'
                             '套用到本学期')
    async def manage_global_periods(sid: int, action: str) -> str:
        return await toolkit.db_write(
            lambda u: _global_periods(u, sid, action),
            tool='manage_global_periods', params={'sid': sid, 'action': action},
            perm='academic.timetable')
