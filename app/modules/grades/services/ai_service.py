# StuLink v1.18.0.0 2026-09-23
# AI 分析服务：权限收敛取数（本次+上次原始成绩）→ payload 组装 → LLM 转发
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import json
import time
import urllib.error
import urllib.request

from sqlalchemy import or_, and_

from app.extensions import db
from app.models.grades import Exam, ExamScore, AiKey, AiGlobalKey, SUBJECTS, TOTAL_SUBJECT
from app.modules.grades.services import scope as scope_service
from app.modules.grades.services import ai_providers
from app.utils.crypto import encrypt_rand, decrypt

DEEPSEEK_BASE = ai_providers.PROVIDERS['deepseek']['base_url']
DEFAULT_MODEL = ai_providers.PROVIDERS['deepseek']['default_model']
DEFAULT_TEMPERATURE = 0.4
DEFAULT_TIMEOUT = 180

SYSTEM_PROMPT = (
    '你是一名严谨的中学考试成绩数据分析助手。你将收到一次（或两次）考试的 JSON 原始成绩数据'
    '（学号、姓名、班级、方向、选科、总分、方向内排名、班内排名、进退步、各科分数）。\n'
    '要求：\n'
    '1. 只依据提供的数据进行分析，禁止编造或推测数据中不存在的信息；数据缺失处明确说明。\n'
    '2. 输出中文 Markdown 报告，建议章节：总体情况（参考人数/均分/最高最低分）、'
    '两次考试对比（均分与排名变化、进退步学生分布）、各科表现（偏科与强弱科）、'
    '分数段与分层分布、班级对比（若数据含多班）、需要关注的个体（仅列学号/姓名等客观事实，'
    '不作人格化评价）。\n'
    '3. 保持客观，不评价教师与学校的教学，不给情绪化结论。\n'
    '4. 报告以“本报告由 AI 根据所提供成绩数据生成，仅供参考”开头。'
)

SYSTEM_PROMPT_CN = SYSTEM_PROMPT

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
    if user.has_role('grade_leader'):
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
    """总分行 → 学生卡（含科目分；任课教师仅返回任教科目）"""
    card = {'no': r.student_no, 'name': r.student_name, 'class': r.class_name,
            'direction': r.direction, 'selection': r.subject_selection,
            'total': r.score, 'rank_dir': r.rank_dir, 'rank_class': r.rank_class,
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
        rows = ExamScore.query.filter_by(exam_id=ex.id).all()
        stu_map = {}
        for r in rows:
            if r.student_no not in nos:
                continue
            if r.subject == TOTAL_SUBJECT:
                stu_map[r.student_no] = _student_card(r)
            else:
                if restrict and r.subject not in restrict:
                    continue  # 任课教师只发本人任教科目列
                card = stu_map.setdefault(r.student_no, {})
                card.setdefault('subjects', {})[r.subject] = r.score
        students = []
        for card in stu_map.values():
            card['subjects'] = sorted(
                (card.get('subjects') or {}).items())
            # 仅保留有分数的科目
            card['subjects'] = {k: v for k, v in card['subjects'] if v is not None}
            students.append(card)
        students.sort(key=lambda c: (c['class'] or '', c['no'] or ''))
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


def resolve_key(user):
    """选择调用配置：个人 Key 优先，其次全局；返回 cfg dict 或 None
    cfg: {'api_key','base_url','model','source','provider','provider_name'}
    """
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

def _chat_completions(cfg, messages, timeout=None, max_tokens=None, temperature=None):
    """统一的 OpenAI 兼容 chat/completions 调用，返回 (ok, payload_or_error, 秒)

    payload 为厂商原始 JSON；调用方按需取用。
    """
    provider = cfg.get('provider') or ai_providers.DEFAULT_PROVIDER
    base = (cfg.get('base_url') or ai_providers.resolve_base_url(provider)).rstrip('/')
    if not base:
        return False, '未配置接口地址（服务商为「自定义」时必须填写接口地址）', 0
    url = base + '/chat/completions'
    body = {'model': ai_providers.resolve_model(provider, cfg.get('model')),
            'messages': messages,
            'temperature': DEFAULT_TEMPERATURE if temperature is None else temperature,
            'stream': False}
    if max_tokens:
        body['max_tokens'] = max_tokens
    data = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(
        url, data=data, method='POST',
        headers={'Content-Type': 'application/json',
                 'Authorization': 'Bearer ' + (cfg.get('api_key') or '')})
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout or DEFAULT_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode('utf-8', 'replace'))
        return True, payload, round(time.time() - started, 2)
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode('utf-8', 'replace')[:200]
        except Exception:
            detail = ''
        return False, _http_error_text(e.code, detail, cfg), round(time.time() - started, 2)
    except urllib.error.URLError:
        return False, ('无法连接 AI 服务商（网络异常或超时），请检查服务器出网、'
                       '接口地址是否正确'), round(time.time() - started, 2)
    except Exception as e:
        return False, f'调用失败：{e}', round(time.time() - started, 2)


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


def build_stats(payload):
    """由成绩 payload 计算图表数据与概览，返回可直给 ECharts 的结构

    charts:
      score_bands 总分分数段分布（柱）
      class_avg   各班总分均分（柱）
      subject_avg 各学科均分（柱）
      move_dist   进退步分布（饼）
      compare     本次 vs 上次 均分（双柱，无上次则空）
    summary: 参考人数/均分/最高/最低/班级数/及格率等
    """
    exam = payload.get('exam') or {}
    prev = payload.get('prev')
    students = exam.get('students') or []

    totals = [c.get('total') for c in students]
    totals = [t for t in totals if isinstance(t, (int, float))]

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

    # 2) 班级均分
    by_class = {}
    for c in students:
        if isinstance(c.get('total'), (int, float)):
            by_class.setdefault(c.get('class') or '未分班', []).append(c['total'])
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
    moves = {'大幅进步(≥50)': 0, '进步': 0, '持平': 0, '退步': 0, '大幅退步(≤-50)': 0}
    for c in students:
        m = c.get('move')
        if not isinstance(m, (int, float)):
            continue
        if m >= 50:
            moves['大幅进步(≥50)'] += 1
        elif m > 0:
            moves['进步'] += 1
        elif m == 0:
            moves['持平'] += 1
        elif m > -50:
            moves['退步'] += 1
        else:
            moves['大幅退步(≤-50)'] += 1
    move_dist = {'labels': list(moves.keys()), 'values': list(moves.values())}

    # 5) 与上次考试均分对比
    compare = {'labels': [], 'values': []}
    prev_totals = []
    if prev:
        prev_totals = [c.get('total') for c in (prev.get('students') or [])
                       if isinstance(c.get('total'), (int, float))]
    if prev_totals:
        compare = {'labels': [prev.get('name') or '上次', exam.get('name') or '本次'],
                   'values': [_mean(prev_totals), _mean(totals)]}

    summary = {
        'students': len(students),
        'avg': _mean(totals),
        'max': max(totals) if totals else None,
        'min': min(totals) if totals else None,
        'classes': len(by_class),
        'pass_rate': (round(sum(1 for t in totals if t >= (max(totals) * 0.6 if totals else 0))
                            / len(totals) * 100, 1) if totals else None),
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
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
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
