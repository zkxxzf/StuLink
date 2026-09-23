# StuLink v1.18.0.0 2026-09-23
# 成绩汇报区 · 教师排名引擎：同方向同科目全体任课教师按指标聚合后排名
# 口径：同一位老师教多个班时，所教学生合并计算（率/去差均分才公平）；
#       去差仍按学生所在行政班的班型规则剔除；并列同名次，竞赛排名（1,2,2,4）。
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from app.models.grades import SUBJECTS, TOTAL_SUBJECT, subject_display, TeacherSubjectLink
from app.models import User
from app.modules.grades.services import stats_service as st
from app.modules.grades.services import report_service as rs

# 参与排名的指标（两层口径一致：单上线/双上线/去差均分；双上线＝单科且总分双过该层线）
RANK_KEYS = ('count', 'online_n', 'online_rate', 'dual_n', 'dual_rate', 'trim_avg')


def _rank_teachers(teachers, keys):
    """对教师列表按各指标降序赋同名次（竞赛排名：并列不占后续连续名次）。
    值为 None（如未划线）的教师不参与该指标排名，rank 对应值为 None。"""
    for k in keys:
        vals = sorted({t[k] for t in teachers if t[k] is not None}, reverse=True)
        rank_of = {v: i + 1 for i, v in enumerate(vals)}
        for t in teachers:
            t['rank'][k] = rank_of.get(t[k])
    teachers.sort(key=lambda t: (
        t['online_rate'] is None, -(t['online_rate'] or -1), t['name']))
    return teachers


def _aggregate(data, direction, rows, layer_name):
    """一层内按 (科目, 教师) 聚合所教全部学生的指标。
    返回 {科目展示名: [教师指标...]}；该科该层未划单科线时 online/dual 为 None。"""
    total_line = rs.pick_layer(data, direction, TOTAL_SUBJECT, layer_name)
    total_lower = total_line[1] if total_line else None
    trimmed = rs.trimmed_nos(data)
    # 班级×科目 → 教师（表上有唯一约束，一班一科一师）
    links = TeacherSubjectLink.query.filter_by(grade=data.exam.grade, active=True).all()
    teacher_of = {(lk.class_name, lk.subject): lk.user_id for lk in links}
    uids = {lk.user_id for lk in links}
    users = User.query.filter(User.id.in_(uids), User.is_active.is_(True)).all() if uids else []
    name_of = {u.id: u.real_name for u in users}
    # 各科该层单科线下界（预算一次，band_list 有实例缓存但避免循环内重复排序）
    sub_lower = {}
    for s in SUBJECTS:
        sl = rs.pick_layer(data, direction, s, layer_name)
        sub_lower[s] = sl[1] if sl else None

    buckets = {}
    for r in rows:
        for s in SUBJECTS:
            sc = data.subj.get((r.student_no, s))
            if sc is None:
                continue
            uid = teacher_of.get((r.class_name or '—', s))
            if uid is None:
                continue  # 该班该科未配任课教师，无法归属，不计教师排名
            b = buckets.setdefault((s, uid), {'n': 0, 'on': 0, 'dual': 0, 'vals': []})
            b['n'] += 1
            if r.student_no not in trimmed:
                b['vals'].append(sc)
            line = sub_lower[s]
            if line is not None and sc >= line:
                b['on'] += 1
                if (total_lower is not None and r.score is not None
                        and r.score >= total_lower):
                    b['dual'] += 1

    out = {}
    for (s, uid), b in buckets.items():
        line = sub_lower[s]
        has_line = line is not None
        t = {'uid': uid, 'name': name_of.get(uid, '—'),
             'count': b['n'],
             'online_n': b['on'] if has_line else None,
             'online_rate': rs._pct(b['on'], b['n']) if has_line else None,
             'dual_n': b['dual'] if has_line else None,
             'dual_rate': rs._pct(b['dual'], b['n']) if has_line else None,
             'trim_avg': rs._avg(b['vals']), 'rank': {}}
        out.setdefault(subject_display(s), []).append(t)
    return {disp: _rank_teachers(ts, RANK_KEYS) for disp, ts in out.items()}


def teacher_rank_report(exam_id, direction=''):
    """某考试某方向 教师×学科 排名（最高两层：特控含双上线、本科单上线）。"""
    data = st.cached_exam_data(exam_id)
    if not data.total_rows:
        return {'error': '该考试暂无成绩'}
    direction = rs.resolve_direction(data, direction)
    layer_names = [n for n, _ in rs.layers_desc(data, direction)[:2]]
    if not layer_names:
        return {'error': '总分尚未划线，请先在「划线分层」设置特控/本科线'}
    rows = [r for r in data.total_rows if not direction or r.direction == direction]
    result = {'direction': direction or '全部',
              'l1_name': layer_names[0],
              'l2_name': layer_names[1] if len(layer_names) > 1 else None,
              'l1': _aggregate(data, direction, rows, layer_names[0])}
    if len(layer_names) > 1:
        result['l2'] = _aggregate(data, direction, rows, layer_names[1])
    else:
        result['l2'] = {}
    return result
