# StuLink v1.18.0.0 2026-09-23
# 个人成绩查询与分析：教职工在自身权限范围内查询单个学生
# 聚合口径复用 stats_service（排名等已落库 exam_scores，不重算）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from datetime import datetime

from app.models.grades import Exam, ExamScore, TOTAL_SUBJECT, SUBJECTS, \
    CERT_SUBJECT_ORDER, DEFAULT_FULL_MARKS, subject_display, semester_of, grade_level_label
from app.models import Student
from app.modules.grades.services import stats_service as st
from app.modules.grades.services.scope import student_in_scope

# 成绩证明底部默认说明（生成时可由经办人在弹窗中改写；改写内容随证明快照固化并参与防伪签名）
DEFAULT_CERT_NOTE = '本证明由系统依据已导入成绩数据生成，排名按方向内统计；仅作在校成绩凭证，不代表最终学历结论。'
# 自定义说明最大长度（防御性限制，防止异常长文本写入快照）
CERT_NOTE_MAX = 500


def _rate(score, full):
    """得分率%（消除语数外150/其余100的满分差异，便于科目间横向比较）"""
    if score is None or not full:
        return None
    return round(score / full * 100, 1)


def _parse_date(s):
    if not s:
        return None
    for fmt in ('%Y-%m-%d', '%Y/%m/%d'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def student_basic(student_no):
    """学号(=student_number) → 基本信息；无则返回 None"""
    s = Student.query.filter_by(student_number=student_no).first()
    if not s:
        return None
    return {'no': s.student_number, 'name': s.name, 'grade': s.grade,
            'class_name': s.class_name, 'selection': s.subject_selection}


def cert_header(student_no):
    """成绩证明抬头字段（仅生成证明时写入快照，不进入普通查询接口）。

    成绩证明为对外文书，需姓名/性别/身份证号/学籍号/入学年月；
    Student.id_card_number 为透明加密字段，此处解密后按模板脱敏备用。
    """
    s = Student.query.filter_by(student_number=student_no).first()
    if not s:
        return {}
    id_card = s.id_card_number or ''
    try:
        enroll_year = int(''.join(ch for ch in str(s.grade) if ch.isdigit())[:4])
    except (TypeError, ValueError):
        enroll_year = None
    return {
        'name': s.name,
        'gender': s.gender or '',
        'id_card': id_card,
        # 公开核验页不展示完整身份证号，仅保留前6后4
        'id_card_masked': (id_card[:6] + '*' * max(len(id_card) - 10, 0) + id_card[-4:])
                           if len(id_card) > 10 else id_card,
        'student_no': s.student_number,
        'grade': s.grade,
        'class_name': s.class_name,
        'enroll_text': f'{enroll_year}年9月' if enroll_year else '',
    }


def build_cert_matrix(grade, exams, student_scores, student_sel, chosen_ids=None):
    """成绩认定矩阵——列=科目（带满分）、行=每次考试。

    单元格口径：未选/未参加/分数/满分（语数外150，其余100）。
    chosen_ids=None：每个时段取代表考试（优先期末）；
    chosen_ids=集合：仅纳入用户勾选的考试（同一时段可多场）。
    """
    buckets = {}
    for e in exams:
        term, sem = (e.term or ''), semester_of(e.exam_date)
        if not term or not sem:
            continue
        buckets.setdefault((term, sem), []).append(e)
    periods = sorted(buckets.keys())

    def _rep(lst):
        finals = [e for e in lst if (e.exam_type or '') == '期末']
        return max(finals or lst, key=lambda e: (e.exam_date, e.id))

    rep_ids = sorted(_rep(lst).id for lst in buckets.values())
    reps = []
    for k in periods:
        lst = buckets[k]
        if chosen_ids:
            chosen = [e for e in lst if e.id in chosen_ids]
            reps.extend((k, e) for e in sorted(chosen, key=lambda e: (e.exam_date, e.id)))
        else:
            reps.append((k, _rep(lst)))
    if not reps:
        return {'cols': [], 'rows': [], 'exam_names': [],
                'exam_ids': [], 'rep_ids': rep_ids}

    # 列头：科目（按 CERT_SUBJECT_ORDER 顺序，附带满分）
    cols = []
    for sub in CERT_SUBJECT_ORDER:
        cols.append({'key': sub, 'label': subject_display(sub),
                     'full': DEFAULT_FULL_MARKS.get(sub, 100)})

    # 行：每次考试
    rows = []
    for (term, sem), e in reps:
        label = sem + (e.exam_type or '')
        if sum(1 for (k2, _e2) in reps if k2 == (term, sem)) > 1:
            label += '·' + e.exam_date.strftime('%m-%d')
        row = {'exam_label': label, 'term': term, 'sem': sem}
        sel = st.subjects_of_selection(student_sel.get(e.id))
        for sub in CERT_SUBJECT_ORDER:
            sc = student_scores.get((e.id, sub))
            full = DEFAULT_FULL_MARKS.get(sub, 100)
            if sel is not None and sub not in sel:
                row[sub] = '未选'
            elif sc is None:
                row[sub] = '未参加'
            else:
                row[sub] = f'{sc:.1f}/{full}'
        rows.append(row)

    return {'cols': cols, 'rows': rows,
            'exam_names': [e.name for _k, e in reps],
            'exam_ids': [e.id for _k, e in reps], 'rep_ids': rep_ids}


def student_exams(student_no, grade, term='', exam_type='', date_from='', date_to=''):
    """该生在范围内、且满足筛选条件、且有总分行记录的考试（时间升序）"""
    q = Exam.query.filter(Exam.grade == grade)
    if term:
        q = q.filter(Exam.term == term)
    if exam_type:
        q = q.filter(Exam.exam_type == exam_type)
    df, dt = _parse_date(date_from), _parse_date(date_to)
    if df:
        q = q.filter(Exam.exam_date >= df)
    if dt:
        q = q.filter(Exam.exam_date <= dt)
    exams = q.order_by(Exam.exam_date.asc(), Exam.id.asc()).all()
    # v1.13.2 性能：只取 exam_id 列（覆盖索引 idx_scores_stu_subject_exam），
    # 不再实例化该生全部总分 ExamScore ORM 对象
    have = {r[0] for r in
            ExamScore.query.filter_by(student_no=student_no, subject=TOTAL_SUBJECT)
            .with_entities(ExamScore.exam_id).all()}
    return [e for e in exams if e.id in have]


def build_data(user, student_no, filters):
    """组装单个学生的查询 + 分析数据（tables/charts 形状与 tab_service 一致，前端可复用渲染）。

    filters: {term, exam_type, date_from, date_to, subject, exam_id}
    返回 dict；越权抛 PermissionError；无数据返回 meta.empty。
    """
    student_in_scope(user, student_no)
    basic = student_basic(student_no)
    if not basic:
        return {'error': '学生不存在'}

    grade = basic['grade']
    exams = student_exams(student_no, grade,
                          term=filters.get('term', ''),
                          exam_type=filters.get('exam_type', ''),
                          date_from=filters.get('date_from', ''),
                          date_to=filters.get('date_to', ''))

    # 预取该生在这些考试的全部成绩行
    student_scores = {}      # (exam_id, subject) -> score
    student_ranks = {}       # (exam_id, subject) -> (方向排名, 班排名)
    student_sel = {}         # exam_id -> 选科组合快照（证明矩阵判断未选用）
    student_total = {}       # exam_id -> ExamScore(总分行)
    if exams:
        ids = [e.id for e in exams]
        rows = (ExamScore.query
                .filter(ExamScore.student_no == student_no,
                        ExamScore.exam_id.in_(ids)).all())
        for r in rows:
            if r.score is not None:
                student_scores[(r.exam_id, r.subject)] = r.score
                student_ranks[(r.exam_id, r.subject)] = (r.rank_dir, r.rank_class)
            if r.subject == TOTAL_SUBJECT:
                student_total[r.exam_id] = r
                student_sel[r.exam_id] = r.subject_selection

    if not exams:
        return {'student': basic, 'exams': [], 'tables': {}, 'charts': {},
                'meta': {'empty': True, 'message': '该生在筛选条件下暂无成绩记录'}}

    # 选定单场（默认最新一场）
    ids = [e.id for e in exams]
    sel_id = filters.get('exam_id')
    if sel_id:
        try:
            sel_id = int(sel_id)
        except (TypeError, ValueError):
            sel_id = None
    if not sel_id or sel_id not in ids:
        sel_id = exams[-1].id
    subject_filter = (filters.get('subject') or '').strip()

    tables, charts = {}, {}

    # ---- T1 历次考试成绩列表 ----
    t1 = []
    for e in exams:
        tr = student_total.get(e.id)
        if not tr:
            continue
        t1.append({'exam': e.name, 'date': e.exam_date.strftime('%Y-%m-%d'),
                   'type': e.exam_type or '', 'total': tr.score,
                   'rank_class': tr.rank_class, 'rank_dir': tr.rank_dir,
                   'move': tr.move_rank, 'class_name': tr.class_name,
                   'direction': tr.direction})
    tables['exam_list'] = {
        'title': '历次考试成绩列表',
        'columns': [{'key': 'exam', 'label': '考试', 'type': 'text'},
                    {'key': 'date', 'label': '日期', 'type': 'text'},
                    {'key': 'type', 'label': '类型', 'type': 'text'},
                    {'key': 'total', 'label': '总分', 'type': 'num'},
                    {'key': 'rank_class', 'label': '班排名', 'type': 'int'},
                    {'key': 'rank_dir', 'label': '方向排名', 'type': 'int'},
                    {'key': 'move', 'label': '进退步', 'type': 'int'}],
        'rows': t1,
    }

    # ---- 选定单场：科目明细 + 对比 ----
    sel_exam = Exam.query.get(sel_id)
    # v1.13.2 性能：走共享缓存，不再每次查询全量重建 ExamData（压测 P95 4.2s 的根因）
    data = st.cached_exam_data(sel_id)
    fm = data.full_marks()
    tr = student_total.get(sel_id)
    cls_name = tr.class_name if tr else ''
    sel_subs = st.subjects_of_selection(tr.subject_selection) if tr else None
    subs = sel_subs or (data.class_subjects(cls_name) if cls_name else [])

    t2 = []
    for sub in subs:
        sc = student_scores.get((sel_id, sub))
        if sc is None:
            continue
        cls_s = data.scores_of_subject(sub, class_name=cls_name)
        grd_s = data.scores_of_subject(sub)
        lines = data.subject_lines(sub)
        full = fm.get(sub, 100)
        t2.append({'subject': sub, 'score': sc, 'full': full,
                   'rate': round(sc / full * 100, 1) if full else None,
                   'cls_avg': st.mean(cls_s), 'grd_avg': st.mean(grd_s),
                   'pass_line': lines['pass'],
                   'is_pass': '是' if sc >= lines['pass'] else '否'})
    if tr:
        t2.append({'subject': '总分', 'score': tr.score,
                   'full': sum(fm.get(s, 100) for s in subs) or '',
                   'rate': None,
                   'cls_avg': st.mean([t['score'] for t in data.totals_of(class_name=cls_name)]),
                   'grd_avg': st.mean([r.score for r in data.total_rows]),
                   'pass_line': '—', 'is_pass': '—'})
    tables['subject_detail'] = {
        'title': f'{sel_exam.name} 科目明细（标注班均/年级均）',
        'columns': [{'key': 'subject', 'label': '科目', 'type': 'text'},
                    {'key': 'score', 'label': '得分', 'type': 'num'},
                    {'key': 'full', 'label': '满分', 'type': 'int'},
                    {'key': 'rate', 'label': '得分率%', 'type': 'num'},
                    {'key': 'cls_avg', 'label': '班均', 'type': 'num'},
                    {'key': 'grd_avg', 'label': '年级均', 'type': 'num'},
                    {'key': 'pass_line', 'label': '及格线', 'type': 'num'},
                    {'key': 'is_pass', 'label': '及格', 'type': 'text'}],
        'rows': t2,
    }

    # ---- 趋势：总分（本人 vs 年级均值） ----
    grd_means = st.exam_score_means(grade, [TOTAL_SUBJECT])
    x, stu_line, grd_line = [], [], []
    for e in exams:
        x.append(e.exam_date.strftime('%m-%d'))
        stu_line.append(student_total[e.id].score if e.id in student_total else None)
        grd_line.append(grd_means.get(e.id, {}).get('means', {}).get(TOTAL_SUBJECT))
    charts['trend_total'] = {
        'type': 'line', 'title': '历次考试总分趋势（含年级均值）',
        'xAxis': x,
        'series': [{'name': '本人总分', 'data': stu_line},
                   {'name': '年级均值', 'data': grd_line}],
    }

    # ---- 单科趋势（聚焦科目） ----
    if subject_filter and subject_filter in SUBJECTS:
        sub_means = st.exam_score_means(grade, [subject_filter])
        sx, sl, sg = [], [], []
        for e in exams:
            sx.append(e.exam_date.strftime('%m-%d'))
            sl.append(student_scores.get((e.id, subject_filter)))
            sg.append(sub_means.get(e.id, {}).get('means', {}).get(subject_filter))
        charts['trend_subject'] = {
            'type': 'line', 'title': f'{subject_filter}历次成绩趋势',
            'xAxis': sx,
            'series': [{'name': f'本人{subject_filter}', 'data': sl},
                       {'name': '年级均值', 'data': sg}],
        }

    # ---- 单场各科对比（本人/班均/年级均） ----
    if tr and subs:
        cmp_subs = [s for s in subs if student_scores.get((sel_id, s)) is not None]
        charts['subject_compare'] = {
            'type': 'groupbar', 'title': f'{sel_exam.name} 各科对比（本人/班均/年级均）',
            'xAxis': cmp_subs,
            'series': [
                {'name': '本人', 'data': [student_scores.get((sel_id, s)) for s in cmp_subs]},
                {'name': '班均', 'data': [st.mean(data.scores_of_subject(s, class_name=cls_name)) for s in cmp_subs]},
                {'name': '年级均', 'data': [st.mean(data.scores_of_subject(s)) for s in cmp_subs]},
            ],
        }

    # ---- 分布：各班总分均值 + 标注该生位置 ----
    if tr:
        summary = st.class_summary_table(data)
        shown = [r['class_name'] for r in summary]
        charts['distribution'] = {
            'type': 'bar', 'title': '本场各班总分均值（标注该生位置）',
            'xAxis': shown,
            'series': [{'name': '班总分均值', 'data': [r['avg'] for r in summary]}],
            'markLine': tr.score,
        }

    # ---- 成绩认定矩阵（科目 × 学年学期时段，纸质证明模板口径） ----
    # v1.12.2：支持用户自选纳入证明的考试（cert_exam_ids），不传则按默认代表考试口径
    chosen_ids = filters.get('cert_exam_ids')
    if chosen_ids:
        idset = {e.id for e in exams}
        chosen_ids = {int(x) for x in chosen_ids if str(x).lstrip('-').isdigit()} & idset
    cert_matrix = build_cert_matrix(grade, exams, student_scores, student_sel,
                                    chosen_ids=chosen_ids or None)

    # ---- 分科纵向对比：全科目趋势 / 排名波动 / 科目间相对表现 ----
    exam_full = {e.id: e.full_marks() for e in exams}
    all_subs = [s for s in CERT_SUBJECT_ORDER
                if any((e.id, s) in student_scores for e in exams)]
    x_labels = [e.exam_date.strftime('%m-%d') for e in exams]

    if all_subs:
        # 趋势：统一用得分率，消除语数外150/其余100的满分差异，各科可比
        charts['subject_trend'] = {
            'type': 'line', 'title': '各科成绩趋势（得分率%，消除满分差异）',
            'xAxis': x_labels,
            'series': [{'name': subject_display(s),
                        'data': [_rate(student_scores.get((e.id, s)),
                                       exam_full[e.id].get(s, 100)) for e in exams]}
                       for s in all_subs],
        }
        # 排名波动：方向排名，Y 轴反向（名次越小越靠上）
        charts['rank_trend'] = {
            'type': 'line', 'title': '各科排名波动（方向排名，曲线越靠上越好）',
            'xAxis': x_labels, 'inverse': True,
            'series': [{'name': subject_display(s),
                        'data': [(student_ranks.get((e.id, s)) or (None, None))[0]
                                 for e in exams]}
                       for s in all_subs],
        }

    # 相对表现：最近一场本人 vs 班均（得分率雷达）
    radar_subs = [s for s in subs if student_scores.get((sel_id, s)) is not None]
    if radar_subs:
        charts['subject_radar'] = {
            'type': 'radar', 'title': f'{sel_exam.name} 各科相对表现（得分率%）',
            'indicators': [{'name': subject_display(s), 'max': 100} for s in radar_subs],
            'series': [
                {'name': '本人', 'data': [_rate(student_scores.get((sel_id, s)),
                                                fm.get(s, 100)) for s in radar_subs]},
                {'name': '班均', 'data': [_rate(st.mean(
                    data.scores_of_subject(s, class_name=cls_name)),
                    fm.get(s, 100)) for s in radar_subs]},
            ],
        }

    # ---- 各科最新成绩与排名波动（对比本人上一次该科考试） ----
    rank_rows = []
    for sub in all_subs:
        pts = [(e, student_scores[(e.id, sub)]) for e in exams
               if (e.id, sub) in student_scores]
        if not pts:
            continue
        e_last, s_last = pts[-1]
        e_prev, s_prev = pts[-2] if len(pts) > 1 else (None, None)
        r_last = student_ranks.get((e_last.id, sub)) or (None, None)
        r_prev = (student_ranks.get((e_prev.id, sub)) or (None, None)) if e_prev else (None, None)
        full = exam_full[e_last.id].get(sub, 100)
        rank_rows.append({
            'subject': subject_display(sub), 'exam': e_last.name,
            'score': s_last, 'full': full, 'rate': _rate(s_last, full),
            'rank_dir': r_last[0], 'rank_class': r_last[1],
            'score_diff': None if s_prev is None else round(s_last - s_prev, 1),
            # 与 move_rank 同口径：正值 = 名次数字变大 = 后退（前端标红）
            'rank_move': None if (r_prev[0] is None or r_last[0] is None)
                         else int(r_last[0] - r_prev[0]),
        })
    if rank_rows:
        tables['subject_rank'] = {
            'title': '各科最新成绩与排名波动（对比本人上一次该科考试）',
            'columns': [
                {'key': 'subject', 'label': '科目', 'type': 'text'},
                {'key': 'exam', 'label': '最近考试', 'type': 'text'},
                {'key': 'score', 'label': '得分', 'type': 'num'},
                {'key': 'full', 'label': '满分', 'type': 'int'},
                {'key': 'rate', 'label': '得分率%', 'type': 'num'},
                {'key': 'rank_dir', 'label': '方向排名', 'type': 'int'},
                {'key': 'rank_class', 'label': '班排名', 'type': 'int'},
                {'key': 'score_diff', 'label': '分数变动', 'type': 'num'},
                {'key': 'rank_move', 'label': '排名变动', 'type': 'int'},
            ],
            'rows': rank_rows,
        }

    return {
        'student': basic,
        'cert_matrix': cert_matrix,
        'exams': [{'id': e.id, 'name': e.name, 'date': e.exam_date.strftime('%Y-%m-%d'),
                   'type': e.exam_type or '', 'term': e.term or '',
                   'semester': semester_of(e.exam_date)} for e in exams],
        'selected_exam_id': sel_id,
        'tables': tables, 'charts': charts,
        'meta': {'empty': False, 'selected_exam': sel_exam.name,
                 'subject': subject_filter,
                 'direction': tr.direction if tr else '',
                 'class_name': cls_name},
    }
