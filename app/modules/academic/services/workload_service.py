# -*- coding: utf-8 -*-
"""课时量（教师工作量）统计服务 —— 2026-10-13 新增。

**用户口径**：查课统计页负责「统计与导出课时量」；导出要能按**课型**分别设置计算规则
（第五节 / 早读 / 早自习 / 晚自习 / 下午最后一节课 / 监考…各自可设），并支持把某些班
级标记为「**考试期间**」——期间不记工作量，也不计违纪/迟到（考试期间教师有正常考务
工作量）。

数据源是三个库，跨库 **不能 join**，因此统一在 Python 里按 teacher_uid 聚合：

    课表课时   timetable.db  ScheduleEntry（周课表：weekday + period_number + week_range）
    监考场次   grades.db     AffairInvigilation × AffairSchedule（按 exam_date 落在区间内）
    教师名录   academic.db   Teacher

两个口径上的约定：

1. **一天有没有这节课** = `TermSchedule.get_week_number(日期)` 得到周号 →
   `schedule_common.week_range_covers(entry.week_range, 周号)`。这是全校唯一口径
   （与调课冲突判定同源），支持单周/双周/不连续周次；学期没配起止日期时周号为 None，
   `week_range_covers` 会按「覆盖」处理，即整学期都算，不会因为没配置就统计为 0。
2. **监考按姓名匹配教师**：考务的监考安排只有姓名快照（`AffairInvigilation.name`，
   允许校外无账号人员），没有 teacher_uid。先按在职教师同名命中转 uid，命中不了的
   保留姓名作为伪编号，并在导出里单列，不做静默丢弃（避免人数对不上）。

折算规则没有记录时按 **1.0**（一节课 = 一课时），所以学校不配置也能得出可用结果；
factor=0 表示这一种课型**不计入**课时量（导出里仍会单独列出，便于核对）。
"""
from datetime import date, datetime, timedelta

from app.extensions import db
from app.models.academic import ExamPeriod, Teacher, WorkloadRule
from app.models.timetable import PeriodDef, ScheduleEntry, TermSchedule
from app.modules.academic.services.schedule_common import week_range_covers
from app.utils.period_label import period_names

_DEFAULT_FACTOR = 1.0
_MAX_DAYS = 366          # 单次统计最长区间（挡误输；跨学年请分两次导出）


# ── 学期与节次 ─────────────────────────────────────────────────────

def resolve_term(from_d=None):
    """按日期定位学期 → TermSchedule（取不到退回当前学期，可能为 None）。

    注意 `term_service.resolve_schedule_by_date()` 返回的是 **(学期, 周号)** 元组，
    这里只取学期；周号另由 `TermSchedule.get_week_number(日期)` 算（同一口径）。
    """
    try:
        from app.modules.academic.services import term_service
        if from_d:
            res = term_service.resolve_schedule_by_date(from_d)
            # 元组解包（有些分支可能只返回学期对象，两种都兼容）
            ts = res[0] if isinstance(res, (tuple, list)) else res
            if ts:
                return ts
    except Exception:      # noqa: BLE001  学期库缺失/异常不影响统计，退回当前学期
        pass
    from app.modules.academic.services.schedule_common import get_active_schedule
    return get_active_schedule()


def period_options(term):
    """当前学期的节次列表（供「课型折算」逐项设置）。

    [{'number': 1, 'name': '早读'}, ...] —— 名称取 PeriodDef.period_name
    （学校自己维护的作息，「第五节」「下午最后一节课」都只是其中一个节次），
    取不到时回落 period_label 的「第N节」。
    """
    if not term:
        return []
    rows = (PeriodDef.query.filter_by(term_schedule_id=term.id)
            .order_by(PeriodDef.sort_order, PeriodDef.period_number).all())
    names = period_names(term.id) or {}
    return [{'number': p.period_number,
             'name': p.period_name or names.get(p.period_number) or f'第{p.period_number}节'}
            for p in rows]


# ── 折算规则 ───────────────────────────────────────────────────────

def rules_of(term_id):
    """{(kind, period_number): factor} —— 学期级优先，term_id=0 的通用规则兜底。"""
    rows = (WorkloadRule.query
            .filter(WorkloadRule.term_schedule_id.in_([term_id or 0, 0])).all())
    generic, mine = {}, {}
    for r in rows:
        (mine if r.term_schedule_id == (term_id or 0) else generic)[
            (r.kind, r.period_number or 0)] = r.factor
    merged = dict(generic)
    merged.update(mine)
    return merged


def factor_of(rules, kind, period_number=0):
    """取折算系数（无规则 = 1.0）。"""
    f = rules.get((kind, period_number or 0))
    return _DEFAULT_FACTOR if f is None else float(f)


def save_rules(term_id, items):
    """批量写折算规则（不 commit，由调用方统一提交）。

    items: [{'kind': 'period'|'invigilation', 'period_number': int|0, 'factor': float}]
    factor 为 None / 空字符串 = **删除该规则**（回落默认 1.0）。
    """
    term_id = int(term_id or 0)
    changed = 0
    for it in items:
        kind = (it.get('kind') or 'period').strip()
        if kind not in ('period', 'invigilation'):
            continue
        try:
            pn = int(it.get('period_number') or 0)
        except (TypeError, ValueError):
            continue
        f = it.get('factor')
        if f is None or f == '':
            f = None
        else:
            try:
                f = max(0.0, float(f))
            except (TypeError, ValueError):
                continue
        row = (WorkloadRule.query
               .filter_by(term_schedule_id=term_id, kind=kind, period_number=pn).first())
        if f is None:
            if row:
                db.session.delete(row)
                changed += 1
            continue
        if row:
            if abs((row.factor or 0) - f) > 1e-9:
                row.factor = f
                changed += 1
        else:
            db.session.add(WorkloadRule(term_schedule_id=term_id, kind=kind,
                                        period_number=pn, factor=f))
            changed += 1
    return changed


# ── 考试期间 ───────────────────────────────────────────────────────

def exam_rows(from_d=None, to_d=None, grade=None):
    """与给定区间有交集的考试期间（不传日期=全部）。"""
    q = ExamPeriod.query
    if from_d:
        q = q.filter(ExamPeriod.end_date >= from_d)
    if to_d:
        q = q.filter(ExamPeriod.start_date <= to_d)
    if grade:
        q = q.filter(ExamPeriod.grade == grade)
    return q.order_by(ExamPeriod.start_date, ExamPeriod.id).all()


def add_exam_period(grade, class_name, start_date, end_date, note='', creator_id=None):
    """标记一段考试期间。返回 (ok, msg, row)。"""
    grade = (grade or '').strip()
    if not grade:
        return False, '请选择年级', None
    if not isinstance(start_date, date) or not isinstance(end_date, date):
        return False, '请选择开始与结束日期', None
    if end_date < start_date:
        return False, '结束日期不能早于开始日期', None
    row = ExamPeriod(grade=grade, class_name=(class_name or '').strip() or None,
                     start_date=start_date, end_date=end_date,
                     note=(note or '').strip() or None, created_by=creator_id)
    db.session.add(row)
    return True, '已标记考试期间', row


def delete_exam_period(pid):
    row = db.session.get(ExamPeriod, int(pid)) if pid else None
    if not row:
        return False, '记录不存在'
    db.session.delete(row)
    return True, '已删除'


def _exam_index(rows):
    """{(grade, class_name): [(start, end)]} —— 班级为空的年级级记录挂在各班名下处理。"""
    idx = {}
    for r in rows:
        idx.setdefault((r.grade, r.class_name or ''), []).append((r.start_date, r.end_date))
    return idx


def _in_exam(idx, d, grade, class_name):
    """某天某班是否处于考试期间（年级级记录覆盖该年级所有班）。"""
    if not idx:
        return False
    for (g, cn), spans in idx.items():
        if g != grade:
            continue
        if cn and cn != (class_name or ''):
            continue
        for s, e in spans:
            if s <= d <= e:
                return True
    return False


# ── 核心统计 ───────────────────────────────────────────────────────

def calc_workload(from_d, to_d, grade=None, class_name=None, term=None):
    """统计 [from_d, to_d] 区间内每位教师的课时量。

    返回 dict：periods（导出列定义）/ rows（教师明细）/ summary（合计）。
    只统计 **有任课教师** 的课表条目（自习/空教师格本来就不产生教师工作量）。
    """
    if not isinstance(from_d, date) or not isinstance(to_d, date):
        raise ValueError('请选择统计的开始与结束日期')
    days = (to_d - from_d).days + 1
    if days <= 0:
        raise ValueError('结束日期不能早于开始日期')
    if days > _MAX_DAYS:
        raise ValueError(f'单次统计最长 {_MAX_DAYS} 天，请分两次导出')

    term = term or resolve_term(from_d)
    term_id = term.id if term else 0
    rules = rules_of(term_id)
    invig_factor = factor_of(rules, 'invigilation', 0)
    exam_idx = _exam_index(exam_rows(from_d, to_d, grade))

    # ① 课表条目先按星期几分组 —— 逐日展开时只扫当天的那一组
    #    （否则 100 天 × 2000 条 = 20 万次周次解析，实测会明显卡住页面）
    q = ScheduleEntry.query.filter(ScheduleEntry.is_deleted.is_(False),
                                   ScheduleEntry.teacher_uid.isnot(None),
                                   ScheduleEntry.teacher_uid != '')
    if term:
        q = q.filter(ScheduleEntry.term_schedule_id == term.id)
    if grade:
        q = q.filter(ScheduleEntry.grade == grade)
    if class_name:
        q = q.filter(ScheduleEntry.class_name == class_name)
    entries = q.all()
    by_weekday = {}
    for e in entries:
        by_weekday.setdefault(e.weekday, []).append(e)

    # ② 逐日展开 → 教师 × 节次计数；落在考试期间的格子跳过（不计工作量）
    counts = {}          # uid -> {'name':…, 'periods': {pn: n}, 'exam': n}
    total_lessons = exam_excluded = 0
    if term:
        cache = {}
        day = from_d
        while day <= to_d:
            week = term.get_week_number(day)
            wd = day.isoweekday()
            for e in by_weekday.get(wd, []):
                key = (e.week_range or '', week)
                hit = cache.get(key)
                if hit is None:
                    hit = week_range_covers(e.week_range, week)
                    cache[key] = hit
                if not hit:
                    continue
                if _in_exam(exam_idx, day, e.grade, e.class_name):
                    exam_excluded += 1
                    # 也记到教师名下：导出要能看出"这位老师的课时是被考试期间扣掉的"
                    ex = counts.setdefault(e.teacher_uid,
                                           {'name': e.teacher_name or '',
                                            'periods': {}, 'exam': 0})
                    ex['exam'] += 1
                    continue
                c = counts.setdefault(e.teacher_uid, {'name': e.teacher_name or '',
                                                      'periods': {}, 'exam': 0})
                c['periods'][e.period_number] = c['periods'].get(e.period_number, 0) + 1
                total_lessons += 1
                if not c['name'] and e.teacher_name:
                    c['name'] = e.teacher_name
            day += timedelta(days=1)

    # ③ 监考场次（考务侧）：一人一格 = 一场次（与考务模块的监考工作量同一口径）。
    #    场次只有日期/科目、**没有班级**，所以一旦按年级/班级筛选就无法归属 ——
    #    这种情形下监考整块不计（导出里会写明），不做"猜所属年级"的分摊。
    invig = {}
    invig_skipped = bool(grade or class_name)
    rows_i = []
    if not invig_skipped:
        try:
            from app.models.grades import AffairInvigilation, AffairSchedule
            scheds = (AffairSchedule.query
                      .filter(AffairSchedule.exam_date.isnot(None),
                              AffairSchedule.exam_date >= from_d,
                              AffairSchedule.exam_date <= to_d).all())
            if scheds:
                rows_i = AffairInvigilation.query.filter(
                    AffairInvigilation.schedule_id.in_({s.id for s in scheds})).all()
        except Exception:  # noqa: BLE001  考务库缺失/未迁移：监考记 0，不影响课表课时
            rows_i = []
    name_to_uid = {}
    for iv in rows_i:
        nm = (iv.name or '').strip()
        if not nm:
            continue
        uid = name_to_uid.get(nm)
        if uid is None:
            t = Teacher.query.filter_by(name=nm, status='active').first()
            uid = t.teacher_uid if t else ('#' + nm)     # 未匹配：保留为伪编号
            name_to_uid[nm] = uid
        invig[uid] = invig.get(uid, {'name': nm, 'count': 0})
        invig[uid]['count'] += 1

    # ④ 组装明细（教师名录用来补齐姓名与学科，离职教师仍出数，便于核对历史）
    periods = period_options(term)
    pmap = {p['number']: p for p in periods}
    teachers = {t.teacher_uid: t for t in Teacher.query.all()}
    rows = []
    for uid in set(list(counts.keys()) + list(invig.keys())):
        c = counts.get(uid) or {'name': '', 'periods': {}, 'exam': 0}
        iv = invig.get(uid) or {'name': '', 'count': 0}
        t = teachers.get(uid)
        name = t.name if t else (c['name'] or iv['name'] or uid)
        period_hours = lessons = 0.0
        for pn, cnt in c['periods'].items():
            lessons += cnt
            period_hours += cnt * factor_of(rules, 'period', pn)
        # 课表里出现、但本学期没定义过的节次也要出列（避免隐性漏统计）
        for pn in c['periods']:
            pmap.setdefault(pn, {'number': pn, 'name': f'第{pn}节'})
        invig_hours = (iv['count'] or 0) * invig_factor
        rows.append({
            'teacher_uid': uid, 'teacher_name': name,
            'subject': (t.subject if t else '') or '',
            'status': (t.status if t else 'unknown'),
            'counts': dict(c['periods']),
            'lessons': lessons,
            'period_hours': round(period_hours, 2),
            'invig_count': iv['count'] or 0,
            'invig_hours': round(invig_hours, 2),
            'total_hours': round(period_hours + invig_hours, 2),
            'exam_excluded': c.get('exam', 0),
        })
    rows.sort(key=lambda r: (-r['total_hours'], r['teacher_name']))

    shown = sorted(pmap.values(), key=lambda p: p['number'])
    for p in shown:
        p['factor'] = factor_of(rules, 'period', p['number'])
    return {
        'from': from_d, 'to': to_d, 'days': days,
        'term_id': term_id, 'term_name': (term.name if term else ''),
        'periods': shown,
        'invigilation_factor': invig_factor,
        'rows': rows,
        'summary': {
            'teachers': len(rows),
            'lessons': total_lessons,
            'period_hours': round(sum(r['period_hours'] for r in rows), 2),
            'invig_slots': sum(r['invig_count'] for r in rows),
            'invig_hours': round(sum(r['invig_hours'] for r in rows), 2),
            'total_hours': round(sum(r['total_hours'] for r in rows), 2),
            'exam_excluded': exam_excluded,
            'invig_skipped': invig_skipped,     # 按班/年级筛选时监考无法归属，整块不计
        },
    }


# ── 导出 Excel ─────────────────────────────────────────────────────

def export_workload(from_d, to_d, grade=None, class_name=None, term=None):
    """生成课时量统计 Excel（openpyxl），返回 BytesIO（文件名由调用方拼）。"""
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    data = calc_workload(from_d, to_d, grade=grade, class_name=class_name, term=term)
    wb = Workbook()
    ws = wb.active
    ws.title = '课时量统计'

    head_font = Font(bold=True, color='FFFFFF')
    head_fill = PatternFill('solid', fgColor='1E3A8A')
    tip_font = Font(size=9, color='64748B')

    ws.cell(row=1, column=1,
            value=f"课时量统计　{from_d} ~ {to_d}　（{data['term_name'] or '未指定学期'}）")
    ws.cell(row=1, column=1).font = Font(bold=True, size=13)
    s = data['summary']
    tip = (f"{s['teachers']} 位教师 · 有课 {s['lessons']} 节次"
           f" · 课表折算 {s['period_hours']} 课时"
           + (f" · 监考 {s['invig_slots']} 场（折算 {s['invig_hours']} 课时）"
              if not s.get('invig_skipped') else ' · 监考：按年级/班级筛选时无法归属，未计入')
           + f" · 合计 {s['total_hours']} 课时"
           + (f" · 考试期间不计 {s['exam_excluded']} 节次" if s['exam_excluded'] else ''))
    ws.cell(row=2, column=1, value='口径：' + tip)
    ws.cell(row=2, column=1).font = tip_font

    periods = data['periods']
    headers = (['序号', '教师', '学科'] + [p['name'] for p in periods]
               + ['节次合计', '监考场次', '课表折算', '合计课时', '考试期间不计'])
    r0 = 3
    for ci, h in enumerate(headers, 1):
        c = ws.cell(row=r0, column=ci, value=h)
        c.font = head_font
        c.fill = head_fill
        c.alignment = Alignment(horizontal='center')

    r = r0
    for i, row in enumerate(data['rows'], 1):
        r += 1
        vals = [i, row['teacher_name'], row['subject']]
        vals += [row['counts'].get(p['number'], 0) for p in periods]
        vals += [row['lessons'], row['invig_count'], row['period_hours'],
                 row['total_hours'], row['exam_excluded']]
        for ci, v in enumerate(vals, 1):
            ws.cell(row=r, column=ci, value=v)
    r += 1
    ws.cell(row=r, column=2, value='合计').font = Font(bold=True)
    ws.cell(row=r, column=len(headers) - 4, value=s['lessons'])
    ws.cell(row=r, column=len(headers) - 3, value=s['invig_slots'])
    ws.cell(row=r, column=len(headers) - 2, value=s['period_hours'])
    ws.cell(row=r, column=len(headers) - 1, value=s['total_hours'])
    ws.cell(row=r, column=len(headers), value=s['exam_excluded'])

    ws.freeze_panes = 'D4'
    out = BytesIO()
    wb.save(out)
    out.seek(0)
    return out, data
