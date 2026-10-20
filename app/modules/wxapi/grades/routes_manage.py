# StuLink v1.19.49.44 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序「成绩管理」入口板：与网页首页 30 个功能入口同一份定义。

入口标题 / 分组 / 权限 / 是否需选考试全部来自 `services/entry_defs.py`，
本文件只做两件事：
1. 按 `user.has_perm` 裁剪（与网页 `entry_catalog._allowed` 同口径）；
2. 给每个入口映射小程序落点；尚无原生落点的标 `desktop=True`（提示到电脑端操作）。
"""
from flask import g

from app.models.grades import Exam
from app.modules.grades.services import scope as scope_service
from app.modules.grades.services.entry_defs import ENTRIES, GROUPS
from app.modules.wxapi import bp
from app.modules.wxapi.core.guards import wx_perm_required
from app.modules.wxapi.core.response import wx_ok
from app.modules.wxapi.grades.routes_grades import PERM

# 分组 → 默认图标（小程序不支持 bootstrap-icons）
GROUP_ICON = {
    '考试管理': '🗂',
    '单场考试': '📄',
    '成绩分析': '📊',
    '个人与证明': '👤',
    '基础配置': '⚙️',
}

# 单个入口图标（比分组图标更直观）
ICON_BY_KEY = {
    'exams': '📚', 'exam_new': '➕', 'affairs': '🗓', 'rooms': '🚪',
    'detail': '📄', 'import': '☁️', 'ai_import': '✨', 'bands': '📶',
    'affair': '📋', 'score_changes': '✏️',
    'analysis_grade': '📈', 'analysis_class': '🏫', 'analysis_subject': '📖',
    'analysis_teacher': '👩‍🏫', 'analysis_compare': '⚖️',
    'global_compare': '🌐', 'near_line': '🎯', 'pivot': '🔀',
    'honor': '🏆', 'report': '📊', 'print_summary': '🖨', 'ai': '🤖',
    'cert_issue': '📄', 'student_query': '👤', 'cert_records': '🧾',
    'reviews': '📝',
    'reviews_manage': '✅',
    'subjects': '🧩', 'band_templates': '🗂', 'teachers': '👥',
    'teachers_import': '📥',
}

# 入口 → 小程序页面；未列出的 = 电脑端专用（desktop）
WX_ROUTES = {
    'exams': '/package-grades/pages/index/index?tab=exam',
    'exam_new': '/package-grades/pages/exam-new/index',
    'affairs': '/package-exam/pages/index/index',
    'detail': '/package-grades/pages/scores/index',
    'import': '/package-grades/pages/import/index',
    'score_changes': '/package-grades/pages/manage-view/index?view=score_changes',
    'analysis_grade': '/package-grades/pages/analysis/index?tab=grade',
    'analysis_class': '/package-grades/pages/analysis/index?tab=class',
    'analysis_subject': '/package-grades/pages/analysis/index?tab=subject',
    'analysis_teacher': '/package-grades/pages/analysis/index?tab=teacher',
    'analysis_compare': '/package-grades/pages/analysis/index?tab=compare',
    'global_compare': '/package-grades/pages/global/index',
    'near_line': '/package-grades/pages/near-line/index',
    'honor': '/package-grades/pages/honor/index',
    'print_summary': '/package-grades/pages/manage-view/index?view=print_summary',
    'student_query': '/package-grades/pages/index/index',
    'cert_records': '/package-grades/pages/manage-view/index?view=cert_records',
    'reviews': '/package-grades/pages/review/index',
    'reviews_manage': '/package-grades/pages/review-manage/index',
    'bands': '/package-grades/pages/bands/index',
    'pivot': '/package-grades/pages/pivot/index',
    'report': '/package-grades/pages/report/index',
    'ai': '/package-grades/pages/ai/index',
    'subjects': '/package-grades/pages/subjects/index',
    'band_templates': '/package-grades/pages/band-templates/index',
    'teachers': '/package-grades/pages/teachers/index',
    'teachers_import': '/package-grades/pages/teachers-import/index',
    'rooms': '/package-grades/pages/rooms/index',
    'affair': '/package-grades/pages/affair/index',
    'ai_import': '/package-grades/pages/ai-import/index',
}

EXAM_LIMIT = 300


def _exam_rows(user):
    """可见年级的考试（最近在前），供「先选考试」的入口使用。"""
    grades = [x for x in (scope_service.visible_grades(user) or []) if x]
    if not grades:
        return []
    rows = (Exam.query.filter(Exam.grade.in_(grades))
            .order_by(Exam.exam_date.desc(), Exam.id.desc())
            .limit(EXAM_LIMIT).all())
    return [{
        'id': e.id,
        'name': e.name or '',
        'grade': e.grade or '',
        'date': e.exam_date.strftime('%Y-%m-%d') if e.exam_date else '',
        'status': e.status or 'draft',
        'imported': (e.status or '') == 'imported',
    } for e in rows]


def _entry(e):
    """入口 → 小程序展示结构（route 为空表示电脑端专用）。"""
    route = WX_ROUTES.get(e['key'], '')
    return {
        'key': e['key'],
        'title': e['title'],
        'group': e['group'],
        'desc': e.get('desc') or '',
        'icon': ICON_BY_KEY.get(e['key'],
                                GROUP_ICON.get(e['group'], '📌')),
        'route': route,
        'desktop': not bool(route),
        'needs_exam': bool(e.get('exam')),
        'imported_only': bool(e.get('imported')),
    }


def _kpi(user):
    """首页 KPI（与网页仪表盘共用 `grades_dashboard_v1_*` 60s 缓存）。"""
    from app.modules.grades.services import dashboard_service
    from app.utils.cache import cache
    key = 'grades_dashboard_v1_%s' % user.id
    data = cache.get(key)
    if data is None:
        data = dashboard_service.build_overview(user)
        cache.set(key, data, timeout=60)
    return data.get('kpi') or {}


@bp.route('/grades/manage/entries')
@wx_perm_required(PERM)
def wx_grades_manage_entries():
    """成绩管理入口板（30 项，按权限裁剪）+ 首页 KPI。"""
    user = g.wx_user
    allowed = [e for e in ENTRIES
               if not e.get('perm') or user.has_perm(e['perm'])]
    groups = []
    for name in GROUPS:
        rows = [_entry(e) for e in allowed if e['group'] == name]
        if rows:
            groups.append({'name': name, 'entries': rows})
    kpi = _kpi(user)
    return wx_ok({
        'groups': groups,
        'total': len(allowed),
        'desktop': sum(1 for g in groups for e in g['entries'] if e['desktop']),
        'exams': _exam_rows(user),
        'kpi': {
            'total_exams': kpi.get('total_exams') or 0,
            'participants': kpi.get('participants') or 0,
            'cert_total': kpi.get('cert_total') or 0,
            'latest_date': kpi.get('latest_date') or '',
        },
    })
