# StuLink v1.9.0 2026-09-05
# AI 分析服务：权限收敛取数（本次+上次原始成绩）→ payload 组装 → LLM 转发
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import json
import urllib.error
import urllib.request

from sqlalchemy import or_, and_

from app.extensions import db
from app.models.grades import Exam, ExamScore, AiKey, AiGlobalKey, SUBJECTS, TOTAL_SUBJECT
from app.modules.grades.services import scope as scope_service
from app.utils.crypto import encrypt_rand, decrypt

DEEPSEEK_BASE = 'https://api.deepseek.com'
DEFAULT_MODEL = 'deepseek-chat'

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

def mask_key(enc):
    """掩码回显：sk-****后4位；无则空"""
    try:
        plain = decrypt(enc)
    except Exception:
        return ''
    if not plain:
        return ''
    if len(plain) <= 8:
        return '****'
    return plain[:3] + '****' + plain[-4:]


def resolve_key(user):
    """选择调用配置：个人 Key 优先，其次全局；返回 (cfg_dict) 或 None
    cfg: {'api_key','base_url','model','source','provider'}
    """
    k = AiKey.query.filter_by(user_id=user.id).first()
    if k and k.api_key_enc:
        return {'api_key': decrypt(k.api_key_enc), 'provider': k.provider or 'deepseek',
                'base_url': (k.base_url or '').strip(), 'model': k.model or DEFAULT_MODEL,
                'source': 'personal'}
    g = AiGlobalKey.query.get(1)
    if g and g.api_key_enc:
        return {'api_key': decrypt(g.api_key_enc), 'provider': g.provider or 'deepseek',
                'base_url': (g.base_url or '').strip(), 'model': g.model or DEFAULT_MODEL,
                'source': 'global'}
    return None


# ==================== LLM 转发（OpenAI 兼容；可被测试 mock） ====================

def call_llm(cfg, messages):
    """调用 chat/completions；返回 (ok, text_or_error)
    错误信息中文化：401=Key无效 429=频率/额度 超时/网络=网络异常
    """
    base = (cfg['base_url'] or DEEPSEEK_BASE).rstrip('/')
    url = base + '/chat/completions'
    body = json.dumps({'model': cfg['model'] or DEFAULT_MODEL,
                       'messages': messages, 'temperature': 0.4,
                       'stream': False}).encode()
    req = urllib.request.Request(url, data=body, method='POST',
                                 headers={'Content-Type': 'application/json',
                                          'Authorization': 'Bearer ' + cfg['api_key']})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode('utf-8', 'replace'))
        text = (data.get('choices') or [{}])[0].get('message', {}).get('content')
        if not text:
            return False, '接口返回异常（无内容），请稍后重试'
        return True, text
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return False, 'API Key 无效或已过期，请检查配置'
        if e.code in (402, 429):
            return False, '账户余额不足或请求过于频繁（HTTP %s），请稍后或检查额度' % e.code
        try:
            detail = e.read().decode('utf-8', 'replace')[:200]
        except Exception:
            detail = ''
        return False, f'服务商返回错误（HTTP {e.code}）{detail}'
    except urllib.error.URLError:
        return False, '无法连接 AI 服务商（网络异常），请检查服务器出网或 base_url 配置'
    except Exception as e:
        return False, f'调用失败：{e}'


def build_messages(payload):
    """把 payload 转成对话消息"""
    user_text = ('请分析以下考试成绩数据（JSON）。'
                 '"exam" 为本次考试，"prev" 为上一次同年级考试（可能为空）。'
                 '若 prev 为空，则本次为第一场已导入考试，不强行对比。\n\n')
    user_text += json.dumps(payload, ensure_ascii=False, indent=1)
    return [{'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': user_text}]
