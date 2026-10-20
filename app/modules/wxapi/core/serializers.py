# StuLink v1.19.49.48 2026-10-18
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序数据裁剪层。

服务层（student_service 等）返回的是给网页大表格用的完整结构，
直接透传会浪费流量且字段形态不稳定；这里统一压成小程序需要的
精简结构，并做 dict / 对象两种形态的兼容取值。
"""
HISTORY_LIMIT = 30          # 历次考试最多返回条数


def _pick(obj, key, default=None):
    """从 dict 或对象中取值（服务层返回两种形态都可能出现）。"""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _rows(table):
    """安全取表格行：table 形如 {title, columns, rows}。"""
    if not isinstance(table, dict):
        return []
    return table.get('rows') or []


def user_brief(user):
    """用户概要（不含任何凭据字段）。"""
    return {
        'id': user.id,
        'username': user.username,
        'real_name': user.real_name,
        'role': user.role,
        'role_display': user.role_display,
        'display_role': user.display_role,
        'grade': user.grade_display,
        'class_name': user.class_name or '',
        'must_change_pwd': bool(user.must_change_pwd),
    }


def group_options(user):
    """该用户可选的身份组（多身份场景，供小程序切换身份）。"""
    out = []
    for g in user.permission_groups:
        if g is not None:
            out.append({'id': g.id, 'name': g.name or ''})
    return out


def exam_item(exam):
    """考试条目精简（考试名 / 日期 / 类型 / 学期 / 年级）。"""
    return {
        'id': _pick(exam, 'id'),
        'name': _pick(exam, 'name') or '',
        'date': str(_pick(exam, 'exam_date') or _pick(exam, 'date') or ''),
        'type': _pick(exam, 'exam_type') or '',
        'term': _pick(exam, 'term') or '',
        'grade': _pick(exam, 'grade') or '',
        # 2026-10-20：批次选择器与网页一致显示「高一上…」
        'semester': _exam_semester(exam),
    }


def _exam_semester(exam):
    """模型取 semester_label()；dict 结构退回自身 semester 字段。"""
    try:
        if hasattr(exam, 'semester_label'):
            return exam.semester_label() or ''
    except Exception:  # noqa: BLE001  # 仅展示字段，失败留空
        return ''
    return _pick(exam, 'semester') or ''


def student_item(stu):
    """学生条目精简（学号 / 姓名 / 年级 / 班级 / 选科）。"""
    return {
        'no': _pick(stu, 'student_number') or _pick(stu, 'no') or '',
        'name': _pick(stu, 'name') or '',
        'grade': _pick(stu, 'grade') or '',
        'class_name': _pick(stu, 'class_name') or '',
        'selection': _pick(stu, 'selection') or '',
    }


def _history_row(row):
    """历次总分行：考试名 / 总分 / 班名 / 校名 / 变化。"""
    return {
        'exam': _pick(row, 'exam') or '',
        'exam_id': _pick(row, 'exam_id'),
        'date': str(_pick(row, 'date') or ''),
        'type': _pick(row, 'type') or '',
        'total': _pick(row, 'total'),
        'rank_class': _pick(row, 'rank_class'),
        'rank_dir': _pick(row, 'rank_dir'),
        'move': _pick(row, 'move'),
        'class_name': _pick(row, 'class_name') or '',
    }


def _pass_flag(raw):
    """归一化「是否及格」：服务层可能给 '是'/'否' 等中文，统一成布尔。"""
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw.strip() not in ('否', '0', 'false', 'False', '不及格')
    return bool(raw)


def _subject_row(row):
    """科目明细行：学科 / 得分 / 满分 / 得分率 / 班均 / 校均 / 排名 / 是否及格。"""
    return {
        'subject': _pick(row, 'subject') or '',
        'score': _pick(row, 'score'),
        'full': _pick(row, 'full'),
        'rate': _pick(row, 'rate'),
        'cls_avg': _pick(row, 'cls_avg'),
        'grd_avg': _pick(row, 'grd_avg'),
        'pass_line': _pick(row, 'pass_line'),
        'rank_dir': _pick(row, 'rank_dir'),
        'rank_class': _pick(row, 'rank_class'),
        'is_pass': _pass_flag(_pick(row, 'is_pass')),
    }


# 小程序只渲染这 5 张图（其余图表是网页专用的大表/矩阵，透传会白耗流量）
CHART_KEYS = ('trend_total', 'trend_subject', 'subject_trend',
              'subject_compare', 'subject_radar', 'rank_trend')


def _chart(chart):
    """裁剪一张图表：只保留渲染必需字段（xAxis / series / 指标 / 参考线）。"""
    if not isinstance(chart, dict):
        return None
    out = {
        'title': chart.get('title') or '',
        'type': chart.get('type') or '',
        'xAxis': chart.get('xAxis') or [],
        'series': [
            {'name': s.get('name') or '', 'data': s.get('data') or []}
            for s in (chart.get('series') or [])
        ],
    }
    if chart.get('indicators'):
        out['indicators'] = chart['indicators']
    if chart.get('markLine') is not None:
        out['markLine'] = chart['markLine']
    if chart.get('inverse'):
        out['inverse'] = True
    return out


def _charts(raw):
    """把 build_data 的 charts 裁成小程序图表字典。"""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key in CHART_KEYS:
        item = _chart(raw.get(key))
        if item and item.get('series'):
            out[key] = item
    return out


def _rank_rows(tables):
    """各科最新成绩与排名波动（表格转数组，前端直接渲染）。"""
    rows = _rows((tables or {}).get('subject_rank'))
    return [{
        'subject': _pick(r, 'subject') or '',
        'exam': _pick(r, 'exam') or '',
        'score': _pick(r, 'score'),
        'full': _pick(r, 'full'),
        'rate': _pick(r, 'rate'),
        'rank_dir': _pick(r, 'rank_dir'),
        'rank_class': _pick(r, 'rank_class'),
        'score_diff': _pick(r, 'score_diff'),
        'rank_move': _pick(r, 'rank_move'),
    } for r in rows]


def grade_payload(data, history_limit=HISTORY_LIMIT):
    """把 build_data 结果裁成小程序结构。

    入参：data=student_service.build_data 的返回值。
    返回：dict {student, exams, history, latest, charts, tables}；
    入参无数据时返回 None。
    """
    if not isinstance(data, dict) or data.get('error'):
        return None
    tables = data.get('tables') or {}
    exams = [exam_item(e) for e in (data.get('exams') or [])]
    history = [_history_row(r) for r in _rows(tables.get('exam_list'))]
    latest = _latest_block(data, exams, tables)
    return {
        'student': _pick(data, 'student') or {},
        'exams': exams,
        'history': history[:history_limit],
        'latest': latest,
        'charts': _charts(data.get('charts')),
        'tables': {'subject_rank': _rank_rows(tables)},
        'meta': data.get('meta') or {},
    }


def _latest_block(data, exams, tables):
    """最近一场考试的科目明细块（含考试名 / 总分块，便于前端直接展示）。"""
    rows = _rows(tables.get('subject_detail'))
    subjects = [_subject_row(r) for r in rows]
    if not subjects:
        return None
    sel_id = data.get('selected_exam_id')
    name = next((e['name'] for e in exams if e.get('id') == sel_id), '')
    total = next((s for s in subjects if s['subject'] == '总分'), None)
    return {
        'exam_id': sel_id,
        'exam_name': name,
        'subjects': [s for s in subjects if s['subject'] != '总分'],
        'total': total,
    }
