# StuLink v1.19.54.64 2026-10-10
# 成绩汇报区 · 压线提醒：总分层线上下浮动范围内的临界生名单（含各科成绩与方向排名）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from app.models.grades import SUBJECTS, TOTAL_SUBJECT, subject_display
from app.modules.grades.services import stats_service as st
from app.modules.grades.services import report_service as rs


def near_line_report(exam_id, direction, layer_name, above, below, class_name=None,
                     allowed_classes=None):
    """压线临界生名单。

    方向/层口径与板块一一致；区间：线下 [L-below, L)、线上 [L, L+above]。
    排序：线下组在前（组内总分降序＝最接近线的最靠前），线上组在后（组内升序＝最危险的靠前）。
    class_name（可选）：按班级筛选（总分行口径＝考试当时班级）；空/非法值一律视为全部，
    返回值 classes 给出该方向出现过的班级（供页面下拉），class_name 回显实际生效值。
    allowed_classes（可选）：**账号可见班级**（None = 不限）。临界生名单带逐生总分、距线、
    各科分数与方向排名，属"跨班逐生明细"，必须按数据范围收敛——班主任只见本班、
    任课教师只见任教班、年级长及以上见整年级/全校（2026-10-15 用户口径）。
      · 传集合时：只保留这些班的行，班级下拉也随之收敛（非法 cls 仍回退为全部可见班）；
      · 传空集时：返回空名单（fail-closed，不回落成"全部班级"）。
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
    if allowed_classes is not None:
        rows_all = [r for r in rows_all if (r.class_name or '') in allowed_classes]
    # 班级下拉：该方向下出现过的班级（总分行口径＝考试当时班级，与班排/方向排一致）；
    # 非法值（手工改 URL）回退为空＝全部，避免只剩半截名单却看不出被筛过
    classes = sorted({(r.class_name or '') for r in rows_all if r.class_name})
    class_name = class_name if class_name in classes else ''
    if class_name:
        rows_all = [r for r in rows_all if (r.class_name or '') == class_name]
    # 2026-10-15 组合级划线：总分线按**学生自身组合**取线（物化生/物化地/物化政/史地政各一条），
    # 距线按各自组合的线算（回落方向线）
    line_of = {}
    for r in rows_all:
        sel = r.subject_selection or ''
        if sel and sel not in line_of:
            _l = rs.pick_layer(data, direction, TOTAL_SUBJECT, lname, selection=sel)
            if _l:
                line_of[sel] = _l[1]

    def _line_of(r):
        return line_of.get(r.subject_selection or '', line)

    below_rows, above_rows = [], []
    # 科目列取范围内学生实际有成绩的科目并集（走班选科不一致时列也能对齐）
    nos = set()
    for r in rows_all:
        gap = r.score - _line_of(r)
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
        gap = round(r.score - _line_of(r), 1)
        row = {'class_name': r.class_name or '—',
               'headteacher': ht.get((data.exam.grade, r.class_name or '—'), ''),
               'name': r.student_name,
               'score': r.score, 'status': status, 'gap': gap,
               'rank_dir': r.rank_dir}
        for s in subj_cols:
            row[s] = data.subj.get((r.student_no, s))
        return row

    rows = [to_row(r, '线下') for r in below_rows] + [to_row(r, '线上') for r in above_rows]
    # 线分展示：各组合线一致时显示分数，否则显示「按组合」（距线已逐生按各自组合的线算）
    _lows = set(line_of.values()) | {line}
    return {'exam': {'id': data.exam.id, 'name': data.exam.name},
            'direction': direction or '全部', 'layer': lname,
            'line': (line if len(_lows) <= 1 else '按组合'),
            'lines': line_of,          # 各组合的线（组合级划线，前端展示/核对用）
            'above': above, 'below': below,
            'above_n': len(above_rows), 'below_n': len(below_rows),
            'classes': classes, 'class_name': class_name,
            'subjects': [{'key': s, 'label': subject_display(s)} for s in subj_cols],
            'rows': rows}
