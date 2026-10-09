# StuLink v1.18.7.1 2026-10-09
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""任课安排 / 备课组长（2026-09-25）。

设计取舍：
- **任课安排不落库**，全部由课表条目（timetable.db.schedule_entries）实时聚合。
  这样"课表一改，任课表立刻跟着变"，不会出现两处数据打架；周课时＝该周次过滤
  后的条目数（1 条目 = 1 节/周）。
- **备课组长落库**（academic.db.subject_leaders）：它无法从课表推导，按
  学年 × 学期 × 年级 × 学科 维护，同键重复保存即更新。
"""
import io
from collections import OrderedDict

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from app.extensions import db
from app.models.academic import SUBJECT_ORDER, SubjectLeader
from app.models.timetable import ScheduleEntry, TermSchedule
from app.modules.academic.services.schedule_common import week_range_covers
from app.utils.export_helpers import xl_safe

# 文化课（图片里「文化课总」列的口径：语数英 + 理科 + 文科）
CORE_SUBJECTS = ['语文', '数学', '英语', '物理', '化学', '生物', '政治', '历史', '地理']

_XLSX_MIME = ('application/vnd.openxmlformats-officedocument'
              '.spreadsheetml.sheet')


# ── 公共小工具 ─────────────────────────────────────────────────────────────

def _subject_key(subject):
    try:
        return (SUBJECT_ORDER.index(subject), subject)
    except ValueError:
        return (len(SUBJECT_ORDER), subject or '')


def order_subjects(subjects):
    """按学科固定顺序排序（未列入 SUBJECT_ORDER 的按名称排在后面）"""
    return sorted({s for s in subjects if s}, key=_subject_key)


def _entries(schedule_id, week=None, grade=None):
    q = ScheduleEntry.query.filter_by(term_schedule_id=schedule_id, is_deleted=False)
    if grade:
        q = q.filter_by(grade=grade)
    rows = q.all()
    if week:
        rows = [e for e in rows if week_range_covers(e.week_range, week)]
    return rows


def _head_teacher_map(pairs):
    """{(grade, class_name): 班主任姓名} —— 优先多对多关联表，其次 users 上的班级字段。

    pairs: 需要查询的 (grade, class_name) 集合；查询失败（如非班主任角色表未初始化）静默返回空。
    """
    result = {}
    try:
        from app.models import User, UserClassLink
        # v1.18.7.1 审核（🟡-3）：原实现对每条 UserClassLink 逐个 db.session.get(User)（N+1），
        # 改为一次 IN 查询预取，避免班数多时上百次往返。
        links = UserClassLink.query.all()
        uids = {lk.user_id for lk in links if lk.user_id}
        umap = ({u.id: u for u in User.query.filter(User.id.in_(uids)).all()}
                if uids else {})
        for link in links:
            u = umap.get(link.user_id)
            if u and u.is_active:
                result.setdefault((link.grade, link.class_name), []).append(u.real_name)
        for u in User.query.filter_by(role='homeroom_teacher').all():
            if u.grade and u.class_name:
                names = result.setdefault((u.grade, u.class_name), [])
                if u.real_name not in names:
                    names.append(u.real_name)
    except Exception:  # noqa: BLE001  班主任信息缺失不影响任课表
        pass
    return {k: '、'.join(v) for k, v in result.items() if v}


# ── 视角一：按班级（图片主形态） ───────────────────────────────────────────

def class_type_options():
    """班级档案里出现过的班型（强基班/卓越班…），供任课表按班型筛选。"""
    try:
        from app.models import ClassProfile
        rows = db.session.query(ClassProfile.class_type).distinct().all()
        return sorted({r[0] for r in rows if r[0]})
    except Exception:  # noqa: BLE001
        return []


def build_class_duty(schedule_id, week=None, grade=None, class_type=None,
                     direction=None):
    """任课安排（按班级）：年级分块 → 班级行 × 学科列。

    每格：{teachers: [姓名], hours: 周课时}；行尾给「文化课总」「周课时合计」；
    表尾给「学科合计」与「学科教师清单」。
    class_type：按班型筛选（只看"强基班"）；direction：按选科方向筛选
    （新高考 3+1+2 的"物理/历史"），两者都读班级档案 ClassProfile。
    """
    from app.modules.academic.services.grade_utils import (class_profile_map,
                                                           grade_labels,
                                                           grade_sort_key)

    rows = _entries(schedule_id, week, grade)
    # 年级候选集取「未按年级过滤」的全量（只受周次影响）：否则筛出某个年级后
    # 按钮组只剩该年级，用户无法切回其它年级
    all_grades = sorted({e.grade for e in _entries(schedule_id, week, None) if e.grade})
    profiles = class_profile_map()
    labels = grade_labels(all_grades)
    all_grades.sort(key=lambda g: (-grade_sort_key(g, labels), g))

    if class_type:
        rows = [e for e in rows
                if profiles.get((e.grade, e.class_name), {}).get('class_type') == class_type]
    if direction:
        rows = [e for e in rows
                if profiles.get((e.grade, e.class_name), {}).get('direction') == direction]

    by_grade = OrderedDict()
    for e in rows:
        by_grade.setdefault(e.grade, []).append(e)

    head_map = _head_teacher_map(by_grade.keys())

    blocks = []
    # 高中习惯：毕业年级在前（高三 → 高二 → 高一）
    for g in sorted(by_grade, key=lambda x: (-grade_sort_key(x, labels), x)):
        grade_entries = by_grade[g]
        subjects = order_subjects(e.subject for e in grade_entries)
        class_names = sorted({e.class_name for e in grade_entries})

        cls_rows = []
        for cn in class_names:
            cells = {}
            row_hours = 0
            core_hours = 0
            for subj in subjects:
                matched = [e for e in grade_entries
                           if e.class_name == cn and e.subject == subj]
                if not matched:
                    continue
                teachers = []
                for e in matched:
                    name = (e.teacher_name or '').strip() or '未指定'
                    if name not in teachers:
                        teachers.append(name)
                cells[subj] = {'teachers': teachers, 'hours': len(matched)}
                row_hours += len(matched)
                if subj in CORE_SUBJECTS:
                    core_hours += len(matched)
            cls_rows.append({
                'grade': g,
                'class_name': cn,
                'class_type': profiles.get((g, cn), {}).get('class_type', ''),
                'direction': profiles.get((g, cn), {}).get('direction', ''),
                'combo': profiles.get((g, cn), {}).get('combo', ''),
                'combo_short': profiles.get((g, cn), {}).get('combo_short', ''),
                'head_teacher': head_map.get((g, cn), ''),
                'cells': cells,
                'row_total': row_hours,
                'core_total': core_hours,
            })

        subject_totals = {}
        subject_teachers = {}
        for subj in subjects:
            matched = [e for e in grade_entries if e.subject == subj]
            subject_totals[subj] = len(matched)
            names = []
            for e in sorted(matched, key=lambda x: (x.teacher_name or '')):
                name = (e.teacher_name or '').strip() or '未指定'
                if name not in names:
                    names.append(name)
            subject_teachers[subj] = names

        blocks.append({
            'grade': g,
            'grade_label': labels.get(g, g),
            'subjects': subjects,
            'rows': cls_rows,
            'subject_totals': subject_totals,
            'subject_teachers': subject_teachers,
            'grand_total': len(grade_entries),
            'core_total': sum(subject_totals.get(s, 0) for s in CORE_SUBJECTS),
            'class_count': len(class_names),
        })

    return {
        'blocks': blocks,
        'grades': [b['grade'] for b in blocks],
        'all_grades': all_grades,
        'labels': labels,
        'class_type': class_type or '',
        'direction': direction or '',
        'directions': sorted({v.get('direction') for v in profiles.values()
                              if v.get('direction')}),
        'grade_filter': grade or '',
        'total': len(rows),
    }


# ── 视角二：按教师 ─────────────────────────────────────────────────────────

def build_teacher_duty(schedule_id, week=None, grade=None, warn_hours=None):
    """任课安排（按教师）：每位教师的姓名/学科/授课班级/周课时数。

    满足"按学科或教师查看"的需求；与按班级视角同源，数据不会不一致。
    warn_hours：周课时超过该值时在结果里标 `warn=True`（用于超课时预警，None=不预警）。
    """
    rows = _entries(schedule_id, week, grade)
    agg = {}
    for e in rows:
        key = e.teacher_uid or f'name:{e.teacher_name or "未指定"}'
        node = agg.setdefault(key, {
            'uid': e.teacher_uid or '',
            'name': (e.teacher_name or '').strip() or '未指定',
            'subjects': set(),
            'classes': set(),
            'grades': set(),
            'hours': 0,
        })
        node['subjects'].add(e.subject)
        node['classes'].add(f'{e.grade}{e.class_name}')
        node['grades'].add(e.grade)
        node['hours'] += 1

    items = []
    for node in agg.values():
        items.append({
            'uid': node['uid'],
            'name': node['name'],
            'subjects': order_subjects(node['subjects']),
            'subject_text': '、'.join(order_subjects(node['subjects'])),
            'classes': sorted(node['classes']),
            'class_text': '、'.join(sorted(node['classes'])),
            'class_count': len(node['classes']),
            'grades': sorted(node['grades']),
            'hours': node['hours'],
            'warn': bool(warn_hours and node['hours'] > warn_hours),
        })
    items.sort(key=lambda x: (-x['hours'], x['name']))
    return items


# ── 视角三：按学科 ─────────────────────────────────────────────────────────

def build_subject_duty(schedule_id, week=None, grade=None):
    """任课安排（按学科）：学科 → 教师 → 授课班级 + 周课时。"""
    rows = _entries(schedule_id, week, grade)
    by_subject = {}
    for e in rows:
        by_subject.setdefault(e.subject, {}).setdefault(
            e.teacher_uid or e.teacher_name or '未指定', []).append(e)

    result = []
    for subj in order_subjects(by_subject):
        teachers = []
        for items in by_subject[subj].values():
            sample = items[0]
            teachers.append({
                'uid': sample.teacher_uid or '',
                'name': (sample.teacher_name or '').strip() or '未指定',
                'classes': sorted({f'{e.grade}{e.class_name}' for e in items}),
                'class_text': '、'.join(sorted({f'{e.grade}{e.class_name}' for e in items})),
                'grades': sorted({e.grade for e in items}),
                'hours': len(items),
            })
        teachers.sort(key=lambda x: (-x['hours'], x['name']))
        result.append({
            'subject': subj,
            'teachers': teachers,
            'hours': sum(t['hours'] for t in teachers),
            'teacher_count': len(teachers),
        })
    return result


# ── 备课组长 ───────────────────────────────────────────────────────────────

def leader_years():
    """已有登记的学年列表（倒序），供页面下拉。"""
    rows = db.session.query(SubjectLeader.school_year).distinct().all()
    years = sorted({r[0] for r in rows if r[0]}, reverse=True)
    return years


def list_leaders(school_year=None, term=None, subject=None, grade=None, keyword=None):
    q = SubjectLeader.query
    if school_year:
        q = q.filter_by(school_year=school_year)
    if term is not None and term != '':
        q = q.filter_by(term=term)
    if subject:
        q = q.filter_by(subject=subject)
    if grade:
        q = q.filter_by(grade=grade)
    if keyword:
        like = f'%{keyword}%'
        q = q.filter(db.or_(SubjectLeader.leader_name.like(like),
                            SubjectLeader.subject.like(like),
                            SubjectLeader.grade.like(like),
                            SubjectLeader.duty.like(like)))
    items = q.all()
    items.sort(key=lambda x: (_subject_key(x.subject), x.grade or '', x.leader_name or ''))
    return items


def group_leaders(leaders):
    """按学科分组：[(subject, [leader, ...]), ...]（学科按固定顺序）"""
    grouped = OrderedDict()
    for ld in leaders:
        grouped.setdefault(ld.subject, []).append(ld)
    return [(subj, grouped[subj]) for subj in sorted(grouped, key=_subject_key)]


def save_leader(data, operator_id=None):
    """新增或更新（同 学年×学期×年级×学科 视为同一条）。

    返回 (leader, created)；数据不合法抛 ValueError。
    """
    school_year = (data.get('school_year') or '').strip()
    term = (data.get('term') or '').strip()
    grade = (data.get('grade') or '').strip()
    subject = (data.get('subject') or '').strip()
    leader_name = (data.get('leader_name') or '').strip()
    if not school_year:
        raise ValueError('学年必填（如 2026-2027）')
    if not subject:
        raise ValueError('学科必填')
    if not leader_name:
        raise ValueError('组长姓名必填')

    leader = SubjectLeader.query.filter_by(school_year=school_year, term=term,
                                           grade=grade, subject=subject).first()
    created = leader is None
    if created:
        leader = SubjectLeader(school_year=school_year, term=term, grade=grade,
                               subject=subject)
        db.session.add(leader)

    leader.leader_uid = (data.get('leader_uid') or '').strip() or None
    leader.leader_name = leader_name[:50]
    leader.members = (data.get('members') or '').strip()[:300] or None
    leader.duty = (data.get('duty') or '').strip()[:500] or None
    try:
        leader.sort_order = int(data.get('sort_order') or 0)
    except (TypeError, ValueError):
        leader.sort_order = 0
    leader.updated_by = operator_id
    db.session.commit()
    return leader, created


def delete_leader(leader_id):
    leader = db.session.get(SubjectLeader, leader_id)
    if not leader:
        return None
    info = f'{leader.school_year} {leader.grade or "全校"}{leader.subject} {leader.leader_name}'
    db.session.delete(leader)
    db.session.commit()
    return info


# ── Excel 导出 ─────────────────────────────────────────────────────────────

def _style_header(ws, row, headers, widths=None):
    hf = Font(bold=True, color='FFFFFF', size=10)
    hfl = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
    tb = Border(left=Side('thin'), right=Side('thin'),
                top=Side('thin'), bottom=Side('thin'))
    center = Alignment(horizontal='center', vertical='center', wrap_text=True)
    for ci, h in enumerate(headers, 1):
        c = ws.cell(row=row, column=ci, value=h)
        c.font = hf
        c.fill = hfl
        c.alignment = center
        c.border = tb
    if widths:
        for ci, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(ci)].width = w


def export_duty_workbook(schedule_id, week=None, grade=None, view='class',
                         class_type=None, direction=None):
    """任课安排导出：view=class（按班级，图片形态）/ teacher / subject。"""
    ts = db.session.get(TermSchedule, schedule_id)
    title = ts.name if ts else '任课安排'
    wb = Workbook()
    wb.remove(wb.active)

    tb = Border(left=Side('thin'), right=Side('thin'),
                top=Side('thin'), bottom=Side('thin'))
    center = Alignment(horizontal='center', vertical='center', wrap_text=True)

    if view == 'teacher':
        ws = wb.create_sheet('按教师')
        _style_header(ws, 1, ['教师', '教师编号', '所授学科', '授课班级', '班级数', '周课时数'],
                      [12, 14, 20, 60, 10, 12])
        for ri, t in enumerate(build_teacher_duty(schedule_id, week=week, grade=grade), 2):
            for ci, v in enumerate([t['name'], t['uid'], t['subject_text'],
                                    t['class_text'], t['class_count'], t['hours']], 1):
                c = ws.cell(row=ri, column=ci, value=xl_safe(v))
                c.border = tb
                c.alignment = center
    elif view == 'subject':
        ws = wb.create_sheet('按学科')
        _style_header(ws, 1, ['学科', '教师', '教师编号', '授课班级', '周课时数'],
                      [12, 12, 14, 70, 12])
        ri = 2
        for grp in build_subject_duty(schedule_id, week=week, grade=grade):
            for t in grp['teachers']:
                for ci, v in enumerate([grp['subject'], t['name'], t['uid'],
                                        t['class_text'], t['hours']], 1):
                    c = ws.cell(row=ri, column=ci, value=xl_safe(v))
                    c.border = tb
                    c.alignment = center
                ri += 1
            ri += 1
    else:
        data = build_class_duty(schedule_id, week=week, grade=grade,
                                class_type=class_type, direction=direction)
        ws = wb.create_sheet('任课安排')
        ri = 1
        ws.cell(row=ri, column=1, value=title).font = Font(bold=True, size=12)
        if week:
            ws.cell(row=ri, column=2, value=f'第 {week} 周').font = Font(bold=True, size=11)
        ri += 2
        for b in data['blocks']:
            subj_cnt = len(b['subjects'])
            headers = ['年级', '班级', '班型', '选科', '班主任'] + b['subjects'] + ['文化课总', '周课时合计']
            _style_header(ws, ri, headers,
                          [8, 10, 10, 12, 12] + [13] * subj_cnt + [11, 12])
            ri += 1
            for row in b['rows']:
                combo = row.get('combo', '')
                vals = [row['grade'], row['class_name'], row['class_type'],
                        (row.get('direction', '') + ('·' + combo if combo else '')) or '—',
                        row['head_teacher']]
                for subj in b['subjects']:
                    cell = row['cells'].get(subj)
                    if not cell:
                        vals.append('')
                    else:
                        label = ' '.join(cell['teachers'])
                        vals.append(f'{label} {cell["hours"]}')
                vals.extend([row['core_total'], row['row_total']])
                for ci, v in enumerate(vals, 1):
                    c = ws.cell(row=ri, column=ci, value=xl_safe(v))
                    c.border = tb
                    c.alignment = center
                ri += 1
            # 学科合计 + 学科教师清单
            totals = ['合计', f'{b["class_count"]} 个班', '', '', '']
            totals += [b['subject_totals'].get(s, 0) for s in b['subjects']]
            totals += [b['core_total'], b['grand_total']]
            for ci, v in enumerate(totals, 1):
                c = ws.cell(row=ri, column=ci, value=xl_safe(v))
                c.border = tb
                c.alignment = center
                c.font = Font(bold=True, size=10)
            ri += 1
            teachers_row = ['教师', '', '', '', ''] + [
                '、'.join(b['subject_teachers'].get(s, [])) for s in b['subjects']]
            teachers_row += ['', '']
            for ci, v in enumerate(teachers_row, 1):
                c = ws.cell(row=ri, column=ci, value=xl_safe(v))
                c.border = tb
                c.alignment = Alignment(horizontal='center', vertical='center',
                                        wrap_text=True)
            ri += 3

    if not wb.sheetnames:
        wb.create_sheet('空').append(['无数据'])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def export_leaders_workbook(leaders, school_year=None):
    """备课组长名单导出（按学科分组，含职责与备课组范围）"""
    wb = Workbook()
    ws = wb.active
    ws.title = '备课组长名单'
    tb = Border(left=Side('thin'), right=Side('thin'),
                top=Side('thin'), bottom=Side('thin'))
    center = Alignment(horizontal='center', vertical='center', wrap_text=True)
    left = Alignment(horizontal='left', vertical='center', wrap_text=True)

    ws.cell(row=1, column=1,
            value=xl_safe(f'{school_year or ""} 备课组长名单'.strip())).font = Font(bold=True, size=13)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=7)
    headers = ['学科', '年级', '学期', '组长', '教师编号', '备课组范围', '主要职责']
    for ci, h in enumerate(headers, 1):
        c = ws.cell(row=2, column=ci, value=h)
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
        c.alignment = center
        c.border = tb
    for ci, w in enumerate([12, 10, 12, 12, 14, 34, 70], 1):
        ws.column_dimensions[get_column_letter(ci)].width = w

    ri = 3
    for subject, rows in group_leaders(leaders):
        for ld in rows:
            for ci, v in enumerate([ld.subject, ld.grade or '全校', ld.term or '整学年',
                                    ld.leader_name, ld.leader_uid or '',
                                    ld.members or '', ld.duty or ''], 1):
                c = ws.cell(row=ri, column=ci, value=xl_safe(v))
                c.border = tb
                c.alignment = center if ci <= 5 else left
            ri += 1
    if ri == 3:
        ws.cell(row=3, column=1, value='暂无记录')
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
