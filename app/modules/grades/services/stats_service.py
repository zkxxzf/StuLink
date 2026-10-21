# StuLink v1.19.50.61 2026-10-10
# 成绩统计分析服务：一次载入考试成绩数据，提供各 tab 需要的聚合（纯函数 + 内存计算）
# 口径遵循设计文档第 8 章：参考学生=有总分行（至少一应考科有分），不分学籍状态
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import logging
import math
import re
import statistics
import threading
import time
from collections import OrderedDict

from sqlalchemy import func

from app.extensions import db
from app.modules.grades.services.class_order import teaching_classes
from app.models.grades import Exam, ExamScore, ExamBand, SUBJECTS, TOTAL_SUBJECT, \
    subjects_of_selection

# ---- 统计口径（2026-10-17 用户需求）----
# ref      = 参考数：单科/总分有分数且 > 0 才算参加考试（0 分按未参考）
# enrolled = 班级实际人数：该班在籍学生数（缺考按 0 分计入均分），
#            单科分母再按「该生选科是否应考该科」收窄（语数外全考）
DENOM_REF = 'ref'
DENOM_ENROLLED = 'enrolled'
DENOM_CHOICES = (DENOM_REF, DENOM_ENROLLED)
# 选科组合（如「物化生」）里能标识某科的字；语数外不在组合里，默认全考
_SEL_CHAR = {'物理': '物', '历史': '史', '化学': '化',
             '生物': '生', '政治': '政', '地理': '地'}
_CORE_SUBJECTS = ('语文', '数学', '外语', '英语')

# 2026-10-11 审计：为缓存清理路径的异常留痕提供 logger
logger = logging.getLogger(__name__)


def _stddev(values):
    # 2026-10-12 质量审计 P2：原先这里返回**未 round 的 float**，而 compare_service /
    # pivot_service 的同名函数内部就 round 1 位 —— 三份实现语义相同但舍入位置不同，
    # 属"未抽象 + 隐蔽不一致"。统一为内部 round 1 位（调用点的 round(_, 1) 幂等，无行为变化）。
    if len(values) <= 1:
        return 0.0
    return round(statistics.stdev(values), 1)


# ---------- ExamData 进程级缓存（v1.13.1 汇报区性能） ----------
# 汇报区 5 个板块各自构造 ExamData 会把同一场考试的全量明细重复拉取 8~10 次，
# 是首屏加载慢的主因。ExamData 构造后只读（惰性属性均为缓存派生值、无懒加载关系），
# 故可跨请求共享；成绩/划线变更入口统一调 invalidate_exam_cache → clear_exam_data_cache。
# LRU 上限 3 场。一场含 2 万+ ExamScore ORM 对象，常驻约 30~50MB/场（3 场合计
# 90~150MB）——原注释写"一场全量约几 MB"与实际差一个量级（2026-10-14 性能审计更正）。
# TTL 900s 与汇报 payload 缓存一致。
_EXAM_DATA_TTL = 900
_EXAM_DATA_MAX = 3
_exam_data_cache = OrderedDict()
# waitress 多线程下 OrderedDict 的 move_to_end/popitem 非原子，需锁保护；
# 构建（全量拉取，耗时）也在锁内。
# ⚠ 2026-10-14 更正：这里**没有**双重检查（旧注释误称有）。并发冷启动时 N 个线程
# 会依次进锁各构建一次（慢但结果一致，最后一个覆盖前面的）。若要真正"只算一次"，
# 需按 exam_id 加构建中占位标记 + 每场一把锁，属后续优化，本次未改（避免并发语义风险）。
_exam_data_lock = threading.Lock()

# 趋势均值聚合（exam_score_means）进程级缓存：key=(grade, frozenset(subjects),
# direction, class_name)，与 ExamData 同 TTL；LRU 上限 200 覆盖全年级各考试复用。
_EXAM_MEANS_MAX = 200
_exam_means_cache = OrderedDict()


def cached_exam_data(exam_id):
    """取共享 ExamData；过期/超限自动重建。
    构造后把涉及的 ORM 实例 expunge 出会话：后续请求的 commit/teardown 不会使其过期，
    共享实例只读列值始终可用（惰性方法 band_list 走类级查询，同样安全）。"""
    now = time.time()
    with _exam_data_lock:
        ent = _exam_data_cache.get(exam_id)
        if ent and now - ent[0] < _EXAM_DATA_TTL:
            _exam_data_cache.move_to_end(exam_id)
            return ent[1]
        data = ExamData(exam_id)
        _detach_shared(data)
        _exam_data_cache[exam_id] = (time.time(), data)
        while len(_exam_data_cache) > _EXAM_DATA_MAX:
            _exam_data_cache.popitem(last=False)
        return data


def _detach_shared(data):
    """将 ExamData 持有的 ORM 实例脱离会话（防止 expire/detach 后读取报错）"""
    from sqlalchemy.exc import InvalidRequestError
    from app.extensions import db
    objs = list(data.rows)
    if data.exam is not None:
        objs.append(data.exam)
    if data.prev_exam is not None:
        objs.append(data.prev_exam)
    for o in objs:
        try:
            db.session.expunge(o)
        except InvalidRequestError as _e:  # noqa: BLE001
            # 2026-10-11 审计：对象本就不在会话中属预期，降为 debug 留痕（原为静默 pass）
            logger.debug('expunge 跳过（对象不在当前会话）: %s', _e)


def clear_exam_data_cache(exam_id=None):
    """成绩/划线变更后清空，防止读到旧快照（由 invalidate_exam_cache 调用）

    exam_id：指定时只丢弃该场的 ExamData（PERF-19）。此前无参数即全清，改一科学分
    会把全部 3 场常驻快照（每场 2 万+ ORM 对象、约 30~50MB）一起丢弃重建，
    导致任何人在改分后打开任意一场考试的分析页都要重新全量拉取。

    v1.19.49.35 审核修正：两个缓存都受 _exam_data_lock 保护，清空时同样持锁，
    避免与 cached_exam_data / exam_score_means 的读写产生 LRU 一致性问题（
    OrderedDict.clear() 在 CPython GIL 下虽安全，但与 move_to_end/popitem 无锁读写同存时仍不严谨）。"""
    with _exam_data_lock:
        if exam_id is None:
            _exam_data_cache.clear()
        else:
            _exam_data_cache.pop(exam_id, None)
        # 趋势均值聚合按年级缓存，与具体考试无关；成绩/划线变动同样使其失效，
        # 否则重导后年级趋势图会残留旧均值（与 ExamData 同样 900s TTL 口径）
        _exam_means_cache.clear()


class _Population:
    """ExamData 的只读「人群视图」（2026-10-16 组合维度分析）

    把 (方向, 选科组合) 作为默认过滤条件绑到 totals_of / scores_of_subject 上；
    其余属性/方法原样代理到底层 ExamData（共享缓存、零拷贝、只读）。
    这样 tab_service 只需在入口 `data = data.population(dr, sel)`，
    下游所有统计（表格、图、分层、临界生）就自动只算该人群，无需逐个改签名。
    """
    __slots__ = ('_d', '_dr', '_sel', '_denom')

    def __init__(self, data, direction=None, selection=None, denom=DENOM_REF):
        self._d = data
        self._dr = (direction or '') or None
        self._sel = (selection or '') or None
        # 统计口径随人群视图一起下传：下游只读 data.denom，无需层层加参
        self._denom = denom if denom in DENOM_CHOICES else DENOM_REF

    @property
    def denom(self):
        """当前统计口径：ref=参考数 / enrolled=班级实际人数"""
        return self._denom

    def __getattr__(self, name):
        return getattr(self._d, name)

    def totals_of(self, class_name=None, direction=None, selection=None):
        return self._d.totals_of(
            class_name=class_name,
            direction=direction if direction is not None else self._dr,
            selection=selection if selection is not None else self._sel)

    def scores_of_subject(self, subject, class_name=None, direction=None, selection=None):
        return self._d.scores_of_subject(
            subject, class_name=class_name,
            direction=direction if direction is not None else self._dr,
            selection=selection if selection is not None else self._sel)


class ExamData:
    """一场考试的只读内存数据视图"""

    # 默认口径：参考数（有分数且 >0 才算参加考试）；population() 可切成 enrolled
    denom = DENOM_REF

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
        # v1.17.x 性能：按班级预分组 snap，使 totals_of(class_name=...) 由
        # O(人数) 全量扫描降为 O(1) 查表；年级/班级/学科/教师各 tab 反复按班级取数
        # （平均上百个班 × 8000 人）是分析页计算慢的主因之一。
        self._by_class = {}
        for sn in self.snap.values():
            self._by_class.setdefault(sn['class_name'], []).append(sn)
        # 上一场同年级考试（趋势/进退步对比基准）
        # 2026-10-19 第十三轮审计：排除 draft（结构建好未导成绩）——
        # 与 trend_exams 的 status='imported' 同口径，否则新建草稿会让
        # 进退步/趋势基准变成一场没有成绩的考试。
        self.prev_exam = (Exam.query
                          .filter(Exam.grade == self.exam.grade,
                                  Exam.status != 'draft',
                                  (Exam.exam_date < self.exam.exam_date) |
                                  ((Exam.exam_date == self.exam.exam_date) &
                                   (Exam.id < self.exam.id)))
                          .order_by(Exam.exam_date.desc(), Exam.id.desc()).first())
        # 惰性：班级在籍名单（enrolled 口径才用，跨库查系统库 students）
        self._enrolled = None

    # ---------- 基础 ----------
    def full_marks(self):
        return self.exam.full_marks()

    def totals_of(self, class_name=None, direction=None, selection=None):
        """总分行快照（dict 行）；direction/selection 非空时按方向/选科组合过滤。

        2026-10-16：新增 selection（选科组合，如 物化生）—— 分析页要能「按组合」看数据。
        """
        if class_name:
            rows = self._by_class.get(class_name)
            if rows is None:
                return []
            if direction:
                rows = [t for t in rows if t['direction'] == direction]
            if selection:
                rows = [t for t in rows if (t.get('selection') or '') == selection]
            # v1.19.49.35 审核修正：不直接返回内部共享 list（_by_class 值），
            # 防止 caller 一旦 append/sort/pop 就会污染进程级 ExamData 缓存 900s。
            # list(rows) 仅拷贝外层引用，内层 snap 字典依旧只读共享，开销 O(班人数) 仍可接受。
            return list(rows)
        out = []
        for r in self.total_rows:
            if direction and r.direction != direction:
                continue
            if selection and (r.subject_selection or '') != selection:
                continue
            out.append(self.snap[r.student_no])
        return out

    def scores_of_subject(self, subject, class_name=None, direction=None, selection=None):
        """科目参考行（score not None）；selection 非空时只统计该选科组合的学生"""
        out = []
        for r in self.total_rows:
            if class_name and r.class_name != class_name:
                continue
            if direction and r.direction != direction:
                continue
            if selection and (r.subject_selection or '') != selection:
                continue
            s = self.subj.get((r.student_no, subject))
            if s is not None:
                out.append(s)
        return out

    def population(self, direction=None, selection=None, denom=DENOM_REF):
        """只读「人群视图」：把 (方向, 组合, 统计口径) 绑到取数方法上。

        下游（tab_service / stats_service / pivot_service）无需改签名 —— 在 tab 入口
        把 data 换成 population(dr, sel, denom)，里面所有 totals_of/scores_of_subject
        就自动按该人群收窄（取线仍走学生自身组合，组合优先）。共享底层缓存，零拷贝。
        """
        return _Population(self, direction, selection, denom)

    # ---------- 在籍人数（enrolled 口径） ----------
    def enrolled_stats(self):
        """{班级: [选科组合, ...]} —— 本场年级在籍学生（惰性查系统库 students）。

        成绩库与学籍库分离（grades.db / system.db），不能 JOIN，只能先聚合再内存拼；
        借读/在籍不在校也算在籍（用户 2026-10-17 口径：班级实际人数）。

        ⚠ 按 Student 的**当前**班级统计：重新分班后回看历史考试，人员会与成绩总分行
        的「考试当时班级」不一致（成绩口径仍以 exam_scores 总分行 为权威，见 CODEBUDDY）。
        """
        if self._enrolled is None:
            from app.models import Student
            rows = (db.session.query(Student.class_name, Student.subject_selection)
                    .filter(Student.grade == self.exam.grade).all())
            out = {}
            for cls, sel in rows:
                out.setdefault(cls, []).append((sel or '').strip())
            self._enrolled = out
        return self._enrolled

    def enrolled_n(self, class_name=None, direction=None, selection=None, subject=None):
        """在籍人数：按班（可再按方向/组合收窄）；单科只算该生应考该科的人。

        返回 0 表示查不到学籍（调用方回落到参考数，避免分母为 0 出空表）。
        """
        stats = self.enrolled_stats()
        classes = [class_name] if class_name else list(stats)
        total = 0
        for cls in classes:
            for sel in stats.get(cls, []):
                if selection and sel != selection:
                    continue
                if direction and _direction_of(sel) != direction:
                    continue
                # 总分（TOTAL_SUBJECT）不分科：在籍学生全都计入
                if subject and subject != TOTAL_SUBJECT \
                        and not _subject_required(sel, subject):
                    continue
                total += 1
        return total

    # ---------- 分层（exam_bands） ----------
    def band_list(self, direction, subject=TOTAL_SUBJECT, selection=''):
        """返回按 seq 升序(最低层在前)的 [(name, lower)]；无配置返回 []
        subject 默认 总分（原行为）；传学科名则取该科单科线，与总分线互相独立。
        2026-10-15 组合维度：selection 非空 = 取该选科组合（如 物化生）的线
        （组合总分线挂在所属方向下），无组合线时回落方向线/通用线；
        selection=''（默认）= 方向线/通用线，若没有则回落到该方向第一个组合线的
        层序列（物理方向总分只按组合划时，各报表的层名/列仍可用，分数线为回落值）。
        修复：按 (方向,学科,组合) 缓存查询结果——分段/分层循环中原来每学生一次 DB 查询（N+1），
        ExamData 实例按请求创建，实例级缓存不跨请求，分层配置保存后不会读到旧值
        """
        cache = getattr(self, '_band_cache', None)
        if cache is None:
            cache = self._band_cache = {}
        key = (direction, subject, selection)
        if key not in cache:
            q = ExamBand.query.filter_by(exam_id=self.exam.id, subject=subject)
            # 2026-10-19 第十三轮审计：只有**总分**按组合取线，其余各科按方向
            # （既有硬口径）。此前 selection 非空即走组合分支，任何场次残留
            # 单科组合线都会让该科判层/上线率与「按方向」口径分裂。
            if selection and subject == TOTAL_SUBJECT:
                bands = (q.filter(ExamBand.direction == direction,
                                  ExamBand.selection == selection)
                         .order_by(ExamBand.seq).all())
                if not bands:      # 该组合未单独划线 → 回落方向线/通用线
                    bands = (q.filter(ExamBand.direction.in_([direction, '']),
                                      ExamBand.selection == '')
                             .order_by(ExamBand.seq).all())
            else:
                bands = (q.filter(ExamBand.direction.in_([direction, '']),
                                  ExamBand.selection == '')
                         .order_by(ExamBand.seq).all())
                if not bands:      # 无方向线但有组合线 → 层序列回落第一组合线
                    alt = (q.filter(ExamBand.direction == direction,
                                    ExamBand.selection != '')
                           .order_by(ExamBand.selection, ExamBand.seq).all())
                    first = None
                    bands = []
                    for b in alt:   # 只取一个组合的层序列（层名各组合一致，
                        if first is None:
                            first = b.selection
                        elif b.selection != first:
                            break   # 否则同名层会按各组合分数线重复出现
                        bands.append(b)
            cache[key] = [(b.name, b.lower_value) for b in bands]
        return cache[key]

    def band_of_score(self, direction, score, subject=TOTAL_SUBJECT, selection=''):
        """score 所属层序号（0=最高层）；无配置返回 None
        band_list 按 seq 升序（seq1=最高层、下界最大），取首个 lower<=score 的层
        selection 非空时按「该生组合的线」判层（组合优先，band_list 内部回落方向线）
        """
        if score is None:
            return None
        bands = self.band_list(direction, subject, selection)
        if not bands:
            return None
        for i, (_name, lower) in enumerate(bands):
            if score >= lower:
                return i
        return -1  # 低于最低层下界（最低层下界=0，理论不出现）

    # ---------- 分数段 / 名次段 ----------
    def score_segments(self):
        """等距分数段 [(label, low, high)]，覆盖到最高分（实例内缓存）

        性能：segment_index() 对每名学生调用一次，若此处每次重算（内部要扫全量
        total_rows 求最高分）会退化成 O(n²)。1500 人 × 多张分段表时，这是分析页
        打开慢的主因。ExamData 按请求创建，实例级缓存不跨请求，改配置后不会读到旧值。
        """
        cache = getattr(self, '_seg_cache', None)
        if cache is not None:
            return cache
        width = self.exam.band_width()
        max_score = max((r.score for r in self.total_rows), default=0) or 0
        top = int(math.ceil(max_score / width) * width)
        segs = []
        for low in range(0, top, width):
            segs.append((f'[{low},{low + width})', low, low + width))
        if not segs:
            segs.append(('[0,0)', 0, 0))
        self._seg_cache = segs
        return segs

    def segment_index(self, score):
        segs = self.score_segments()
        for i, (_label, low, high) in enumerate(segs):
            if low <= score < high:
                return i
        return len(segs) - 1

    def rank_segments(self):
        """名次段 [(label, low, high)]，rank 1 起始（实例内缓存，理由同 score_segments）"""
        cache = getattr(self, '_rank_seg_cache', None)
        if cache is not None:
            return cache
        edges = self.exam.rank_bands()
        segs = []
        prev = 1
        for e in edges:
            segs.append((f'{prev}-{e}', prev, e))
            prev = e + 1
        segs.append((f'{prev}+', prev, 10 ** 9))
        self._rank_seg_cache = segs
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

    # ---------- 分批导入（一次导一科）识别 ----------
    @property
    def imported_subjects(self):
        """本场已有成绩的科目（分批导入时可能只导入了其中几科）"""
        cache = getattr(self, '_imported_subs', None)
        if cache is None:
            present = {r.subject for r in self.rows
                       if r.subject != TOTAL_SUBJECT and r.score is not None}
            self._imported_subs = [s for s in SUBJECTS if s in present]
        return self._imported_subs

    @property
    def expected_subjects(self):
        """本届学生应考科目并集（由选科组合推断）"""
        cache = getattr(self, '_expected_subs', None)
        if cache is None:
            sel = set()
            for r in self.total_rows:
                sel |= set(subjects_of_selection(r.subject_selection) or [])
            self._expected_subs = [s for s in SUBJECTS if s in sel]
        return self._expected_subs

    def partial_import(self):
        """分批导入中：仍有应考科目未导入（此时总分＝已导入科目合计，口径不完整）。

        返回 None 表示科目已齐（或未分科无法判断）；否则返回
        {'imported': [...], 'expected': [...], 'missing': [...]}。
        """
        exp = self.expected_subjects
        if not exp:
            return None
        missing = [s for s in exp if s not in self.imported_subjects]
        if not missing:
            return None
        return {'imported': self.imported_subjects, 'expected': exp,
                'missing': missing}


# ============ 指标计算 ============

def mean(values):
    return round(sum(values) / len(values), 1) if values else None


def fmt_rate(n, d):
    return round(n / d * 100, 1) if d else None


def _direction_of(selection):
    """选科组合 → 方向（物化生=物理、史政地=历史）；无选科（高一）返回 ''。"""
    first = (selection or '')[:1]
    if first == '物':
        return '物理'
    if first == '史':
        return '历史'
    return ''


def _subject_required(selection, subject):
    """该生是否应考某科：语数外全考；无选科（高一）全考；否则看组合里有没有该科字。"""
    if not selection or subject in _CORE_SUBJECTS:
        return True
    return _SEL_CHAR.get(subject, '') in selection


def ref_scores(data, subject=None, class_name=None, direction=None):
    """实考分数（口径 ref 的分子与分母来源）：只有 >0 的分数才算参加考试。"""
    if subject in (None, TOTAL_SUBJECT):
        rows = data.totals_of(class_name=class_name, direction=direction)
        return [t['score'] for t in rows if (t['score'] or 0) > 0]
    scores = data.scores_of_subject(subject, class_name=class_name, direction=direction)
    return [s for s in scores if (s or 0) > 0]


def base_count(data, subject=None, class_name=None, direction=None):
    """按当前口径取分母人数。

    ref      → 实考人数（有分数且 >0）
    enrolled → 该班在籍人数（单科按应考科目收窄）；查不到学籍时回落实考人数
    """
    if getattr(data, 'denom', DENOM_REF) == DENOM_ENROLLED:
        n = data.enrolled_n(
            class_name=class_name,
            direction=direction if direction is not None else getattr(data, '_dr', None),
            selection=getattr(data, '_sel', None), subject=subject)
        if n:
            return n
    return len(ref_scores(data, subject, class_name, direction))


def avg_by_denom(scores, n):
    """口径均分：enrolled 口径下缺考按 0 分计入（总分÷在籍人数），其余按实际分数平均。"""
    if not scores:
        return None
    if n and n > len(scores):
        return round(sum(scores) / n, 1)
    return mean(scores)


def class_summary_table(data, direction=None):
    """A1-T1 年级各班汇总表行（含分层计数列）"""
    fm = data.full_marks()
    rows = []
    # 只统计正规教学班（排除 不分班/已转出/离校 等），与全局对比页同一口径；
    # 行序＝班型分组 + 组内班号升序（2026-10-21 用户口径：同班型班级相邻）
    for cls in teaching_classes(data):
        totals = data.totals_of(class_name=cls, direction=direction)
        if not totals:
            continue
        # 2026-10-17 口径开关：count/avg 随 data.denom（enrolled 时缺考按 0 计入均分）
        scores = ref_scores(data, class_name=cls, direction=direction)
        if not scores:
            continue
        n_base = base_count(data, class_name=cls, direction=direction)
        # 分层按学生自身方向判定后并入班级；组合总分线按学生自身组合取线（组合优先）
        layers = {}
        for t in totals:
            sel = t.get('selection') or ''
            bands = data.band_list(t['direction'], selection=sel)
            if not bands:
                continue
            idx = data.band_of_score(t['direction'], t['score'], selection=sel)
            if idx is not None and 0 <= idx < len(bands):
                name = bands[idx][0]
                layers[name] = layers.get(name, 0) + 1
        # 班级方向按**本班学生**的方向取多数（2026-10-15 修）。
        # 原实现取 data.directions[0]（全考试方向列表首项，中文序 '历史' < '物理'），
        # 于是物理班全被标成"历史"（AI 报告据此写出错误方向）；选科前（directions 为空）
        # 还会 IndexError。个别插班生不改变班级方向（如历史班混入 2 名物理生仍是历史班）。
        dirs = [t['direction'] for t in totals if t['direction']]
        row = {
            'class_name': cls,
            'direction': max(set(dirs), key=dirs.count) if dirs else '',
            'count': n_base,
            'avg': avg_by_denom(scores, n_base),
            'std': round(_stddev(scores), 1),
            'max': max(scores),
            'min': min(scores),
            'layers': layers,
        }
        rows.append(row)
    return rows


def rate_layer_pair(bands):
    """原「及格率/优秀率」两列在本场划线里对应的层（2026-10-15 用户口径）。

    bands: band_list 输出 [(层名, 下界)]（seq 升序＝最高层在前）。
    - 优秀率位：层名含「优秀」的层，否则最高层；
    - 及格率位：层名含「及格」的层，否则最低层。
    返回 ((及格层名, 下界), (优秀层名, 下界))；未划线返回 (None, None)。
    """
    if not bands:
        return None, None

    def _pick(key, idx):
        for n, lo in bands:
            if key in (n or ''):
                return (n, lo)
        return bands[idx]

    return _pick('及格', -1), _pick('优秀', 0)


def rate_names_for(data, subject, directions=None):
    """原「及格率/优秀率」两列在本场划线里对应的层名（见 rate_layer_pair）。

    取第一个有划线的方向（未分科时 ['']）；都没有返回 (None, None)。
    directions 可显式传入（如学科分析只统计所选方向）。
    """
    dirs = (data.directions or ['']) if directions is None else directions
    for d in dirs:
        _p, _g = rate_layer_pair(data.band_list(d or '', subject))
        if _p or _g:
            return (_p[0] if _p else None, _g[0] if _g else None)
    return (None, None)


def band_lines(exam_id, subject=None):
    """本场划线明细（方向级 + 选科组合级）——「分数线」的唯一读取口径。

    2026-10-15 用户口径：划线分层向所有老师只读展示（网页＝划线页）——
    Web AI 与 MCP 同口径带上分数线，避免"网页能看、AI 说拿不到"（两端一致是铁律）。

    subject=None 取全部科目；带科目名只取该科。返回列表按
    「总分在前 → 科目 → 方向 → 组合 → 层序(seq 升序=低层在前)」排列，每项：
      {subject, direction, selection, scope, layer, lower}
    其中 scope 是给 AI/界面直接说人话的写法（物理 / 物理·物化生）；无划线返回 []。
    """
    q = ExamBand.query.filter_by(exam_id=exam_id)
    if subject:
        q = q.filter(ExamBand.subject == subject)
    rows = q.all()
    if not rows:
        return []
    order = {s: i for i, s in enumerate([TOTAL_SUBJECT] + list(SUBJECTS))}
    out = []
    for b in sorted(rows, key=lambda r: (order.get(r.subject, 99), r.direction or '',
                                         r.selection or '', r.seq)):
        d = b.direction or '全部'
        out.append({'subject': b.subject,
                    'direction': d,
                    'selection': b.selection or '',
                    'scope': f'{d}·{b.selection}' if b.selection else d,
                    'layer': b.name,
                    'lower': b.lower_value})
    return out


def subject_table(data, direction=None):
    """A1-T2 年级科目总表"""
    rows = []
    for sub in SUBJECTS:
        scores = ref_scores(data, sub, direction=direction)
        if not scores:
            continue
        # 2026-10-17 口径开关：count/avg/pass_rate/good_rate 均按所选分母
        n_base = base_count(data, sub, direction=direction)
        lines = data.subject_lines(sub)
        pass_n = sum(1 for s in scores if s >= lines['pass'])
        good_n = sum(1 for s in scores if s >= lines['excellent'])
        rows.append({
            'subject': sub,
            'count': n_base,
            'full': data.full_marks().get(sub, 100),
            'avg': avg_by_denom(scores, n_base),
            'max': max(scores),
            'min': min(scores),
            'std': round(_stddev(scores), 1),
            'pass_rate': fmt_rate(pass_n, n_base),
            'good_rate': fmt_rate(good_n, n_base),
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
    """历次考试（已导入）列表（时间升序）

    2026-10-19 第十三轮审计：原按 status='imported' 过滤，会漏掉
    `dirty`（已导入、改过分待重算）的场次 —— 它同样是有效考试。
    排除未导入的 draft 即可。
    """
    return (Exam.query.filter(Exam.grade == grade, Exam.status != 'draft')
            .order_by(Exam.exam_date.asc(), Exam.id.asc()).all())


# ============ 汇总打印版（用户样表 1:1 复刻） ============
#
# 2026-10-16 用户给了样表照片并强调「1:1 复刻」，列结构（三行列头）：
#   ① 班级 | 姓名（班主任） | 参考数
#   ② 211上线： 单上线 | 上线率
#   ③ 特控上线：单上线 | 进退步 | 上线率 | 本排名
#   ④ 本科上线：单上线 | 上线率 | 本排名
#   ⑤ 均分：   上届均分 | 排名 | 最高分
# 每科一张表（总赋/语文/数学/外语/物理/化学/生物/政治/地理），表内按方向分组
# （物理类 / 历史类），每组各班 + 「班均」「年级」两个汇总行。
# 「进退步」= 与上一场同层上线人数之差（多了为正）；「上届均分」= 上一场该班该科均分。
#
# 2026-10-20 用户反馈：本场没划 211 线，页面却还挂着「211上线」；均分组缺「去差均分」。
# 于是列组改为**按本场实际分层动态生成** —— 211/特控/本科 沿用样表的差异化列
# （2 / 4 / 3 列），其余层（前10…前550 等）统一 2 列；均分组新增「去差均分」
# （按班型剔除各班总分末尾 N 人，与全局对比同一口径）。层数多的场次由模板自动切
# A4 横向并缩字号，见 print_summary.html。

_PS_LAYER_ORDER = {'211': 0, '特控': 1, '本科': 2, 'rank': 3}
# 小程序端仍按这三个 key 取数（wx_manage_print_summary），补别名用
_PS_LEGACY = ('211', '特控', '本科')


def _ps_layer_kind(name):
    """层名 → 列组类型：211/特控/本科 沿用样表列，其余（前N 等）统一 2 列"""
    for kind in _PS_LEGACY:
        if kind in (name or ''):
            return kind
    return 'rank'


def _ps_layer_cells(kind):
    """每层占哪几列（顺序即显示顺序）"""
    if kind == '特控':
        return ['n', 'move', 'rate', 'rank']
    if kind == '本科':
        return ['n', 'rate', 'rank']
    return ['n', 'rate']


def _ps_layer_num(name):
    m = re.search(r'(\d+)', name or '')
    return int(m.group(1)) if m else 10 ** 9


def _ps_layers(data):
    """本场实际存在的分层（总分优先，取不到再看单科），按 211→特控→本科→前N 排好"""
    names = []

    def _collect(subject):
        for d in (data.directions or ['']):
            for n, _lo in data.band_list(d or '', subject):
                if n and n not in names:
                    names.append(n)

    _collect(TOTAL_SUBJECT)
    if not names:
        for subject in SUBJECTS:
            _collect(subject)
    names.sort(key=lambda n: (_PS_LAYER_ORDER[_ps_layer_kind(n)],
                              _ps_layer_num(n)))
    out = []
    for n in names:
        kind = _ps_layer_kind(n)
        # 样表列组名是「211上线 / 特控上线 / 本科上线」；名次段直接叫「前10」
        label = n if kind == 'rank' else (
            (n[:-1] if n.endswith('线') else n) + '上线')
        out.append({'key': n, 'label': label, 'kind': kind,
                    'cells': _ps_layer_cells(kind)})
    return out


def _ps_alias_legacy(row, layers):
    """小程序端按 211/特控/本科 取 key：补一份别名，老客户端不会变成空白"""
    for it in layers:
        if it['kind'] in _PS_LEGACY:
            row['bands'].setdefault(it['kind'], row['bands'].get(it['key']))


def _ps_find_band(bands, key):
    """从 [(层名, 下界)] 里取名字含 key 的层（完全相同优先）"""
    for n, lo in bands:
        if (n or '').strip() == key:
            return (n, lo)
    for n, lo in bands:
        if key in (n or ''):
            return (n, lo)
    return None


def _ps_class_teachers(grade):
    """班级 → 班主任姓名（user_class_links，多班主任取第一个）"""
    out = {}
    try:
        from app.models import User, UserClassLink
        links = UserClassLink.query.filter_by(grade=grade).all()
        # 2026-10-19 第十三轮审计：原循环内 User.query.get()（N+1，20~60 条额外 SQL）
        users = {u.id: u for u in User.query.filter(
            User.id.in_({lk.user_id for lk in links})).all()} if links else {}
        for lk in links:
            if lk.class_name in out:
                continue
            u = users.get(lk.user_id)
            if u is not None and (u.real_name or u.username):
                out[lk.class_name] = u.real_name or u.username
    except Exception as _e:  # noqa: BLE001  用户名只是附注，取不到不该让打印页 500
        logger.warning('print_summary 取班主任失败（已降级为空）: %s', _e)
    return out


def _ps_class_pass(data, sub, cls, d, layers, trim_nos=frozenset()):
    """某班某科**逐生**统计：分数列表 + 各层上线人数。

    ⚠️ 2026-10-16 用户要求复查「不同学科时上线数的计算是否正确」，查出真问题：
    原实现用 `band_list(direction, subject)`（**只传方向**）取一条线、再拿全班分数去比，
    可是分数线可能**按选科组合**划（exam 23 的总分线就是 物理三个组合各一条），
    方向级查询会「回落到第一个组合的线序列」，于是全班被套用**同一条组合的线** → 上线数错。

    权威口径（`pivot_service.layer_pass_stats`、各报表一致）：**每个学生按自己的
    选科组合取线**（`band_list(d, sub, 学生组合)`，该组合没线才回落方向线/通用线）。
    单科同理 —— 单科线若挂在组合下（如某组合单独的化学线），也必须逐生取。

    返回 (scores, on_map, band_dir, trim_avg)：
      scores    该班该科有效分数（均分/最高分用）
      on_map    {层名: n|None}；None = 本场没这条层（层名来自 `_ps_layers`）
      band_dir  方向级层序列（仅用于表头展示下界）
      trim_avg  去差均分（剔除本班总分末尾 N 人后重算，无去差规则时为全体均分）
    """
    bl_dir = data.band_list(d or '', sub)
    bl_cache = {}
    scores, trim_scores = [], []
    hit = {it['key']: 0 for it in layers}
    exists = {it['key']: False for it in layers}
    for r in data.totals_of(class_name=cls, direction=d):
        if sub == TOTAL_SUBJECT:
            sc = r.get('score')
        else:
            sc = data.subj.get((r.get('no'), sub))
        if sc is None:
            continue
        scores.append(sc)
        if r.get('no') not in trim_nos:
            trim_scores.append(sc)
        sel = r.get('selection') or ''
        if sel not in bl_cache:
            bl_cache[sel] = data.band_list(d or '', sub, sel) if sel else bl_dir
        bset = bl_cache[sel]
        for it in layers:
            b = _ps_find_band(bset, it['key'])
            if b is None:
                continue
            exists[it['key']] = True
            if sc >= b[1]:
                hit[it['key']] += 1
    on_map = {k: (hit[k] if exists[k] else None) for k in hit}
    trim_avg = round(mean(trim_scores), 1) if trim_scores else None
    return scores, on_map, bl_dir, trim_avg


def _ps_metrics(sc, bands, layers, on_map=None, prev_on=None,
                prev_avg=None, trim_avg=None):
    """一个班在该科的完整指标行（上线人数由 `_ps_class_pass` 逐生算好传进来）"""
    n = len(sc)
    row = {'n': n, 'avg': mean(sc), 'max': max(sc) if sc else None,
           'prev_avg': prev_avg, 'trim_avg': trim_avg, 'bands': {}}
    for it in layers:
        key = it['key']
        cnt = (on_map or {}).get(key)
        if cnt is None or not sc:
            row['bands'][key] = None
            continue
        b = _ps_find_band(bands, key)        # 仅用于展示层名/下界
        prev_n = (prev_on or {}).get(key)
        # ⚠️ `move` 必须**总是**存在（值可为 None）：模板在严格模式下取不存在的键会直接
        # 500 UndefinedError —— 2026-10-16 用户点开 exam 23（该场没有上一场）时就踩到了。
        row['bands'][key] = {
            'band_name': b[0] if b else '', 'lower': round(b[1], 1) if b else None,
            'n': cnt, 'rate': fmt_rate(cnt, n), 'rank': None,
            'move': (cnt - prev_n) if prev_n is not None else None}
    _ps_alias_legacy(row, layers)
    return row


def _ps_rank(rows, value_of, key='rank'):
    """组内按某指标降序**竞争排名**：同值同名次，下一名次按人数顺延（1,1,3…）。

    ⚠️ 2026-10-17 修：原实现 `enumerate(order, 1)` 直接编号（1,2,3），
    两班同分时名次会各差一位，与《成绩分析算法手册》3. 排名算法第 2 条
    「分数相同 → 名次相同（并列），下一名次按人数顺延」不一致。
    """
    order = sorted(rows, key=lambda r: -(value_of(r) if value_of(r) is not None else -1e9))
    prev_v, prev_rank = object(), 0
    for i, r in enumerate(order, 1):
        v = value_of(r)
        if v is not None and v == prev_v:
            rank = prev_rank                      # 与上一个同值 → 同名次
        else:
            rank = i
            prev_v, prev_rank = v, i
        r.setdefault('rank', {})[key] = rank


def print_summary(exam_id):
    """汇总打印版数据（1:1 复刻用户样表，见文件上部列结构说明）"""
    data = cached_exam_data(exam_id)
    exam = data.exam
    # 分组顺序照样表：物理类在前、历史类在后（data.directions 是中文排序，会反过来）
    _dirs = list(data.directions or [])
    directions = ([d for d in _dirs if d == '物理'] + [d for d in _dirs if d == '历史']
                  + [d for d in _dirs if d not in ('物理', '历史')]) or ['']
    teachers = _ps_class_teachers(exam.grade)
    prev = data.prev_exam
    pdata = cached_exam_data(prev.id) if prev is not None else None
    # 2026-10-20：列组按本场实际分层生成；去差名单两场各算一份（班型口径按场快照）
    from app.modules.grades.services import report_service as _rs
    layers = _ps_layers(data)
    trim_nos = _rs.trimmed_nos(data) if data.total_rows else set()
    ptrim = _rs.trimmed_nos(pdata) if pdata is not None else set()

    subjects = [TOTAL_SUBJECT] + list(SUBJECTS)
    blocks = []
    # 班级展示顺序（2026-10-21 用户口径：同班型相邻、组内班号升序）
    _cls_order = {c: i for i, c in enumerate(teaching_classes(data))}
    for sub in subjects:
        if sub != TOTAL_SUBJECT and not data.scores_of_subject(sub):
            continue                      # 本场没有的科目不出版块
        if sub == TOTAL_SUBJECT and not data.total_rows:
            continue
        groups = []
        for d in directions:
            # 本方向有哪些班：该科该方向有分数行即算（班可能混方向，按有数据归属）
            cls_rows = []
            # 只统计正规教学班（排除 不分班/已转出/离校），否则这些班会当成一个班显示、还参与排名
            for cls in teaching_classes(data):
                # 逐生按各自选科组合取线（2026-10-16 复查上线数口径后统一，见 _ps_class_pass）
                sc, on_map, bands, trim_avg = _ps_class_pass(
                    data, sub, cls, d, layers, trim_nos)
                if not sc:
                    continue
                # 上一场同口径：逐生按组合取线算「上次各层上线人数」；取不到再回落不分方向
                prev_sc, prev_on, _pbl, _ptrim = ([], {}, [], None)
                if pdata is not None:
                    prev_sc, prev_on, _pbl, _ptrim = _ps_class_pass(
                        pdata, sub, cls, d, layers, ptrim)
                    if not prev_sc:
                        prev_sc, prev_on, _pbl, _ptrim = _ps_class_pass(
                            pdata, sub, cls, None, layers, ptrim)
                r = _ps_metrics(sc, bands, layers, on_map, prev_on,
                                mean(prev_sc) if prev_sc else None, trim_avg)
                r['cls'] = cls
                r['teacher'] = teachers.get(cls, '')
                cls_rows.append(r)
            if not cls_rows:
                continue
            # 排名：各上线人数排名 + 均分排名（都在本方向内比）
            for it in layers:
                _ps_rank(cls_rows,
                         lambda r, k=it['key']:
                         (r['bands'].get(k) or {}).get('n'), it['key'])
            _ps_rank(cls_rows, lambda r: r['avg'], 'avg')
            for r in cls_rows:
                for it in layers:
                    band = r['bands'].get(it['key'])
                    if band is not None:
                        band['rank'] = r['rank'][it['key']]
                r['avg_rank'] = r['rank']['avg']
            cls_rows.sort(key=lambda r: _cls_order.get(r['cls'], 9999))
            groups.append({
                'direction': d or '全体',
                'rows': cls_rows,
                'sum_rows': _ps_sum_rows(cls_rows, sub, d, data, pdata,
                                         layers, trim_nos, ptrim),
            })
        if groups:
            blocks.append({'subject': sub, 'groups': groups})
    n_cols = 3 + sum(len(it['cells']) for it in layers) + 4
    return {'exam': {'name': exam.name, 'grade': exam.grade,
                     'date': exam.exam_date.strftime('%Y-%m-%d')},
            'prev_name': prev.name if prev is not None else '',
            'blocks': blocks, 'layers': layers, 'n_cols': n_cols}


def _ps_scores(data, sub, cls, direction):
    """某班某科在某方向的分数列表（总分走 totals_of，单科走 scores_of_subject）

    注意：`scores_of_subject()` 返回的是**分数数值列表**，`totals_of()` 返回的是行 dict
    （2026-10-16 自测时把两者混用导致 500：'float' object has no attribute 'get'）。
    """
    if data is None:
        return []
    if sub == TOTAL_SUBJECT:
        return [t['score'] for t in data.totals_of(class_name=cls, direction=direction)
                if t.get('score') is not None]
    return [s for s in data.scores_of_subject(sub, class_name=cls, direction=direction)
            if s is not None]


def _ps_trim_avg(data, sub, cls, direction, trim_nos):
    """去差均分：剔除各班总分末尾 N 人后的均分（与全局对比同一口径）"""
    if data is None:
        return None
    vals = []
    for r in data.totals_of(class_name=cls, direction=direction):
        if r.get('no') in trim_nos:
            continue
        sc = r.get('score') if sub == TOTAL_SUBJECT else data.subj.get(
            (r.get('no'), sub))
        if sc is not None:
            vals.append(sc)
    return round(mean(vals), 1) if vals else None


def _ps_sum_rows(rows, sub, direction, data, pdata=None, layers=None,
                 trim_nos=frozenset(), ptrim=frozenset()):
    """组末汇总行：班均（各班均分的加权平均/上线合计）、年级（该方向全体）"""
    layers = layers or []

    def _agg(label, n_total, avg_v, max_v, bands_n, prev_avg, trim_v):
        row = {'cls': label, 'teacher': '', 'n': n_total, 'avg': avg_v,
               'max': max_v, 'prev_avg': prev_avg, 'trim_avg': trim_v,
               'is_sum': True, 'avg_rank': None, 'bands': {}}
        for it in layers:
            key = it['key']
            cnt = (bands_n or {}).get(key)
            row['bands'][key] = (None if cnt is None else {
                'n': cnt, 'rank': None, 'move': None,
                'rate': fmt_rate(cnt, n_total), 'lower': None,
                'band_name': ''})
        _ps_alias_legacy(row, layers)
        return row

    all_sc = _ps_scores(data, sub, None, direction) if hasattr(data, 'classes') else []

    # 「班均」行（2026-10-17 修）：
    #   均分必须＝**全体参考学生的加权均分**，不能是「各班均分的简单平均」——
    #   各班人数不等时两者不同（手册《成绩分析算法手册》1. 术语表「参考数口径」：
    #   所有均值/率/分层/排名类指标均只基于参考学生）。举例：50 人班 400 分 + 10 人班 600 分，
    #   简单平均 500、全体均分 433，差 67 分。原来「参考数=各班求和、均分=各班简单平均」口径自相矛盾。
    #   参考数/上线人数仍＝各班求和（比率才对；2026-10-16 曾误用班级数当分母 → 上线率 1000%）。
    n_cls = len(rows)
    # 参考数与均分都只基于「正规教学班的学生」（rows 已排除 不分班/已转出），
    # 否则会出现「人数=正规班之和、均分=全部学生」的分子分母口径错位。
    n_stu = sum(r['n'] or 0 for r in rows)
    _den = sum(r['n'] or 0 for r in rows if r['avg'] is not None)
    _num = sum((r['avg'] or 0) * (r['n'] or 0) for r in rows if r['avg'] is not None)
    _avgs = [r['avg'] for r in rows if r['avg'] is not None]
    cls_avg = round(_num / _den, 1) if _den else (mean(_avgs) if _avgs else None)
    cls_n = {it['key']: sum((r['bands'].get(it['key']) or {}).get('n') or 0
                            for r in rows) for it in layers}
    _tn = sum(r['n'] or 0 for r in rows if r.get('trim_avg') is not None)
    _tv = sum((r['trim_avg'] or 0) * (r['n'] or 0)
              for r in rows if r.get('trim_avg') is not None)
    cls_trim = round(_tv / _tn, 1) if _tn else None
    _prev_all = _ps_scores(pdata, sub, None, direction)
    if not _prev_all:
        _prev_all = _ps_scores(pdata, sub, None, None)
    avg_row = _agg('班均', n_stu, cls_avg,
                   max(all_sc) if all_sc else max([r['max'] for r in rows
                                                   if r['max'] is not None] or [None]),
                   cls_n, mean(_prev_all) if _prev_all else None, cls_trim)
    avg_row['n_cls'] = n_cls

    # 年级行的上线人数＝**各班逐生结果之和**（口径与班级行严格一致；
    # 2026-10-16 复查前这里用"方向一条线 + 全体分数"重算，会与班级行口径不一致）
    def _sum_of(key):
        vals = [(r['bands'].get(key) or {}).get('n') for r in rows]
        vals = [v for v in vals if v is not None]
        return sum(vals) if vals else None

    grd_n = {it['key']: _sum_of(it['key']) for it in layers}
    # 「年级」行的「上次均分」：上一场该（方向×科目）全体学生的均分
    # （同方向取不到时回落不分方向，与逐班口径一致；取不到则空着显示 —）
    _prev_all = _ps_scores(pdata, sub, None, direction)
    if not _prev_all:
        _prev_all = _ps_scores(pdata, sub, None, None)
    grade_row = _agg('年级', len(all_sc), mean(all_sc),
                     max(all_sc) if all_sc else None, grd_n,
                     mean(_prev_all) if _prev_all else None,
                     _ps_trim_avg(data, sub, None, direction, trim_nos))
    return [avg_row, grade_row]


def exam_score_means(grade, subjects, direction=None, class_name=None):
    """历次考试（同年级 imported）在指定 subjects 上的均值（单次分组聚合查询）。

    用于趋势折线/总览表，避免为每场历史考试构造完整 ExamData（后者会加载全部明细行
    并查 prev_exam，是成绩分析打开慢的主因）。

    返回 {exam_id: {'name', 'date', 'means': {subject: avg|None},
                     'counts': {subject: int}}}，键按 trend_exams 顺序。
    direction / class_name 过滤被聚合的分数行。

    v1.17.x 性能：趋势聚合按「年级+科目+方向(+班级)」计算，与当前看哪场考试无关，
    却被每个考试的 grade/class 分析重复扫描全年级历史（25 场 × 8000 人 ≈ 22 万行）。
    进程级 LRU 缓存一次后，同年级所有考试的分析页直接复用，冷启动趋势段几乎瞬出。
    """
    key = (grade, frozenset(subjects), direction, class_name)
    with _exam_data_lock:
        ent = _exam_means_cache.get(key)
        if ent is not None and time.time() - ent[0] < _EXAM_DATA_TTL:
            # v1.19.49.35 审核修正：返回时拷贝外层与 means/counts 内层，
            # 避免 caller 就地修改污染进程级缓存（当前 caller 均为只读，修修正防未来变更）。
            cached = ent[1]
            return {eid: {**info,
                          'means': dict(info.get('means') or {}),
                          'counts': dict(info.get('counts') or {})}
                    for eid, info in cached.items()}
    result = _exam_score_means_uncached(grade, subjects, direction, class_name)
    with _exam_data_lock:
        _exam_means_cache[key] = (time.time(), result)
        while len(_exam_means_cache) > _EXAM_MEANS_MAX:
            _exam_means_cache.popitem(last=False)
    return result


def _exam_score_means_uncached(grade, subjects, direction=None, class_name=None):
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


def _bands_for_student(by_key, direction, selection):
    """按学生的 (方向, 组合) 从某场的层线表里取层序。

    by_key: {(方向, 组合): [(层名, 下界)]}。回落链与 ExamData.band_list 一致：
    (方向,组合) → (方向,通用) → (通用,通用) → 该方向任一组合线（总分组合线场景）。
    """
    for key in ((direction, selection), (direction, ''), ('', '')):
        b = by_key.get(key)
        if b:
            return b
    for (dd, ss), b in by_key.items():
        if ss and dd == direction:
            return b
    return []


def _layer_lower(bands, name):
    """层序里按层名取该层的下界；没有这条线返回 None"""
    for n, lo in bands:
        if n == name:
            return lo
    return None


def exam_trend_series(grade, subject, direction=None, class_name=None, need_rates=False,
                      pass_name=None, good_name=None):
    """历次考试（同年级 imported）在指定 subject 上的聚合序列（按时间升序）。

    全程只做聚合查询，不加载明细行。返回 list：
    {'id','name','date','avg','count','pass_rate','good_rate'（need_rates 时）}。
    用于学科/教师历次趋势表。

    2026-10-12 质量审计 P0-2：原实现每场考试 3 次查询，25 场历史 = 75 次查询；
    现改为一次查询取回全部 (exam_id, score)，内存里按各场自己的线求率。

    2026-10-15 用户口径：率**按各场自己绑定的划线**算（原按满分 60%/90% 固定线）——
    - 传 pass_name/good_name（历次表列名=本场层名）：逐场按**同名层**求率，
      该场没划这条线的学生不计入分母、整场都没有则记 None（前端显示 —）；
    - 都不传：回落到「及格率位＝含『及格』层否则最低层、优秀率位＝含『优秀』层否则最高层」
      （见 rate_layer_pair）。
    两者都逐生按自身方向/组合取线。
    """
    exams = trend_exams(grade)
    if not exams:
        return []
    ids = [e.id for e in exams]
    q = (ExamScore.query
         .filter(ExamScore.exam_id.in_(ids), ExamScore.subject == subject,
                 ExamScore.score.isnot(None)))
    if direction:
        q = q.filter(ExamScore.direction == direction)
    if class_name:
        q = q.filter(ExamScore.class_name == class_name)
    if not need_rates:
        rows = (q.group_by(ExamScore.exam_id)
                  .with_entities(ExamScore.exam_id,
                                 func.avg(ExamScore.score),
                                 func.count(ExamScore.score)).all())
        agg = {eid: (avg, cnt) for eid, avg, cnt in rows}
        out = []
        for ex in exams:
            got = agg.get(ex.id)
            if not got or not got[1]:
                continue
            out.append({'id': ex.id, 'name': ex.name,
                        'date': ex.exam_date.strftime('%Y-%m-%d'),
                        'avg': round(got[0], 1), 'count': got[1]})
        return out

    # need_rates：率按各场自己的划线算（线是考试级配置，无法在 SQL 里统一比较），
    # 一次取回 (exam_id, score, direction, selection) + 各场该科的层线，内存里求率。
    per_exam = {}
    for eid, sc, _d, _sel in q.with_entities(
            ExamScore.exam_id, ExamScore.score,
            ExamScore.direction, ExamScore.subject_selection).all():
        per_exam.setdefault(eid, []).append((sc, _d or '', _sel or ''))
    bands_by_exam = {}
    for b in (ExamBand.query
              .filter(ExamBand.exam_id.in_(ids), ExamBand.subject == subject)
              .order_by(ExamBand.seq).all()):
        bands_by_exam.setdefault(b.exam_id, {}).setdefault(
            (b.direction or '', b.selection or ''), []).append((b.name, b.lower_value))
    out = []
    for ex in exams:
        rows_ = per_exam.get(ex.id) or []
        if not rows_:
            continue
        cnt = len(rows_)
        rec = {'id': ex.id, 'name': ex.name,
               'date': ex.exam_date.strftime('%Y-%m-%d'),
               'avg': round(sum(r[0] for r in rows_) / cnt, 1), 'count': cnt}
        if need_rates:
            by_key = bands_by_exam.get(ex.id) or {}
            hit_p = hit_g = tot_p = tot_g = 0
            for sc, _d, _sel in rows_:
                bands = _bands_for_student(by_key, _d, _sel)
                if pass_name:
                    _plo = _layer_lower(bands, pass_name)
                else:
                    _pp = rate_layer_pair(bands)[0]
                    _plo = _pp[1] if _pp else None
                if _plo is not None:
                    tot_p += 1
                    if sc >= _plo:
                        hit_p += 1
                if good_name:
                    _glo = _layer_lower(bands, good_name)
                else:
                    _gg = rate_layer_pair(bands)[1]
                    _glo = _gg[1] if _gg else None
                if _glo is not None:
                    tot_g += 1
                    if sc >= _glo:
                        hit_g += 1
            rec['pass_rate'] = round(hit_p / tot_p * 100, 1) if tot_p else None
            rec['good_rate'] = round(hit_g / tot_g * 100, 1) if tot_g else None
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
