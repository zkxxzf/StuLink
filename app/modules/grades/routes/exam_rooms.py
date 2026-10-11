# StuLink v1.19.0 2026-10-10
# 考场维护（基础配置）：考场房间库的分组归集与房间增删改
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""考场维护

数据来源：`affair_room_libs`（考场房间库，跨批次共享的房间）。
- 分组：`exam_room_groups`（默认 高一考场 / 高二考场 / 高三考场）
- 关联：`exam_room_group_map`（房间 ↔ 分组，多对多——不同年级考试错开，可共用房间）

注意：分组只影响本页归集与后续建场筛选，**不改动任何历史场次的考场**
（`affair_rooms` 从属于各自考务批次，彼此独立）。
"""
from flask import render_template, request, jsonify, send_file
from flask_login import login_required, current_user

import io

import openpyxl
from openpyxl.styles import Font

from app.extensions import db
from app.models.grades import AffairRoomLib, ExamRoomGroup, ExamRoomGroupMap
from app.modules.grades import bp
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation
from app.utils.text_guard import sanitize_label
from app.utils.upload_guard import validate_upload

DEFAULT_GROUPS = ('高一考场', '高二考场', '高三考场')


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _ensure_default_groups():
    """首次进入建 3 个默认分组（已存在任何分组则不动，不覆盖用户改动）"""
    if ExamRoomGroup.query.count() == 0:
        for i, name in enumerate(DEFAULT_GROUPS):
            db.session.add(ExamRoomGroup(name=name, sort_order=i))
        db.session.commit()


@bp.route('/rooms')
@login_required
@perm_required('grades.edit')
def exam_rooms():
    """考场维护页面：分组卡片 + 全部房间列表"""
    _ensure_default_groups()
    groups = ExamRoomGroup.query.order_by(ExamRoomGroup.sort_order,
                                          ExamRoomGroup.id).all()
    gmap = {}
    for r in ExamRoomGroupMap.query.all():
        gmap.setdefault(r.lib_id, []).append(r.group_id)
    rooms = AffairRoomLib.query.order_by(AffairRoomLib.location).all()
    room_rows = [{'id': r.id, 'location': r.location,
                  'note': r.note or '', 'group_ids': sorted(gmap.get(r.id, []))}
                 for r in rooms]
    # 分组卡片：每组带出成员房间（避免在模板里做“包含”判断）
    group_rows = [{'id': g.id, 'name': g.name,
                   'rooms': [r for r in room_rows if g.id in r['group_ids']]}
                  for g in groups]
    return render_template('grades/rooms.html', groups=group_rows, rooms=room_rows,
                           group_ids=[g['id'] for g in group_rows],
                           group_names=[g['name'] for g in group_rows])


@bp.route('/rooms/save', methods=['POST'])
@login_required
@perm_required('grades.edit')
def exam_room_save():
    """新增/编辑房间（位置 / 备注）。

    容量**不在此设置**：同一房间在不同考试（乃至不同年级）容量可能不同，
    统一在各场考试的「考务安排」里设置（新建考务时默认沿用该年级上一次的安排）。
    """
    lid = _int(request.form.get('lib_id'))
    location = sanitize_label(request.form.get('location'), 100)
    if not location:
        return jsonify({'ok': False, 'msg': '房间位置必填'})
    note = sanitize_label(request.form.get('note'), 100)
    if lid:
        row = db.session.get(AffairRoomLib, lid)
        if not row:
            return jsonify({'ok': False, 'msg': '房间不存在'})
        dup = AffairRoomLib.query.filter(AffairRoomLib.location == location,
                                         AffairRoomLib.id != lid).first()
        if dup:
            return jsonify({'ok': False, 'msg': f'房间「{location}」已存在'})
    else:
        if AffairRoomLib.query.filter_by(location=location).first():
            return jsonify({'ok': False, 'msg': f'房间「{location}」已存在'})
        row = AffairRoomLib(location=location)
        db.session.add(row)
    row.location = location
    row.note = note
    db.session.commit()
    log_operation(current_user, '保存', '考场房间', row.id, location, module='grades')
    return jsonify({'ok': True, 'id': row.id})


@bp.route('/rooms/<int:lid>/delete', methods=['POST'])
@login_required
@perm_required('grades.edit')
def exam_room_delete(lid):
    """删除房间（同时解除其分组关联；不影响历史场次考场）"""
    row = db.session.get(AffairRoomLib, lid)
    if not row:
        return jsonify({'ok': False, 'msg': '房间不存在'})
    loc = row.location
    ExamRoomGroupMap.query.filter_by(lib_id=lid).delete()
    db.session.delete(row)
    db.session.commit()
    log_operation(current_user, '删除', '考场房间', lid, loc, module='grades')
    return jsonify({'ok': True})


@bp.route('/rooms/template')
@login_required
@perm_required('grades.edit')
def exam_rooms_template():
    """v1.19.40.2 房间库批量导入模板（只需「位置」列，「备注」可选）"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '房间'
    ws.append(['位置', '备注'])
    ws.append(['东211', '2楼·东侧'])
    for ci in (1, 2):
        ws.cell(row=1, column=ci).font = Font(bold=True)
    ws.column_dimensions['A'].width = 18
    ws.column_dimensions['B'].width = 22
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return send_file(out, as_attachment=True, download_name='房间导入模板.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@bp.route('/rooms/import', methods=['POST'])
@login_required
@perm_required('grades.edit')
def exam_rooms_import():
    """v1.19.40.2 批量导入学校房间（写入房间库 affair_room_libs）。

    表头容错：位置/房间/地点/考场 任一即可，备注/说明 可选（大小写与空格不敏感）。
    按「位置」去重：库里已有 → 仅更新备注；没有 → 新增。返回新增/更新/跳过条数。
    """
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'ok': False, 'msg': '请选择 Excel 文件'})
    ok, msg = validate_upload(f.filename, allowed_exts=['xlsx', 'xls'], stream=f.stream)
    if not ok:
        return jsonify({'ok': False, 'msg': msg})
    try:
        wb = openpyxl.load_workbook(f.stream, read_only=True, data_only=True)
    except Exception as e:
        return jsonify({'ok': False, 'msg': f'Excel 解析失败：{e}'})
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return jsonify({'ok': False, 'msg': '文件为空'})
    head = [str(c).strip() if c is not None else '' for c in rows[0]]

    def _find(names):
        for i, h in enumerate(head):
            if h and any(n in h for n in names):
                return i
        return None

    i_loc = _find(('位置', '房间', '地点', '考场'))
    i_note = _find(('备注', '说明'))
    if i_loc is None:
        return jsonify({'ok': False, 'msg': '表头需包含「位置」列（可另加「备注」列）'})

    exist = {r.location: r for r in AffairRoomLib.query.all()}
    added = updated = skipped = 0
    for r in rows[1:]:
        loc = sanitize_label(str(r[i_loc]) if i_loc < len(r) and r[i_loc] is not None else '', 100)
        if not loc:
            skipped += 1
            continue
        note = ''
        if i_note is not None and i_note < len(r) and r[i_note] is not None:
            note = sanitize_label(str(r[i_note]), 100)
        row = exist.get(loc)
        if row is None:
            row = AffairRoomLib(location=loc, note=note)
            db.session.add(row)
            exist[loc] = row
            added += 1
        elif note and note != (row.note or ''):
            row.note = note
            updated += 1
        else:
            skipped += 1          # 已存在且备注无变化
    db.session.commit()
    wb.close()
    log_operation(current_user, '导入', '考场房间', 0,
                  f'新增{added}个/更新{updated}个/跳过{skipped}个', module='grades')
    return jsonify({'ok': True, 'added': added, 'updated': updated, 'skipped': skipped})


@bp.route('/rooms/<int:lid>/groups', methods=['POST'])
@login_required
@perm_required('grades.edit')
def exam_room_set_groups(lid):
    """设置房间所属分组（多选，直接覆盖）"""
    if not db.session.get(AffairRoomLib, lid):
        return jsonify({'ok': False, 'msg': '房间不存在'})
    ids = []
    for tok in (request.form.get('group_ids') or '').split(','):
        v = _int(tok)
        if v is not None:
            ids.append(v)
    valid = set()
    if ids:
        valid = {g.id for g in
                 ExamRoomGroup.query.filter(ExamRoomGroup.id.in_(ids)).all()}
    ExamRoomGroupMap.query.filter_by(lib_id=lid).delete()
    for gid in sorted(valid):
        db.session.add(ExamRoomGroupMap(group_id=gid, lib_id=lid))
    db.session.commit()
    return jsonify({'ok': True})


@bp.route('/rooms/groups/save', methods=['POST'])
@login_required
@perm_required('grades.edit')
def exam_room_group_save():
    """新增/重命名分组"""
    gid = _int(request.form.get('group_id'))
    name = sanitize_label(request.form.get('name'), 50)
    if not name:
        return jsonify({'ok': False, 'msg': '分组名必填'})
    if gid:
        row = db.session.get(ExamRoomGroup, gid)
        if not row:
            return jsonify({'ok': False, 'msg': '分组不存在'})
        dup = ExamRoomGroup.query.filter(ExamRoomGroup.name == name,
                                         ExamRoomGroup.id != gid).first()
    else:
        dup = ExamRoomGroup.query.filter_by(name=name).first()
        row = None
    if dup:
        return jsonify({'ok': False, 'msg': f'分组「{name}」已存在'})
    if row is None:
        row = ExamRoomGroup(name=name, sort_order=ExamRoomGroup.query.count())
        db.session.add(row)
    row.name = name
    db.session.commit()
    log_operation(current_user, '保存', '考场分组', row.id, name, module='grades')
    return jsonify({'ok': True, 'id': row.id})


@bp.route('/rooms/groups/<int:gid>/delete', methods=['POST'])
@login_required
@perm_required('grades.edit')
def exam_room_group_delete(gid):
    """删除分组（解除关联；房间本身保留）"""
    row = db.session.get(ExamRoomGroup, gid)
    if not row:
        return jsonify({'ok': False, 'msg': '分组不存在'})
    name = row.name
    ExamRoomGroupMap.query.filter_by(group_id=gid).delete()
    db.session.delete(row)
    db.session.commit()
    log_operation(current_user, '删除', '考场分组', gid, name, module='grades')
    return jsonify({'ok': True})
