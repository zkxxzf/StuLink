# StuLink v1.19.47.29 2026-10-13
# 成绩汇报区「AI 一句话分析」：只读成绩分析工具集（OpenAI 兼容 tool schema + 分发）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""Web 端只读成绩分析工具集（供 AI 一句话分析做 tool calling）。

设计口径（与 MCP 服务一致，AI 看到的数字 = 网页端看到的数字）：
  · **取数只复用既有服务**：stats_service / pivot_service / report_service /
    report_pivot_service / report_warning_service —— 与汇报区、分析页同一套
    `cached_exam_data` 缓存与口径，不另写统计逻辑。
  · **权限只走 score_guard**（app.modules.grades.services.score_guard，与 MCP 同源）：
    年级级（require_exam）→ 班级级（visible_classes / require_class）→ 学科级
    （class_subjects）→ 总分口径（class_total_visible：任课教师不给总分与排名）。
  · **全部只读**：本模块不提供任何写入口（不 insert/update/delete）。
  · 结果统一截断（行数 + 字符数），避免一次把整库灌进模型上下文。

对外接口：
  · `TOOLS`        —— OpenAI `tools=[…]` 参数所需的 JSON Schema 列表
  · `dispatch(name, args, user, default_exam_id=None)` —— 执行工具，返回 (ok, 文本)
"""
import json
import logging

from app.models.grades import Exam, TOTAL_SUBJECT, SUBJECTS, subject_display
from app.modules.grades.services import (pivot_service, report_pivot_service as rps,
                                         report_service as rs,
                                         report_warning_service as rws,
                                         score_guard, stats_service as st)

logger = logging.getLogger(__name__)

# ---- 结果体积上限（防一次把全库灌进上下文）----
MAX_ROWS = 60            # 单个工具的表格行上限
MAX_CHARS = 14000        # 单个工具返回的 JSON 字符上限
_CUT_KEYS = ('rows', 'students', 'trend', 'classes')   # 需要限行的列表键

# 总分口径的字段名：任课教师（只见所教学科）必须整体隐藏这些
_TOTAL_FIELDS = ('avg', 'std', 'max', 'min', 'score', 'total_score', 'rank_class',
                 'rank_dir', 'move', 'layers', 'pass_rate', 'prev_avg')

# 工具描述里的通用口径说明，避免每个工具重复
_NOTE = '（只读；数据范围与登录账号在网页端可见范围一致）'


# ==================== 通用 helper ====================

def _exam(user, exam_id, default_exam_id=None):
    """取考试并做年级级权限校验；返回 (exam, scope)。"""
    eid = int(exam_id or default_exam_id or 0)
    exam = Exam.query.get(eid) if eid else None
    if exam is None:
        raise ValueError(f'找不到考试（exam_id={eid or "空"}）：'
                         '可先调用 list_exams 查看账号可见的考试')
    scope = score_guard.require_exam(user, exam)
    return exam, scope


def _rows_for_scope(rows, scope, grade, key='class_name'):
    """按班级可见范围过滤表格行（visible_classes 为 None = 全部班）。"""
    visible = scope.visible_classes(grade)
    if visible is None:
        return rows
    return [r for r in rows if (r.get(key) or '') in visible]


def _hide_total(row, scope, grade, class_name):
    """不可见该班总分口径时，抹掉总分相关字段（任课教师只被授予单科）。"""
    if scope.class_total_visible(grade, class_name):
        return row
    for key in _TOTAL_FIELDS:
        row.pop(key, None)
    row['total_hidden'] = True
    return row


def _subject_filter(allowed, names):
    """按可见学科收窄字符串列表；allowed 为 None = 全部学科。"""
    if allowed is None:
        return list(names)
    return [n for n in names if n in allowed]


def _cut(data):
    """统一裁剪：先限行、再限总字符数，超限时附带说明。"""
    if isinstance(data, dict):
        for key in _CUT_KEYS:
            items = data.get(key)
            if isinstance(items, list) and len(items) > MAX_ROWS:
                data[key] = items[:MAX_ROWS]
                data[f'{key}_truncated'] = f'仅显示前 {MAX_ROWS} 行（共 {len(items)} 行）'
    text = json.dumps(data, ensure_ascii=False, default=str)
    if len(text) > MAX_CHARS:
        note = (f'（结果过长已截断到 {MAX_CHARS} 字符；'
                '可缩小范围，如指定班级/科目/方向后再分析）')
        text = text[:MAX_CHARS] + '…' + note
    return text


# ==================== 工具实现 ====================

def _t_list_exams(user, grade='', limit=20, **kw):
    scope = score_guard.resolve(user)
    grades = scope.visible_grades()
    if grades is not None and not grades:
        return ({'count': 0, 'exams': [],
                 'note': '当前账号没有任何可见年级（既非任课教师、也不是班主任/年级长及以上）'},
                '0 场考试')
    query = Exam.query
    if grades is not None:
        query = query.filter(Exam.grade.in_(sorted(grades)))
    if grade:
        query = query.filter(Exam.grade == grade)
    exams = query.order_by(Exam.exam_date.desc(), Exam.id.desc()) \
        .limit(max(1, min(int(limit or 20), 50))).all()
    return ({'count': len(exams),
             'visible_grades': sorted(grades) if grades is not None else '全部年级',
             'exams': [{'exam_id': e.id, 'name': e.name, 'grade': e.grade,
                        'exam_date': e.exam_date.strftime('%Y-%m-%d') if e.exam_date else '',
                        'status': e.status} for e in exams]},
            f'{len(exams)} 场考试')


def _t_exam_overview(user, exam_id=0, **kw):
    exam, scope = _exam(user, exam_id, kw.get('default_exam_id'))
    data = st.cached_exam_data(exam.id)
    visible = scope.visible_classes(exam.grade)
    snaps = [s for s in data.snap.values()
             if visible is None or s['class_name'] in visible]
    allowed = scope.grade_subjects(exam.grade)
    imported = _subject_filter(allowed, data.imported_subjects)
    expected = _subject_filter(allowed, data.expected_subjects)
    class_names = {s['class_name'] for s in snaps}
    total_ok = bool(class_names) and all(
        scope.class_total_visible(exam.grade, c) for c in class_names)
    totals = [s['score'] for s in snaps if isinstance(s.get('score'), (int, float))]
    out = {
        'exam_id': exam.id, 'exam_name': exam.name, 'grade': exam.grade,
        'exam_date': exam.exam_date.strftime('%Y-%m-%d') if exam.exam_date else '',
        'visible_classes': sorted(visible) if visible is not None else '全部班级',
        'visible_subjects': sorted(allowed) if allowed is not None else '全部学科',
        'student_count': len(snaps),
        'class_count': len(class_names),
        'directions': sorted({s['direction'] for s in snaps if s['direction']}),
        'imported_subjects': imported,
        'expected_subjects': expected,
        'missing_subjects': [s for s in expected if s not in imported],
        'avg_total': round(sum(totals) / len(totals), 1) if (totals and total_ok) else None,
        'max_total': max(totals) if (totals and total_ok) else None,
        'min_total': min(totals) if (totals and total_ok) else None,
    }
    if not total_ok:
        out['total_hidden'] = True
        out['note'] = '任课教师仅可见所教学科成绩，总分口径已隐藏'
    return out, f'{len(snaps)} 名学生 · {len(class_names)} 个班'


def _t_class_summary(user, exam_id=0, direction='', **kw):
    exam, scope = _exam(user, exam_id, kw.get('default_exam_id'))
    data = st.cached_exam_data(exam.id)
    rows = st.class_summary_table(data, direction or None)
    rows = _rows_for_scope(rows, scope, exam.grade)
    for row in rows:
        _hide_total(row, scope, exam.grade, row['class_name'])
    return ({'exam_id': exam.id, 'exam_name': exam.name, 'direction': direction or '全部',
             'rows': rows}, f'{len(rows)} 个班')


def _t_subject_summary(user, exam_id=0, direction='', **kw):
    exam, scope = _exam(user, exam_id, kw.get('default_exam_id'))
    data = st.cached_exam_data(exam.id)
    rows = st.subject_table(data, direction or None)
    allowed = scope.grade_subjects(exam.grade)
    rows = [r for r in rows if allowed is None or r['subject'] in allowed]
    return ({'exam_id': exam.id, 'exam_name': exam.name, 'direction': direction or '全部',
             'rows': rows}, f'{len(rows)} 个科目')


def _t_score_segments(user, exam_id=0, class_name='', by_rank=False, **kw):
    exam, scope = _exam(user, exam_id, kw.get('default_exam_id'))
    if class_name:
        score_guard.require_class(scope, exam.grade, class_name)
    data = st.cached_exam_data(exam.id)
    rows = st.segment_table(data, class_name=class_name or None, by_rank=bool(by_rank))
    if not class_name:
        # 段表的 counts 是逐班人数：任课教师只能看到自己可见的班
        visible = scope.visible_classes(exam.grade)
        if visible is not None:
            for r in rows:
                if isinstance(r.get('counts'), dict):
                    r['counts'] = {k: v for k, v in r['counts'].items() if k in visible}
    return ({'exam_id': exam.id, 'exam_name': exam.name,
             'class_name': class_name or '全部班级',
             'mode': '名次段' if by_rank else '分数段', 'rows': rows},
            f'{"名次" if by_rank else "分数"}段 {len(rows)} 档')


def _t_layer_online(user, exam_id=0, subject=TOTAL_SUBJECT, **kw):
    exam, scope = _exam(user, exam_id, kw.get('default_exam_id'))
    data = st.cached_exam_data(exam.id)
    allowed = scope.grade_subjects(exam.grade)
    if subject != TOTAL_SUBJECT and allowed is not None and subject not in allowed:
        raise PermissionError(f'无权查看 {exam.grade} 的「{subject}」成绩'
                              '（任课教师仅能查看自己所教学科）')
    stats = pivot_service.layer_pass_stats(data, subject)
    if not stats:
        raise ValueError(f'该考试「{subject}」尚未划线，无法统计上线人数')
    # stats 形如 {'layers': [...], 'rows': [{class_name:…, 层名…}]}
    rows = stats.get('rows') if isinstance(stats, dict) else stats
    if isinstance(stats, dict) and isinstance(rows, list):
        rows[:] = _rows_for_scope(rows, scope, exam.grade)
        if subject == TOTAL_SUBJECT:
            # 总分上线对任课教师不可见：只保留其可见总分的班
            rows[:] = [r for r in rows
                       if scope.class_total_visible(exam.grade, r.get('class_name') or '')]
        return ({'exam_id': exam.id, 'exam_name': exam.name, 'subject': subject,
                 'layers': stats.get('layers'), 'rows': rows}, f'{len(rows)} 个班')
    return ({'exam_id': exam.id, 'exam_name': exam.name, 'subject': subject,
             'stats': stats}, '分层上线统计')


def _t_class_subject_report(user, exam_id=0, class_name='', **kw):
    exam, scope = _exam(user, exam_id, kw.get('default_exam_id'))
    if not class_name:
        raise ValueError('请指定 class_name（可用 list_classes 或 exam_overview 核对班级名）')
    score_guard.require_class(scope, exam.grade, class_name)
    out = rps.class_subject_report(exam.id, class_name)
    if out.get('error'):
        raise ValueError(out['error'])
    allowed = scope.class_subjects(exam.grade, class_name)
    rows = out.get('rows') or []
    if allowed is not None:
        rows = [r for r in rows if r.get('subject') == '总分' or r.get('subject') in allowed]
    if not scope.class_total_visible(exam.grade, class_name):
        rows = [r for r in rows if r.get('subject') != '总分']
        out['total_hidden'] = True
    out['rows'] = rows
    return out, f'{class_name} · {len(rows)} 行'


def _t_subject_class_report(user, exam_id=0, subject='', direction='', **kw):
    exam, scope = _exam(user, exam_id, kw.get('default_exam_id'))
    if not subject:
        raise ValueError('请指定 subject（如 语文/数学/英语/物理…，或 总分）')
    allowed = scope.grade_subjects(exam.grade)
    if subject != TOTAL_SUBJECT and allowed is not None and subject not in allowed:
        raise PermissionError(f'无权查看 {exam.grade} 的「{subject}」成绩'
                              '（任课教师仅能查看自己所教学科）')
    out = rps.subject_class_report(exam.id, direction, subject)
    if out.get('error'):
        raise ValueError(out['error'])
    visible = scope.visible_classes(exam.grade)
    if visible is not None and isinstance(out.get('rows'), list):
        out['rows'] = [r for r in out['rows'] if (r.get('class_name') or '') in visible]
    return out, f'{subject} · {len(out.get("rows") or [])} 个班'


def _t_grade_subject_layer(user, exam_id=0, direction='', layer='', **kw):
    exam, scope = _exam(user, exam_id, kw.get('default_exam_id'))
    out = rs.subject_layer_report(exam.id, direction, layer, no_compare=True)
    if out.get('error'):
        raise ValueError(out['error'])
    allowed = scope.grade_subjects(exam.grade)
    if allowed is not None:
        # 本表的 subject 列是**展示名**（学科如「语文」），收窄时按展示名比对
        disp = {subject_display(s) for s in allowed}
        out['rows'] = [r for r in (out.get('rows') or []) if r.get('subject') in disp]
        if out.get('teachers'):
            out['teachers'] = {k: v for k, v in out['teachers'].items() if k in disp}
    return out, f'{out.get("direction") or "全部"} · {len(out.get("rows") or [])} 个科目'


def _t_class_overview(user, exam_id=0, direction='', **kw):
    exam, scope = _exam(user, exam_id, kw.get('default_exam_id'))
    out = rs.class_overview_report(exam.id, direction)
    if out.get('error'):
        raise ValueError(out['error'])
    if isinstance(out.get('rows'), list):
        out['rows'] = _rows_for_scope(out['rows'], scope, exam.grade)
    return out, f'{len(out.get("rows") or [])} 个班概况'


def _t_near_line(user, exam_id=0, direction='', layer='', above=10, below=20,
                 class_name='', **kw):
    exam, scope = _exam(user, exam_id, kw.get('default_exam_id'))
    above = max(0, min(int(above or 0), rs.NEAR_LINE_MAX))
    below = max(0, min(int(below or 0), rs.NEAR_LINE_MAX))
    if class_name:
        score_guard.require_class(scope, exam.grade, class_name)
    data = rws.near_line_report(exam.id, direction, layer, above, below, class_name)
    if data.get('error'):
        raise ValueError(data['error'])
    visible = scope.visible_classes(exam.grade)
    rows = []
    for r in data.get('rows') or []:
        cls = r.get('class_name') or ''
        if visible is not None and cls not in visible:
            continue
        if not scope.class_total_visible(exam.grade, cls):
            continue      # 总分临界生名单：看不到总分的账号不该拿到
        allowed = scope.class_subjects(exam.grade, cls)
        if allowed is not None:
            r = {k: v for k, v in r.items() if k not in SUBJECTS or k in allowed}
        rows.append(r)
    # 科目列与上/线下人数都按**收窄后**的名单重算，避免"人数与表格对不上"
    subjects = [s for s in (data.get('subjects') or [])
                if any(r.get(s['key']) is not None for r in rows)]
    below_n = sum(1 for r in rows if r.get('status') != '线上')
    above_n = sum(1 for r in rows if r.get('status') == '线上')
    out = dict(data, rows=rows, subjects=subjects, below_n=below_n, above_n=above_n)
    out.pop('classes', None)
    return out, f'临界生 {len(rows)} 人（线下 {below_n}／线上 {above_n}）'


def _t_student_scores(user, student_no='', exam_id=0, **kw):
    from app.models import Student
    if not student_no:
        raise ValueError('请提供 student_no（学号）')
    student = Student.query.filter_by(student_number=student_no).first()
    if student is None:
        raise ValueError(f'找不到学号 {student_no} 的学生')
    scope = score_guard.resolve(user)
    score_guard.require_student(scope, student)
    total_ok = scope.class_total_visible(student.grade, student.class_name)
    if not int(exam_id or 0):
        from app.models.grades import ExamScore
        rows = (ExamScore.query.filter_by(student_no=student_no, subject=TOTAL_SUBJECT)
                .order_by(ExamScore.exam_id.desc()).limit(30).all())
        ids = [r.exam_id for r in rows]
        exams = {e.id: e for e in Exam.query.filter(Exam.id.in_(ids or [0])).all()}
        grades = scope.visible_grades()
        items = []
        for r in rows:
            exam = exams.get(r.exam_id)
            if exam is None or (grades is not None and exam.grade not in grades):
                continue
            item = {'exam_id': r.exam_id, 'exam_name': exam.name, 'grade': exam.grade,
                    'exam_date': exam.exam_date.strftime('%Y-%m-%d') if exam.exam_date else ''}
            if total_ok:
                item.update({'total_score': r.score, 'rank_class': r.rank_class,
                             'rank_dir': r.rank_dir})
            items.append(item)
        out = {'student_no': student_no, 'name': student.name, 'grade': student.grade,
               'class_name': student.class_name, 'total_visible': total_ok,
               'count': len(items), 'exams': items}
        if not total_ok:
            out['total_hidden'] = True
        return out, f'{student.name} · {len(items)} 场考试'
    exam, scope = _exam(user, exam_id, kw.get('default_exam_id'))
    data = st.cached_exam_data(exam.id)
    snap = data.snap.get(student_no)
    if snap is None:
        raise ValueError(f'该学生在「{exam.name}」没有成绩记录')
    allowed = scope.class_subjects(student.grade, student.class_name)
    subjects = []
    for s in _subject_filter(allowed, data.imported_subjects):
        value = data.subj.get((student_no, s))
        if value is not None:
            subjects.append({'subject': s, 'score': value})
    out = {'exam_name': exam.name, 'student_no': student_no, 'name': student.name,
           'class_name': snap.get('class_name'), 'subjects': subjects}
    if total_ok:
        out['total_score'] = snap.get('score')
        out['rank_class'] = snap.get('rank_class')
        out['rank_dir'] = snap.get('rank_dir')
    else:
        out['total_hidden'] = True
    return out, f'{student.name} · {len(subjects)} 科'


def _t_student_trend(user, student_no='', subject=TOTAL_SUBJECT, limit=8, **kw):
    from app.models import Student
    from app.models.grades import ExamScore
    if not student_no:
        raise ValueError('请提供 student_no（学号）')
    student = Student.query.filter_by(student_number=student_no).first()
    if student is None:
        raise ValueError(f'找不到学号 {student_no} 的学生')
    scope = score_guard.resolve(user)
    score_guard.require_student(scope, student)
    subj = (subject or TOTAL_SUBJECT).strip() or TOTAL_SUBJECT
    allowed = scope.class_subjects(student.grade, student.class_name)
    if allowed is not None and subj not in allowed:
        raise PermissionError(f'无权查看 {student.grade} {student.class_name} 的'
                              f'「{subj}」成绩（任课教师仅能查看自己所教学科）')
    if subj == TOTAL_SUBJECT and not scope.class_total_visible(student.grade,
                                                               student.class_name):
        raise PermissionError('总分不对任课教师开放（任课教师仅可见所教学科）')
    rows = (ExamScore.query.filter_by(student_no=student_no, subject=subj)
            .order_by(ExamScore.exam_id.desc()).limit(max(1, min(int(limit or 8), 30))).all())
    ids = [r.exam_id for r in rows]
    exams = {e.id: e for e in Exam.query.filter(Exam.id.in_(ids or [0])).all()}
    grades = scope.visible_grades()
    items = []
    for r in rows:
        exam = exams.get(r.exam_id)
        if exam is None or (grades is not None and exam.grade not in grades):
            continue
        items.append({'exam_id': r.exam_id, 'exam_name': exam.name,
                      'exam_date': exam.exam_date.strftime('%Y-%m-%d') if exam.exam_date else '',
                      'score': r.score, 'rank_class': r.rank_class, 'rank_dir': r.rank_dir})
    items.reverse()
    return ({'student_no': student_no, 'name': student.name, 'grade': student.grade,
             'class_name': student.class_name, 'subject': subj,
             'count': len(items), 'trend': items},
            f'{student.name} · {subj} {len(items)} 次')


def _t_my_scope(user, **kw):
    scope = score_guard.resolve(user)
    desc = scope.describe()
    return desc, f'{desc.get("role") or "?"} · {desc.get("visible_grades")}'


# ==================== 直接复用 MCP 服务的工具实现 ====================
# 2026-10-13：MCP 服务（mcp_server/tools/）与 Web 端原本是"两套封装、同一套口径"
# （都调用 app 内同一批分析服务 + 同一份 score_guard）。为了彻底消除漂移，
# 下面这两个 MCP 侧独有、Web 侧原先没有的能力，**直接调用 MCP 的那份实现**：
#   · mcp_server/tools/scores.py 里的 `_list_classes` / `_class_score_detail` 是
#     纯同步函数（user 入参 + 返回 dict），不依赖 MCP 框架（register() 才用到 SDK），
#     因此可以在 Flask 进程内直接调用，不需要额外的 MCP 服务进程、也不需要账号密码。
#   · 权限、范围、截断全部沿用 MCP 那一侧的规则（score_guard + config.max_rows）。

def _mcp_scores():
    """取 MCP 成绩工具模块（延迟导入：MCP 是可选项，缺失时不影响其它工具）。"""
    from mcp_server.tools import scores as mcp_scores
    return mcp_scores


def _t_list_classes(user, grade='', exam_id=0, **kw):
    """班级清单（＝ MCP 的 list_classes，同一份实现）。"""
    ms = _mcp_scores()
    data = ms._list_classes(user, (grade or '').strip(),
                            int(exam_id or kw.get('default_exam_id') or 0))
    return data, f'{data.get("count") or 0} 个班'


def _t_class_detail(user, exam_id=0, class_name='', subject='', **kw):
    """某班逐生成绩明细（＝ MCP 的 get_class_score_detail，同一份实现）。"""
    exam, _scope = _exam(user, exam_id, kw.get('default_exam_id'))
    ms = _mcp_scores()
    data = ms._class_score_detail(user, exam.id, (class_name or '').strip(),
                                  (subject or '').strip())
    return data, f'{class_name} · {data.get("student_count") or 0} 人'


# 工具名 → 实现
HANDLERS = {
    'list_my_score_scope': _t_my_scope,
    'list_exams': _t_list_exams,
    'list_classes': _t_list_classes,
    'class_detail': _t_class_detail,
    'exam_overview': _t_exam_overview,
    'class_summary': _t_class_summary,
    'subject_summary': _t_subject_summary,
    'score_segments': _t_score_segments,
    'layer_online': _t_layer_online,
    'class_subject_report': _t_class_subject_report,
    'subject_class_report': _t_subject_class_report,
    'grade_subject_layer': _t_grade_subject_layer,
    'class_overview': _t_class_overview,
    'near_line': _t_near_line,
    'student_scores': _t_student_scores,
    'student_trend': _t_student_trend,
}


# ==================== OpenAI 兼容工具定义 ====================

def _schema(name, desc, props, required=()):
    return {'type': 'function', 'function': {
        'name': name, 'description': desc + _NOTE,
        'parameters': {'type': 'object', 'properties': props,
                       'required': list(required)}}}


_P_EXAM = {'type': 'integer', 'description': '考试 id（省略时用当前正在看的考试）'}
_P_DIR = {'type': 'string', 'description': '方向：物理 / 历史；留空为自动（物理优先）'}
_P_CLASS = {'type': 'string', 'description': '班级名，如 "01班"'}

TOOLS = [
    _schema('list_my_score_scope',
            '查看当前账号在成绩分析中的可见范围（可见年级/班级/学科与授权原因）。'
            '拿不准能分析什么时先调它。', {}),
    _schema('list_exams', '列出账号可见范围内的考试（按考试日期倒序），用于确定 exam_id。',
            {'grade': {'type': 'string', 'description': '年级，如 "2025级"；留空为全部'},
             'limit': {'type': 'integer', 'description': '返回条数，默认 20，最大 50'}}),
    _schema('list_classes',
            '班级清单：账号可见范围内的班级及可看学科、是否可见总分。'
            '班级口径是"考试当时的班级"（不是当前花名册），不含"不分班/已转出/离校"；'
            'class_name 拿不准时先调它核对。',
            {'grade': {'type': 'string', 'description': '年级，如 "2025级"；留空为全部'},
             'exam_id': _P_EXAM}),
    _schema('exam_overview',
            '某场考试的概览：参考人数、班级数、总分均分/最高/最低、方向、已导入科目。'
            '分析任何考试前建议先调它确认数据完整性（看清是否缺科目、缺班级）。',
            {'exam_id': _P_EXAM}, ('exam_id',)),
    _schema('class_summary',
            '年级各班总分汇总：人数、均分、标准差、最高、最低、各分层人数。'
            '做班级横向对比、找最强/最弱班时用它。',
            {'exam_id': _P_EXAM, 'direction': _P_DIR}, ('exam_id',)),
    _schema('class_detail',
            '某班**逐生成绩明细**：每个学生的各科分数（班主任及以上另含总分与排名）。'
            '做"某班名单/个别人成绩/班内分布"类分析用它；只需班级概况时用 class_summary。',
            {'exam_id': _P_EXAM, 'class_name': _P_CLASS,
             'subject': {'type': 'string', 'description': '科目名，如 数学；留空返回全部可见学科'}},
            ('exam_id', 'class_name')),
    _schema('subject_summary',
            '年级各学科总表：参考数、满分、均分、最高、最低、标准差、及格率、优秀率。'
            '判断学科整体强弱、偏科时用它。',
            {'exam_id': _P_EXAM, 'direction': _P_DIR}, ('exam_id',)),
    _schema('score_segments',
            '分数段（或名次段）分布：每档人数 × 各班 + 合计。'
            'by_rank=true 表示按方向排名分段（名次段），否则按总分分段。',
            {'exam_id': _P_EXAM, 'class_name': _P_CLASS,
             'by_rank': {'type': 'boolean', 'description': 'true=名次段，默认 false=分数段'}},
            ('exam_id',)),
    _schema('layer_online',
            '各班 × 各分层线的累计上线人数与上线率（subject 默认"总分"，也可传单科）。'
            '做"特控/本科上线率"类分析用它。未划线会报错。',
            {'exam_id': _P_EXAM,
             'subject': {'type': 'string', 'description': '科目名，默认 "总分"'}},
            ('exam_id',)),
    _schema('class_subject_report',
            '某个班各科的层上线（单/双）与去差均分，末行为总分；做单班内部学科诊断用它。',
            {'exam_id': _P_EXAM, 'class_name': _P_CLASS}, ('exam_id', 'class_name')),
    _schema('subject_class_report',
            '某个科目在年级各班的层上线（单/双）与去差均分；做"某科各班差异"分析用它。',
            {'exam_id': _P_EXAM, 'subject': {'type': 'string', 'description': '科目名，如 物理'},
             'direction': _P_DIR}, ('exam_id', 'subject')),
    _schema('grade_subject_layer',
            '年级各学科在指定层线（默认最高层）上的单/双上线人数、上线率、去差均分。',
            {'exam_id': _P_EXAM, 'direction': _P_DIR,
             'layer': {'type': 'string', 'description': '层名，如 特控；留空取默认层'}},
            ('exam_id',)),
    _schema('class_overview',
            '各班概况：参考数、各总分层上线人数与上线率、去差均分、班主任。'
            '汇报口径的班级总览。',
            {'exam_id': _P_EXAM, 'direction': _P_DIR}, ('exam_id',)),
    _schema('near_line',
            '临界生（压线生）名单：总分线上下 N/M 分内的学生，含班级、班主任、姓名、'
            '总分、距线、方向排名与各科分数。class_name 可只看一个班。'
            '做"临界生盯人/分包"分析用它。',
            {'exam_id': _P_EXAM, 'direction': _P_DIR,
             'layer': {'type': 'string', 'description': '总分层名，如 特控；留空取默认层'},
             'above': {'type': 'integer', 'description': '线上 N 分内，默认 10'},
             'below': {'type': 'integer', 'description': '线下 M 分内，默认 20'},
             'class_name': _P_CLASS}, ('exam_id',)),
    _schema('student_scores',
            '学生成绩：传 exam_id 返回该场各科分数与排名；exam_id 传 0 返回该生有成绩的历次考试清单。',
            {'student_no': {'type': 'string', 'description': '学号'},
             'exam_id': {'type': 'integer', 'description': '考试 id；0=历次清单'}},
            ('student_no',)),
    _schema('student_trend',
            '学生某学科（默认"总分"）历次成绩走势，用于看进退步。',
            {'student_no': {'type': 'string', 'description': '学号'},
             'subject': {'type': 'string', 'description': '科目名，默认 "总分"'},
             'limit': {'type': 'integer', 'description': '取最近 N 次，默认 8，最大 30'}},
            ('student_no',)),
]


# ==================== 分发 ====================

def dispatch(name, args, user, default_exam_id=None):
    """执行一个工具调用，返回 (ok, 文本, 摘要)。

    ok=False 时文本是给模型看的失败原因（权限不足 / 参数不对 / 无数据），
    模型可以据此换一个工具或调整参数，而不会把异常抛穿到 HTTP 层。
    """
    fn = HANDLERS.get(name)
    if fn is None:
        return False, f'未知工具：{name}', ''
    params = dict(args or {})
    if default_exam_id and not params.get('exam_id'):
        params['exam_id'] = default_exam_id
    params['default_exam_id'] = default_exam_id
    try:
        out = fn(user, **params)
        data, summary = out if isinstance(out, tuple) else (out, '')
        return True, _cut(data), summary
    except PermissionError as e:
        return False, f'权限不足：{e}', ''
    except ValueError as e:
        return False, str(e), ''
    except Exception as e:      # noqa: BLE001  工具异常不应打断整轮对话
        logger.warning('AI 工具执行失败 tool=%s args=%s', name, params, exc_info=True)
        return False, f'工具执行失败（{type(e).__name__}）：{e}', ''
