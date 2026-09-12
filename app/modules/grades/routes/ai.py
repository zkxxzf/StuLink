# StuLink v1.9.0 2026-09-05
# AI 分析：个人/全局 Key 管理、发送范围预览、分析生成、报告历史
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from datetime import datetime

from flask import render_template, request, jsonify, abort, redirect, url_for, flash
from flask_login import login_required, current_user
from app.extensions import db
from app.models.grades import Exam, AiKey, AiGlobalKey, AiReport
from app.modules.grades import bp
from app.modules.grades.services import ai_service, scope as scope_service
from app.utils.crypto import encrypt_rand, decrypt
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation


def _guard_key_manage():
    """全局 Key 管理权限：管理员或拥有 system.users 权限者"""
    if current_user.role == 'admin' or current_user.has_perm('system.users'):
        return
    abort(403)


# ==================== 个人 Key ====================

@bp.route('/ai/key', methods=['GET'])
@login_required
@perm_required('grades.view')
def ai_key_get():
    k = AiKey.query.filter_by(user_id=current_user.id).first()
    if not k or not k.api_key_enc:
        return jsonify(success=True, data={'configured': False})
    return jsonify(success=True, data={
        'configured': True, 'masked': ai_service.mask_key(k.api_key_enc),
        'provider': k.provider or 'deepseek',
        'base_url': k.base_url or '', 'model': k.model or 'deepseek-chat'})


@bp.route('/ai/key', methods=['POST'])
@login_required
@perm_required('grades.view')
def ai_key_save():
    data = request.get_json(silent=True) or {}
    api_key = (data.get('api_key') or '').strip()
    k = AiKey.query.filter_by(user_id=current_user.id).first()
    keep_key = api_key == 'keep'   # 前端“只改其它项”时不更换 Key
    if keep_key:
        if k is None or not k.api_key_enc:
            return jsonify(success=False, message='请输入 API Key'), 400
    elif not api_key or len(api_key) > 300:
        return jsonify(success=False, message='请输入有效的 API Key'), 400
    base_url = (data.get('base_url') or '').strip()
    if base_url and not (base_url.startswith('https://') or base_url.startswith('http://')):
        return jsonify(success=False, message='自定义地址需以 http(s):// 开头'), 400
    provider = data.get('provider') or 'deepseek'
    model = (data.get('model') or '').strip() or 'deepseek-chat'
    if k is None:
        k = AiKey(user_id=current_user.id)
        db.session.add(k)
    k.provider = provider
    k.base_url = base_url
    k.model = model
    if not keep_key:
        k.api_key_enc = encrypt_rand(api_key)   # AES 随机 IV 加密，不落明文
    db.session.commit()
    log_operation(current_user, '配置', 'AI Key', None, '个人 AI Key 已更新', module='grades')
    return jsonify(success=True, message='已保存（仅本人可见，加密存储）')


@bp.route('/ai/key', methods=['DELETE'])
@login_required
@perm_required('grades.view')
def ai_key_delete():
    k = AiKey.query.filter_by(user_id=current_user.id).first()
    if k:
        db.session.delete(k)
        db.session.commit()
    return jsonify(success=True, message='已清除')


# ==================== 全局兜底 Key（管理员） ====================

@bp.route('/ai/global-key', methods=['GET'])
@login_required
@perm_required('grades.view')
def ai_global_key_get():
    _guard_key_manage()
    g = AiGlobalKey.query.get(1)
    if not g or not g.api_key_enc:
        return jsonify(success=True, data={'configured': False})
    return jsonify(success=True, data={
        'configured': True, 'masked': ai_service.mask_key(g.api_key_enc),
        'provider': g.provider or 'deepseek',
        'base_url': g.base_url or '', 'model': g.model or 'deepseek-chat'})


@bp.route('/ai/global-key', methods=['POST'])
@login_required
@perm_required('grades.view')
def ai_global_key_save():
    _guard_key_manage()
    data = request.get_json(silent=True) or {}
    api_key = (data.get('api_key') or '').strip()
    g = AiGlobalKey.query.get(1)
    keep_key = api_key == 'keep'
    if keep_key:
        if g is None or not g.api_key_enc:
            return jsonify(success=False, message='请输入 API Key'), 400
    elif not api_key or len(api_key) > 300:
        return jsonify(success=False, message='请输入 API Key'), 400
    base_url = (data.get('base_url') or '').strip()
    if base_url and not (base_url.startswith('https://') or base_url.startswith('http://')):
        return jsonify(success=False, message='自定义地址需以 http(s):// 开头'), 400
    if g is None:
        g = AiGlobalKey(id=1)
        db.session.add(g)
    g.provider = data.get('provider') or 'deepseek'
    g.base_url = base_url
    g.model = (data.get('model') or '').strip() or 'deepseek-chat'
    if not keep_key:
        g.api_key_enc = encrypt_rand(api_key)
    g.operator_id = current_user.id
    db.session.commit()
    log_operation(current_user, '配置', 'AI 全局Key', None, '公共 AI Key 已更新', module='grades')
    return jsonify(success=True, message='全局 Key 已保存（所有未配置个人 Key 的用户将使用它）')


# ==================== 发送范围预览 ====================

@bp.route('/ai/scope')
@login_required
@perm_required('grades.view')
def ai_scope():
    exam_id = request.args.get('exam_id', type=int)
    exam = Exam.query.get_or_404(exam_id)
    try:
        scope_service.check_exam_visible(current_user, exam)
    except PermissionError:
        abort(403)
    payload = ai_service.build_payload(current_user, exam)
    cfg = ai_service.resolve_key(current_user)
    prev = Exam.query.get(payload['prev']['id']) if payload['prev'] else None
    key_source = cfg['source'] if cfg else None
    return jsonify(success=True, data={
        'exam': {'id': exam.id, 'name': exam.name, 'grade': exam.grade,
                 'date': exam.exam_date.strftime('%Y-%m-%d')},
        'prev': (prev and {'id': prev.id, 'name': prev.name,
                           'date': prev.exam_date.strftime('%Y-%m-%d')}) or None,
        'scope_desc': payload['scope_desc'],
        'scope_students': payload['scope_students'],
        'student_count': len(payload['exam']['students']),
        'key_source': key_source,
        'provider': (cfg or {}).get('provider'),
        'model': (cfg or {}).get('model'),
    })


# ==================== 分析生成 ====================

@bp.route('/ai/analyze', methods=['POST'])
@login_required
@perm_required('grades.view')
def ai_analyze():
    data = request.get_json(silent=True) or {}
    exam_id = int(data.get('exam_id') or 0)
    exam = Exam.query.get_or_404(exam_id)
    try:
        scope_service.check_exam_visible(current_user, exam)
    except PermissionError:
        abort(403)
    cfg = ai_service.resolve_key(current_user)
    if not cfg:
        return jsonify(success=False, message='尚未配置 AI API Key（可配置个人 Key，或联系管理员配置公共 Key）'), 400
    payload = ai_service.build_payload(current_user, exam)
    if not payload['exam']['students']:
        return jsonify(success=False, message='当前权限范围内没有可发送的成绩数据'), 400
    ok, text = ai_service.call_llm(cfg, ai_service.build_messages(payload))
    if not ok:
        return jsonify(success=False, message=text), 502
    report = AiReport(user_id=current_user.id, exam_id=exam.id,
                      prev_exam_id=(payload['prev'] or {}).get('id'),
                      grade=exam.grade, scope_desc=payload['scope_desc'],
                      scope_students=payload['scope_students'],
                      provider=cfg['provider'], model=cfg['model'], content=text)
    db.session.add(report)
    db.session.commit()
    log_operation(current_user, '生成', 'AI 报告', report.id,
                  f'{exam.name} 范围：{payload["scope_desc"]}（{payload["scope_students"]}人）',
                  module='grades')
    return jsonify(success=True, message='报告已生成',
                   data={'report_id': report.id, 'content': text})


# ==================== 报告历史 ====================

def _report_visible(report):
    if current_user.role == 'admin':
        return True
    return report.user_id == current_user.id


@bp.route('/ai/reports')
@login_required
@perm_required('grades.view')
def ai_reports():
    """报告历史页（本人；admin 全部）"""
    return render_template('grades/ai_reports.html')


@bp.route('/ai/reports/list')
@login_required
@perm_required('grades.view')
def ai_reports_list():
    exam_id = request.args.get('exam_id', type=int)
    q = AiReport.query
    if current_user.role != 'admin':
        q = q.filter_by(user_id=current_user.id)
    if exam_id:
        q = q.filter_by(exam_id=exam_id)
    rows = q.order_by(AiReport.created_at.desc()).limit(200).all()
    from app.models import User
    names = {u.id: u.real_name for u in User.query.filter(
        User.id.in_([r.user_id for r in rows] or [0])).all()}
    exams = {e.id: (e.name, e.grade) for e in Exam.query.filter(
        Exam.id.in_([r.exam_id for r in rows] or [0])).all()}
    return jsonify(success=True, data=[{
        'id': r.id, 'exam_id': r.exam_id,
        'exam_name': (exams.get(r.exam_id) or ['已删除'])[0],
        'grade': r.grade,
        'scope_desc': r.scope_desc, 'scope_students': r.scope_students,
        'provider': r.provider, 'model': r.model,
        'operator': names.get(r.user_id, ''),
        'created_at': r.created_at.strftime('%Y-%m-%d %H:%M') if r.created_at else '',
        'mine': r.user_id == current_user.id,
    } for r in rows])


@bp.route('/ai/reports/<int:rid>')
@login_required
@perm_required('grades.view')
def ai_report_detail(rid):
    r = AiReport.query.get_or_404(rid)
    if not _report_visible(r):
        abort(403)
    from app.models import User
    u = User.query.get(r.user_id)
    return jsonify(success=True, data={
        'id': r.id, 'content': r.content,
        'exam_id': r.exam_id, 'scope_desc': r.scope_desc,
        'scope_students': r.scope_students, 'provider': r.provider, 'model': r.model,
        'operator': u.real_name if u else '',
        'created_at': r.created_at.strftime('%Y-%m-%d %H:%M') if r.created_at else '',
    })


@bp.route('/ai/reports/<int:rid>/delete', methods=['POST'])
@login_required
@perm_required('grades.view')
def ai_report_delete(rid):
    r = AiReport.query.get_or_404(rid)
    if not _report_visible(r):
        abort(403)
    db.session.delete(r)
    db.session.commit()
    log_operation(current_user, '删除', 'AI 报告', rid, '', module='grades')
    return jsonify(success=True, message='报告已删除')
