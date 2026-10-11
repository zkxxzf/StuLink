# StuLink v1.19.2 2026-10-11
# 考务导入的公共落库逻辑（考生安排 + 考场位置）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""考务导入公共逻辑（被两处复用）

  · 考务页  /affairs/<aid>/ai-import （P2：考生安排 / 考场位置）
  · 成绩页  /exams/<id>/ai-import   （v1.19.2：同页也能导入考务表，写到该考试的考务批次）
"""
from app.extensions import db
from app.models import Student
from app.models.grades import AffairRoom, AffairStudent
from app.modules.grades.services import ai_import_service as aisvc
from app.modules.grades.utils import normalize_class_name


def norm_room(no):
    """考场号统一两位（01、02…）"""
    s = str(no or '').strip()
    return s.zfill(2) if s.isdigit() else s


def parse(payload):
    """按工作表用途解析 → (students, room_locs, errors)

    payload: [{_sheet, mapping, kind}]，kind ∈ {'students','rooms','skip'}
    students: [{no,name,class_name,exam_no,room,seat,selection,attend,...}]
    room_locs: {考场号: 教室/位置}
    """
    students, rooms, errors = {}, {}, []
    for item in payload or []:
        kind = item.get('kind') or 'skip'
        sh = item.get('_sheet')
        if sh is None or kind == 'skip':
            continue
        rows, errs = aisvc.rows_from_affair_mapping(sh, item.get('mapping') or {})
        errors.extend(errs)
        if kind == 'rooms':
            for r in rows:
                rn = norm_room(r.get('room') or r.get('no'))
                loc = r.get('room_loc') or r.get('name') or ''
                if rn and loc:
                    rooms[rn] = loc
        else:                                       # students
            for r in rows:
                key = (r.get('no') or r.get('exam_no')
                       or (r.get('name') or '') + '|' + (r.get('class_name') or ''))
                students[key] = r
    return list(students.values()), rooms, errors


def resolve_student_no(affair, rows):
    """把只有考号/姓名的行解析出学号：① 考号 → 学号；② 姓名 + 班级 → 学号"""
    if not any(not r.get('no') for r in rows):
        return rows, []
    no_map, name_map = {}, {}
    for s in AffairStudent.query.filter_by(affair_id=affair.id).all():
        if s.exam_number:
            no_map.setdefault(s.exam_number, s.student_no)
        if s.name and s.class_name:
            name_map.setdefault((s.name, normalize_class_name(s.class_name)), s.student_no)
    for st in Student.query.filter_by(grade=affair.grade).all():
        if st.name and st.class_name:
            name_map.setdefault((st.name, normalize_class_name(st.class_name)), st.student_number)
    errs, miss = [], 0
    for r in rows:
        if r.get('no'):
            continue
        cand = no_map.get(r.get('exam_no')) if r.get('exam_no') else None
        if not cand:
            nm = r.get('name') or ''
            cls = normalize_class_name(r.get('class_name') or '')
            cand = name_map.get((nm, cls)) if (nm and cls) else None
        if cand:
            r['no'] = cand
        else:
            miss += 1
            errs.append({'line': r.get('line', 0),
                         'no': r.get('exam_no') or r.get('name') or '',
                         'reason': '未匹配到学号（考号/姓名+班级）'})
    if miss:
        rows[:] = [r for r in rows if r.get('no')]
    return rows, errs


def apply_arrangement(affair, rows, room_locs, replace=True):
    """写库：按考场生成 AffairRoom、名单 upsert 到 AffairStudent。

    返回 (added, updated, removed, n_rooms)。调用方负责 commit。
    """
    exist_rooms = {r.room_no: r for r in AffairRoom.query.filter_by(affair_id=affair.id).all()}
    room_nos = {norm_room(r['room']) for r in rows if r.get('room')}
    for rn in sorted(room_nos):
        row = exist_rooms.get(rn)
        if row is None:
            row = AffairRoom(affair_id=affair.id, room_no=rn, capacity=30, subject='默认')
            db.session.add(row)
            exist_rooms[rn] = row
        if room_locs.get(rn):
            row.location = room_locs[rn]
    for rn, loc in (room_locs or {}).items():        # 位置表里、没人坐的考场也建出来
        if rn not in exist_rooms:
            db.session.add(AffairRoom(affair_id=affair.id, room_no=rn,
                                      location=loc, capacity=30, subject='默认'))

    exist = {s.student_no: s for s in AffairStudent.query.filter_by(affair_id=affair.id).all()}
    added = updated = 0
    keep = set()
    for r in rows:
        no = r['no']
        keep.add(no)
        st = exist.get(no)
        if st is None:
            st = AffairStudent(affair_id=affair.id, student_no=no)
            db.session.add(st)
            exist[no] = st
            added += 1
        else:
            updated += 1
        st.name = r.get('name') or st.name
        st.class_name = normalize_class_name(r.get('class_name') or '') or st.class_name
        st.subject_selection = r.get('selection') or st.subject_selection or ''
        st.subject = r.get('selection') or '默认'
        st.is_attend = (r.get('attend') is not False)
        st.exam_number = r.get('exam_no') or st.exam_number
        st.room_no = norm_room(r.get('room')) or None
        try:
            st.seat_no = int(str(r.get('seat')).strip()) if str(r.get('seat') or '').strip() else None
        except ValueError:
            st.seat_no = None
    removed = 0
    if replace:
        for no, st in list(exist.items()):
            if no not in keep:
                db.session.delete(st)
                removed += 1
    if room_nos:
        affair.status = 'arranged'
    return added, updated, removed, len(room_nos)
