# StuLink 2026-10-18
# 回收站：查看 / 恢复 / 彻底清除被删除的记录
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""删除记录回收站（2026-10-18 审计新增）。

解决"删了就只剩一条日志、内容找不回"的问题：
- 列表：按模块/类型/关键词/状态筛选，显示摘要、条数、操作人、过期时间；
- 详情：展开看被删内容的 JSON 预览（前 50 行）；
- 恢复：按行重建（新 id；唯一约束冲突的行跳过）；
- 彻底删除 / 清理过期：仅管理员，且都写操作日志。
"""
import json
import os
from datetime import datetime

from flask import (Blueprint, render_template, request, jsonify)
from flask_login import login_required, current_user

from config import BASE_DIR
from app.extensions import db
from app.models.trash import TrashRecord, TRASH_RETENTION_DAYS
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation
from app.utils.trash import purge_expired_records as purge_expired_impl
from app.utils.trash import restore as restore_record

bp = Blueprint('trash', __name__, url_prefix='/trash')

PERM = 'system.users'          # 与「操作日志」同权限：管理员/受授权组
PER_PAGE = 50


@bp.route('/')
@login_required
@perm_required(PERM)
def list_trash():
    q = TrashRecord.query
    module = (request.args.get('module') or '').strip()
    kind = (request.args.get('kind') or '').strip()
    kw = (request.args.get('kw') or '').strip()
    show = (request.args.get('show') or 'active').strip()      # active/restored/all
    if module:
        q = q.filter(TrashRecord.module == module)
    if kind:
        q = q.filter(TrashRecord.kind == kind)
    if kw:
        q = q.filter(db.or_(TrashRecord.summary.contains(kw),
                            TrashRecord.operator_name.contains(kw),
                            TrashRecord.kind_label.contains(kw)))
    if show == 'active':
        q = q.filter(TrashRecord.restored_at.is_(None))
    elif show == 'restored':
        q = q.filter(TrashRecord.restored_at.isnot(None))
    try:
        page = max(1, int(request.args.get('page') or 1))
    except (TypeError, ValueError):
        page = 1
    total = q.count()
    pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    page = min(page, pages)
    rows = (q.order_by(TrashRecord.id.desc())
            .offset((page - 1) * PER_PAGE).limit(PER_PAGE).all())
    modules = sorted({m[0] for m in db.session.query(TrashRecord.module).distinct()
                      if m[0]})
    kinds = sorted({(k[0], k[1] or k[0])
                    for k in db.session.query(TrashRecord.kind,
                                              TrashRecord.kind_label).distinct()
                    if k[0]})
    expired = (TrashRecord.query
               .filter(TrashRecord.restored_at.is_(None),
                       TrashRecord.expires_at < datetime.now()).count())
    return render_template('system/trash.html', rows=rows, total=total, page=page,
                           pages=pages, modules=modules, kinds=kinds,
                           module=module, kind=kind, kw=kw, show=show,
                           expired=expired, retention=TRASH_RETENTION_DAYS,
                           now=datetime.now())


@bp.route('/<int:rid>')
@login_required
@perm_required(PERM)
def detail(rid):
    """返回被删内容的 JSON 预览（页面内展开用）。"""
    rec = TrashRecord.query.get_or_404(rid)
    try:
        data = json.loads(rec.payload or 'null')
    except (TypeError, ValueError):
        data = None
    return jsonify({'ok': True, 'record': {
        'id': rec.id, 'module': rec.module, 'kind': rec.kind,
        'kind_label': rec.kind_label, 'ref_id': rec.ref_id,
        'summary': rec.summary, 'item_count': rec.item_count,
        'operator': rec.operator_name,
        'created_at': rec.created_at.strftime('%Y-%m-%d %H:%M:%S')
                      if rec.created_at else '',
        'expires_at': rec.expires_at.strftime('%Y-%m-%d') if rec.expires_at else '',
        'restored_at': rec.restored_at.strftime('%Y-%m-%d %H:%M')
                       if rec.restored_at else '',
    }, 'rows': (data if isinstance(data, list) else ([data] if data else []))[:50]})


@bp.route('/<int:rid>/restore', methods=['POST'])
@login_required
@perm_required(PERM)
def restore_one(rid):
    rec = TrashRecord.query.get_or_404(rid)
    if rec.restored_at is not None:
        return jsonify({'ok': False, 'message': '这条已经恢复过，无需重复恢复'}), 400
    ok, msg, n = restore_record(rec)
    log_operation(current_user, '恢复' if ok else '恢复失败', '回收站', rid,
                  f'{rec.kind_label or rec.kind}：{msg}', module='system')
    return jsonify({'ok': ok, 'message': msg, 'restored': n}), (200 if ok else 400)


@bp.route('/<int:rid>/purge', methods=['POST'])
@login_required
@perm_required(PERM)
def purge_one(rid):
    """彻底删除一条归档（不可恢复）。"""
    rec = TrashRecord.query.get_or_404(rid)
    label = f'{rec.kind_label or rec.kind}：{rec.summary or ""}'
    db.session.delete(rec)
    db.session.commit()
    log_operation(current_user, '彻底删除', '回收站', rid, label, module='system')
    return jsonify({'ok': True, 'message': '已彻底删除'})


@bp.route('/purge-expired', methods=['POST'])
@login_required
@perm_required(PERM)
def purge_expired():
    """清理已过期（默认 30 天）且未恢复的归档；删除前自动导出元数据。"""
    result = purge_expired_impl(
        dry_run=False,
        export_dir=os.path.join(BASE_DIR, 'data', 'backups'),
        include_restored=False)
    if result.get('error'):
        return jsonify({'ok': False, 'message': result['error']}), 500
    n = result.get('deleted', 0)
    log_operation(current_user, '清理', '回收站', None,
                  f'过期归档 {n} 条；导出 {result.get("export") or "无"}',
                  module='system')
    return jsonify({'ok': True, 'deleted': n, 'export': result.get('export'),
                    'message': f'已清理 {n} 条过期归档（删除前已导出元数据）'})
