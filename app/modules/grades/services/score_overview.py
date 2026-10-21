# StuLink v1.19.50.60 2026-10-15
# 考试级概览的授权塑形（Web 端 AI 工具与 MCP 服务共用同一份，避免口径漂移）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""考试级概览（考试概览 / get_exam_overview / exam_overview）的授权塑形。

为什么单独成模块：`score_guard` 管"谁能看什么"，本模块管"看完之后怎么把数字
摆给 AI 看"，两边（`app/modules/grades/services/ai_tools.py` 与
`mcp_server/tools/scores.py`）必须逐字一致，否则同一账号在网页端 AI 与 MCP
客户端会看到互相矛盾的数字——属安全红线（口径漂移），所以只留这一份。

修的是这个口径错位（2026-10-15 用户反馈）：
  旧实现用 `scope.grade_subjects(grade)` 把"学科可见范围"压成一个年级级单值，
  而**班主任**的授权是"整班全科"（班级级）：于是一个"班主任 + 化学任课"的账号，
  在考试概览里被显示成「可见学科＝化学、总分口径已隐藏」，可他本班的全科与总分
  本来就看得到（同账号的 `get_class_score_summary` 也是这么返回的），自相矛盾。

现口径（按班判定 → 再聚合）：
  · 学科：逐班取 `class_subjects()`；任一可见班给了"全部学科"就按"全部学科"展示，
          同时输出 `class_scope` 逐班明细，防止 AI 把 A 班的可见学科套到 B 班；
  · 总分：只统计"总分口径已授权"的班级（班主任 / 年级长及以上 / 管理员 / 显式授权）；
          一个都没有时才整体隐藏（纯任课教师仍是隐藏，口径不变），
          部分隐藏时明确列出被隐藏的班级。
"""


def _filter(subjects, allowed):
    """allowed 为 None 表示全部学科；否则只保留可见学科。"""
    if allowed is None:
        return list(subjects or [])
    return [s for s in (subjects or []) if s in allowed]


def build(exam, scope, data):
    """返回考试概览的数据部分（不含 exam 自身字段，由调用方各自补充）。

    exam  : Exam 实例（只读 grade）
    scope : `score_guard.resolve()` 得到的 ScoreScope
    data  : `stats_service.cached_exam_data(exam.id)` 的结果
    """
    grade = exam.grade
    visible = scope.visible_classes(grade)
    snaps = [s for s in data.snap.values()
             if visible is None or s['class_name'] in visible]
    class_names = {s['class_name'] for s in snaps}

    # 学科：逐班判定后聚合（任一班给了"全部学科"，考试级就按全部学科处理）
    by_class = {c: scope.class_subjects(grade, c) for c in class_names}
    if any(v is None for v in by_class.values()):
        allowed = None
    else:
        allowed = set()
        for _subjects in by_class.values():
            allowed |= set(_subjects)
    imported = _filter(data.imported_subjects, allowed)
    expected = _filter(data.expected_subjects, allowed)
    missing = [s for s in expected if s not in imported]

    # 总分：只统计已获总分口径的班级
    total_classes = sorted(c for c in class_names
                           if scope.class_total_visible(grade, c))
    hidden_classes = sorted(c for c in class_names if c not in total_classes)
    totals = [s['score'] for s in snaps
              if s['class_name'] in total_classes and isinstance(s.get('score'), (int, float))]

    out = {
        'visible_classes': sorted(visible) if visible is not None else '全部班级',
        'visible_subjects': sorted(allowed) if allowed is not None else '全部学科',
        'student_count': len(snaps),
        'score_count': len(totals) if total_classes else None,
        'avg_total': round(sum(totals) / len(totals), 1) if totals else None,
        'max_total': max(totals) if totals else None,
        'min_total': min(totals) if totals else None,
        'class_count': len(class_names),
        'directions': sorted({s['direction'] for s in snaps if s['direction']}),
        'imported_subjects': imported,
        'expected_subjects': expected,
        'missing_subjects': missing,
    }
    if missing:
        out['partial_import'] = {'imported': imported, 'expected': expected,
                                 'missing': missing}

    # 各班可见口径不一致时给出逐班明细（班级数有限，体积可控）
    kinds = {v if v is None else frozenset(v) for v in by_class.values()}
    if len(kinds) > 1:
        out['class_scope'] = [
            {'class_name': c,
             'subjects': sorted(v) if v is not None else '全部学科',
             'total_visible': scope.class_total_visible(grade, c)}
            for c, v in sorted(by_class.items())]

    if total_classes:
        if hidden_classes:
            out['total_classes'] = total_classes
            out['total_hidden_classes'] = hidden_classes
            out['total_note'] = ('总分口径（均分/最高/最低/参考人数）只统计已获授权的班级：'
                                 + '、'.join(total_classes)
                                 + '；未授权班级：' + '、'.join(hidden_classes))
    else:
        out['total_hidden'] = True
        out['note'] = '任课教师仅可见所教学科成绩，总分口径已隐藏'

    # 2026-10-15：本场划线（层名 + 各方向/组合的分数线）——划线是学校口径、聚合层信息，
    # 网页端「划线分层」对所有能看该场成绩的账号只读展示；AI 端（Web AI 与 MCP 共用本函数）
    # 同口径带上，让"这次本科线多少分"这类问题两个入口都能直接答，不再"网页能看 AI 拿不到"。
    from app.models.grades import TOTAL_SUBJECT as _TOTAL
    from app.modules.grades.services.stats_service import band_lines as _band_lines
    _lines = _band_lines(exam.id, _TOTAL)
    if _lines:
        _layers = []
        for _b in _lines:
            if _b['layer'] not in _layers:
                _layers.append(_b['layer'])
        out['bands'] = {
            'layers': _layers,
            'lines': _lines,
            'note': ('层线按「方向 / 选科组合」分别划（组合线优先、方向线兜底），'
                     '这是学校划线口径、聚合层信息，能看该场成绩的账号都可读；'
                     '单科分数线用 layer_online（subject=科目名）查询'),
        }
    return out
