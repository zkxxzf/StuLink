# StuLink v1.19.50.55 2026-10-13
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""德育奖惩 —— 列表 / 登记 / 编辑 / 状态流转 / 删除 / 附件

权限：morality.view 查看；morality.edit 登记·编辑·删除·状态流转·附件。
"""
from datetime import datetime

from flask import (abort, jsonify, render_template, request, send_from_directory)
from flask_login import login_required, current_user

from app.extensions import db
from app.models.morality import (RewardPunishment, RewardPunishmentAttachment,
                                 KIND_PUNISH, KINDS, STATUSES, DOC_TYPES,
                                 REWARD_CATEGORIES, PUNISH_CATEGORIES,
                                 REWARD_LEVELS, PUNISH_LEVELS,
                                 STATUS_CONFIRMED, STATUS_DRAFT, STATUS_REVOKED)
from app.modules.morality import bp
from app.modules.morality.services import reward_service as svc
from app.utils.decorators import perm_required
from app.utils.text_guard import safe_download_name


def _filters():
    return {
        'kind': (request.args.get('kind') or '').strip(),
        'category': (request.args.get('category') or '').strip(),
        'level': (request.args.get('level') or '').strip(),
        'status': (request.args.get('status') or '').strip(),
        'grade': (request.args.get('grade') or '').strip(),
        'class_name': (request.args.get('class_name') or '').strip(),
        'kw': (request.args.get('kw') or '').strip()[:30],
        'date_from': (request.args.get('date_from') or '').strip(),
        'date_to': (request.args.get('date_to') or '').strip(),
    }


def _or_404(rid):
    rec = db.session.get(RewardPunishment, rid)
    if not rec:
        abort(404)
    if not svc.can_see(current_user, rec):
        abort(403)
    return rec


# ==================== 列表页 ====================

@bp.route('/rewards')
@login_required
@perm_required('morality.view')
def rewards_page():
    """奖惩信息：筛选 + 分页 + 概览"""
    f = _filters()
    page = request.args.get('page', 1, type=int)
    pagination = svc.list_records(f, current_user, page=page, per_page=30)
    items = pagination.items
    ids = [r.id for r in items]
    atts = {}
    if ids:
        for a in RewardPunishmentAttachment.query.filter(
                RewardPunishmentAttachment.record_id.in_(ids)).all():
            atts.setdefault(a.record_id, []).append(a)
    # 年级/班级下拉：取可见范围内已有记录的班级（跨库不 join，就地聚合）
    # 2026-10-13 审计修复：原来写 `visible_grades(...) or <全表去重>`，当用户无任何
    # 可见年级时（visible_grades 返回空列表）会回退列出全校年级，等于把无权查看的
    # 年级名泄露到下拉里。空列表时保持为空，不再回退。
    from app.modules.grades.services.scope import visible_grades
    _vg = visible_grades(current_user)
    grades = sorted(_vg) if _vg else []

    return render_template('morality/rewards.html',
                           items=items, pagination=pagination, f=f,
                           atts=atts, grades=grades,
                           kinds=KINDS, statuses=STATUSES,
                           reward_categories=REWARD_CATEGORIES,
                           punish_categories=PUNISH_CATEGORIES,
                           reward_levels=REWARD_LEVELS,
                           punish_levels=PUNISH_LEVELS,
                           doc_types=DOC_TYPES,
                           stats=svc.stats(f, current_user),
                           can_edit=current_user.has_perm('morality.edit'))


@bp.route('/api/students')
@login_required
@perm_required('morality.view')
def api_students():
    """按学号/姓名查学生（限可见范围），用于登记时选择学生"""
    kw = (request.args.get('q') or '').strip()
    return jsonify({'success': True,
                    'students': svc.search_students(kw, current_user)})


# ==================== 登记 / 编辑 / 删除 ====================

@bp.route('/rewards/add', methods=['POST'])
@login_required
@perm_required('morality.edit')
def rewards_add():
    files = [x for x in (request.files.getlist('files')
                         or request.files.getlist('file'))
             if x and (x.filename or '').strip()]
    ok, msg, rec = svc.create(request.form, current_user, files=files)
    if not ok:
        return jsonify({'success': False, 'message': msg}), 400
    _log('登记', rec, f'{rec.student_name} {rec.title}')
    return jsonify({'success': True, 'message': msg, 'id': rec.id,
                    'record': rec.to_dict()})


@bp.route('/rewards/<int:rid>/edit', methods=['POST'])
@login_required
@perm_required('morality.edit')
def rewards_edit(rid):
    rec = _or_404(rid)
    ok, msg = svc.update(rec, request.form, current_user)
    if not ok:
        return jsonify({'success': False, 'message': msg}), 400
    _log('修改', rec, f'{rec.student_name} {rec.title}')
    return jsonify({'success': True, 'message': msg,
                    'record': rec.to_dict(
                        attachments=_atts_of(rec.id))})


@bp.route('/rewards/<int:rid>/delete', methods=['POST'])
@login_required
@perm_required('morality.edit')
def rewards_delete(rid):
    rec = _or_404(rid)
    # 2026-10-20 第十七轮：归档失败时不得删库，也不得写"删除"审计
    if not svc.delete(rec):
        return jsonify({'success': False,
                        'message': '删除前归档失败，已中止（避免数据不可恢复）'}), 500
    _log('删除', rec, f'{rec.student_name} {rec.title}')
    return jsonify({'success': True, 'message': '已删除'})


@bp.route('/rewards/<int:rid>/status', methods=['POST'])
@login_required
@perm_required('morality.edit')
def rewards_status(rid):
    """状态流转：待确认 → 已生效 / 撤销"""
    rec = _or_404(rid)
    status = (request.form.get('status') or '').strip()
    reason = (request.form.get('reason') or '').strip()
    ok, msg = svc.set_status(rec, status, current_user, reason=reason)
    if not ok:
        return jsonify({'success': False, 'message': msg}), 400
    _log('状态' if status != STATUS_REVOKED else '撤销', rec,
         f'{rec.student_name} {rec.title} → {rec.status_text()}')
    return jsonify({'success': True, 'message': msg,
                    'record': rec.to_dict(attachments=_atts_of(rec.id))})


@bp.route('/rewards/<int:rid>')
@login_required
@perm_required('morality.view')
def rewards_detail(rid):
    rec = _or_404(rid)
    return jsonify({'success': True,
                    'record': rec.to_dict(attachments=_atts_of(rec.id)),
                    'can_edit': current_user.has_perm('morality.edit')})


# ==================== 附件 ====================

@bp.route('/rewards/<int:rid>/attachment', methods=['POST'])
@login_required
@perm_required('morality.edit')
def attachment_upload(rid):
    rec = _or_404(rid)
    files = [x for x in (request.files.getlist('files')
                         or request.files.getlist('file'))
             if x and (x.filename or '').strip()]
    if not files:
        return jsonify({'success': False, 'message': '没有选择文件'}), 400
    uploaded, errors = 0, []
    doc_type = (request.form.get('doc_type') or '').strip()
    for f in files:
        ok, msg, _att = svc.save_attachment(rec, f, current_user, doc_type=doc_type)
        if ok:
            uploaded += 1
        else:
            errors.append(f'{f.filename}：{msg}')
    if uploaded:
        _log('上传附件', rec, f'{uploaded} 个附件')
    return jsonify({'success': uploaded > 0, 'uploaded': uploaded,
                    'errors': errors,
                    'message': f'成功上传 {uploaded} 个附件' if uploaded
                    else errors[0],
                    'attachments': [a.to_dict() for a in _atts_of(rec.id)]})


@bp.route('/rewards/attachment/<int:fid>/delete', methods=['POST'])
@login_required
@perm_required('morality.edit')
def attachment_delete(fid):
    att = db.session.get(RewardPunishmentAttachment, fid)
    if not att:
        abort(404)
    rec = db.session.get(RewardPunishment, att.record_id)
    if not rec or not svc.can_see(current_user, rec):
        abort(403)
    _log('删除附件', rec, att.file_name or '')
    svc.delete_attachment(att)
    return jsonify({'success': True, 'message': '已删除',
                    'attachments': [a.to_dict() for a in _atts_of(rec.id)]})


@bp.route('/rewards/attachment/<int:fid>')
@login_required
@perm_required('morality.view')     # 2026-10-13 审计修复：附件同属模块可见范围，
                                    # 原缺此权限点 → 仅知附件 id 即可越权下载
def rp_file_view(fid):
    """附件预览：图片/PDF 内联打开，其它类型强制下载"""
    att = db.session.get(RewardPunishmentAttachment, fid)
    if not att:
        abort(404)
    rec = db.session.get(RewardPunishment, att.record_id)
    if not rec or not svc.can_see(current_user, rec):
        abort(403)
    root, stored = svc.attachment_path(att)
    if not root:
        abort(404)
    if not svc.is_previewable(att.ext):
        return send_from_directory(root, stored, as_attachment=True,
                                   download_name=safe_download_name(att.file_name or stored))
    return send_from_directory(root, stored, as_attachment=False,
                               download_name=safe_download_name(att.file_name or stored),
                               mimetype=att.mime or 'application/octet-stream')


@bp.route('/rewards/attachment/<int:fid>/download')
@login_required
@perm_required('morality.view')     # 2026-10-13 审计修复：同 rp_file_view
def rp_file_download(fid):
    att = db.session.get(RewardPunishmentAttachment, fid)
    if not att:
        abort(404)
    rec = db.session.get(RewardPunishment, att.record_id)
    if not rec or not svc.can_see(current_user, rec):
        abort(403)
    root, stored = svc.attachment_path(att)
    if not root:
        abort(404)
    return send_from_directory(root, stored, as_attachment=True,
                               download_name=safe_download_name(att.file_name or stored))


# ==================== 内部工具 ====================

def _atts_of(rid):
    return (RewardPunishmentAttachment.query.filter_by(record_id=rid)
            .order_by(RewardPunishmentAttachment.id).all())


def _log(action, rec, detail):
    try:
        from app.utils.helpers import log_operation
        log_operation(current_user, action, '德育奖惩', rec.id, detail,
                      module='morality')
    except Exception:                                # noqa: BLE001
        # 2026-10-19 第十三轮审计：原来静默 pass，审计日志丢失无从发现
        import logging
        logging.getLogger('morality.records').warning(
            '德育操作日志写入失败 action=%s rid=%s', action, getattr(rec, 'id', None),
            exc_info=True)
