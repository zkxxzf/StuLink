# -*- coding: utf-8 -*-
"""成绩模块「功能入口」的链接构建、权限过滤与用户偏好合并（2026-10-19）。

入口定义（标题 / 路由 / 权限 / 是否需选考试）在 ``entry_defs``；
本模块只负责：
1. 用 ``url_for`` 生成真实链接与 ``__EXAM__`` 链接模板（前端替换考试 id）；
2. 按菜单权限 + 业务门槛（``gate``）过滤；
3. 合并用户偏好（显隐 / 顺序，见 ``entry_prefs``，存 system_settings，无需迁移）。
"""
import logging

from flask import url_for

from app.models.grades import Exam
from app.modules.grades.services import entry_prefs
from app.modules.grades.services import scope as scope_service
from app.modules.grades.services.entry_defs import ENTRIES, GROUPS

logger = logging.getLogger(__name__)

_STATUS_LABEL = {'draft': '未导入', 'imported': '已导入', 'dirty': '待重算'}
_EXAM_LIMIT = 300                  # 考试下拉上限（远超实际场次，纯防爆）


def _urls(entry):
    """生成 href（无 JS 兜底）与 tpl（把 __EXAM__ 换成考试 id）"""
    kind, query = entry['exam'], dict(entry['tab'])
    if kind == 'path':
        tpl = url_for(entry['endpoint'], exam_id=0).replace(
            '/0', '/__EXAM__', 1)
        return url_for('grades.exams_list'), tpl
    if kind == 'query':
        href = url_for(entry['endpoint'], **query)
        query['exam'] = '__EXAM__'
        return href, url_for(entry['endpoint'], **query)
    href = url_for(entry['endpoint'], **query)
    return href, href


def _format(entry, hidden=False):
    """补上链接与前端需要的字段"""
    href, tpl = _urls(entry)
    return {'key': entry['key'], 'title': entry['title'],
            'icon': entry['icon'], 'group': entry['group'],
            'desc': entry['desc'], 'href': href, 'tpl': tpl,
            'needs_exam': bool(entry['exam']),
            'imported_only': bool(entry['imported']), 'hidden': hidden}


def _allowed(entry, user):
    """权限过滤（权限 key 为空 = 登录 + grades.view 即可）"""
    perm = entry.get('perm')
    return not perm or user.has_perm(perm)


def _gate_ok(entry, user, exams):
    """入口的业务门槛（除菜单权限外还要过这一关，避免"看得见点不进"）"""
    if entry.get('gate') != 'scoresheet':
        return True
    if user.has_perm('grades.edit'):
        return True
    latest = next((x for x in (exams or ()) if x['imported']), None)
    if not latest:
        return False
    try:                              # 与成绩单页同一份判定（services/scoresheet）
        from app.modules.grades.services import scoresheet
        exam = Exam.query.get(latest['id'])
        return bool(exam and scoresheet.visible(user, exam)[1])
    except Exception as e:            # noqa: BLE001  判定失败按不可见处理
        logger.warning('成绩单入口判定失败: %s', e)
        return False


def build_board(user, exams=None):
    """构建入口板：visible=首页展示，all=自定义弹窗（含被隐藏的）"""
    prefs = entry_prefs.load(user)
    order = {k: i for i, k in enumerate(prefs.get('order') or [])}
    # 新登记的入口不在用户的旧排序里 → 按定义表位置插回去，
    # 而不是一律排到最后（否则新加的「开具成绩证明」会掉到组末）
    default_pos = {e['key']: i for i, e in enumerate(ENTRIES)}
    hidden = set(prefs.get('hidden') or [])
    allowed = [e for e in ENTRIES
               if _allowed(e, user) and _gate_ok(e, user, exams)]
    allowed.sort(key=lambda e: order.get(e['key'], default_pos[e['key']]))
    visible = [_format(e) for e in allowed if e['key'] not in hidden]
    everything = [_format(e, hidden=e['key'] in hidden) for e in allowed]
    return {'groups': _grouped(visible), 'all_groups': _grouped(everything),
            'total': len(allowed), 'shown': len(visible)}


def _grouped(items):
    """按 GROUPS 顺序分组（空组不出现）"""
    out = []
    for name in GROUPS:
        rows = [it for it in items if it['group'] == name]
        if rows:
            # 注意：键名不能叫 items —— Jinja 的 g.items 会先命中 dict.items 方法
            out.append({'name': name, 'entries': rows})
    return out


def exam_options(user):
    """考试下拉数据：按可见年级收敛，最近在前（不含参考人数，避免重查询）"""
    try:
        grades = [g for g in (scope_service.visible_grades(user) or []) if g]
    except Exception as e:                          # noqa: BLE001
        logger.warning('入口板可见年级解析失败，按空集处理: %s', e)
        grades = []
    if not grades:
        return []
    rows = (Exam.query.filter(Exam.grade.in_(grades))
            .order_by(Exam.exam_date.desc(), Exam.id.desc())
            .limit(_EXAM_LIMIT).all())
    return [{'id': e.id, 'name': e.name or e.title_with_dimensions(),
             'grade': e.grade or '', 'stage': (e.exam_type or '').strip(),
             'date': e.exam_date.strftime('%Y-%m-%d') if e.exam_date else '',
             'status': e.status or 'draft',
             'status_label': _STATUS_LABEL.get(e.status or 'draft', ''),
             'imported': (e.status or '') == 'imported'} for e in rows]
