# StuLink v1.19.65.64 2026-10-10
# AI 分析服务：权限收敛取数（本次+上次原始成绩）→ payload 组装 → LLM 转发
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import json
import time
import urllib.error
import urllib.request

from flask import current_app
from sqlalchemy import or_, and_

from app.extensions import db
from app.models.grades import Exam, ExamScore, AiKey, AiGlobalKey, SUBJECTS, TOTAL_SUBJECT
from app.modules.grades.services import scope as scope_service
from app.modules.grades.services import ai_providers
from app.utils.crypto import encrypt_rand, decrypt
from app.utils.url_guard import assert_outbound_url_allowed

DEFAULT_TEMPERATURE = 0.4
DEFAULT_TIMEOUT = 180


def _request_timeout():
    """P2：优先读 app 配置的 AI_REQUEST_TIMEOUT（秒），无上下文时用默认值。"""
    try:
        from flask import current_app, has_app_context
        if has_app_context():
            value = int(current_app.config.get('AI_REQUEST_TIMEOUT',
                                               DEFAULT_TIMEOUT) or DEFAULT_TIMEOUT)
            return max(10, value)
    except Exception:  # noqa: BLE001
        pass
    return DEFAULT_TIMEOUT

# v1.19.49.35 S-4：不自动跟随 3xx，防 SSRF 以 approved.com → 302 → 1.19.49.30 绕过 url_guard

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirect())


def _safe_urlopen(req, timeout):
    """经 opener.open 的封装；3xx 抛 HTTPError（因 redirect_request 返回 None）"""
    return _NO_REDIRECT_OPENER.open(req, timeout=timeout)

SYSTEM_PROMPT = (
    '你是一名严谨的中学考试成绩数据分析助手。你将收到一次（或两次）考试的 JSON 原始成绩数据'
    '（学号、姓名、班级、方向、选科、方向内排名、班内排名、进退步、各科分数；'
    '**逐生不含总分**，总分只提供聚合统计）。\n'
    '要求：\n'
    '1. 只依据提供的数据进行分析，禁止编造或推测数据中不存在的信息；数据缺失处明确说明。\n'
    '2. 输出中文 Markdown 报告，建议章节：总体情况（参考人数/均分/最高最低分）、'
    '两次考试对比（均分与排名变化、进退步学生分布）、各科表现（偏科与强弱科）、'
    '分数段与分层分布、班级对比（若数据含多班）、需要关注的个体（仅列学号/姓名等客观事实，'
    '不作人格化评价）。\n'
    '3. 保持客观，不评价教师与学校的教学，不给情绪化结论。\n'
    '4. 报告以“本报告由 AI 根据所提供成绩数据生成，仅供参考”开头。'
)

# 多轮对话用的系统提示（更口语化，允许追问）
CHAT_SYSTEM_PROMPT = (
    '你是中学考试成绩分析助手，正在与老师进行多轮对话。\n'
    '要求：\n'
    '1. 只依据已提供的成绩统计与上下文作答，不编造；数据不足时明说。\n'
    '2. 回答尽量简洁（300 字以内），必要时用 Markdown 列表或小表格。\n'
    '3. 系统已把分数段分布、班级均分、学科均分、进退步分布渲染成图表，'
    '请围绕这些图表做解读，不要把数据整表重抄一遍。\n'
    '4. 涉及个体只陈述客观事实，不作人格化评价。'
)


# ==================== 权限收敛：确定可发送的学生与科目 ====================

def scope_of(user, exam):
    """按用户权限返回 (student_nos, desc, restrict_subjects)
    student_nos：该考试参考学生中用户可见范围（空集合=无权）
    desc：范围说明；restrict_subjects：任课教师时仅其任教科目（None=全部）
    """
    nos = set()
    total_rows = ExamScore.query.filter_by(exam_id=exam.id,
                                           subject=TOTAL_SUBJECT).all()
    by_class = {}
    for r in total_rows:
        if r.score is None:
            continue
        nos.add(r.student_no)
        by_class.setdefault(r.class_name, []).append(r.student_no)

    if user.role == 'admin':
        return nos, f'{exam.grade}全年级', None
    # 2026-10-12 P1：与网页端分析（_guard_tab/check_exam_visible）同口径：
    # 用户级范围 > 组级 school > 组级 grade > 年级长角色回落 > 班主任 > 任课教师。
    # 原实现按 role 硬判：① staff+校级组/用户级授权账号一律拿到空集（AI 无数据）；
    # ② scope_of 自身不设年级防线（只靠路由层 check_exam_visible 单点防护）。
    ug = scope_service.user_grade_scope(user)
    if ug is not None:
        if exam.grade not in ug:
            return set(), '授权年级不含本场考试', None
        # 2026-10-16 第七轮审计修复（S1）：与 score_guard.resolve 的 R4 同口径 ——
        # 该年级存在班级白名单（user_data_scopes.class_name）时**按班收窄**。
        # 此前忽略白名单，「只授权 01 班」的账号会把全年级逐生数据（学号/姓名/
        # 单科分/名次）整份发给 AI/第三方 LLM，与「个人成绩按班×科收敛」相悖。
        from app.models.user_data_scope import get_user_scope_classes
        _wl = get_user_scope_classes(user.id, exam.grade)
        if _wl:
            allow = set()
            for _c in _wl:
                allow |= set(by_class.get(_c, []))
            return allow, '授权班级：' + '、'.join(sorted(_wl)), None
        return nos, '授权年级：' + exam.grade, None
    scope, my_grade = scope_service.get_scope(user)
    if scope == scope_service.SCOPE_SCHOOL:
        return nos, f'{exam.grade}全年级', None
    if scope == scope_service.SCOPE_GRADE or user.has_role('grade_leader'):
        # 2026-10-19：管理年级可多选（user.grades）
        my_grades = set(scope_service.get_scope_grades(user)) or set(user.grades)
        if my_grades and exam.grade not in my_grades:
            return set(), '非本人年级', None
        return nos, f'{exam.grade}全年级', None
    if user.has_role('homeroom_teacher'):
        links = scope_service.visible_classes(user) or []
        classes = {c for g, c in links if g == exam.grade}
        if not classes:
            return set(), '所辖班级（无本年级关联）', None
        allow = set()
        for cls in classes:
            allow |= set(by_class.get(cls, []))
        desc = '所辖班级：' + '、'.join(sorted(classes))
        return allow, desc, None
    if user.has_role('teacher'):
        from app.models.grades import TeacherSubjectLink
        links = TeacherSubjectLink.query.filter_by(user_id=user.id, active=True).all()
        classes = {l.class_name for l in links if l.grade == exam.grade}
        subjects = {l.subject for l in links if l.grade == exam.grade}
        if not classes:
            return set(), '本人任课班级（本年级无任课记录）', None
        allow = set()
        for cls in classes:
            allow |= set(by_class.get(cls, []))
        desc = '本人任课：' + '、'.join(sorted(classes))
        return allow, desc, (sorted(subjects) if subjects else None)
    return set(), '无权访问', None


def prev_exam_of(exam):
    """上一场同年级已导入考试"""
    return (Exam.query
            .filter(Exam.grade == exam.grade,
                    or_(Exam.exam_date < exam.exam_date,
                        and_(Exam.exam_date == exam.exam_date,
                             Exam.id < exam.id)))
            .order_by(Exam.exam_date.desc(), Exam.id.desc()).first())


def _student_card(r, subjects_allow=None):
    """总分行 → 学生卡（含科目分；任课教师仅返回任教科目）

    2026-10-15 用户拍板（第六轮审计 P0-1）：「**不能把学生的总分发了，可以发名次**」
    —— 逐生**总分不进 payload**（payload 会整份发给 AI/第三方 LLM），只带名次类字段
    （`rank_dir` 方向排名、`rank_class` 班排、`move` 进退步 = 名次变化）。
    总分级统计（均分/分数段/及格率）由 `build_stats` 在**服务端本地**算，只外发聚合值。
    """
    card = {'no': r.student_no, 'name': r.student_name, 'class': r.class_name,
            'direction': r.direction, 'selection': r.subject_selection,
            'rank_dir': r.rank_dir, 'rank_class': r.rank_class,
            'move': r.move_rank}
    return card


def build_payload(user, exam):
    """组装本次+上次 原始数据 payload（权限收敛）
    返回 {'exam':…, 'prev':…|None, 'students': [...], 'scope_desc':…,
          'scope_students': n, 'restrict_subjects': […]}
    """
    nos, desc, restrict = scope_of(user, exam)
    prev = prev_exam_of(exam)

    def collect(ex):
        if ex is None:
            return None
        # 2026-10-14 第四轮审计修复（PERF-101）：原先每次追问都在这里重查全量
        # （本次 + 上次两场 ≈ 1.1 万行 ORM 实例，AI 对话每条追问都跑一次）。
        # 改为复用共享的 ExamData 只读缓存（900s TTL，导入/改分时会失效）。
        from app.modules.grades.services import stats_service as _stats
        rows = _stats.cached_exam_data(ex.id).rows
        # v1.19.0 修复：原先“总分行直接替换整张卡”，若总分行在科目行之后被处理，
        # 会把已收集的 subjects 整片覆盖掉（payload 里出现 subjects:{}）。
        # 改为两趟收集：先各自归集总分行卡与科目分，再合并。
        cards, subs = {}, {}
        for r in rows:
            if r.student_no not in nos:
                continue
            if r.subject == TOTAL_SUBJECT:
                cards[r.student_no] = _student_card(r)
            else:
                if restrict and r.subject not in restrict:
                    continue  # 任课教师只发本人任教科目列
                subs.setdefault(r.student_no, {})[r.subject] = r.score
        # P1 M-12：默认匿名化 —— 姓名/学号替换为序号，分数/名次/班级保留。
        # 用稳定排序保证同一场考试多次生成的序号一致（便于对照/复现）。
        anon = bool(current_app.config.get('AI_ANONYMIZE', True))
        students = []
        seq = 0
        ordered = sorted(cards.items(),
                         key=lambda kv: ((kv[1].get('class') or ''), kv[0]))
        for no, card in ordered:
            card['subjects'] = {k: v for k, v in (subs.get(no) or {}).items()
                                if v is not None}
            if anon:
                seq += 1
                card['no'] = f'S{seq:03d}'
                card['name'] = f'学生{seq:03d}'
            students.append(card)
        # 只有科目行、没有总分行的学生也补一张最小卡（不丢人）
        for no, sv in subs.items():
            if no in cards:
                continue
            sv = {k: v for k, v in sv.items() if v is not None}
            if sv:
                item = {'no': no, 'subjects': sv}
                if anon:
                    seq += 1
                    item['no'] = f'S{seq:03d}'
                    item['name'] = f'学生{seq:03d}'
                students.append(item)
        students.sort(key=lambda c: (c.get('class') or '', c.get('no') or ''))
        return {'id': ex.id, 'name': ex.name, 'grade': ex.grade,
                'date': ex.exam_date.strftime('%Y-%m-%d'),
                'students': students}

    return {
        'exam': collect(exam),
        'prev': collect(prev),
        'scope_desc': desc,
        'scope_students': len(nos),
        'restrict_subjects': restrict,
    }


# ==================== Key 管理与选择 ====================

def decrypt_key(enc):
    """解密 API Key 并做健全性检查

    注意：decrypt 在解密失败时会静默把密文当明文返回，若直接拿去调用必然 401，
    且报错「Key 无效」会误导用户（实际是本地密钥/密文问题）。这里显式校验并报错。
    """
    plain = decrypt(enc)
    if not plain:
        return ''
    ok, msg = ai_providers.validate_api_key(plain)
    if not ok:
        raise ValueError('本地保存的 API Key 无法正确解密（%s）。'
                         '可能是 data/.encryption_key 丢失或被更换，请重新填写 API Key。' % msg)
    return plain


def mask_key(enc):
    """掩码回显：sk-****后4位；解密异常时返回空（不暴露密文）"""
    try:
        plain = decrypt(enc)
    except Exception:
        return ''
    if not plain:
        return ''
    ok, _ = ai_providers.validate_api_key(plain)
    if not ok:
        return '（已失效，请重新填写）'
    if len(plain) <= 8:
        return '****'
    return plain[:3] + '****' + plain[-4:]


def resolve_key(user, client_cfg=None):
    """选择调用配置，优先级：**浏览器本地 Key** → 个人（服务器存量）→ 全局

    cfg: {'api_key','base_url','model','source','provider','provider_name'}

    client_cfg：由前端从 **localStorage** 随请求带上，形如
        {'api_key','base_url','model','provider'}
    用户要求：**私人 Key 只存浏览器本地，服务器不留存** —— 这里仅作当次调用使用，
    不写入数据库、不记日志（且支持任意标准 OpenAI 兼容地址：base_url + model 自定义）。
    """
    cc = client_cfg or {}
    ckey = (cc.get('api_key') or '').strip()
    if ckey:
        provider = (cc.get('provider') or 'custom').strip() or 'custom'
        out = {'api_key': ckey, 'provider': provider,
               'provider_name': ai_providers.get_provider(provider)['name'],
               'base_url': ai_providers.resolve_base_url(
                   provider, (cc.get('base_url') or '').strip()),
               'model': ai_providers.resolve_model(
                   provider, (cc.get('model') or '').strip()),
               'source': 'local'}      # 本地 Key：不落库
        # v1.19.0 本机可选参数（不填 = 不传，由服务商决定）：
        #   max_tokens=输出上限  reasoning_effort=思考强度(low/medium/high)  max_input=输入上限
        for _k in ('max_tokens', 'max_input', 'reasoning_effort'):
            _v = cc.get(_k)
            if _v not in (None, '', 0, '0'):
                out[_k] = _v
        return out
    k = AiKey.query.filter_by(user_id=user.id).first()
    if k and k.api_key_enc:
        provider = k.provider or ai_providers.DEFAULT_PROVIDER
        return {'api_key': decrypt_key(k.api_key_enc), 'provider': provider,
                'provider_name': ai_providers.get_provider(provider)['name'],
                'base_url': ai_providers.resolve_base_url(provider, k.base_url),
                'model': ai_providers.resolve_model(provider, k.model),
                'source': 'personal'}
    g = AiGlobalKey.query.get(1)
    if g and g.api_key_enc:
        provider = g.provider or ai_providers.DEFAULT_PROVIDER
        return {'api_key': decrypt_key(g.api_key_enc), 'provider': provider,
                'provider_name': ai_providers.get_provider(provider)['name'],
                'base_url': ai_providers.resolve_base_url(provider, g.base_url),
                'model': ai_providers.resolve_model(provider, g.model),
                'source': 'global'}
    return None


# ==================== LLM 转发（OpenAI 兼容；可被测试 mock） ====================

# 思考强度：仅接受 OpenAI 兼容的标准三档（空 = 不传，按服务商默认）
REASONING_EFFORTS = ('low', 'medium', 'high')
# 输入预算估算：中文约 1.6 字符/token（保守估），英文/数字约 4 字符/token
_CHARS_PER_TOKEN = 1.6


def _cap_prompt(messages, max_input):
    """按用户设置的「输入上限」守护提示词

    超过时**成比例截断最长的一条内容**并附上说明标记（而不是报错阻断）：
    长文本主要是成绩/名册等结构化数据，截尾还能用；报错则完全用不了。
    """
    try:
        limit = int(max_input or 0)
    except (TypeError, ValueError):
        limit = 0
    if limit <= 0 or not messages:
        return messages
    total_chars = sum(len(str(m.get('content') or '')) for m in messages)
    est_tokens = int(total_chars / _CHARS_PER_TOKEN)
    if est_tokens <= limit:
        return messages
    # 成比例缩到 92%，留出余量
    keep = int(total_chars * limit * 0.92 / est_tokens)
    out, budget = [], max(keep, 400)
    for m in sorted(messages, key=lambda x: -len(str(x.get('content') or ''))):
        c = str(m.get('content') or '')
        take = c[:budget]
        if take != c:
            take += '\n\n[提示：内容超出你设置的输入上限，已截断；如需完整数据请调高输入上限或缩小分析范围]'
        out.append(dict(m, content=take))
        budget -= len(take)
        if budget <= 0:
            break
    # 保持原有角色顺序关系（至少保留 system 与最后一条）
    for m in messages:
        if m.get('role') == 'system' and all(o.get('role') != 'system' for o in out):
            out.insert(0, m)
    return out


def _send_once(url, cfg, body, timeout):
    """发一次请求；返回 (ok, payload_or_msg, 秒, http_code)"""
    data = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(
        url, data=data, method='POST',
        headers={'Content-Type': 'application/json',
                 'Authorization': 'Bearer ' + (cfg.get('api_key') or '')})
    started = time.time()
    try:
        with _safe_urlopen(req, timeout=timeout or DEFAULT_TIMEOUT) as resp:
            return True, json.loads(resp.read().decode('utf-8', 'replace')), \
                round(time.time() - started, 2), 200
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode('utf-8', 'replace')[:200]
        except Exception:
            detail = ''
        return False, _http_error_text(e.code, detail, cfg), \
            round(time.time() - started, 2), e.code
    except urllib.error.URLError:
        return False, ('无法连接 AI 服务商（网络异常或超时），请检查服务器出网、'
                       '接口地址是否正确'), round(time.time() - started, 2), 0
    except Exception as e:
        return False, f'调用失败：{e}', round(time.time() - started, 2), 0


def _chat_completions(cfg, messages, timeout=None, max_tokens=None, temperature=None,
                      tools=None, tool_choice=None):
    """统一的 OpenAI 兼容 chat/completions 调用，返回 (ok, payload_or_error, 秒)

    payload 为厂商原始 JSON；调用方按需取用。
    v1.19.0：用户在“用户设置”里填的 **输出上限 / 思考强度 / 输入上限** 优先于代码默认值；
    三者都不填时完全不传，由服务商自行决定（与以前行为一致）。
    2026-10-13：新增 tools / tool_choice（OpenAI 兼容 function calling），
    供成绩汇报区「AI 一句话分析」的 agent 循环使用；不传则请求体与以前完全一致。
    """
    if timeout is None:
        timeout = _request_timeout()
    provider = cfg.get('provider') or ai_providers.DEFAULT_PROVIDER
    base = (cfg.get('base_url') or ai_providers.resolve_base_url(provider)).rstrip('/')
    if not base:
        return False, '未配置接口地址（服务商为「自定义」时必须填写接口地址）', 0
    url = base + '/chat/completions'
    # H-9：出站前统一校验（域名白名单 + DNS 解析后拦私网/环回/链路本地/云元数据）
    try:
        assert_outbound_url_allowed(url)
    except ValueError as e:
        return False, str(e), 0
    # 用户本机设置优先：输出上限 / 思考强度 / 输入上限（三者都不填 = 交给服务商默认）
    mt = cfg.get('max_tokens') or max_tokens
    effort = (cfg.get('reasoning_effort') or '').strip().lower()
    messages = _cap_prompt(messages, cfg.get('max_input'))
    body = {'model': ai_providers.resolve_model(provider, cfg.get('model')),
            'messages': messages,
            'temperature': DEFAULT_TEMPERATURE if temperature is None else temperature,
            'stream': False}
    if mt:
        try:
            body['max_tokens'] = int(mt)
        except (TypeError, ValueError):
            if max_tokens:
                body['max_tokens'] = max_tokens
    if effort in REASONING_EFFORTS:
        body['reasoning_effort'] = effort
    if tools:
        body['tools'] = tools
        if tool_choice:
            body['tool_choice'] = tool_choice

    ok, out, sec, code = _send_once(url, cfg, body, timeout)
    if (not ok) and effort and code == 400:
        # 部分服务商/模型不认 reasoning_effort → 去掉后重试一次，
        # 不因一个可选设置项把整个 AI 功能卡死
        body.pop('reasoning_effort', None)
        ok, out, sec, code = _send_once(url, cfg, body, timeout)
    return ok, out, sec


# v1.19.0 截图导入：图片 → 结构化数据（OpenAI 兼容的多模态 vision 调用）
VISION_PROMPT_AFFAIR = (
    '这是一张考场安排表（可能是推荐名单/纸质表格的照片或截图）。'
    '请把表格内容读出来，只输出一个 JSON（不要任何解释）：\n'
    '{"students":[{"name":"姓名","class_name":"班级","room_no":"考场号","seat_no":1}],'
    '"rooms":[{"room_no":"考场号","location":"位置","capacity":30,"subject":"选科"}],'
    '"notes":"看不清或需要提醒的地方"}\n'
    '规则：看不清的字段留空字符串或 null，不要猜测；seat_no 必须是整数。'
)
VISION_PROMPT_BANDS = (
    '这是一张考试成绩分数线表（截图或照片）。请读出分数线，只输出一个 JSON（不要任何解释）：\n'
    '{"bands":[{"layer":"层级名（如特控线/本科线）","direction":"物理|历史|空",'
    '"subject":"科目名或空表示总分","score":123.5}],"notes":"提醒"}\n'
    '规则：层级名原样保留；每个层级至少给出总分线（subject 留空）；看不清的行不要输出。'
)


def chat_with_image(cfg, prompt, image_bytes, mime='image/png',
                    timeout=None, max_tokens=None):
    """把图片作为**多模态消息**发给 OpenAI 兼容接口，返回 (ok, 文本或错误, 秒)

    H-9 口径不变：仍走出站白名单与 DNS 校验（均在 _chat_completions 内），
    Key 依旧只由调用方传入、不落库。图片以 data URI 内联（base64），
    避免再引入对象存储；单张控制在调用方限流（建议 ≤ 4MB）。
    """
    import base64
    b64 = base64.b64encode(image_bytes).decode('ascii')
    messages = [{'role': 'user', 'content': [
        {'type': 'text', 'text': prompt},
        {'type': 'image_url',
         'image_url': {'url': 'data:%s;base64,%s' % (mime or 'image/png', b64)}},
    ]}]
    ok, payload, sec = _chat_completions(cfg, messages, timeout=timeout,
                                        max_tokens=max_tokens)
    if not ok:
        return False, payload, sec
    try:
        text = payload['choices'][0]['message']['content']
    except (KeyError, IndexError, TypeError):
        return False, '服务商返回内容无法解析（可能不支持图片输入）', sec
    return True, text, sec


def _http_error_text(code, detail, cfg):
    """把厂商 HTTP 错误码翻译为中文化提示"""
    provider_name = cfg.get('provider_name') or ai_providers.get_provider(
        cfg.get('provider'))['name']
    model = ai_providers.resolve_model(cfg.get('provider'), cfg.get('model'))
    if code in (401, 403):
        return (f'{provider_name} 拒绝鉴权（HTTP {code}）：API Key 无效、已过期，'
                f'或该 Key 无 {model} 的访问权限')
    if code in (402, 429):
        return f'{provider_name}：账户余额不足或请求过于频繁（HTTP {code}），请检查额度'
    if code == 404:
        return (f'{provider_name} 未找到该模型或接口（HTTP 404）：请检查接口地址与模型名'
                f'（当前 {model}）')
    if code == 400:
        return f'{provider_name} 认为请求参数有误（HTTP 400）：请检查模型名 {model} 是否正确'
    if code >= 500:
        return f'{provider_name} 服务端异常（HTTP {code}），请稍后重试'
    return f'{provider_name} 返回错误（HTTP {code}）{detail}'


def call_llm(cfg, messages):
    """生成分析：调用 chat/completions，返回 (ok, text_or_error)"""
    ok, payload, _ = _chat_completions(cfg, messages)
    if not ok:
        return False, payload
    text = ((payload.get('choices') or [{}])[0]
            .get('message', {}).get('content'))
    if not text:
        return False, '接口返回异常（无内容），请稍后重试'
    return True, text


def test_connection(cfg):
    """测试 Key 连通性：极小请求（不发送任何成绩数据，不生成报告）

    返回 (ok, message, 秒)
    """
    provider_name = cfg.get('provider_name') or ai_providers.get_provider(
        cfg.get('provider'))['name']
    model = ai_providers.resolve_model(cfg.get('provider'), cfg.get('model'))
    ok, payload, cost = _chat_completions(
        cfg, [{'role': 'user', 'content': 'ping'}],
        timeout=min(DEFAULT_TIMEOUT, 30), max_tokens=8)
    if not ok:
        return False, payload, cost
    # 部分厂商不返回内容也算连通
    return True, (f'{provider_name} 连接成功：模型 {model} 可用（耗时 {cost}s）'), cost


# ==================== 本地统计（图表数据，不依赖模型，保证准确） ====================

def _mean(values):
    vals = [v for v in values if isinstance(v, (int, float))]
    return round(sum(vals) / len(vals), 1) if vals else None


def _total_pass_rate(payload, totals):
    """总分及格率：**与成绩分析页/汇报区同一口径**（2026-10-17 修）。

    ⚠️ 原来读 `ex.subject_lines(总分)['pass']`——那是「满分 × 60%」的**固定线**，
    而且不区分选科组合。成绩分析页/汇报区早就改成「**本场划线的最低层（及格位）**、
    **逐生按自己选科组合取线**」（见 stats_service.rate_layer_pair / rate_names_for），
    两边**不是同一个数**，前端却都标成「及格率」→ 页面之间互相矛盾
    （旧代码注释里已经点明了这个矛盾，但一直没修）。

    现改为与分析页一致；未划线/无成绩返回 None，前端显示「-」，不给误导性数字。
    """
    if not totals:
        return None
    exam_id = (payload.get('exam') or {}).get('id')
    if not exam_id:
        return None
    try:
        from app.modules.grades.services import stats_service as st
        data = st.cached_exam_data(int(exam_id))
        p_name, _g = st.rate_names_for(data, st.TOTAL_SUBJECT)
        if not p_name:
            return None
        hit = n = 0
        cache = {}
        for r in data.totals_of():
            sc = r.get('score')
            if sc is None:
                continue
            n += 1
            d = (r.get('direction') or '') if isinstance(r, dict) else (r.direction or '')
            sel = ((r.get('selection') or '') if isinstance(r, dict)
                   else (getattr(r, 'subject_selection', '') or ''))
            key = (d, sel)
            if key not in cache:
                cache[key] = st.rate_layer_pair(
                    data.band_list(d, st.TOTAL_SUBJECT, sel))[0]
            p_pair = cache[key]
            if p_pair and sc >= p_pair[1]:
                hit += 1
        return round(hit / n * 100, 1) if n else None
    except Exception:  # noqa: BLE001  统计口径不应让报告生成失败
        from flask import current_app
        current_app.logger.warning('AI 统计：总分及格率计算失败 exam_id=%s', exam_id,
                                   exc_info=True)
        return None


def _server_total_rows(exam_id):
    """服务端本地取该场逐生总分 → [(班级, 总分)]（复用共享 ExamData 缓存）

    ⚠️ 只用于**聚合统计**（均分/最高最低/分数段/及格率），**绝不进 payload 或提示词**：
    2026-10-15 用户拍板「不能把学生的总分发了，可以发名次」——逐生总分不入 payload，
    统计在本地算完只把聚合结果给 AI。
    """
    if not exam_id:
        return []
    try:
        from app.modules.grades.services import stats_service as _stats
        rows = _stats.cached_exam_data(int(exam_id)).rows
        return [(r.class_name or '未分班', r.score) for r in rows
                if r.subject == TOTAL_SUBJECT and isinstance(r.score, (int, float))]
    except Exception:  # noqa: BLE001  统计口径不应让报告生成失败
        from flask import current_app
        current_app.logger.warning('AI 统计：总分取数失败 exam_id=%s', exam_id)
        return []


def build_stats(payload):
    """由成绩 payload 计算图表数据与概览，返回可直给 ECharts 的结构

    charts:
      score_bands 总分分数段分布（柱）
      class_avg   各班总分均分（柱）
      subject_avg 各学科均分（柱）
      move_dist   进退步分布（饼）
      compare     本次 vs 上次 均分（双柱，无上次则空）
    summary: 参考人数/均分/最高/最低/班级数/及格率等

    2026-10-15：总分统计改为**服务端本地**取数（payload 已不含逐生总分，见 `_student_card`），
    外发的内容只有聚合值（均分/分数段人数/及格率…）。
    """
    exam = payload.get('exam') or {}
    prev = payload.get('prev')
    students = exam.get('students') or []

    _rows = _server_total_rows(exam.get('id'))
    totals = [s for _c, s in _rows]

    # 1) 分数段分布（按总分，段宽自适应为 50 分）
    bands = {}
    if totals:
        lo, hi = min(totals), max(totals)
        width = 50
        start = int(lo // width) * width
        end = int(hi // width) * width
        labels, values = [], []
        cur = start
        while cur <= end:
            n = sum(1 for t in totals if cur <= t < cur + width)
            labels.append(f'{cur}-{cur + width}')
            values.append(n)
            cur += width
        bands = {'labels': labels, 'values': values}

    # 2) 班级均分（服务端本地取数，payload 无逐生总分）
    by_class = {}
    for _cls, _s in _rows:
        by_class.setdefault(_cls, []).append(_s)
    class_avg = {'labels': sorted(by_class.keys()),
                 'values': [_mean(by_class[k]) for k in sorted(by_class.keys())]}

    # 3) 学科均分
    subj = {}
    for c in students:
        for k, v in (c.get('subjects') or {}).items():
            if isinstance(v, (int, float)):
                subj.setdefault(k, []).append(v)
    subject_avg = {'labels': sorted(subj.keys()),
                   'values': [_mean(subj[k]) for k in sorted(subj.keys())]}

    # 4) 进退步分布
    # v1.19.49.35 修复：move_rank = 本场方向排名 − 上一场方向排名，
    # 负值 = 名次数字变小 = 进步、正值 = 退步（与成绩单、个人查询同口径）。
    # 原实现把 ≥50 记成"大幅进步"、≤-50 记成"大幅退步"，饼图标签整体标反，此处纠正。
    moves = {'大幅进步(≤-50)': 0, '进步': 0, '持平': 0, '退步': 0, '大幅退步(≥50)': 0}
    for c in students:
        m = c.get('move')
        if not isinstance(m, (int, float)):
            continue
        if m <= -50:
            moves['大幅进步(≤-50)'] += 1
        elif m < 0:
            moves['进步'] += 1
        elif m == 0:
            moves['持平'] += 1
        elif m < 50:
            moves['退步'] += 1
        else:
            moves['大幅退步(≥50)'] += 1
    move_dist = {'labels': list(moves.keys()), 'values': list(moves.values())}

    # 5) 与上次考试均分对比
    compare = {'labels': [], 'values': []}
    prev_totals = []
    if prev:
        prev_totals = [s for _c, s in _server_total_rows(prev.get('id'))]
    if prev_totals:
        compare = {'labels': [prev.get('name') or '上次', exam.get('name') or '本次'],
                   'values': [_mean(prev_totals), _mean(totals)]}

    summary = {
        'students': len(students),
        'avg': _mean(totals),
        'max': max(totals) if totals else None,
        'min': min(totals) if totals else None,
        'classes': len(by_class),
        # 2026-10-12 质量审计 P2：原口径是 `t >= max(totals) * 0.6`（拍脑袋的"六成最高分"），
        # 与分析页/汇报区「及格率来自 exam.subject_lines()['pass']」**不是同一个数**，
        # 而前端 workbench_grades.js 把它标成「及格率」展示 → 两个口径互相矛盾。
        # 现改为读本场考试自己的总分行及格线；取不到（未划线/无数据）时返回 None，
        # 由前端显示「-」，不再给一个会误导的数字。
        'pass_rate': _total_pass_rate(payload, totals),
        'prev_avg': _mean(prev_totals) if prev_totals else None,
        'delta_avg': (round(_mean(totals) - _mean(prev_totals), 1)
                      if prev_totals and totals else None),
    }

    return {
        'score_bands': bands, 'class_avg': class_avg, 'subject_avg': subject_avg,
        'move_dist': move_dist, 'compare': compare, 'summary': summary,
    }


def stats_brief(stats):
    """给模型的精简文字版统计（图表已由前端渲染，模型只负责解读）"""
    s = stats.get('summary') or {}
    lines = [
        f"参考人数 {s.get('students')}，班级数 {s.get('classes')}",
        f"总分均分 {s.get('avg')}，最高 {s.get('max')}，最低 {s.get('min')}",
    ]
    if s.get('prev_avg') is not None:
        lines.append(f"上次均分 {s.get('prev_avg')}，变化 {s.get('delta_avg')}")
    ca = stats.get('class_avg') or {}
    if ca.get('labels'):
        pairs = ', '.join(f'{k}:{v}' for k, v in zip(ca['labels'], ca['values']))
        lines.append('各班均分：' + pairs)
    sa = stats.get('subject_avg') or {}
    if sa.get('labels'):
        pairs = ', '.join(f'{k}:{v}' for k, v in zip(sa['labels'], sa['values']))
        lines.append('各学科均分：' + pairs)
    md = stats.get('move_dist') or {}
    if md.get('labels'):
        pairs = ', '.join(f'{k}:{v}人' for k, v in zip(md['labels'], md['values'])
                          if v)
        lines.append('进退步分布：' + pairs)
    return '\n'.join(lines)


def build_messages(payload):
    """把 payload 转成对话消息（含本地统计，引导模型做图表解读而非抄表）"""
    stats = build_stats(payload)
    user_text = ('请分析以下考试成绩数据（JSON）。'
                 '"exam" 为本次考试，"prev" 为上一次同年级考试（可能为空）。'
                 '若 prev 为空，则本次为第一场已导入考试，不强行对比。\n\n'
                 '系统已根据数据生成图表（分数段分布、班级均分、学科均分、进退步分布、'
                 '与上次均分对比），请围绕这些图表进行解读与归因，'
                 '不要把成绩整表重抄，只在必要时给出关键明细小表。\n\n'
                 '【本地统计概览】\n' + stats_brief(stats) + '\n\n')
    user_text += json.dumps(payload, ensure_ascii=False, indent=1)
    return [{'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': user_text}]


# ==================== 流式输出（思维链 + 正文增量） ====================

def stream_chat(cfg, messages):
    """流式调用，yield 事件 dict

    事件类型：
      {'type': 'stage',    'text': '已连接服务商，正在生成…'}
      {'type': 'reasoning','text': …}   思维链（部分模型返回 reasoning_content）
      {'type': 'delta',    'text': …}   正文增量
      {'type': 'error',    'text': …}
      {'type': 'done'}
    """
    provider = cfg.get('provider') or ai_providers.DEFAULT_PROVIDER
    base = (cfg.get('base_url') or ai_providers.resolve_base_url(provider)).rstrip('/')
    if not base:
        yield {'type': 'error', 'text': '未配置接口地址（服务商为「自定义」时必须填写接口地址）'}
        return
    url = base + '/chat/completions'
    # H-9：与 _chat_completions 同一道守卫（流式生成同样会把 Key 与成绩数据发往该地址）
    try:
        assert_outbound_url_allowed(url)
    except ValueError as e:
        yield {'type': 'error', 'text': str(e)}
        return
    body = {'model': ai_providers.resolve_model(provider, cfg.get('model')),
            'messages': messages, 'temperature': DEFAULT_TEMPERATURE,
            'stream': True}
    data = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(
        url, data=data, method='POST',
        headers={'Content-Type': 'application/json',
                 'Authorization': 'Bearer ' + (cfg.get('api_key') or ''),
                 'Accept': 'text/event-stream'})

    try:
        with _safe_urlopen(req, timeout=_request_timeout()) as resp:
            yield {'type': 'stage', 'text': '已连接服务商，正在生成…'}
            for raw in resp:
                line = raw.decode('utf-8', 'replace').strip()
                if not line or not line.startswith('data:'):
                    continue
                payload_str = line[5:].strip()
                if payload_str == '[DONE]':
                    break
                try:
                    obj = json.loads(payload_str)
                except Exception:
                    continue
                choices = obj.get('choices') or []
                if not choices:
                    continue
                delta = choices[0].get('delta') or {}
                reasoning = (delta.get('reasoning_content')
                             or delta.get('reasoning') or '')
                if reasoning:
                    yield {'type': 'reasoning', 'text': reasoning}
                content = delta.get('content') or ''
                if content:
                    yield {'type': 'delta', 'text': content}
        yield {'type': 'done'}
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode('utf-8', 'replace')[:200]
        except Exception:
            detail = ''
        yield {'type': 'error', 'text': _http_error_text(e.code, detail, cfg)}
    except urllib.error.URLError:
        yield {'type': 'error',
               'text': '无法连接 AI 服务商（网络异常或超时），请检查服务器出网、接口地址是否正确'}
    except Exception as e:
        yield {'type': 'error', 'text': f'调用失败：{e}'}
