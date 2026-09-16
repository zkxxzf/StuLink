# StuLink v1.9.2 2026-09-16
# 成绩汇报区 · 压线提醒：总分层线上下浮动范围内的临界生名单（含各科成绩与方向排名）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from app.models.grades import SUBJECTS, TOTAL_SUBJECT, subject_display
from app.modules.grades.services import stats_service as st
from app.modules.grades.services import report_service as rs


def near_line_report(exam_id, direction, layer_name, above, below):
    """压线临界生名单。

    方向/层口径与板块一一致；区间：线下 [L-below, L)、线上 [L, L+above]。
    排序：线下组在前（组内总分降序＝最接近线的最靠前），线上组在后（组内升序＝最危险的靠前）。
    """
    data = st.cached_exam_data(exam_id)
    if not data.total_rows:
        return {'error': '该考试暂无成绩'}
    # 未指定方向时落到第一个有总分划线的方向（物理优先），与其他板块一致
    direction = rs.resolve_direction(data, direction)
    layer = rs.pick_layer(data, direction, TOTAL_SUBJECT, layer_name)
    if not layer:
        if layer_name and rs.layers_desc(data, direction):
            return {'error': f'{direction or ""}方向总分划线中没有「{layer_name}」层'}
        return {'error': '总分尚未划线，请先在「划线分层」设置特控/本科线'}
    lname, line = layer

    rows_all = [r for r in data.total_rows if (not direction or r.direction == direction)
                and r.score is not None]
    below_rows, above_rows = [], []
    # 科目列取范围内学生实际有成绩的科目并集（走班选科不一致时列也能对齐）
    nos = set()
    for r in rows_all:
        gap = r.score - line
        if -below <= gap < 0:
            below_rows.append(r)
            nos.add(r.student_no)
        elif 0 <= gap <= above:
            above_rows.append(r)
            nos.add(r.student_no)
    # 同分时按学号确定先后，避免名单顺序随数据库返回波动
    below_rows.sort(key=lambda r: (-r.score, r.student_no))
    above_rows.sort(key=lambda r: (r.score, r.student_no))

    subj_cols = [s for s in SUBJECTS
                 if any(data.subj.get((no, s)) is not None for no in nos)]
    ht = rs.headteacher_map(data.exam.grade)  # 名单附班主任，便于临界生分包盯人

    def to_row(r, status):
        gap = round(r.score - line, 1)
        row = {'class_name': r.class_name or '—',
               'headteacher': ht.get((data.exam.grade, r.class_name or '—'), ''),
               'name': r.student_name,
               'score': r.score, 'status': status, 'gap': gap,
               'rank_dir': r.rank_dir}
        for s in subj_cols:
            row[s] = data.subj.get((r.student_no, s))
        return row

    rows = [to_row(r, '线下') for r in below_rows] + [to_row(r, '线上') for r in above_rows]
    return {'exam': {'id': data.exam.id, 'name': data.exam.name},
            'direction': direction or '全部', 'layer': lname, 'line': line,
            'above': above, 'below': below,
            'above_n': len(above_rows), 'below_n': len(below_rows),
            'subjects': [{'key': s, 'label': subject_display(s)} for s in subj_cols],
            'rows': rows}
