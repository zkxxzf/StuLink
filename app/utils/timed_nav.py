# StuLink v1.19.50.55 2026-10-20
# 限时练场次的侧栏上下文（与普通考试 exam_nav 同一套悬浮流程栏外观）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""限时练侧栏上下文：进入某场限时练时把侧栏切成「悬浮流程栏」。

普通考试的考试态由 ``exam_nav()`` 提供（场次 / 四步进度 / 分组导航）；
限时练是独立表，需要在 ``/grades/timed/<tid>`` 系列页面上单独解析，
外观与交互复用同一批 ``.exam-flow-nav`` / ``.efn-*`` 样式，避免第二套 UI。

三步进度与页内页签对应：建场次 → 导入成绩 → 成绩分析；后两步依赖
"至少导入一科"，没有成绩时显示为未完成。（2026-10-20 审计：原注释还写着
"四步 / 划线分层"，该页签早已删除，改为与代码一致的三步。）
"""
from flask import g, request, url_for

# (tab 键, 侧栏文案)：顺序即导航顺序，不要随意调整
# 2026-10-20 用户口径（第三版）：删除「成绩单」，学生成绩=汇总表
# 2026-10-20 追加「学生分析」：每人每科 T 分轨迹
TIMED_TABS = (
    ('students', '学生成绩'),
    ('trends', '学生分析'),
    ('import', '数据导入'),
    ('analysis', '成绩总览分析'),
)
# 页签 → 进度圆点下标
_TAB_STEP = {'students': 2, 'trends': 2, 'import': 1, 'analysis': 2}
_TAB_KEYS = [t[0] for t in TIMED_TABS]


def timed_nav():
    """当前请求的限时练上下文；不在限时练场次页面时返回 None"""
    if request.blueprint != 'grades':
        return None
    if getattr(g, '_timed_nav_done', False):
        return getattr(g, '_timed_nav_val', None)
    g._timed_nav_done = True
    g._timed_nav_val = None
    try:
        val = _resolve()
    except Exception:                   # 导航解析失败绝不能影响页面渲染
        val = None
    g._timed_nav_val = val
    return val


def _resolve():
    """按 view_args 里的 tid 还原场次 + 当前页签 + 四步进度"""
    from app.extensions import db
    from app.models.timed import TimedExam
    from app.modules.grades.services import timed_service

    # 只有 grades.timed* 的页面才是限时练 —— grades 蓝图里 AI 任务等路由
    # 也用 <int:tid>，单看 tid 会把限时练流程栏挂到别的页面上（2026-10-21 审计）
    if not (request.endpoint or '').startswith('grades.timed'):
        return None
    tid = (request.view_args or {}).get('tid')
    if not tid:
        return None
    try:
        exam = db.session.get(TimedExam, int(tid))
    except (TypeError, ValueError):
        return None
    if exam is None:
        return None

    subjects = timed_service.subjects_of(exam.id)
    has_scores = bool(subjects)
    tab = (request.args.get('tab') or 'students').strip()
    if tab not in _TAB_KEYS:
        tab = 'students'
    cur = _TAB_STEP.get(tab, 0)

    labels = ('建场次', '导入成绩', '成绩分析')
    steps = []
    for i, label in enumerate(labels):
        steps.append({'label': label, 'done': True if i == 0 else has_scores,
                      'current': cur == i})

    subject = (request.args.get('subject') or '').strip()
    class_name = (request.args.get('class') or '').strip()
    # 2026-10-21：导入是写操作，入口与路由同门槛（grades.import），
    # 只读账号不显示该页签，避免「看得见点不进」的 403 死链。
    from flask_login import current_user
    can_import = current_user.has_perm('grades.import')
    nav = []
    for key, label in TIMED_TABS:
        if key == 'import' and not can_import:
            continue
        # 查询参数名就叫 class —— 是 Python 关键字，不能直接写关键字实参
        params = {'tid': exam.id, 'tab': key}
        if subject:
            params['subject'] = subject
        if class_name:
            params['class'] = class_name
        nav.append({'tab': key, 'label': label,
                    'url': url_for('grades.timed_detail', **params)})
    return {'exam': exam, 'exam_id': exam.id, 'tab': tab, 'steps': steps,
            'nav': nav, 'subjects': subjects,
            'done_count': sum(1 for s in steps if s['done'])}
