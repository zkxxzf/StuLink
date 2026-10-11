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
from flask import render_template, request, jsonify
from flask_login import login_required, current_user

from app.extensions import db
from app.models.grades import AffairRoomLib, ExamRoomGroup, ExamRoomGroupMap
from app.modules.grades import bp
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation
from app.utils.text_guard import sanitize_label

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
