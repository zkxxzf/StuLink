# StuLink v1.19.51.64 2026-10-10
# AI 分析：服务商注册表 / 个人与全局 Key 管理 / 连通性测试 / 发送范围预览 /
#          分析生成 / 报告历史
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import json
import logging
from datetime import datetime

from flask import (render_template, request, jsonify, abort, redirect, url_for,
                   flash, Response, stream_with_context)
from flask_login import login_required, current_user
from app.extensions import db
from app.models.grades import (Exam, AiKey, AiGlobalKey, AiReport, AiChatMessage,
                               AiTask, AiTrash)
from app.modules.grades import bp
from app.modules.grades.services import (ai_prompt, ai_service, ai_task_service,
                                         scope as scope_service)
from app.modules.grades.services import ai_providers
from app.utils import ai_quota
from app.utils.crypto import encrypt_rand, decrypt
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation

logger = logging.getLogger(__name__)
MAX_KEY_LEN = 300


def _archive_ai_row(kind, row):
    """删除前把整行写进 ai_trash（可按 ref_id 恢复）。

    2026-10-18：用户误点"清空"一次删光全部 AI 分析，而报告正文无法重新生成一致。
    归档只花几 KB，却能把"删错了"从不可挽回变成一条 SQL 就能救。
    """
    try:
        data = {}
        for col in row.__table__.columns:
            if col.name == 'id':
                continue
            val = getattr(row, col.name, None)
            data[col.name] = val.isoformat() if hasattr(val, 'isoformat') else val
        db.session.add(AiTrash(user_id=row.user_id, kind=kind, ref_id=row.id,
                               payload=json.dumps(data, ensure_ascii=False)))
    except Exception:                       # noqa: BLE001  归档失败不阻塞删除
        logger.warning('AI 记录归档失败 kind=%s id=%s', kind,
                       getattr(row, 'id', None), exc_info=True)


def _client_ai_cfg():
    """读取前端**浏览器本地（localStorage）**传来的 AI 配置。

    用户要求：私人 Key 只存本地、**服务器不留存**。故这里只在“当次请求”里
    透传给调用层，不写库、不记日志。
    支持两种携带方式：
      - JSON body: {"ai": {"api_key":…, "base_url":…, "model":…, "provider":…}}
      - 请求头:    X-AI-Key / X-AI-Base / X-AI-Model / X-AI-Provider
    多个文件/多次请求各自带各自的，互不影响。
    """
    d = request.get_json(silent=True) or {}
    ai = d.get('ai') if isinstance(d.get('ai'), dict) else {}
    key = (ai.get('api_key') or request.headers.get('X-AI-Key') or '').strip()
    if not key:
        return None
    return {
        'api_key': key[:MAX_KEY_LEN],
        'base_url': (ai.get('base_url') or request.headers.get('X-AI-Base') or '').strip(),
        'model': (ai.get('model') or request.headers.get('X-AI-Model') or '').strip(),
        'provider': (ai.get('provider') or request.headers.get('X-AI-Provider') or '').strip(),
        # v1.19.0 可选：输出上限 / 输入上限 / 思考强度（本机设置，服务器不落库）
        'max_tokens': (ai.get('max_tokens') or request.headers.get('X-AI-Max-Tokens') or ''),
        'max_input': (ai.get('max_input') or request.headers.get('X-AI-Max-Input') or ''),
        'reasoning_effort': (ai.get('reasoning_effort')
                             or request.headers.get('X-AI-Reasoning') or ''),
    }


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
    """全局 Key 管理权限：持有 grades.ai_manage 者（admin 由 has_perm 恒真覆盖）。

    2026-10-14 第四轮审计修复（SEC-04）：原判据是「admin 或 system.users」，
    但三个全局 Key 端点的**装饰器**只要求 grades.view —— 一旦重构漏拷函数体
    守卫，任何只读账号即可改写全局 AI Key。现在装饰器与函数体统一用独立权限点
    `grades.ai_manage`（迁移 scripts/migrate/migrate_ai_key_perm_20261014.py 已给原
    「有 system.users 的组」补齐该 key，行为不倒退）。
    """
    if current_user.has_perm('grades.ai_manage'):
        return
    abort(403)


# 2026-10-18（用户最终口径）：AI 分析的报告 / 任务 / 追问对话**仅对生成者本人可见**，
# 管理员也不例外 —— 不设 `?scope=all` 之类的审计后门。原来的 `role == 'admin'` 放行
# 让管理员默认看到全校教师的报告（含逐生成绩与评语），观感就是"所有人都能看"，已删除。


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
    if base_url:
        # H-9：保存阶段即校验（域名白名单 + 私网/环回/元数据地址拒绝），
        # 避免"保存成功、一点测试连接就打到内网"。
        try:
            from app.utils.url_guard import assert_outbound_url_allowed
            assert_outbound_url_allowed(base_url)
        except ValueError as e:
            return None, str(e)
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
        # 2026-10-12 数据可信度审计（§3.8）：保存有日志、清除原先无 ——
        # Key 被清除（误操作或恶意）时无迹可查。
        log_operation(current_user, '清除', 'AI Key', None, '个人 AI Key 已清除',
                      module='grades')
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
            # 2026-10-18：解密异常的原文可能带出密钥材料线索，不外露；改为固定文案
            from flask import current_app
            current_app.logger.warning('AI 密钥解密失败: %s', e)
            return jsonify(success=False, message='密钥解析失败，请重新配置'), 400

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
@perm_required('grades.ai_manage')
def ai_global_key_get():
    _guard_key_manage()
    g = AiGlobalKey.query.get(1)
    if not g or not g.api_key_enc:
        return jsonify(success=True, data={'configured': False})
    return jsonify(success=True, data=_cfg_to_client(g))


@bp.route('/ai/global-key', methods=['POST'])
@login_required
@perm_required('grades.ai_manage')
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
@perm_required('grades.ai_manage')
def ai_global_key_delete():
    _guard_key_manage()
    g = AiGlobalKey.query.get(1)
    if g:
        db.session.delete(g)
        db.session.commit()
        # 2026-10-12 数据可信度审计（§3.8）：全局 Key 被清除属高危事件
        # （会影响所有未配个人 Key 的用户），必须留业务痕迹。
        log_operation(current_user, '清除', 'AI 全局 Key', None, '全局 AI Key 已清除',
                      module='grades')
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
        cfg = ai_service.resolve_key(current_user, _client_ai_cfg())
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
        cfg = ai_service.resolve_key(current_user, _client_ai_cfg())
    except ValueError as e:
        return jsonify(success=False, message=str(e)), 400
    if not cfg:
        return jsonify(success=False,
                       message='尚未配置 AI API Key（可配置个人 Key，或联系管理员配置公共 Key）'), 400
    payload = ai_service.build_payload(current_user, exam)
    if not payload['exam']['students']:
        return jsonify(success=False, message='当前权限范围内没有可发送的成绩数据'), 400

    # P2：每日 AI 配额（发起即计数；失败不返还）
    _ok, _used, _limit = ai_quota.consume(current_user.id)
    if not _ok:
        return jsonify(success=False,
                       message=f'今日 AI 调用次数已用完（{_used}/{_limit} 次），请明天再试'), 429

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
        cfg = ai_service.resolve_key(current_user, _client_ai_cfg())
    except ValueError as e:
        return jsonify(success=False, message=str(e)), 400
    if not cfg:
        return jsonify(success=False, message='尚未配置 AI API Key'), 400

    _ok, _used, _limit = ai_quota.consume(current_user.id)
    if not _ok:
        return jsonify(success=False,
                       message=f'今日 AI 调用次数已用完（{_used}/{_limit} 次），请明天再试'), 429

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


# ==================== 一句话分析（工具调用 → HTML 报告） ====================

@bp.route('/ai/ask', methods=['POST'])
@login_required
@perm_required('grades.view')
def ai_ask():
    """提交一次「AI 一句话分析」：**只建任务**，生成在后台线程继续。

    与 `/ai/analyze`（把整包成绩塞进 prompt 单次生成 Markdown）不同：
    这里只把**只读工具清单**给模型，由它自己决定查什么，取回的是与网页端同源的
    真实数字，最终产出 **HTML 片段**（`app/utils/html_guard.py` 白名单消毒后进页面），
    结果连同工具轨迹一起写进 AI 报告历史。

    2026-10-13 起改为任务式：提问 → 建任务（`ai_tasks`）→ 立即返回 `task_id`，
    前端订阅 `/ai/tasks/<id>/stream` 看进度；**切页/刷新不会中断生成**，
    回到页面即可续看（详情接口带进度回放与报告正文）。
    """
    data = _json()
    exam_id = int(data.get('exam_id') or 0)
    question = (data.get('question') or '').strip()[:ai_task_service.MAX_QUESTION]
    exam = Exam.query.get_or_404(exam_id)
    if not question:
        return jsonify(success=False, message='请输入要分析的问题'), 400
    try:
        scope_service.check_exam_visible(current_user, exam)
    except PermissionError:
        abort(403)
    # 页面内多轮追问：只留最近 6 条，每条限长；仅作本会话上下文，随任务一次性使用
    history = []
    if isinstance(data.get('history'), list):
        for h in data['history'][-6:]:
            if isinstance(h, dict) and str(h.get('content') or '').strip():
                history.append({'role': 'user' if h.get('role') == 'user' else 'assistant',
                                'content': str(h.get('content'))[:2000]})
    # 提示词生成器的结构化选项：只做清洗留档（供「重新编辑提示词」回填）。
    # 这里**不**因选项非法而拒绝任务 —— question 才是真正发出去的话，用户手动改过也照跑；
    # 数据边界由工具层的 score_guard 兜底，选项本身不构成权限通道。
    raw_gen = data.get('gen')
    gen = ai_prompt.normalize_gen(raw_gen) if isinstance(raw_gen, dict) and raw_gen else None
    ok, out = ai_task_service.submit(
        current_user, exam, question,
        direction=(data.get('direction') or '').strip(),
        layer=(data.get('layer') or '').strip(),
        client_cfg=_client_ai_cfg(), history=history, gen=gen)
    if not ok:
        return jsonify(success=False, message=str(out)), 400
    return jsonify(success=True, data={'task_id': out})


def _loads_gen(text):
    """任务上的生成器选项 JSON → dict；为空/坏数据返回 None（前端据此隐藏回填入口）。"""
    try:
        data = json.loads(text) if text else None
    except Exception:                      # noqa: BLE001  坏数据不影响任务详情
        return None
    return data if isinstance(data, dict) and data else None


def _task_json(task, with_body=False, names=None):
    """任务 → 前端结构；with_body 时附进度事件与报告正文。"""
    exam = Exam.query.get(task.exam_id)
    out = {
        'id': task.id, 'origin': 'task', 'exam_id': task.exam_id,
        'exam_name': exam.name if exam else '（考试已删除）',
        'grade': exam.grade if exam else '', 'question': task.question,
        'gen': _loads_gen(task.gen),
        'status': task.status, 'stage': task.stage or '', 'error': task.error or '',
        'tool_count': task.tool_count or 0, 'report_id': task.report_id,
        'kind': 'html',
        'operator': (names or {}).get(task.user_id, ''),
        'mine': task.user_id == current_user.id,
        'created_at': task.created_at.strftime('%Y-%m-%d %H:%M') if task.created_at else '',
        'finished_at': task.finished_at.strftime('%Y-%m-%d %H:%M') if task.finished_at else '',
    }
    if with_body:
        out['events'] = [ev for ev in ai_task_service.task_events(task.id)
                         if ev.get('type') in ('stage', 'tool')]
        html_text = None
        if task.report_id:
            report = AiReport.query.get(task.report_id)
            if report is not None:
                html_text = report.content
                out['kind'] = report.kind or 'html'
        out['html'] = html_text
        out['md'] = None
        try:
            out['trace'] = json.loads(task.trace) if task.trace else []
        except Exception:                      # noqa: BLE001  轨迹坏掉不影响看报告
            out['trace'] = []
    return out


def _report_as_task(report, names=None):
    """把"旧口径报告"（AI 分析按钮生成的 Markdown、或已无对应任务的报告）包装成任务结构。

    id 用 **负数**（= -report_id）以便与任务 id 区分，但详情/订阅接口对负数同样可用，
    前端因此不需要为历史报告写第二套渲染分支。
    """
    exam = Exam.query.get(report.exam_id)
    kind = report.kind or 'md'
    return {
        'id': -report.id, 'origin': 'report', 'exam_id': report.exam_id,
        'exam_name': exam.name if exam else '（考试已删除）',
        'grade': report.grade or (exam.grade if exam else ''),
        'question': '（旧口径 AI 报告，来自「AI 分析」按钮）',
        'status': 'done', 'stage': '已完成', 'error': '', 'tool_count': 0,
        'report_id': report.id, 'kind': kind, 'events': [],
        'html': report.content if kind == 'html' else None,
        'md': report.content if kind != 'html' else None,
        'trace': [],
        'operator': (names or {}).get(report.user_id, ''),
        'mine': report.user_id == current_user.id,
        'created_at': (report.created_at.strftime('%Y-%m-%d %H:%M')
                       if report.created_at else ''),
        'finished_at': (report.created_at.strftime('%Y-%m-%d %H:%M')
                        if report.created_at else ''),
    }


@bp.route('/ai/tasks')
@login_required
@perm_required('grades.view')
def ai_tasks():
    """我的 AI 分析条目：任务（进行中/已完成）+ 无对应任务的旧报告。"""
    exam_id = request.args.get('exam_id', type=int)
    limit = max(1, min(request.args.get('limit', default=30, type=int) or 30, 100))
    # 2026-10-18（用户最终口径）：AI 分析**仅对本人生效** —— 管理员也只列自己的，
    # 不做"全部用户"的审计视图。
    tasks = ai_task_service.list_tasks(current_user, exam_id, limit)
    linked = {t.report_id for t in tasks if t.report_id}
    q = AiReport.query.filter_by(user_id=current_user.id)
    if exam_id:
        q = q.filter_by(exam_id=exam_id)
    reports = [r for r in q.order_by(AiReport.id.desc()).limit(limit).all()
               if r.id not in linked]
    from app.models import User
    uids = {t.user_id for t in tasks} | {r.user_id for r in reports}
    names = {u.id: u.real_name for u in User.query.filter(
        User.id.in_(sorted(uids) or [0])).all()}
    items = [_task_json(t, names=names) for t in tasks]
    items += [_report_as_task(r, names) for r in reports]
    items.sort(key=lambda x: (x.get('finished_at') or x.get('created_at') or ''), reverse=True)
    return jsonify(success=True, data=items)


@bp.route('/ai/tasks/<int:tid>')
@login_required
@perm_required('grades.view')
def ai_task_detail(tid):
    """任务详情（含进度回放与报告正文）；负数 id = 旧口径报告。"""
    if tid < 0:
        report = AiReport.query.get_or_404(-tid)
        if not _report_visible(report):
            abort(403)
        return jsonify(success=True, data=_report_as_task(report))
    task = AiTask.query.get_or_404(tid)
    # 2026-10-18（用户最终口径）：AI 分析仅对生成者本人可见 —— 管理员也不例外
    if task.user_id != current_user.id:
        abort(403)
    return jsonify(success=True, data=_task_json(task, with_body=True))


@bp.route('/ai/tasks/<int:tid>/stream')
@login_required
@perm_required('grades.view')
def ai_task_stream(tid):
    """SSE 订阅任务进度（GET，可用 EventSource，浏览器自带断线重连）。

    先补发已产生的事件，再实时推；任务结束即发 `done` 并收尾。
    `{'type':'ping'}` 转成 SSE 注释帧保活（waitress channel_timeout 默认 120s）。
    """
    if tid < 0:
        def gen_old():
            yield ': open\n\n'
            yield _sse({'type': 'done'})
        return Response(stream_with_context(gen_old()), headers=_sse_headers())
    task = AiTask.query.get_or_404(tid)
    # 2026-10-18（用户最终口径）：AI 分析仅对生成者本人可见 —— 管理员也不例外
    if task.user_id != current_user.id:
        abort(403)

    def gen():
        yield ': open\n\n'
        for ev in ai_task_service.subscribe(tid):
            if ev.get('type') == 'ping':
                yield ': ping\n\n'
                continue
            yield _sse(ev)
            if ev.get('type') == 'done':
                return

    return Response(stream_with_context(gen()), headers=_sse_headers())


@bp.route('/ai/tasks/<int:tid>/delete', methods=['POST'])
@login_required
@perm_required('grades.view')
def ai_task_delete(tid):
    """删除一条任务记录（进行中的不可删：线程还在写回状态）。"""
    task = AiTask.query.get_or_404(tid)
    # 2026-10-18（用户最终口径）：AI 分析仅对生成者本人可见 —— 管理员也不例外
    if task.user_id != current_user.id:
        abort(403)
    if task.status == 'running':
        return jsonify(success=False, message='任务进行中，请等它跑完再删除'), 400
    # 2026-10-18：删任务时把它生成的报告**一起删掉**。否则列表里会残留一条
    # "旧口径报告"（id 为负），用户点了删除发现"还在"，就是删不干净。
    rid = task.report_id
    _archive_ai_row('task', task)
    if rid:
        rep = db.session.get(AiReport, rid)
        if rep is not None and rep.user_id == current_user.id:
            _archive_ai_row('report', rep)
            db.session.delete(rep)
    db.session.delete(task)
    db.session.commit()
    ai_task_service.drop_events(tid)
    log_operation(current_user, '删除', 'AI 任务', tid, '', module='grades')
    return jsonify(success=True, message='已删除')


@bp.route('/ai/tasks/clear', methods=['POST'])
@login_required
@perm_required('grades.view')
def ai_clear():
    """清空**我自己的**全部 AI 分析记录（任务 + 报告）；进行中的任务保留。

    2026-10-18：用户误点后一次删光。现在两道闸：
    ① 前端入口已移除（界面不再有一键清空）；
    ② 接口要求 `confirm=CLEAR` 才执行，且**删除前逐条归档**到 ai_trash（可恢复）。
    仅本人生效（AI 分析属个人内容，管理员也不例外）。
    """
    if (request.form.get('confirm') or request.args.get('confirm') or '') != 'CLEAR':
        return jsonify(success=False,
                       message='清空操作需要显式确认（confirm=CLEAR）'), 400
    reports = AiReport.query.filter_by(user_id=current_user.id).all()
    tasks = (AiTask.query.filter(AiTask.user_id == current_user.id,
                                 AiTask.status != 'running').all())
    running = AiTask.query.filter_by(user_id=current_user.id,
                                     status='running').count()
    for r in reports:
        _archive_ai_row('report', r)
    for t in tasks:
        _archive_ai_row('task', t)
    db.session.flush()
    n_rep, n_task = len(reports), len(tasks)
    for r in reports:
        db.session.delete(r)
    for t in tasks:
        db.session.delete(t)
    db.session.commit()
    log_operation(current_user, '清空', 'AI 分析记录', None,
                  f'任务 {n_task} 条、报告 {n_rep} 条', module='grades')
    msg = f'已清空 {n_task} 条任务记录、{n_rep} 份报告'
    if running:
        msg += f'；{running} 个任务仍在进行中，已保留（跑完可再清空）'
    return jsonify(success=True, message=msg, deleted_tasks=n_task,
                   deleted_reports=n_rep, running=running)


@bp.route('/ai/tasks/<int:tid>/stop', methods=['POST'])
@login_required
@perm_required('grades.view')
def ai_task_stop(tid):
    """请求停止一个进行中的任务（2026-10-14）。

    语义是"当前这一步跑完就停"：模型 HTTP 请求在飞时无法中断，线程会在下一轮
    模型调用前 / 下一次工具调用前看到标记并停下，任务状态记为 `canceled`，
    前端随即给出「重新编辑提示词」（用 task.gen 无损回填生成器）。
    """
    task = AiTask.query.get_or_404(tid)
    # 2026-10-18（用户最终口径）：AI 分析仅对生成者本人可见 —— 管理员也不例外
    if task.user_id != current_user.id:
        abort(403)
    if task.status != 'running':
        return jsonify(success=False, message='任务已结束，无需停止'), 400
    if not ai_task_service.request_stop(tid):
        return jsonify(success=False,
                       message='任务不在当前进程中执行（可能刚被中断），请刷新列表'), 409
    log_operation(current_user, '停止', 'AI 任务', tid, '', module='grades')
    return jsonify(success=True, message='已请求停止，当前步骤完成即停')


# ==================== 提示词生成器（2026-10-14） ====================
# 老师用下拉选项拼装提示词：选项（权限收窄）→ 拼装（真实性+权限双校验）→
# 复制 / 直接分析 / 存模板。落库与执行仍走既有的 /ai/ask 任务链路。

@bp.route('/ai/prompt/options')
@login_required
@perm_required('grades.view')
def ai_prompt_options():
    """生成器选项：年级 / 班级 / 学科 + 侧重 / 输出格式 / 附加要求预设。

    班级/学科都是**本场考试成绩里真实存在的值**（班级=总分行口径），
    且按账号成绩可见范围收窄——下拉里选出来的组合天然不会越权。
    """
    exam_id = request.args.get('exam_id', type=int)
    exam = Exam.query.get_or_404(exam_id or 0)
    try:
        scope_service.check_exam_visible(current_user, exam)
    except PermissionError:
        abort(403)
    try:
        data = ai_prompt.options(current_user, exam)
    except PermissionError as e:
        return jsonify(success=False, message=str(e)), 403
    return jsonify(success=True, data=data)


@bp.route('/ai/prompt/build', methods=['POST'])
@login_required
@perm_required('grades.view')
def ai_prompt_build():
    """校验生成器选项并拼装提示词（只返回文本，不发起分析）。"""
    data = _json()
    exam = Exam.query.get_or_404(int(data.get('exam_id') or 0))
    try:
        scope_service.check_exam_visible(current_user, exam)
    except PermissionError:
        abort(403)
    try:
        out = ai_prompt.build(current_user, exam, data.get('gen'))
    except PermissionError as e:
        return jsonify(success=False, message=str(e)), 403
    except ValueError as e:
        return jsonify(success=False, message=str(e)), 400
    return jsonify(success=True, data=out)


@bp.route('/ai/prompt/templates', methods=['GET', 'POST'])
@login_required
@perm_required('grades.view')
def ai_prompt_templates():
    """我的提示词模板：GET 列表 / POST 保存（新建或按 id/同名覆盖）。"""
    if request.method == 'GET':
        return jsonify(success=True, data=ai_prompt.list_templates(current_user))
    try:
        row = ai_prompt.save_template(current_user, _json())
    except ValueError as e:
        return jsonify(success=False, message=str(e)), 400
    log_operation(current_user, '保存', 'AI 提示词模板', row['id'], row['name'],
                  module='grades')
    return jsonify(success=True, data=row, message='模板已保存')


@bp.route('/ai/prompt/templates/<int:tid>/delete', methods=['POST'])
@login_required
@perm_required('grades.view')
def ai_prompt_template_delete(tid):
    """删除我的模板（只允许本人）。"""
    if not ai_prompt.delete_template(current_user, tid):
        return jsonify(success=False, message='模板不存在或不属于你'), 404
    log_operation(current_user, '删除', 'AI 提示词模板', tid, '', module='grades')
    return jsonify(success=True, message='模板已删除')


@bp.route('/ai')
@login_required
@perm_required('grades.view')
def ai_studio():
    """AI 分析工作台：任务列表 + 提问 + 进度 + 报告（原「AI 报告历史」并入此页）。"""
    return render_template('grades/ai_studio.html')


# ==================== 报告历史 ====================

def _report_visible(report):
    """AI 报告可见性（2026-10-18 用户最终口径）：**仅生成者本人**，管理员也不例外。

    原来 `role == 'admin' → True`：管理员一进工作台就看到全校教师的报告
    （含逐生成绩、评语），观感就是"所有人都能看"。现在无任何例外。
    """
    return report.user_id == current_user.id


@bp.route('/ai/reports')
@login_required
@perm_required('grades.view')
def ai_reports():
    """原「AI 报告历史」页：2026-10-13 已并入「AI 分析工作台」(`/grades/ai`)。

    旧地址保留 302 跳转（书签与页面里的历史链接不失效），并透传 exam 参数。
    """
    args = {k: v for k, v in request.args.items() if k in ('exam', 'exam_id')}
    return redirect(url_for('grades.ai_studio', **args))


@bp.route('/ai/reports/<int:rid>')
@login_required
@perm_required('grades.view')
def ai_report_detail(rid):
    r = AiReport.query.get_or_404(rid)
    if not _report_visible(r):
        abort(403)
    from app.models import User
    u = User.query.get(r.user_id)
    charts, trace = None, []
    if r.charts:
        try:
            charts = json.loads(r.charts)
        except Exception:
            charts = None
    if r.trace:
        try:
            trace = json.loads(r.trace) or []
        except Exception:
            trace = []
    return jsonify(success=True, data={
        'id': r.id, 'content': r.content, 'charts': charts,
        'kind': r.kind or 'md', 'trace': trace,
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
    _archive_ai_row('report', r)      # 2026-10-18：删除前归档，误删可恢复
    db.session.delete(r)
    db.session.commit()
    log_operation(current_user, '删除', 'AI 报告', rid, '', module='grades')
    return jsonify(success=True, message='报告已删除')
