# StuLink v1.9.0 2026-09-03
# 成绩统计分析服务：一次载入考试成绩数据，提供各 tab 需要的聚合（纯函数 + 内存计算）
# 口径遵循设计文档第 8 章：参考学生=有总分行（至少一应考科有分），不分学籍状态
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import math
import statistics

from sqlalchemy import func

from app.models.grades import Exam, ExamScore, ExamBand, SUBJECTS, TOTAL_SUBJECT, \
    subjects_of_selection


def _stddev(values):
    if len(values) <= 1:
        return 0.0
    return statistics.stdev(values)


class ExamData:
    """一场考试的只读内存数据视图"""

    def __init__(self, exam_id):
        self.exam = Exam.query.get(exam_id)
        self.rows = ExamScore.query.filter_by(exam_id=exam_id).all()
        self.total_rows = [r for r in self.rows if r.subject == TOTAL_SUBJECT and r.score is not None]
        # (no, subject) -> score
        self.subj = {(r.student_no, r.subject): r.score for r in self.rows}
        self.snap = {}
        for r in self.total_rows:
            self.snap[r.student_no] = {
                'no': r.student_no, 'name': r.student_name, 'class_name': r.class_name,
                'direction': r.direction, 'selection': r.subject_selection,
                'score': r.score, 'rank_dir': r.rank_dir, 'rank_class': r.rank_class,
                'move': r.move_rank,
            }
        # 班级列表（保持班号顺序）
        classes = sorted({(r.class_name) for r in self.total_rows if r.class_name},
                         key=lambda c: int(''.join(filter(str.isdigit, c)) or 0))
        self.classes = classes
        self.directions = sorted({r.direction for r in self.total_rows if r.direction})
        # 上一场同年级考试（趋势/进退步对比基准）
        self.prev_exam = (Exam.query
                          .filter(Exam.grade == self.exam.grade,
                                  (Exam.exam_date < self.exam.exam_date) |
                                  ((Exam.exam_date == self.exam.exam_date) &
                                   (Exam.id < self.exam.id)))
                          .order_by(Exam.exam_date.desc(), Exam.id.desc()).first())
        self.prev_totals = {}
        if self.prev_exam:
            for r in ExamScore.query.filter_by(exam_id=self.prev_exam.id,
                                               subject=TOTAL_SUBJECT).all():
                if r.score is not None:
                    self.prev_totals[r.student_no] = (r.score, r.rank_dir)

    # ---------- 基础 ----------
    def full_marks(self):
        return self.exam.full_marks()

    def totals_of(self, class_name=None, direction=None):
        out = []
        for r in self.total_rows:
            if class_name and r.class_name != class_name:
                continue
            if direction and r.direction != direction:
                continue
            out.append(self.snap[r.student_no])
        return out

    def scores_of_subject(self, subject, class_name=None, direction=None):
        """科目参考行（score not None）"""
        out = []
        for r in self.total_rows:
            if class_name and r.class_name != class_name:
                continue
            if direction and r.direction != direction:
                continue
            s = self.subj.get((r.student_no, subject))
            if s is not None:
                out.append(s)
        return out

    # ---------- 分层（exam_bands） ----------
    def band_list(self, direction, subject=TOTAL_SUBJECT):
        """返回按 seq 升序(最低层在前)的 [(name, lower)]；无配置返回 []
        subject 默认 总分（原行为）；传学科名则取该科单科线，与总分线互相独立。
        修复：按 (方向,学科) 缓存查询结果——分段/分层循环中原来每学生一次 DB 查询（N+1），
        ExamData 实例按请求创建，实例级缓存不跨请求，分层配置保存后不会读到旧值
        """
        cache = getattr(self, '_band_cache', None)
        if cache is None:
            cache = self._band_cache = {}
        key = (direction, subject)
        if key not in cache:
            q = ExamBand.query.filter_by(exam_id=self.exam.id, subject=subject)
            bands = q.filter(ExamBand.direction.in_([direction, ''])).order_by(ExamBand.seq).all()
            cache[key] = [(b.name, b.lower_value) for b in bands]
        return cache[key]

    def band_of_score(self, direction, score, subject=TOTAL_SUBJECT):
        """score 所属层序号（0=最高层）；无配置返回 None
        band_list 按 seq 升序（seq1=最高层、下界最大），取首个 lower<=score 的层
        """
        if score is None:
            return None
        bands = self.band_list(direction, subject)
        if not bands:
            return None
        for i, (_name, lower) in enumerate(bands):
            if score >= lower:
                return i
        return -1  # 低于最低层下界（最低层下界=0，理论不出现）

    # ---------- 分数段 / 名次段 ----------
    def score_segments(self):
        """等距分数段 [(label, low, high)]，覆盖到最高分"""
        width = self.exam.band_width()
        max_score = max((r.score for r in self.total_rows), default=0) or 0
        top = int(math.ceil(max_score / width) * width)
        segs = []
        for low in range(0, top, width):
            segs.append((f'[{low},{low + width})', low, low + width))
        if not segs:
            segs.append(('[0,0)', 0, 0))
        return segs

    def segment_index(self, score):
        segs = self.score_segments()
        for i, (_label, low, high) in enumerate(segs):
            if low <= score < high:
                return i
        return len(segs) - 1

    def rank_segments(self):
        """名次段 [(label, low, high)]，rank 1 起始"""
        edges = self.exam.rank_bands()
        segs = []
        prev = 1
        for e in edges:
            segs.append((f'{prev}-{e}', prev, e))
            prev = e + 1
        segs.append((f'{prev}+', prev, 10 ** 9))
        return segs

    def rank_segment_index(self, rank):
        for i, (_label, low, high) in enumerate(self.rank_segments()):
            if low <= rank <= high:
                return i
        return len(self.rank_segments()) - 1

    # ---------- 科目线（及格/优秀/低分） ----------
    def subject_lines(self, subject):
        return self.exam.subject_lines(subject)

    # ---------- 各班应考科目（实际出现科目并集，保持 9 科序） ----------
    def class_subjects(self, class_name):
        """班内学生实际应考科目（依据选科组合，与方向无关地返回并集，按 SUBJECTS 顺序）"""
        sel_sets = []
        for r in self.total_rows:
            if r.class_name == class_name:
                sel = subjects_of_selection(r.subject_selection) or []
                sel_sets.append(set(sel))
        union = set()
        for s in sel_sets:
            union |= s
        return [s for s in SUBJECTS if s in union]


# ============ 指标计算 ============

def mean(values):
    return round(sum(values) / len(values), 1) if values else None


def fmt_rate(n, d):
    return round(n / d * 100, 1) if d else None


def class_summary_table(data, direction=None):
    """A1-T1 年级各班汇总表行（含分层计数列）"""
    fm = data.full_marks()
    rows = []
    for cls in data.classes:
        totals = data.totals_of(class_name=cls, direction=direction)
        if not totals:
            continue
        scores = [t['score'] for t in totals]
        # 分层按学生自身方向判定后并入班级
        layers = {}
        for t in totals:
            bands = data.band_list(t['direction'])
            if not bands:
                continue
            idx = data.band_of_score(t['direction'], t['score'])
            if idx is not None and 0 <= idx < len(bands):
                name = bands[idx][0]
                layers[name] = layers.get(name, 0) + 1
        row = {
            'class_name': cls,
            'direction': data.directions[0] if len({t['direction'] for t in totals}) == 1 else '混合',
            'count': len(totals),
            'avg': mean(scores),
            'std': round(_stddev(scores), 1),
            'max': max(scores),
            'min': min(scores),
            'layers': layers,
        }
        rows.append(row)
    return rows


def subject_table(data, direction=None):
    """A1-T2 年级科目总表"""
    rows = []
    for sub in SUBJECTS:
        scores = data.scores_of_subject(sub, direction=direction)
        if not scores:
            continue
        lines = data.subject_lines(sub)
        pass_n = sum(1 for s in scores if s >= lines['pass'])
        good_n = sum(1 for s in scores if s >= lines['excellent'])
        rows.append({
            'subject': sub,
            'count': len(scores),
            'full': data.full_marks().get(sub, 100),
            'avg': mean(scores),
            'max': max(scores),
            'min': min(scores),
            'std': round(_stddev(scores), 1),
            'pass_rate': fmt_rate(pass_n, len(scores)),
            'good_rate': fmt_rate(good_n, len(scores)),
        })
    return rows


def segment_table(data, class_name=None, direction=None, by_rank=False):
    """A1-T3 分数段 / A1-T4 名次段：段 × 各班人数 + 合计"""
    segs = data.rank_segments() if by_rank else data.score_segments()
    header = []
    totals = data.totals_of(class_name=class_name, direction=direction)
    rows = [{'label': s[0], 'counts': {}, 'total': 0} for s in segs]
    for t in totals:
        if by_rank:
            if not t['rank_dir']:
                continue
            idx = data.rank_segment_index(t['rank_dir'])
        else:
            idx = data.segment_index(t['score'])
        if not (0 <= idx < len(rows)):
            continue
        rows[idx]['counts'][t['class_name']] = rows[idx]['counts'].get(t['class_name'], 0) + 1
        rows[idx]['total'] += 1
    return segs, rows


def trend_exams(grade):
    """历次考试（imported）列表（时间升序）"""
    return (Exam.query.filter_by(grade=grade, status='imported')
            .order_by(Exam.exam_date.asc(), Exam.id.asc()).all())


def exam_score_means(grade, subjects, direction=None, class_name=None):
    """历次考试（同年级 imported）在指定 subjects 上的均值（单次分组聚合查询）。

    用于趋势折线/总览表，避免为每场历史考试构造完整 ExamData（后者会加载全部明细行
    并查 prev_exam，是成绩分析打开慢的主因）。

    返回 {exam_id: {'name', 'date', 'means': {subject: avg|None},
                     'counts': {subject: int}}}，键按 trend_exams 顺序。
    direction / class_name 过滤被聚合的分数行。
    """
    exams = trend_exams(grade)
    out = {e.id: {'name': e.name, 'date': e.exam_date.strftime('%Y-%m-%d')}
           for e in exams}
    ids = list(out.keys())
    if ids and subjects:
        q = (ExamScore.query
             .filter(ExamScore.exam_id.in_(ids),
                     ExamScore.subject.in_(subjects),
                     ExamScore.score.isnot(None)))
        if direction:
            q = q.filter(ExamScore.direction == direction)
        if class_name:
            q = q.filter(ExamScore.class_name == class_name)
        rows = (q.group_by(ExamScore.exam_id, ExamScore.subject)
                  .with_entities(ExamScore.exam_id, ExamScore.subject,
                                 func.avg(ExamScore.score), func.count(ExamScore.score))
                  .all())
        for eid, subj, avg, cnt in rows:
            info = out.setdefault(eid, {'name': '', 'date': ''})
            info.setdefault('means', {})[subj] = round(avg, 1) if avg is not None else None
            info.setdefault('counts', {})[subj] = cnt
    return out


def exam_trend_series(grade, subject, direction=None, class_name=None, need_rates=False):
    """历次考试（同年级 imported）在指定 subject 上的聚合序列（按时间升序）。

    全程只做聚合查询，不加载明细行。返回 list：
    {'id','name','date','avg','count','pass_rate','good_rate'（need_rates 时）}。
    用于学科/教师历次趋势表（含及格率/优秀率）。
    """
    out = []
    for ex in trend_exams(grade):
        base = ExamScore.query.filter_by(exam_id=ex.id, subject=subject)
        base = base.filter(ExamScore.score.isnot(None))
        if direction:
            base = base.filter(ExamScore.direction == direction)
        if class_name:
            base = base.filter(ExamScore.class_name == class_name)
        avg, cnt = base.with_entities(func.avg(ExamScore.score),
                                      func.count(ExamScore.score)).one()
        if not cnt:
            continue
        rec = {'id': ex.id, 'name': ex.name,
               'date': ex.exam_date.strftime('%Y-%m-%d'),
               'avg': round(avg, 1), 'count': cnt}
        if need_rates:
            lines = ex.subject_lines(subject)
            rec['pass_rate'] = round(
                base.filter(ExamScore.score >= lines['pass']).count() / cnt * 100, 1)
            rec['good_rate'] = round(
                base.filter(ExamScore.score >= lines['excellent']).count() / cnt * 100, 1)
        out.append(rec)
    return out



def box_five(values):
    """箱线五数概括 [min, q1, median, q3, max]；单样本直接返回自身（防四分位越界）"""
    if not values:
        return None
    vs = sorted(values)
    n = len(vs)
    if n == 1:
        v = vs[0]
        return [v, v, v, v, v]
    med = vs[n // 2] if n % 2 else (vs[n // 2 - 1] + vs[n // 2]) / 2

    def quartile(a):
        m = len(a)
        return a[m // 2] if m % 2 else (a[m // 2 - 1] + a[m // 2]) / 2

    return [vs[0], quartile(vs[:n // 2]), med, quartile(vs[(n + 1) // 2:]), vs[-1]]
