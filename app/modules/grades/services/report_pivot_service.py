# StuLink v1.9.2 2026-09-16
# 成绩汇报区指标引擎（板块三/四）：单班各科分析、单科各班分析（含任课教师）
# 口径与 report_service 完全一致，复用其层线/去差/百分比公共函数
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from app.models.grades import SUBJECTS, TOTAL_SUBJECT, subject_display, TeacherSubjectLink
from app.models import User
from app.modules.grades.services import stats_service as st
from app.modules.grades.services import report_service as rs

# 展示名 → 系统科目名（图4下拉传展示名时反查）
DISPLAY_TO_SYS = {subject_display(s): s for s in SUBJECTS}


def _teacher_names(grade, class_name=None, subject=None):
    """任课教师姓名映射：(班级,科目)→'姓名'；可只传班级或只传科目。
    同班同科多任教师（换师记录）按 user_id 去重顿号连接。"""
    q = TeacherSubjectLink.query.filter_by(grade=grade, active=True)
    links = q.all()
    uids = {lk.user_id for lk in links}
    users = User.query.filter(User.id.in_(uids), User.is_active.is_(True)).all() if uids else []
    names = {u.id: u.real_name for u in users}
    out, seen = {}, {}
    for lk in links:
        key = (lk.class_name, lk.subject)
        if class_name and lk.class_name != class_name:
            continue
        if subject and lk.subject != subject:
            continue
        nm = names.get(lk.user_id)
        bag = seen.setdefault(key, set())
        if nm and lk.user_id not in bag:
            bag.add(lk.user_id)
            out[key] = (out[key] + '、' + nm) if key in out else nm
    return out


def _top_layers(data, direction, k=2):
    """该方向总分前 k 个层（高→低），PPT 中即特控、本科"""
    return [n for n, _ in rs.layers_desc(data, direction)[:k]]


# ============ 板块三：单班各科成绩分析（对应 PPT 图3） ============

def class_subject_report(exam_id, class_name):
    """某班各科 特控(单/双)、本科(单)、去差均分；末行为总分。
    班内混方向时按人数最多的方向取层线（行政班通常单一方向）。"""
    data = st.cached_exam_data(exam_id)
    rows_all = [r for r in data.total_rows if (r.class_name or '—') == class_name]
    if not rows_all:
        return {'error': f'该考试没有「{class_name}」的成绩'}
    # 班内主方向（人数最多）；平票时物理优先，再按方向名，保证结果稳定不随查询顺序波动
    dirs = {}
    for r in rows_all:
        dirs[r.direction or ''] = dirs.get(r.direction or '', 0) + 1
    dir_pref = {'物理': 0, '历史': 1}
    direction = sorted(dirs, key=lambda d: (-dirs[d], dir_pref.get(d, 2), d))[0]
    layer_names = _top_layers(data, direction, 2)
    if not layer_names:
        return {'error': '总分尚未划线，请先在「划线分层」设置特控/本科线'}
    l1, l2 = layer_names[0], (layer_names[1] if len(layer_names) > 1 else None)
    total_l1 = rs.pick_layer(data, direction, TOTAL_SUBJECT, l1)
    total_l2 = rs.pick_layer(data, direction, TOTAL_SUBJECT, l2) if l2 else None
    t_low1 = total_l1[1] if total_l1 else None
    t_low2 = total_l2[1] if total_l2 else None
    trimmed = rs.trimmed_nos(data)
    teachers = _teacher_names(data.exam.grade, class_name=class_name)
    ht = rs.headteacher_map(data.exam.grade).get((data.exam.grade, class_name), '')

    def subject_row(sub):
        sl1 = rs.pick_layer(data, direction, sub, l1)
        sl2 = rs.pick_layer(data, direction, sub, l2) if l2 else None
        n = s1 = d1 = s2 = 0
        vals = []
        for r in rows_all:
            sc = data.subj.get((r.student_no, sub))
            if sc is None:
                continue
            n += 1
            if r.student_no not in trimmed:
                vals.append(sc)
            if sl1 and sc >= sl1[1]:
                s1 += 1
                if t_low1 is not None and r.score is not None and r.score >= t_low1:
                    d1 += 1
            if sl2 and sc >= sl2[1]:
                s2 += 1
        # 某科未划该层单科线时对应指标为 None（前端显示「—」），与「0 人上线」区分
        return {'subject': subject_display(sub), 'teacher': teachers.get((class_name, sub), ''),
                'count': n,
                'l1_n': s1 if sl1 else None, 'l1_rate': rs._pct(s1, n) if sl1 else None,
                'dual_n': d1 if sl1 else None, 'dual_rate': rs._pct(d1, n) if sl1 else None,
                'l2_n': s2 if sl2 else None, 'l2_rate': rs._pct(s2, n) if sl2 else None,
                'trim_avg': rs._avg(vals)}

    out_rows = [subject_row(s) for s in SUBJECTS
                if any(data.subj.get((r.student_no, s)) is not None for r in rows_all)]
    # 总分行（双上线列无意义，前端显示「-」）
    tn = len(rows_all)
    tv = [r.score for r in rows_all
          if r.student_no not in trimmed and r.score is not None]
    p1 = sum(1 for r in rows_all if t_low1 is not None and r.score is not None and r.score >= t_low1)
    p2 = sum(1 for r in rows_all if t_low2 is not None and r.score is not None and r.score >= t_low2)
    out_rows.append({'subject': '总分', 'teacher': ht, 'count': tn,
                     'l1_n': p1, 'l1_rate': rs._pct(p1, tn),
                     'dual_n': None, 'dual_rate': None,
                     'l2_n': p2, 'l2_rate': rs._pct(p2, tn),
                     'trim_avg': rs._avg(tv)})
    return {'exam': {'id': data.exam.id, 'name': data.exam.name},
            'direction': direction or '未分科', 'class_name': class_name,
            'student_n': tn, 'l1_name': l1, 'l2_name': l2, 'rows': out_rows}


# ============ 板块四：单科各班成绩分析（对应 PPT 图4） ============

def subject_class_report(exam_id, direction, subject_disp):
    """某方向某科 各班：教师/参考数/特控(单/双)/本科(单)/去差均分"""
    sub = DISPLAY_TO_SYS.get(subject_disp, subject_disp)
    data = st.cached_exam_data(exam_id)
    if not data.total_rows:
        return {'error': '该考试暂无成绩'}
    # 与板块一一致：未选方向时落到第一个有总分划线的方向（物理优先）
    direction = rs.resolve_direction(data, direction)
    layer_names = _top_layers(data, direction, 2)
    if not layer_names:
        return {'error': '总分尚未划线，请先在「划线分层」设置特控/本科线'}
    l1, l2 = layer_names[0], (layer_names[1] if len(layer_names) > 1 else None)
    sl1 = rs.pick_layer(data, direction, sub, l1)
    sl2 = rs.pick_layer(data, direction, sub, l2) if l2 else None
    if not sl1 and not sl2:
        return {'error': f'{direction}方向「{subject_disp}」尚未划线'}
    t1 = rs.pick_layer(data, direction, TOTAL_SUBJECT, l1)
    t2 = rs.pick_layer(data, direction, TOTAL_SUBJECT, l2) if l2 else None
    t_low1, t_low2 = (t1[1] if t1 else None), (t2[1] if t2 else None)
    trimmed = rs.trimmed_nos(data)
    teachers = _teacher_names(data.exam.grade, subject=sub)

    by_class = {}
    for r in data.total_rows:
        if direction and r.direction != direction:
            continue
        sc = data.subj.get((r.student_no, sub))
        if sc is None:
            continue
        by_class.setdefault(r.class_name or '—', []).append((r, sc))
    order = {c: i for i, c in enumerate(data.classes)}
    out_rows = []
    g_n = g_s1 = 0           # 年级该科参考人数、特控上线人数（基准率用）
    g_trim_sum = g_trim_cnt = 0  # 年级去差均分（人数加权，不能用各班均分平均）
    for cls in sorted(by_class, key=lambda c: order.get(c, 999)):
        items = by_class[cls]
        n = s1 = d1 = s2 = 0
        vals = []
        for r, sc in items:
            n += 1
            if r.student_no not in trimmed:
                vals.append(sc)
            if sl1 and sc >= sl1[1]:
                s1 += 1
                if t_low1 is not None and r.score is not None and r.score >= t_low1:
                    d1 += 1
            if sl2 and sc >= sl2[1]:
                s2 += 1
        g_n += n
        g_s1 += s1
        g_trim_sum += sum(vals)
        g_trim_cnt += len(vals)
        out_rows.append({'class_name': cls, 'teacher': teachers.get((cls, sub), ''),
                         'count': n,
                         'l1_n': s1 if sl1 else None,
                         'l1_rate': rs._pct(s1, n) if sl1 else None,
                         'dual_n': d1 if sl1 else None,
                         'dual_rate': rs._pct(d1, n) if sl1 else None,
                         'l2_n': s2 if sl2 else None,
                         'l2_rate': rs._pct(s2, n) if sl2 else None,
                         'trim_avg': rs._avg(vals)})
    grade = {'l1_rate': rs._pct(g_s1, g_n),
             'trim_avg': round(g_trim_sum / g_trim_cnt, 1) if g_trim_cnt else None,
             # 红绿标注阈值随接口下发，前后端口径一致，调阈值只改配置区
             'rate_pp': rs.STRONG_RATE_PP, 'avg_diff': rs.STRONG_AVG_DIFF}
    return {'exam': {'id': data.exam.id, 'name': data.exam.name},
            'direction': direction or '全部', 'subject': subject_disp,
            'l1_name': l1, 'l2_name': l2, 'grade': grade, 'rows': out_rows}
