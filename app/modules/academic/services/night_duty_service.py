# StuLink v1.18.2.1 2026-09-24
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""晚自习值班服务（2026-09-26，高中教务）。

高中晚自习不排学科课，而是排教师**值班看班/巡楼**。这里负责：
- 取晚自习节次（PeriodDef 里 period_type='evening'）；
- **均衡自动排班**：同一位教师同一天只值一节、一周次数均衡、白天课多的少排；
- 手工调整 / 清空单个班次；
- 冲突检查（同一教师同天多节、单周次数超标）；
- 今日值班（给门卫/巡楼用）与教师值班次数统计；
- 导出 Excel（贴墙用）。

值班只读课表，不写课表条目 —— 值班不是课，不该出现在班级课表网格里。
"""
from collections import defaultdict
from datetime import date, datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from app.extensions import db
from app.models.timetable import (WEEKDAY_NAMES, NightDuty, PeriodDef,
                                  ScheduleEntry)
from app.modules.academic.services.schedule_service import _base_entry_query
from app.utils.export_helpers import xl_safe

WEEKDAYS = (1, 2, 3, 4, 5)
WEEKLY_CAP = 2          # 每位教师每周值班上限（高中惯例 1~2 次）


def evening_period_numbers(schedule_id):
    """晚自习节次号（升序）。没有配置 evening 节次时返回空表，页面会给出引导。"""
    rows = (PeriodDef.query.filter_by(term_schedule_id=schedule_id)
            .order_by(PeriodDef.period_number).all())
    return [p.period_number for p in rows if (p.period_type or '') == 'evening']


def grades_of(schedule_id):
    """该学期有课的年级（升序）。"""
    rows = (db.session.query(ScheduleEntry.grade)
            .filter_by(term_schedule_id=schedule_id, is_deleted=False)
            .distinct().all())
    return sorted({g[0] for g in rows if g[0]})


def teacher_pool():
    """可值班教师池：在职教师（含行政，学科可能为空）。"""
    try:
        from app.models.academic import Teacher
        rows = Teacher.query.filter_by(status='active').order_by(Teacher.id).all()
    except Exception:  # noqa: BLE001  教师表不可用时退化为空池
        return []
    return [{'uid': t.teacher_uid, 'name': t.name, 'subject': t.subject or ''}
            for t in rows if t.teacher_uid]


def get_roster(schedule_id, grades=None):
    """值班表数据：按年级分块，每块 {weekday: {period: duty}}。"""
    grades = grades or grades_of(schedule_id)
    periods = evening_period_numbers(schedule_id)
    rows = NightDuty.query.filter_by(term_schedule_id=schedule_id).all()
    grid = {g: {wd: {} for wd in WEEKDAYS} for g in grades}
    for d in rows:
        if d.grade in grid:
            grid[d.grade].setdefault(d.weekday, {})[d.period_number] = d.to_dict()
    blocks = [{'grade': g, 'grid': grid[g]} for g in grades]
    return {'blocks': blocks, 'periods': periods, 'grades': grades,
            'total': len(rows)}


def set_duty(schedule_id, grade, weekday, period_number, teacher_uid=None,
             teacher_name=None, note=None, operator=None):
    """手工指定某个班次的值班教师；teacher_uid 为空＝清空该班次。"""
    slot = NightDuty.query.filter_by(term_schedule_id=schedule_id, grade=grade,
                                     weekday=weekday,
                                     period_number=period_number).first()
    if not teacher_uid:
        if slot:
            db.session.delete(slot)
            db.session.commit()
        return True, '已清空'
    if slot is None:
        slot = NightDuty(term_schedule_id=schedule_id, grade=grade, weekday=weekday,
                         period_number=period_number)
        db.session.add(slot)
    slot.teacher_uid = teacher_uid
    slot.teacher_name = teacher_name
    slot.note = note
    db.session.commit()
    if operator is not None:
        try:
            from app.utils.helpers import log_operation
            log_operation(operator, '更新', '晚自习值班', schedule_id,
                          f'{grade} 周{weekday} 第{period_number}节 {teacher_name}',
                          module='academic')
        except Exception:  # noqa: BLE001
            pass
    return True, '已保存'


def auto_assign(schedule_id, max_per_week=WEEKLY_CAP, grades=None, weekdays=None,
                replace=True, operator=None):
    """均衡自动排班 → (ok, message)。

    规则（贴近高中教务的排班习惯）：
    1. 同一位教师**同一天**只值一节（值完就走，不连轴）；
    2. 每位教师**一周**不超过 max_per_week 次；
    3. 同等条件下，优先选**当天课少**的教师（避免白天满课还要值晚自习）；
    4. 教师实在不够时放宽周上限，仍不够才留空（页面会提示哪些班次空着）。
    """
    periods = evening_period_numbers(schedule_id)
    if not periods:
        return False, '该学期没有「晚自习」节次：请先到节次配置里把晚自习的类型设为 evening'
    grades = grades or grades_of(schedule_id)
    if not grades:
        return False, '该学期还没有课表数据，无法确定要值班的年级'
    pool = teacher_pool()
    if not pool:
        return False, '教师表为空，无法排班'

    if replace:
        NightDuty.query.filter_by(term_schedule_id=schedule_id).delete()
        db.session.flush()

    week_count = {}
    day_used = set()
    for d in NightDuty.query.filter_by(term_schedule_id=schedule_id).all():
        if d.teacher_uid:
            week_count[d.teacher_uid] = week_count.get(d.teacher_uid, 0) + 1
            day_used.add((d.teacher_uid, d.weekday))

    # 教师当天课量：同分时优先排白天课少的
    day_load = defaultdict(int)
    for e in _base_entry_query(schedule_id).all():
        if e.teacher_uid and e.weekday:
            day_load[(e.teacher_uid, e.weekday)] += 1

    filled = skipped = 0
    for grade in grades:
        for wd in weekdays or WEEKDAYS:
            for pn in periods:
                slot = NightDuty.query.filter_by(
                    term_schedule_id=schedule_id, grade=grade, weekday=wd,
                    period_number=pn).first()
                if slot and not replace:
                    continue
                cands = [t for t in pool
                         if (t['uid'], wd) not in day_used
                         and week_count.get(t['uid'], 0) < max_per_week]
                if not cands:      # 放宽周上限，尽量不留空
                    cands = [t for t in pool if (t['uid'], wd) not in day_used]
                if not cands:
                    skipped += 1
                    continue
                pick = min(cands, key=lambda t: (week_count.get(t['uid'], 0),
                                                 day_load.get((t['uid'], wd), 0),
                                                 t['uid']))
                if slot is None:
                    slot = NightDuty(term_schedule_id=schedule_id, grade=grade,
                                     weekday=wd, period_number=pn)
                    db.session.add(slot)
                slot.teacher_uid = pick['uid']
                slot.teacher_name = pick['name']
                week_count[pick['uid']] = week_count.get(pick['uid'], 0) + 1
                day_used.add((pick['uid'], wd))
                filled += 1
    db.session.commit()

    if operator is not None:
        try:
            from app.utils.helpers import log_operation
            log_operation(operator, '更新', '晚自习值班', schedule_id,
                          f'自动排班 {filled} 个班次', module='academic')
        except Exception:  # noqa: BLE001
            pass
    msg = f'已排 {filled} 个班次'
    if skipped:
        msg += f'，教师不足有 {skipped} 个班次留空'
    return True, msg


def check_conflicts(schedule_id):
    """值班冲突：同一教师同一天多节、单周次数较多。"""
    rows = NightDuty.query.filter_by(term_schedule_id=schedule_id).all()
    by_day = defaultdict(list)
    week_count = defaultdict(int)
    name_of = {}
    for d in rows:
        if not d.teacher_uid:
            continue
        name_of[d.teacher_uid] = d.teacher_name or d.teacher_uid
        week_count[d.teacher_uid] += 1
        by_day[(d.teacher_uid, d.weekday)].append(d)
    out = []
    for (uid, wd), ds in sorted(by_day.items()):
        if len(ds) > 1:
            out.append({'type': 'same_day', 'level': 'warning',
                        'teacher': name_of.get(uid, uid),
                        'weekday': wd, 'weekday_text': WEEKDAY_NAMES.get(wd, ''),
                        'count': len(ds),
                        'slots': [f'{x.grade}第{x.period_number}节' for x in ds]})
    for uid, cnt in sorted(week_count.items()):
        if cnt > max(WEEKLY_CAP, 3):
            out.append({'type': 'over_week', 'level': 'info',
                        'teacher': name_of.get(uid, uid), 'count': cnt})
    return out


def today_duties(schedule_id, weekday=None):
    """今日值班清单（默认取系统当天星期；周末返回空表）。"""
    wd = weekday or date.today().isoweekday()
    rows = (NightDuty.query.filter_by(term_schedule_id=schedule_id, weekday=wd)
            .order_by(NightDuty.grade, NightDuty.period_number).all())
    return [d.to_dict() for d in rows]


def teacher_stats(schedule_id):
    """教师值班次数统计（按次数降序）。"""
    rows = NightDuty.query.filter_by(term_schedule_id=schedule_id).all()
    stat = {}
    for d in rows:
        if not d.teacher_uid:
            continue
        item = stat.setdefault(d.teacher_uid, {
            'uid': d.teacher_uid, 'name': d.teacher_name or d.teacher_uid,
            'count': 0, 'days': []})
        item['count'] += 1
        wd = WEEKDAY_NAMES.get(d.weekday, '')
        if wd not in item['days']:
            item['days'].append(wd)
    return sorted(stat.values(), key=lambda x: (-x['count'], x['name']))


def export_workbook(schedule_id, grades=None):
    """导出值班表 Excel（每行一个班次，按年级分块，贴墙/发年级组用）。"""
    data = get_roster(schedule_id, grades=grades)
    wb = Workbook()
    ws = wb.active
    ws.title = '晚自习值班表'
    thin = Side(style='thin', color='BBBBBB')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    title = ws.cell(row=1, column=1, value='晚自习值班表')
    title.font = Font(bold=True, size=14)
    periods = data['periods']
    head = ws.cell(row=2, column=1, value='年级')
    head.font = Font(bold=True)
    for i, wd in enumerate(WEEKDAYS):
        ws.cell(row=2, column=2 + i, value=WEEKDAY_NAMES.get(wd, ''))
        ws.cell(row=2, column=2 + i).font = Font(bold=True)
    fill = PatternFill('solid', fgColor='EEF2FF')
    for c in ws[2]:
        c.fill = fill
        c.alignment = Alignment(horizontal='center')
        c.border = border
    ri = 3
    for b in data['blocks']:
        for pn in periods:
            ws.cell(row=ri, column=1,
                    value=xl_safe(f"{b['grade']} 第{pn}节")).border = border
            for i, wd in enumerate(WEEKDAYS):
                cell = ws.cell(row=ri, column=2 + i,
                               value=xl_safe((b['grid'].get(wd, {}) or {})
                                             .get(pn, {}).get('teacher_name') or ''))
                cell.alignment = Alignment(horizontal='center')
                cell.border = border
            ri += 1
    ws.column_dimensions['A'].width = 18
    for i in range(len(WEEKDAYS)):
        ws.column_dimensions[chr(ord('B') + i)].width = 14
    return wb
