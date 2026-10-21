# StuLink v1.19.57.64 2026-10-15
# 成绩「年级维度」塑形：年级各班汇总 + 年级基准 + 班级排名 + 可下钻标记
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""成绩分析的**年级维度**（聚合层）塑形，Web 端 AI 与 MCP 共用同一份。

用户口径（2026-10-15）：
  · **年级成绩分析的结果可以公开** —— AI 分析某班成绩时，必须同时拿到年级口径的对照
    （年级均分、本班在年级中的位次、各班横向数据），否则"本班均分 440"没有参照物；
  · **不能公开的只有学生个人成绩**（"除非是他们自己班的"）—— 逐生明细仍按
    班级 / 学科收敛，由 `score_guard`（`can_view_class` / `class_subjects` /
    `class_total_visible`）判定，不在本模块。

因此本模块只做**聚合层**（人数 / 均分 / 标准差 / 最高 / 最低 / 分层计数 / 各科均分 /
位次），**不含任何逐生字段**；逐生取数仍在 `mcp_server/tools/scores.py::_class_score_detail`
等（那里的范围校验一个都不能少）。

为什么单独成模块：同一场考试，"网页端 AI 说的话"与"MCP 客户端 AI 说的话"必须一致
（铁律：AI 可见范围 = 网页端可见范围；口径只能有一份）；此前 MCP 把班级汇总按
`visible_classes` 收窄到只剩自己班，网页端 AI 却是全年级，两边数字对不上。
"""
from app.modules.grades.services import stats_service as st


def filter_subjects(subjects, allowed):
    """allowed 为 None 时不过滤（全部学科），否则只保留可见学科。"""
    if allowed is None:
        return list(subjects or [])
    return [s for s in (subjects or []) if s in allowed]


def subject_avgs(data, class_name, allowed=None):
    """某班各科均分（聚合口径，默认全部学科）。"""
    out = []
    for subject in filter_subjects(data.imported_subjects, allowed):
        values = data.scores_of_subject(subject, class_name=class_name)
        out.append({'subject': subject, 'count': len(values),
                    'avg': st.mean(values) if values else None})
    return out


def grade_baseline(data, direction=None):
    """整年级基准（总分口径）：人数 / 均分 / 最高 / 最低。"""
    scores = [t['score'] for t in data.totals_of(direction=direction or None)
              if t.get('score') is not None]
    return {'student_count': len(scores), 'avg': st.mean(scores),
            'max': max(scores) if scores else None,
            'min': min(scores) if scores else None}


def annotate(scope, exam, data, rows, with_subjects=True):
    """给班级汇总行补：班级位次（按均分，同分并列）/ 可下钻标记 / 总分口径 / 各科均分。

    各科均分属"年级成绩分析结果"，按用户口径可公开（不看账号所教学科）；
    `detail_visible` / `total_visible` 只用于告诉 AI"这个班能不能下钻到逐生"。
    """
    ordered = sorted(rows, key=lambda r: (-(r['avg'] if r.get('avg') is not None else -1e9),
                                          r['class_name']))
    rank, prev_avg, prev_rank = {}, None, 0
    for i, row in enumerate(ordered, 1):
        if row.get('avg') != prev_avg:
            prev_avg, prev_rank = row.get('avg'), i
        rank[row['class_name']] = prev_rank
    for row in rows:
        row['class_rank'] = rank.get(row['class_name'])
        row['detail_visible'] = bool(scope.can_view_class(exam.grade, row['class_name']))
        row['total_visible'] = bool(scope.class_total_visible(exam.grade, row['class_name']))
        if with_subjects:
            row['subject_avg'] = subject_avgs(data, row['class_name'])
    return rows


def class_rows(scope, exam, data, direction=None, with_subjects=True):
    """年级各班汇总（公开口径）＋ 年级基准。

    返回 {'grade': {...基准...}, 'classes': [每班一行]}；班级清单＝本场考试出现过的班
    （考试当时班级），与网页端「年级分析」表一致。
    """
    rows = annotate(scope, exam, data,
                    st.class_summary_table(data, direction or None),
                    with_subjects=with_subjects)
    return {'grade': grade_baseline(data, direction), 'classes': rows}


def class_vs_grade(scope, exam, data, class_name, direction=None, with_peers=True):
    """单班 vs 年级（逐生分析的对照块）：本班位次 + 年级基准 +（可选）各班横向数据。

    逐生明细工具用它给 AI 一个参照系，避免"只报本班数字、无法判断好坏"。
    """
    rows = annotate(scope, exam, data,
                    st.class_summary_table(data, direction or None),
                    with_subjects=False)
    mine = next((r for r in rows if r['class_name'] == class_name), None)
    out = {'class_count': len(rows),
           'grade': grade_baseline(data, direction),
           'class_rank': (mine or {}).get('class_rank'),
           'class_avg': (mine or {}).get('avg'),
           'class_count_in': (mine or {}).get('count')}
    if with_peers:
        out['classes'] = [{'class_name': r['class_name'], 'count': r['count'],
                           'avg': r['avg'], 'max': r['max'], 'min': r['min'],
                           'class_rank': r['class_rank']} for r in rows]
    return out
