# StuLink v1.19.70.64 2026-10-14
# AI 成绩分析「提示词生成器」：选项（权限收窄）/ 拼装 / 校验 / 模板
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""AI 成绩分析「提示词生成器」（2026-10-14）。

老师不用自己措辞：界面按「分析范围（年级 / 班级 / 学科）→ 分析侧重 → 输出格式 →
附加要求」四个维度点选，本模块把这些选项**拼装成一段可直接发给 AI 的提示词**，
并做两道校验：

  ① 真实性：班级 / 学科必须是这场考试成绩里真实存在的值（防手输乱值让模型白跑）；
  ② 权限：选项必须在 `score_guard` 可见范围内（越权直接报错，不给模型留"试探"空间）。

口径（与既有 AI 分析一致，不要另立）：
  · 拼出的提示词就是 `AiTask.question`（最终提问）——数据仍由模型调用只读工具取，
    每次工具调用都过 `score_guard`，所以"范围选得比权限大"不会造成越权，只会被工具拒绝；
  · 输出格式选项全是 HTML 变体：渲染链路只认 HTML 片段（见 `ai_agent.AGENT_SYSTEM_PROMPT`），
    这里调的是篇幅与结构，不改变产物类型；
  · 班级口径 = 本场考试**总分行**的班级（考试当时班级，与成绩分析页一致）。
"""
import json
from datetime import datetime

from app.extensions import db
from app.models.academic import SUBJECT_ORDER
from app.models.grades import AiPromptTemplate, ExamScore, TOTAL_SUBJECT
from app.modules.grades.services import scope as scope_service
from app.modules.grades.services import score_guard
from app.modules.grades.utils import numeric_classes

MAX_CLASSES = 40          # 班级多选上限（再多就失去"范围收窄"的意义）
MAX_SUBJECTS = 20
MAX_EXTRA = 500           # 附加要求上限（最终 prompt 落 AiTask.question，上限 2000）
MAX_TEMPLATE_NAME = 30
DEFAULT_FOCUS = 'overall'
DEFAULT_OUTPUT = 'html'

# 分析侧重（唯一维护点：前端下拉由 options() 下发，服务端拼装也读这里）
FOCUS_OPTIONS = (
    ('overall', '整体分析：年级全景 + 班级横向对比',
     '整体分析：先看年级整体（参考人数、各科均分/及格率/优秀率、分层上线情况），'
     '再做班级横向对比，指出最强与最弱的班，并说明差距主要来自哪些学科。'),
    ('class_diff', '班级差异：各班强弱与差距来源',
     '班级差异分析：重点横向对比各班（总分与单科的去差均分、分层上线率、分数段分布），'
     '指出差距最大与最小的学科组合，列出值得关注的班级（只陈述客观数据）。'),
    ('subject_weak', '学科强弱：偏科与短板学科',
     '学科分析：重点看各学科整体表现与偏科情况（各科均分/及格率/优秀率、学科在班间的差异），'
     '指出明显偏强/偏弱的学科及其涉及的主要班级。'),
    ('layers', '分层上线：各层线上线人数与比率',
     '分层上线分析：重点统计各班各层线（如特控线/本科线）的上线人数与上线率（单/双上线口径），'
     '说明上线结构；该场考试未划线时如实说明无法统计，不要用别的口径顶替。'),
    ('segments', '分数段分布：各档人数与集中度',
     '分数段分布分析：重点看总分（或名次）各档人数与班级分布，说明分数集中度与尾部情况。'),
    ('near_line', '临界生：线上下学生名单与分布',
     '临界生分析：重点找总分线上下一定范围内的学生（班级、姓名、总分、距线分数、各科），'
     '按班汇总人数并指出临界生最多的班；名单只列前若干人并注明"仅列前 N 人"。'),
    ('trend', '进退步：与上一次考试对比',
     '进退步对比：重点做与上一次考试的对比（班级/学科层面的均分与分层上线变化、'
     '进步最大与退步最大的班级）；没有上一次考试数据时如实说明。'),
)

# 输出格式（全是 HTML 变体：渲染链路只认 HTML 片段，这里调篇幅与结构）
OUTPUT_OPTIONS = (
    ('html', 'HTML 报告：标准结构（结论 + 表格 + 关注名单）',
     '标准 HTML 报告：结论摘要（3~5 条要点）→ 数据支撑（表格）→ 需要关注的班级/学生'
     '（只陈述客观事实）→ 数据口径与限制说明。'),
    ('brief', '简要小结：约 300 字要点式',
     '简要小结：只给 3~6 条要点 + 一张关键表格，正文约 300 字的内容量（仍用 HTML 片段输出）。'),
    ('detailed', '详细报告：分节展开 + 多表格',
     '详细报告：分节展开、可含多张表格（每张不超过 15 行），补充班级/学科细看与'
     '临界生名单（名单只列前 N 人并注明）。'),
)

# 附加要求常用预设（前端做 chips，点选后追加到文本框，仍可手动编辑）
EXTRA_PRESETS = (
    '只陈述客观数据，不做人格化评价',
    '与上一次考试对比，说明进退步',
    '给出下一步需要重点关注的班级与建议（只针对数据本身）',
    '表格精简，每张不超过 10 行',
)

_FOCUS_MAP = {v: (lb, text) for v, lb, text in FOCUS_OPTIONS}
_OUTPUT_MAP = {v: (lb, text) for v, lb, text in OUTPUT_OPTIONS}


# ==================== 规范化 ====================

def _str_list(raw, key, limit, each=10):
    """取字符串列表：去空、去重、限长、限个数。"""
    val = raw.get(key)
    out, seen = [], set()
    if isinstance(val, list):
        for item in val:
            s = str(item or '').strip()[:each]
            if s and s not in seen:
                seen.add(s)
                out.append(s)
            if len(out) >= limit:
                break
    return out


def normalize_gen(raw):
    """清洗前端传来的生成器选项（字段白名单 + 长度 + 去重）。"""
    raw = raw if isinstance(raw, dict) else {}
    return {
        'grades': _str_list(raw, 'grades', 3, 10),
        'classes': _str_list(raw, 'classes', MAX_CLASSES),
        'subjects': _str_list(raw, 'subjects', MAX_SUBJECTS),
        'focus': str(raw.get('focus') or '').strip()[:30] or DEFAULT_FOCUS,
        'output': str(raw.get('output') or '').strip()[:30] or DEFAULT_OUTPUT,
        'extra': str(raw.get('extra') or '').strip()[:MAX_EXTRA],
    }


# ==================== 选项（按权限收窄） ====================

def _exam_classes(exam):
    """本场考试的班级＝成绩（考试当时班级）∪ 任课快照（ExamTeacherLink）。

    与成绩分析页的班级口径同源（`routes/analysis.py::_classes_by_exam`）：
    这样"已排考务/任课核对、但还没导入成绩"的考试也能在生成器里选到班级，
    而不是显示"没有可选项"。班级仍只来自本场考试自己的数据，不用当前花名册。
    """
    names = set()
    # 2026-10-14 第四轮审计（PERF-112 同口径）：班级取**总分行**（考试当时班级，
    # 全库唯一权威口径），不能按科目行的导入时班级枚举（选科分班后会漂移）。
    for (cn,) in (db.session.query(ExamScore.class_name)
                  .filter(ExamScore.exam_id == exam.id,
                          ExamScore.subject == TOTAL_SUBJECT)
                  .group_by(ExamScore.class_name).all()):
        if cn:
            names.add(cn)
    try:
        from app.models.grades import ExamTeacherLink
        for (cn,) in (db.session.query(ExamTeacherLink.class_name)
                      .filter(ExamTeacherLink.exam_id == exam.id)
                      .group_by(ExamTeacherLink.class_name).all()):
            if cn:
                names.add(cn)
    except Exception:                          # noqa: BLE001  旧库无任课快照表时忽略
        pass
    return numeric_classes(list(names))


def _exam_subjects(exam):
    """本场考试实际有成绩的学科（排除总分），按学科习惯顺序排。"""
    rows = (db.session.query(ExamScore.subject)
            .filter(ExamScore.exam_id == exam.id).group_by(ExamScore.subject).all())
    subs = {str(r[0]).strip() for r in rows if r[0] and str(r[0]).strip() != TOTAL_SUBJECT}
    order = {s: i for i, s in enumerate(SUBJECT_ORDER)}
    order.setdefault('外语', order.get('英语', 2))
    return sorted(subs, key=lambda s: (order.get(s, 999), s))


def options(user, exam):
    """生成器选项：年级 / 班级 / 学科 + 侧重 / 输出 / 附加要求预设（全部按权限收窄）。"""
    scope = score_guard.resolve(user)
    grades = list(scope_service.visible_grades(user) or [])
    if exam.grade and exam.grade not in grades:
        grades = [exam.grade] + grades            # 兜底：考试可见但不在年级下拉（理论不发生）

    classes = _exam_classes(exam)
    vis_c = scope.visible_classes(exam.grade)     # None = 该年级全部班级
    if vis_c is not None:
        classes = [c for c in classes if c in vis_c]

    subjects = _exam_subjects(exam)
    vis_s = scope.grade_subjects(exam.grade)      # None = 全部学科
    if vis_s is not None:
        subjects = [s for s in subjects if s in vis_s]

    return {
        'exam': {'id': exam.id, 'name': exam.name, 'grade': exam.grade or '',
                 'date': exam.exam_date.strftime('%Y-%m-%d') if exam.exam_date else ''},
        'exam_grade': exam.grade or '',
        'grades': grades,
        'classes': classes,
        'subjects': subjects,
        'focus': [{'value': v, 'label': lb} for v, lb, _ in FOCUS_OPTIONS],
        'output': [{'value': v, 'label': lb} for v, lb, _ in OUTPUT_OPTIONS],
        'default_focus': DEFAULT_FOCUS,
        'default_output': DEFAULT_OUTPUT,
        'extra_presets': list(EXTRA_PRESETS),
        'limits': {'max_classes': MAX_CLASSES, 'max_subjects': MAX_SUBJECTS,
                   'max_extra': MAX_EXTRA},
    }


# ==================== 校验 + 拼装 ====================

def _check(user, exam, gen, classes, subjects):
    """真实性 + 权限双校验；越权/非法抛 ValueError（路由转 400）。"""
    scope = score_guard.resolve(user)

    for g in gen['grades']:
        if g != exam.grade and not scope.can_view_grade(g):
            raise ValueError(f'年级「{g}」不在你的成绩可见范围内')

    bad = [c for c in gen['classes'] if c not in classes]
    if bad:
        raise ValueError('班级「%s」不在本场考试的成绩数据中' % '、'.join(bad))
    bad = [c for c in gen['classes'] if not scope.can_view_class(exam.grade, c)]
    if bad:
        raise ValueError('班级「%s」不在你的成绩可见范围内' % '、'.join(bad))

    bad = [s for s in gen['subjects'] if s not in subjects]
    if bad:
        raise ValueError('学科「%s」不在本场考试的成绩数据中' % '、'.join(bad))
    vis_s = scope.grade_subjects(exam.grade)
    if vis_s is not None:
        bad = [s for s in gen['subjects'] if s not in vis_s]
        if bad:
            raise ValueError('学科「%s」不在你的成绩可见范围内' % '、'.join(bad))

    if gen['focus'] not in _FOCUS_MAP:
        raise ValueError('分析侧重选项不认识（%s），请刷新页面后重试' % gen['focus'])
    if gen['output'] not in _OUTPUT_MAP:
        raise ValueError('输出格式选项不认识（%s），请刷新页面后重试' % gen['output'])


def _scope_line(exam, gen):
    """把范围选项写成人话（含"跨年级"提示，避免模型在错误的考试里找数据）。"""
    if not gen['grades'] or gen['grades'] == [exam.grade]:
        grade_part = '本场考试所属年级（%s）' % (exam.grade or '未知')
    else:
        grade_part = ('%s（注意：页面当前选中的考试属于 %s；如需分析 %s 的考试，'
                      '请先用 list_exams 找到对应考试再取数）'
                      % ('、'.join(gen['grades']), exam.grade or '未知',
                         '、'.join(gen['grades'])))
    class_part = ('仅统计以下班级：%s' % '、'.join(gen['classes'])
                  if gen['classes'] else '全部班级')
    subject_part = ('仅统计以下学科：%s' % '、'.join(gen['subjects'])
                    if gen['subjects'] else '全部学科')
    return '年级：%s；班级：%s；学科：%s' % (grade_part, class_part, subject_part)


def _compose(exam, gen):
    """拼装最终提示词（结构与 AGENT_SYSTEM_PROMPT 的 HTML 规范互补，不冲突）。"""
    exam_line = '%s（%s%s）' % (
        exam.name, exam.grade or '未知年级',
        '，' + exam.exam_date.strftime('%Y-%m-%d') if exam.exam_date else '')
    lines = [
        '请基于本场考试的真实成绩数据，完成一次学业成绩分析报告。',
        '',
        '【考试】%s' % exam_line,
        '【分析范围】%s' % _scope_line(exam, gen),
        '【分析重点】%s' % _FOCUS_MAP[gen['focus']][1],
        '【输出要求】%s' % _OUTPUT_MAP[gen['output']][1],
    ]
    if gen['extra']:
        lines.append('【附加要求】%s' % gen['extra'])
    lines += [
        '',
        '【数据纪律】报告中的每个数字都必须来自工具查询结果，禁止编造、禁止凭印象推测；',
        '工具报「权限不足 / 无数据」时如实说明，不要用别的口径顶替。',
        '开始前先调用 exam_overview 确认数据完整性；班级或学科名称拿不准时先调用 list_classes 核对；',
        '需要与上一次考试对比时，先确认上一场考试存在再下结论。',
    ]
    return '\n'.join(lines)


def build(user, exam, raw_gen):
    """校验并拼装提示词；返回 {'prompt', 'scope_text', 'gen'}；非法抛 ValueError。"""
    gen = normalize_gen(raw_gen)
    classes = _exam_classes(exam)
    subjects = _exam_subjects(exam)
    _check(user, exam, gen, classes, subjects)
    prompt = _compose(exam, gen)
    scope_text = '；'.join([
        '年级：%s' % ('、'.join(gen['grades']) if gen['grades'] else (exam.grade or '—')),
        '班级：%s' % ('、'.join(gen['classes']) if gen['classes'] else '全部'),
        '学科：%s' % ('、'.join(gen['subjects']) if gen['subjects'] else '全部'),
    ])
    return {'prompt': prompt, 'scope_text': scope_text, 'gen': gen}


# ==================== 模板（仅本人可见） ====================

def list_templates(user):
    rows = (AiPromptTemplate.query.filter_by(user_id=user.id)
            .order_by(AiPromptTemplate.updated_at.desc(), AiPromptTemplate.id.desc())
            .limit(50).all())
    return [{'id': r.id, 'name': r.name, 'payload': _loads(r.payload),
             'created_at': r.created_at.strftime('%Y-%m-%d %H:%M') if r.created_at else '',
             'updated_at': r.updated_at.strftime('%Y-%m-%d %H:%M') if r.updated_at else ''}
            for r in rows]


def _loads(text):
    try:
        data = json.loads(text) if text else {}
    except Exception:                              # noqa: BLE001  坏数据不影响列表
        data = {}
    return data if isinstance(data, dict) else {}


def save_template(user, data):
    """新建 / 按 id 更新（同用户）；返回模板行。name 必填、同用户同名视为覆盖。"""
    data = data if isinstance(data, dict) else {}
    name = str(data.get('name') or '').strip()[:MAX_TEMPLATE_NAME]
    if not name:
        raise ValueError('请给模板起个名字（便于下次套用）')
    gen = normalize_gen(data.get('gen') or data)
    payload = dict(gen)
    payload['prompt'] = str(data.get('prompt') or '').strip()[:2000]
    payload['saved_at'] = datetime.now().strftime('%Y-%m-%d %H:%M')
    text = json.dumps(payload, ensure_ascii=False)

    row = None
    tid = data.get('id')
    if tid:
        try:
            tid = int(tid)
        except (TypeError, ValueError):
            tid = 0
        if tid:
            row = AiPromptTemplate.query.filter_by(id=tid, user_id=user.id).first()
    if row is None:                                # 同名覆盖：避免越攒越多
        row = AiPromptTemplate.query.filter_by(user_id=user.id, name=name).first()
    if row is None:
        row = AiPromptTemplate(user_id=user.id, name=name, payload=text,
                               created_at=datetime.now(), updated_at=datetime.now())
        db.session.add(row)
    else:
        row.name = name
        row.payload = text
        row.updated_at = datetime.now()
    db.session.commit()
    return {'id': row.id, 'name': row.name, 'payload': _loads(row.payload)}


def delete_template(user, tid):
    row = AiPromptTemplate.query.filter_by(id=int(tid), user_id=user.id).first()
    if row is None:
        return False
    db.session.delete(row)
    db.session.commit()
    return True
