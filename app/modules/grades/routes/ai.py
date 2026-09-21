# StuLink v1.9.3 2026-09-19
# AI 分析：服务商注册表 / 个人与全局 Key 管理 / 连通性测试 / 发送范围预览 /
#          分析生成 / 报告历史
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import json
from datetime import datetime

from flask import (render_template, request, jsonify, abort, redirect, url_for,
                   flash, Response, stream_with_context)
from flask_login import login_required, current_user
from app.extensions import db
from app.models.grades import Exam, AiKey, AiGlobalKey, AiReport, AiChatMessage
from app.modules.grades import bp
from app.modules.grades.services import ai_service, scope as scope_service
from app.modules.grades.services import ai_providers
from app.utils.crypto import encrypt_rand, decrypt
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation

MAX_KEY_LEN = 300


def _sse(obj):
    """SSE 单帧"""
    return 'data: ' + json.dumps(obj, ensure_ascii=False) + '\n\n'


def _sse_headers():
    return {'Content-Type': 'text/event-stream; charset=utf-8',
            'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}


def _json():
    """解析请求 JSON

    说明：force=True 可兼容「body 是 JSON 但未声明 content-type」的客户端；
    但如果 content-type 是表单类型，body 会被 CSRF 校验先行消费，
    因此前端必须显式设置 contentType: 'application/json'（grades.js 已修正）。
    """
    return request.get_json(force=True, silent=True) or {}


def _guard_key_manage():
    """全局 Key 管理权限：管理员或拥有 system.users 权限者"""
    if current_user.role == 'admin' or current_user.has_perm('system.users'):
        return
    abort(403)


def _normalize_config(data, old=None):
    """统一解析并校验服务商配置，返回 (cfg, error_message)

    cfg: {'provider', 'provider_name', 'base_url', 'model'}
    api_key 单独处理（'keep' 表示不更换）
    """
    provider = (data.get('provider') or (old.provider if old else '')
                or ai_providers.DEFAULT_PROVIDER).strip()
    if not ai_providers.provider_exists(provider):
        provider = ai_providers.CUSTOM_PROVIDER
    base_url = (data.get('base_url') or '').strip()
    if base_url and not (base_url.startswith('https://') or base_url.startswith('http://')):
        return None, '接口地址需以 http(s):// 开头'
    if base_url and len(base_url) > 200:
        return None, '接口地址过长'
    model = (data.get('model') or '').strip()
    if len(model) > 100:
        return None, '模型名过长'
    return {
        'provider': provider,
        'provider_name': ai_providers.get_provider(provider)['name'],
        'base_url': base_url,
        'model': model or ai_providers.resolve_model(provider, ''),
    }, None


def _cfg_to_client(row, extra=None):
    """把 Key 行转成前端需要的结构"""
    provider = row.provider or ai_providers.DEFAULT_PROVIDER
    data = {
        'configured': bool(row.api_key_enc),
        'masked': ai_service.mask_key(row.api_key_enc) if row.api_key_enc else '',
        'provider': provider,
        'provider_name': ai_providers.get_provider(provider)['name'],
        'base_url': row.base_url or '',
        'default_base_url': ai_providers.get_provider(provider)['base_url'],
        'model': ai_providers.resolve_model(provider, row.model),
    }
    if extra:
        data.update(extra)
    return data


# ==================== 服务商列表 ====================

@bp.route('/ai/providers')
@login_required
@perm_required('grades.view')
def ai_providers_list():
    """供前端下拉：所有支持的服务商、默认模型与可选模型"""
    return jsonify(success=True,
                   data={'default': ai_providers.DEFAULT_PROVIDER,
                         'providers': ai_providers.list_providers()})


# ==================== 个人 Key ====================

@bp.route('/ai/key', methods=['GET'])
@login_required
@perm_required('grades.view')
def ai_key_get():
    k = AiKey.query.filter_by(user_id=current_user.id).first()
    if not k or not k.api_key_enc:
        return jsonify(success=True, data={'configured': False})
    return jsonify(success=True, data=_cfg_to_client(k))


@bp.route('/ai/key', methods=['POST'])
@login_required
@perm_required('grades.view')
def ai_key_save():
    data = _json()
    api_key = (data.get('api_key') or '').strip()
    k = AiKey.query.filter_by(user_id=current_user.id).first()
    keep_key = api_key == 'keep'   # 前端“只改其它项”时不更换 Key
    if keep_key:
        if k is None or not k.api_key_enc:
            return jsonify(success=False, message='请输入 API Key'), 400
    else:
        ok, msg = ai_providers.validate_api_key(api_key)
        if not ok:
            return jsonify(success=False, message=msg), 400
    cfg, err = _normalize_config(data, k)
    if err:
        return jsonify(success=False, message=err), 400
    if k is None:
        k = AiKey(user_id=current_user.id)
        db.session.add(k)
    k.provider = cfg['provider']
    k.base_url = cfg['base_url']
    k.model = cfg['model']
    if not keep_key:
        k.api_key_enc = encrypt_rand(api_key)   # AES 随机 IV 加密，不落明文
    db.session.commit()
    log_operation(current_user, '配置', 'AI Key', None,
                  f"个人 AI Key 已更新（{cfg['provider_name']} / {cfg['model']}）",
                  module='grades')
    return jsonify(success=True,
                   message=f"已保存（{cfg['provider_name']}，加密存储，仅本人可见）")


@bp.route('/ai/key', methods=['DELETE'])
@login_required
@perm_required('grades.view')
def ai_key_delete():
    k = AiKey.query.filter_by(user_id=current_user.id).first()
    if k:
        db.session.delete(k)
        db.session.commit()
    return jsonify(success=True, message='已清除')


# ==================== 连通性测试 ====================

@bp.route('/ai/key/test', methods=['POST'])
@login_required
@perm_required('grades.view')
def ai_key_test():
    """用极小请求验证 Key 是否有效（不发送任何成绩数据、不落报告）"""
    data = _json()
    scope = (data.get('scope') or 'personal').strip()
    api_key = (data.get('api_key') or '').strip()

    old = None
    if scope == 'global':
        _guard_key_manage()
        old = AiGlobalKey.query.get(1)
    else:
        old = AiKey.query.filter_by(user_id=current_user.id).first()

    if api_key and api_key != 'keep':
        ok, msg = ai_providers.validate_api_key(api_key)
        if not ok:
            return jsonify(success=False, message=msg), 400
    else:
        if old is None or not old.api_key_enc:
            return jsonify(success=False, message='尚未配置 API Key'), 400
        try:
            api_key = ai_service.decrypt_key(old.api_key_enc)
        except ValueError as e:
            return jsonify(success=False, message=str(e)), 400

    cfg, err = _normalize_config(data, old)
    if err:
        return jsonify(success=False, message=err), 400
    cfg['api_key'] = api_key

    ok, msg, cost = ai_service.test_connection(cfg)
    if not ok:
        return jsonify(success=False, message=msg, data={'cost': cost}), 400
    return jsonify(success=True, message=msg,
                   data={'cost': cost, 'provider': cfg['provider'], 'model': cfg['model']})


# ==================== 全局兜底 Key（管理员） ====================

@bp.route('/ai/global-key', methods=['GET'])
@login_required
@perm_required('grades.view')
def ai_global_key_get():
    _guard_key_manage()
    g = AiGlobalKey.query.get(1)
    if not g or not g.api_key_enc:
        return jsonify(success=True, data={'configured': False})
    return jsonify(success=True, data=_cfg_to_client(g))


@bp.route('/ai/global-key', methods=['POST'])
@login_required
@perm_required('grades.view')
def ai_global_key_save():
    _guard_key_manage()
    data = _json()
    api_key = (data.get('api_key') or '').strip()
    g = AiGlobalKey.query.get(1)
    keep_key = api_key == 'keep'
    if keep_key:
        if g is None or not g.api_key_enc:
            return jsonify(success=False, message='请输入 API Key'), 400
    else:
        ok, msg = ai_providers.validate_api_key(api_key)
        if not ok:
            return jsonify(success=False, message=msg), 400
    cfg, err = _normalize_config(data, g)
    if err:
        return jsonify(success=False, message=err), 400
    if g is None:
        g = AiGlobalKey(id=1)
        db.session.add(g)
    g.provider = cfg['provider']
    g.base_url = cfg['base_url']
    g.model = cfg['model']
    if not keep_key:
        g.api_key_enc = encrypt_rand(api_key)
    g.operator_id = current_user.id
    db.session.commit()
    log_operation(current_user, '配置', 'AI 全局Key', None,
                  f"公共 AI Key 已更新（{cfg['provider_name']} / {cfg['model']}）",
                  module='grades')
    return jsonify(success=True,
                   message='全局 Key 已保存（未配置个人 Key 的用户将使用它）')


@bp.route('/ai/global-key', methods=['DELETE'])
@login_required
@perm_required('grades.view')
def ai_global_key_delete():
    _guard_key_manage()
    g = AiGlobalKey.query.get(1)
    if g:
        db.session.delete(g)
        db.session.commit()
    return jsonify(success=True, message='已清除')


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
    try:
        cfg = ai_service.resolve_key(current_user)
    except ValueError as e:
        # 本地解密异常：明确提示，避免误判为「Key 无效」
        return jsonify(success=False, message=str(e)), 400
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
        'provider_name': (cfg or {}).get('provider_name'),
        'model': (cfg or {}).get('model'),
    })


# ==================== 分析生成 ====================

@bp.route('/ai/analyze', methods=['POST'])
@login_required
@perm_required('grades.view')
def ai_analyze():
    data = _json()
    exam_id = int(data.get('exam_id') or 0)
    exam = Exam.query.get_or_404(exam_id)
    try:
        scope_service.check_exam_visible(current_user, exam)
    except PermissionError:
        abort(403)
    try:
        cfg = ai_service.resolve_key(current_user)
    except ValueError as e:
        return jsonify(success=False, message=str(e)), 400
    if not cfg:
        return jsonify(success=False,
                       message='尚未配置 AI API Key（可配置个人 Key，或联系管理员配置公共 Key）'), 400
    payload = ai_service.build_payload(current_user, exam)
    if not payload['exam']['students']:
        return jsonify(success=False, message='当前权限范围内没有可发送的成绩数据'), 400

    charts = ai_service.build_stats(payload)
    charts_json = json.dumps(charts, ensure_ascii=False)
    messages = ai_service.build_messages(payload)

    # ---- 流式模式：边生成边推送（含思维链）----
    if data.get('stream'):
        def gen():
            yield _sse({'type': 'stage', 'text': '正在按权限范围整理成绩…'})
            yield _sse({'type': 'charts', 'data': charts})
            yield _sse({'type': 'stage', 'text': '已整理 %d 名学生数据，正在请求 %s…'
                        % (payload['scope_students'], cfg.get('provider_name') or cfg['provider'])})
            buf = []
            for ev in ai_service.stream_chat(cfg, messages):
                yield _sse(ev)
                if ev.get('type') == 'delta':
                    buf.append(ev.get('text') or '')
                if ev.get('type') == 'error':
                    return
            text = ''.join(buf).strip()
            if not text:
                yield _sse({'type': 'error', 'text': '模型未返回内容，请稍后重试'})
                return
            report = AiReport(user_id=current_user.id, exam_id=exam.id,
                              prev_exam_id=(payload['prev'] or {}).get('id'),
                              grade=exam.grade, scope_desc=payload['scope_desc'],
                              scope_students=payload['scope_students'],
                              provider=cfg['provider'], model=cfg['model'],
                              content=text, charts=charts_json)
            db.session.add(report)
            db.session.commit()
            log_operation(current_user, '生成', 'AI 报告', report.id,
                          f'{exam.name} 范围：{payload["scope_desc"]}（{payload["scope_students"]}人）',
                          module='grades')
            yield _sse({'type': 'saved', 'report_id': report.id})

        return Response(stream_with_context(gen()), headers=_sse_headers())

    # ---- 兼容旧调用：一次性返回 ----
    ok, text = ai_service.call_llm(cfg, messages)
    if not ok:
        return jsonify(success=False, message=text), 502
    report = AiReport(user_id=current_user.id, exam_id=exam.id,
                      prev_exam_id=(payload['prev'] or {}).get('id'),
                      grade=exam.grade, scope_desc=payload['scope_desc'],
                      scope_students=payload['scope_students'],
                      provider=cfg['provider'], model=cfg['model'],
                      content=text, charts=charts_json)
    db.session.add(report)
    db.session.commit()
    log_operation(current_user, '生成', 'AI 报告', report.id,
                  f'{exam.name} 范围：{payload["scope_desc"]}（{payload["scope_students"]}人）',
                  module='grades')
    return jsonify(success=True, message='报告已生成',
                   data={'report_id': report.id, 'content': text, 'charts': charts})


# ==================== AI 多轮对话 ====================

CHAT_HISTORY_LIMIT = 12   # 携带的历史消息条数（用户+助手合计）


def _chat_context(user, exam):
    """追问时的上下文：考试成绩统计 + 上一份报告摘要"""
    payload = ai_service.build_payload(user, exam)
    stats = ai_service.build_stats(payload)
    parts = [
        f'考试：{exam.name}（{exam.grade}）',
        f'发送范围：{payload["scope_desc"]}（{payload["scope_students"]}人）',
        '【成绩统计】',
        ai_service.stats_brief(stats),
    ]
    last = (AiReport.query.filter_by(user_id=user.id, exam_id=exam.id)
            .order_by(AiReport.created_at.desc()).first())
    if last and last.content:
        parts.append('【此前生成的报告节选】\n' + last.content[:2000])
    return '\n'.join(parts)


@bp.route('/ai/chat', methods=['POST'])
@login_required
@perm_required('grades.view')
def ai_chat():
    """多轮追问（流式）。body: {exam_id, message}"""
    data = _json()
    exam_id = int(data.get('exam_id') or 0)
    question = (data.get('message') or '').strip()
    exam = Exam.query.get_or_404(exam_id)
    if not question:
        return jsonify(success=False, message='请输入内容'), 400
    try:
        scope_service.check_exam_visible(current_user, exam)
    except PermissionError:
        abort(403)
    try:
        cfg = ai_service.resolve_key(current_user)
    except ValueError as e:
        return jsonify(success=False, message=str(e)), 400
    if not cfg:
        return jsonify(success=False, message='尚未配置 AI API Key'), 400

    # 保存用户提问
    user_msg = AiChatMessage(user_id=current_user.id, exam_id=exam.id, role='user',
                             content=question, provider=cfg['provider'], model=cfg['model'])
    db.session.add(user_msg)
    db.session.commit()

    history = (AiChatMessage.query.filter_by(user_id=current_user.id, exam_id=exam.id)
               .order_by(AiChatMessage.id.desc()).limit(CHAT_HISTORY_LIMIT).all())
    history = list(reversed(history))[:-1]   # 去掉刚存的那条用户消息，稍后按角色追加

    messages = [{'role': 'system', 'content': ai_service.CHAT_SYSTEM_PROMPT},
                {'role': 'system', 'content': _chat_context(current_user, exam)}]
    for m in history:
        messages.append({'role': m.role, 'content': m.content or ''})
    messages.append({'role': 'user', 'content': question})

    def gen():
        buf = []
        for ev in ai_service.stream_chat(cfg, messages):
            yield _sse(ev)
            if ev.get('type') == 'delta':
                buf.append(ev.get('text') or '')
            if ev.get('type') == 'error':
                return
        answer = ''.join(buf).strip()
        if answer:
            row = AiChatMessage(user_id=current_user.id, exam_id=exam.id, role='assistant',
                                content=answer, provider=cfg['provider'], model=cfg['model'])
            db.session.add(row)
            db.session.commit()

    return Response(stream_with_context(gen()), headers=_sse_headers())


@bp.route('/ai/chat/history')
@login_required
@perm_required('grades.view')
def ai_chat_history():
    exam_id = request.args.get('exam_id', type=int)
    if not exam_id:
        return jsonify(success=True, data=[])
    rows = (AiChatMessage.query.filter_by(user_id=current_user.id, exam_id=exam_id)
            .order_by(AiChatMessage.id).limit(200).all())
    return jsonify(success=True, data=[{'role': r.role, 'content': r.content} for r in rows])


@bp.route('/ai/chat/history', methods=['DELETE'])
@login_required
@perm_required('grades.view')
def ai_chat_clear():
    exam_id = request.args.get('exam_id', type=int)
    if exam_id:
        AiChatMessage.query.filter_by(user_id=current_user.id, exam_id=exam_id).delete()
        db.session.commit()
    return jsonify(success=True, message='对话已清空')


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
        'provider': r.provider,
        'provider_name': ai_providers.get_provider(r.provider)['name'],
        'model': r.model,
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
    charts = None
    if r.charts:
        try:
            charts = json.loads(r.charts)
        except Exception:
            charts = None
    return jsonify(success=True, data={
        'id': r.id, 'content': r.content, 'charts': charts,
        'exam_id': r.exam_id, 'scope_desc': r.scope_desc,
        'scope_students': r.scope_students, 'provider': r.provider,
        'provider_name': ai_providers.get_provider(r.provider)['name'],
        'model': r.model,
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
