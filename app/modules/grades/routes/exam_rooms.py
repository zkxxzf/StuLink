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
    gorder = {}     # 2026-10-14：组内房间顺序（lib_id 列表）—— 页面上可直接上移 / 下移，
    #                 考务步骤②「一键带入」按这个顺序生成 01、02… 考场编号
    for m in ExamRoomGroupMap.query.order_by(ExamRoomGroupMap.sort_order,
                                             ExamRoomGroupMap.id).all():
        gmap.setdefault(m.lib_id, []).append(m.group_id)
        gorder.setdefault(m.group_id, []).append(m.lib_id)
    rooms = AffairRoomLib.query.order_by(AffairRoomLib.location).all()
    # v1.19.47.31：房间带出「容量（人）」——分组面板里维护，考务编排「一键带入」时作为
    # 各考场的默认容量（每场考务的容量仍各自存在 affair_rooms.capacity，可再调整）
    room_rows = [{'id': r.id, 'location': r.location, 'capacity': r.capacity or 30,
                  'note': r.note or '', 'group_ids': sorted(gmap.get(r.id, []))}
                 for r in rooms]
    # 分组卡片：每组带出成员房间（避免在模板里做“包含”判断）+ 组内顺序（lib_ids）
    group_rows = [{'id': g.id, 'name': g.name, 'lib_ids': gorder.get(g.id, []),
                   'rooms': [r for r in room_rows if g.id in r['group_ids']]}
                  for g in groups]
    return render_template('grades/rooms.html', groups=group_rows, rooms=room_rows,
                           group_ids=[g['id'] for g in group_rows],
                           group_names=[g['name'] for g in group_rows],
                           group_lib_ids=[g['lib_ids'] for g in group_rows])


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
    """v1.19.58.64 房间库批量导入模板（只需「位置」列，「备注」可选）"""
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
    """v1.19.58.64 批量导入学校房间（写入房间库 affair_room_libs）。

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


@bp.route('/rooms/<int:lid>/capacity', methods=['POST'])
@login_required
@perm_required('grades.edit')
def exam_room_capacity(lid):
    """v1.19.47.31 设置房间的「容量（人）」——考务编排带入时的默认容量。

    用户口径（2026-10-14）：容量在「考场维护」的分组面板里维护（已选入分组的房间
    才有），考务步骤②选模板「一键带入」时直接带出。**只改房间库默认值**：每场考务的
    容量仍各自存在 `affair_rooms.capacity`，已生成的考场不受影响。
    """
    row = db.session.get(AffairRoomLib, lid)
    if not row:
        return jsonify({'ok': False, 'msg': '房间不存在'})
    try:
        cap = int(str(request.form.get('capacity') or '').strip())
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'msg': '容量必须是整数'})
    if cap < 1 or cap > 500:
        return jsonify({'ok': False, 'msg': '容量需在 1~500 之间'})
    row.capacity = cap
    db.session.commit()
    log_operation(current_user, '设置', '考场房间', lid,
                  f'{row.location} 容量={cap}', module='grades')
    return jsonify({'ok': True, 'capacity': cap})


@bp.route('/rooms/capacity-batch', methods=['POST'])
@login_required
@perm_required('grades.edit')
def exam_room_capacity_batch():
    """2026-10-14 用户要求：**一键批量设置容量** —— 把一个分组（或勾选的几个房间）
    的容量统一设成同一个值（现实中一个年级的考场容量基本一致，逐个填太慢）。

    body：`lib_ids`（逗号分隔，前端按「整组」或「已勾选」算好）+ `capacity`。
    与单条设置同口径：**只改房间库默认值**（考务步骤②「一键带入」时的默认容量），
    每场考务的容量仍各自存在 `affair_rooms.capacity`，已生成的考场不受影响。
    """
    ids = []
    for tok in (request.form.get('lib_ids') or '').split(','):
        v = _int(tok)
        if v is not None:
            ids.append(v)
    ids = list(dict.fromkeys(ids))[:500]        # 去重 + 上限，防一次塞几万行
    if not ids:
        return jsonify({'ok': False, 'msg': '请先选择要设置容量的房间'})
    try:
        cap = int(str(request.form.get('capacity') or '').strip())
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'msg': '容量必须是整数'})
    if cap < 1 or cap > 500:
        return jsonify({'ok': False, 'msg': '容量需在 1~500 之间'})
    rows = AffairRoomLib.query.filter(AffairRoomLib.id.in_(ids)).all()
    if not rows:
        return jsonify({'ok': False, 'msg': '房间不存在'})
    changed = 0
    for r in rows:
        if (r.capacity or 30) != cap:
            r.capacity = cap
            changed += 1
    db.session.commit()
    log_operation(current_user, '批量设置', '考场房间', 0,
                  f'{len(rows)} 个房间容量={cap}', module='grades')
    return jsonify({'ok': True, 'count': len(rows), 'changed': changed, 'capacity': cap})


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
    # 2026-10-14：顺序（sort_order）要保住 —— 原来「整条删了重插」会把组内顺序重置成
    # 「最后加入的排最后」，用户在分组里调好的顺序会被一次改归属抹掉，也带不进考务批次。
    # 口径：已有的组保留原 sort_order；本次新加入的组追加到该组末尾（max + 10）。
    old = {m.group_id: (m.sort_order or 0)
           for m in ExamRoomGroupMap.query.filter_by(lib_id=lid).all()}
    ExamRoomGroupMap.query.filter_by(lib_id=lid).delete()
    for gid in sorted(valid):
        so = old.get(gid)
        if so is None:
            mx = (db.session.query(db.func.max(ExamRoomGroupMap.sort_order))
                  .filter(ExamRoomGroupMap.group_id == gid).scalar() or 0)
            so = mx + 10
        db.session.add(ExamRoomGroupMap(group_id=gid, lib_id=lid, sort_order=so))
    db.session.commit()
    return jsonify({'ok': True})


@bp.route('/rooms/groups/<int:gid>/reorder', methods=['POST'])
@login_required
@perm_required('grades.edit')
def exam_room_group_reorder(gid):
    """分组内房间「上移 / 下移」（2026-10-14 用户口径：顺序要能调，而且要能带着复用）。

    参数：`lib_id` + `dir`（-1 上移 / 1 下移）。
    组内按下标互换相邻两行，交换后**整组重排**为 10、20、30…（避免历史 0 值并列导致
    顺序不稳），落库在 `exam_room_group_map.sort_order`；
    考务步骤②「一键带入」按这个顺序生成 01、02… 编号（`affair_rooms_from_lib_batch`）。
    返回 `{ok, order:[lib_id…]}`，前端照它重渲染右侧列表。
    """
    group = db.session.get(ExamRoomGroup, gid)
    if not group:
        return jsonify({'ok': False, 'msg': '分组不存在'})
    lid = _int(request.form.get('lib_id'))
    dirn = _int(request.form.get('dir')) or 0
    rows = (ExamRoomGroupMap.query.filter_by(group_id=gid)
            .order_by(ExamRoomGroupMap.sort_order, ExamRoomGroupMap.id).all())
    idx = [i for i, m in enumerate(rows) if m.lib_id == lid]
    if not idx:
        return jsonify({'ok': False, 'msg': '该房间不在这个分组里'})
    i = idx[0]
    j = i + (1 if dirn >= 0 else -1)
    if j < 0 or j >= len(rows):
        return jsonify({'ok': False, 'msg': '已经在最前 / 最后了'})
    rows[i], rows[j] = rows[j], rows[i]
    for pos, m in enumerate(rows, start=1):
        m.sort_order = pos * 10
    db.session.commit()
    order = [m.lib_id for m in
             ExamRoomGroupMap.query.filter_by(group_id=gid)
             .order_by(ExamRoomGroupMap.sort_order, ExamRoomGroupMap.id).all()]
    log_operation(current_user, '排序', '考场分组', gid,
                  f'「{group.name}」房间顺序：{lid} {"上移" if dirn < 0 else "下移"}'
                  f'（第 {i + 1} → {j + 1} 位）', module='grades')
    return jsonify({'ok': True, 'order': order})


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
