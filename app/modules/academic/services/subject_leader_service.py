# StuLink v1.18.8.0 2026-10-09
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""备课组长（2026-09-25）。

**备课组长落库**（academic.db.subject_leaders）：它无法从课表推导，按
学年 × 学期 × 年级 × 学科 维护，同键重复保存即更新。

任课安排（原 `/academic/duty`，由课表条目实时聚合）已于 2026-10-10 下线，
教师-学科关系以「任课教师映射」（TeacherSubjectLink）为唯一依据。
"""
import io
from collections import OrderedDict

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from app.extensions import db
from app.models.academic import SUBJECT_ORDER, SubjectLeader
from app.utils.export_helpers import xl_safe

# ── 公共小工具 ─────────────────────────────────────────────────────────────

def _subject_key(subject):
    try:
        return (SUBJECT_ORDER.index(subject), subject)
    except ValueError:
        return (len(SUBJECT_ORDER), subject or '')


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
