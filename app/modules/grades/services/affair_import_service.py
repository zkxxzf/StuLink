# StuLink v1.19.2 2026-10-11
# 考务导入的公共落库逻辑（考生安排 + 考场位置）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""考务导入公共逻辑（v1.19.47.23 起只剩一处入口）

  · 成绩页  /exams/<id>/ai-import   （v1.19.2：同页能导入考务表，写到该考试的考务批次）
  · 考务页  /affairs/<aid>/ai-import 已于 v1.19.47.23 下线（AI 导入统一走每场考试的入口）
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


def _seat_int(v):
    """座号 → int（非数字返回 None）"""
    s = str(v or '').strip()
    return int(s) if s.isdigit() else None


def _bump_seat(seat_max, row):
    """记录某考场出现过的最大座号（用于推断考场容量）"""
    rn = norm_room(row.get('room'))
    st = _seat_int(row.get('seat'))
    if rn and st and st > seat_max.get(rn, 0):
        seat_max[rn] = st


def parse(payload):
    """按工作表用途解析 → (students, rooms, errors)

    payload: [{_sheet, mapping, kind}]，kind ∈ {'students','rooms','seats','skip'}
    students: [{no,name,class_name,selection,subject,attend,fixed_room,fixed_seat,
                custom_number,room,seat,exam_no,...}]
    rooms:    {考场号: {location, subject, capacity, prefix, note}}

    考场容量：优先用「考场信息表」里显式给出的容量；否则按座位表/名单里出现的
    **最大座号**推断（座位表如桌签会把每间考场的座位排满，能反映真实容量）。
    """
    students, rooms, errors = {}, {}, []
    seat_max = {}                                     # 考场号 → 出现过的最大座号
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
                if not rn:
                    continue
                info = rooms.setdefault(rn, {})
                loc = r.get('room_loc') or r.get('name') or ''
                if loc:
                    info['location'] = loc
                if r.get('room_subject'):
                    info['subject'] = r['room_subject']
                if r.get('capacity'):
                    info['capacity'] = int(r['capacity'])
                if r.get('room_prefix'):
                    info['prefix'] = r['room_prefix']
                if r.get('note'):
                    info['note'] = r['note']
            continue
        if kind == 'seats':                           # 座位表/桌签：只取座号 → 容量
            for r in rows:
                _bump_seat(seat_max, r)
            continue
        for r in rows:                                # students
            _bump_seat(seat_max, r)
            key = (r.get('no') or r.get('exam_no')
                   or (r.get('name') or '') + '|' + (r.get('class_name') or ''))
            students[key] = r
    # 没有显式容量时，用最大座号补上（房间来自名单/座位表）
    for rn, mx in seat_max.items():
        info = rooms.setdefault(rn, {})
        if not info.get('capacity'):
            info['capacity'] = mx
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


def apply_arrangement(affair, rows, rooms, replace=True):
    """写库：按考场/考场信息表生成 AffairRoom、名单 upsert 到 AffairStudent。

    rooms: {考场号: {location, subject, capacity, prefix, note}}
    返回 (added, updated, removed, n_rooms)。调用方负责 commit。
    """
    exist_rooms = {r.room_no: r for r in AffairRoom.query.filter_by(affair_id=affair.id).all()}
    room_nos = {norm_room(r['room']) for r in rows if r.get('room')}
    for rn in sorted(room_nos | set(rooms or {})):    # 名单里的考场 + 考场表里的考场
        row = exist_rooms.get(rn)
        if row is None:
            row = AffairRoom(affair_id=affair.id, room_no=rn, capacity=30, subject='默认')
            db.session.add(row)
            exist_rooms[rn] = row
        spec = (rooms or {}).get(rn) or {}
        if spec.get('location'):
            row.location = spec['location']
        if spec.get('subject'):
            row.subject = spec['subject']
        if spec.get('capacity'):
            row.capacity = spec['capacity']
        if spec.get('prefix'):
            row.prefix = spec['prefix']
        if spec.get('note'):
            row.note = spec['note']

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
        st.subject = r.get('subject') or r.get('selection') or st.subject or '默认'
        st.is_attend = (r.get('attend') is not False)
        st.exam_number = r.get('exam_no') or st.exam_number
        st.room_no = norm_room(r.get('room')) or None
        try:
            st.seat_no = int(str(r.get('seat')).strip()) if str(r.get('seat') or '').strip() else None
        except ValueError:
            st.seat_no = None
        if r.get('fixed_room'):                       # 编排输入：固定考场/座号、自定义考号
            st.fixed_room = norm_room(r['fixed_room'])
        if str(r.get('fixed_seat') or '').strip():
            try:
                st.fixed_seat = int(str(r['fixed_seat']).strip())
            except ValueError:
                pass
        if r.get('custom_number'):
            st.custom_number = r['custom_number']
    removed = 0
    if replace:
        for no, st in list(exist.items()):
            if no not in keep:
                db.session.delete(st)
                removed += 1
    if room_nos:
        affair.status = 'arranged'
    return added, updated, removed, len(room_nos)
