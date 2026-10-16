# StuLink v1.19.49.36 2026-10-10
# 成绩统计分析服务：一次载入考试成绩数据，提供各 tab 需要的聚合（纯函数 + 内存计算）
# 口径遵循设计文档第 8 章：参考学生=有总分行（至少一应考科有分），不分学籍状态
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import logging
import math
import statistics
import threading
import time
from collections import OrderedDict

from sqlalchemy import func

from app.models.grades import Exam, ExamScore, ExamBand, SUBJECTS, TOTAL_SUBJECT, \
    subjects_of_selection

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
    共享实例只读列值始终可用（惰性属性 band_list/prev_totals 走类级查询，同样安全）。"""
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

    v1.19.49.36 审核修正：两个缓存都受 _exam_data_lock 保护，清空时同样持锁，
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
    __slots__ = ('_d', '_dr', '_sel')

    def __init__(self, data, direction=None, selection=None):
        self._d = data
        self._dr = (direction or '') or None
        self._sel = (selection or '') or None

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
        self.prev_exam = (Exam.query
                          .filter(Exam.grade == self.exam.grade,
                                  (Exam.exam_date < self.exam.exam_date) |
                                  ((Exam.exam_date == self.exam.exam_date) &
                                   (Exam.id < self.exam.id)))
                          .order_by(Exam.exam_date.desc(), Exam.id.desc()).first())
        # 惰性：仅班级分析（进退步对比）需要上一场成绩，年级/学科/教师分析用不到，
        # 故改为首次访问时才查，避免每次构造 ExamData 都白拉一场全量总分行
        self._prev_totals = None

    @property
    def prev_totals(self):
        """上一场考试 {学号: (总分, 方向排名)}（惰性加载）"""
        if self._prev_totals is None:
            out = {}
            if self.prev_exam:
                for r in ExamScore.query.filter_by(exam_id=self.prev_exam.id,
                                                   subject=TOTAL_SUBJECT).all():
                    if r.score is not None:
                        out[r.student_no] = (r.score, r.rank_dir)
            self._prev_totals = out
        return self._prev_totals

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
            # v1.19.49.36 审核修正：不直接返回内部共享 list（_by_class 值），
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

    def population(self, direction=None, selection=None):
        """只读「人群视图」：把 (方向, 组合) 作为默认过滤条件绑到取数方法上。

        下游（tab_service / stats_service / pivot_service）无需改签名 —— 在 tab 入口
        把 data 换成 population(dr, sel)，里面所有 totals_of/scores_of_subject 就自动
        按该人群收窄（取线仍走学生自身组合，组合优先）。共享底层缓存，零拷贝。
        """
        return _Population(self, direction, selection)

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
            if selection:
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


def class_summary_table(data, direction=None):
    """A1-T1 年级各班汇总表行（含分层计数列）"""
    fm = data.full_marks()
    rows = []
    for cls in data.classes:
        totals = data.totals_of(class_name=cls, direction=direction)
        if not totals:
            continue
        scores = [t['score'] for t in totals]
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
            'count': len(totals),
            'avg': mean(scores),
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
# 「211 / 特控 / 本科」直接取本场该（方向×科目）的分层线（库里的层名就是这三个）；
# 「进退步」= 与上一场同层上线人数之差（多了为正）；「上届均分」= 上一场该班该科均分。

_PS_TAGS = (('211', '211', '211上线'),
            ('特控', '特控', '特控上线'),
            ('本科', '本科', '本科上线'))


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
        for lk in links:
            if lk.class_name in out:
                continue
            u = User.query.get(lk.user_id)
            if u is not None and (u.real_name or u.username):
                out[lk.class_name] = u.real_name or u.username
    except Exception as _e:  # noqa: BLE001  用户名只是附注，取不到不该让打印页 500
        logger.warning('print_summary 取班主任失败（已降级为空）: %s', _e)
    return out


def _ps_class_pass(data, sub, cls, d):
    """某班某科**逐生**统计：分数列表 + 各层上线人数。

    ⚠️ 2026-10-16 用户要求复查「不同学科时上线数的计算是否正确」，查出真问题：
    原实现用 `band_list(direction, subject)`（**只传方向**）取一条线、再拿全班分数去比，
    可是分数线可能**按选科组合**划（exam 23 的总分线就是 物理三个组合各一条），
    方向级查询会「回落到第一个组合的线序列」，于是全班被套用**同一条组合的线** → 上线数错。

    权威口径（`pivot_service.layer_pass_stats`、各报表一致）：**每个学生按自己的
    选科组合取线**（`band_list(d, sub, 学生组合)`，该组合没线才回落方向线/通用线）。
    单科同理 —— 单科线若挂在组合下（如某组合单独的化学线），也必须逐生取。

    返回 (scores, on_map, band_dir)：
      scores    该班该科有效分数（均分/最高分用）
      on_map    {'211': n|None, '特控': n|None, '本科': n|None}；None = 本场没这条层
      band_dir  方向级层序列（仅用于表头展示下界）
    """
    bl_dir = data.band_list(d or '', sub)
    bl_cache = {}
    scores = []
    hit = {k: 0 for k, _a, _b in _PS_TAGS}
    exists = {k: False for k, _a, _b in _PS_TAGS}
    for r in data.totals_of(class_name=cls, direction=d):
        if sub == TOTAL_SUBJECT:
            sc = r.get('score')
        else:
            sc = data.subj.get((r.get('no'), sub))
        if sc is None:
            continue
        scores.append(sc)
        sel = r.get('selection') or ''
        if sel not in bl_cache:
            bl_cache[sel] = data.band_list(d or '', sub, sel) if sel else bl_dir
        bset = bl_cache[sel]
        for key, _a, _b in _PS_TAGS:
            b = _ps_find_band(bset, key)
            if b is None:
                continue
            exists[key] = True
            if sc >= b[1]:
                hit[key] += 1
    on_map = {k: (hit[k] if exists[k] else None) for k, _a, _b in _PS_TAGS}
    return scores, on_map, bl_dir


def _ps_metrics(sc, bands, on_map=None, prev_n=None, prev_avg=None):
    """一个班在该科的完整指标行（上线人数由 `_ps_class_pass` 逐生算好传进来）"""
    n = len(sc)
    row = {'n': n, 'avg': mean(sc), 'max': max(sc) if sc else None,
           'prev_avg': prev_avg, 'bands': {}}
    for key, _k2, _label in _PS_TAGS:
        cnt = (on_map or {}).get(key)
        if cnt is None or not sc:
            row['bands'][key] = None
            continue
        b = _ps_find_band(bands, key)        # 仅用于展示层名/下界
        # ⚠️ `move` 必须**总是**存在（值可为 None）：模板在严格模式下取不存在的键会直接
        # 500 UndefinedError —— 2026-10-16 用户点开 exam 23（该场没有上一场）时就踩到了。
        row['bands'][key] = {
            'band_name': b[0] if b else '', 'lower': round(b[1], 1) if b else None,
            'n': cnt, 'rate': fmt_rate(cnt, n), 'rank': None,
            'move': (cnt - prev_n) if prev_n is not None else None}
    return row


def _ps_rank(rows, value_of, key='rank'):
    """组内按某指标降序排名（None 排最后）"""
    order = sorted(rows, key=lambda r: -(value_of(r) if value_of(r) is not None else -1e9))
    for i, r in enumerate(order, 1):
        r.setdefault('rank', {})[key] = i


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

    subjects = [TOTAL_SUBJECT] + list(SUBJECTS)
    blocks = []
    for sub in subjects:
        if sub != TOTAL_SUBJECT and not data.scores_of_subject(sub):
            continue                      # 本场没有的科目不出版块
        if sub == TOTAL_SUBJECT and not data.total_rows:
            continue
        groups = []
        for d in directions:
            # 本方向有哪些班：该科该方向有分数行即算（班可能混方向，按有数据归属）
            cls_rows = []
            for cls in data.classes:
                # 逐生按各自选科组合取线（2026-10-16 复查上线数口径后统一，见 _ps_class_pass）
                sc, on_map, bands = _ps_class_pass(data, sub, cls, d)
                if not sc:
                    continue
                # 上一场同口径：逐生按组合取线算「上次各层上线人数」；取不到再回落不分方向
                prev_sc, prev_on, _pbl = ([], {}, [])
                if pdata is not None:
                    prev_sc, prev_on, _pbl = _ps_class_pass(pdata, sub, cls, d)
                    if not prev_sc:
                        prev_sc, prev_on, _pbl = _ps_class_pass(pdata, sub, cls, None)
                r = _ps_metrics(sc, bands, on_map,
                                prev_n=prev_on.get('特控'),
                                prev_avg=mean(prev_sc) if prev_sc else None)
                r['cls'] = cls
                r['teacher'] = teachers.get(cls, '')
                cls_rows.append(r)
            if not cls_rows:
                continue
            # 排名：各上线人数排名 + 均分排名（都在本方向内比）
            for key, _k2, _l in _PS_TAGS:
                _ps_rank(cls_rows,
                         lambda r, k=key: (r['bands'].get(k) or {}).get('n'), key)
            _ps_rank(cls_rows, lambda r: r['avg'], 'avg')
            for r in cls_rows:
                for key, _k2, _l in _PS_TAGS:
                    it = r['bands'].get(key)
                    if it is not None:
                        it['rank'] = r['rank'][key]
                r['avg_rank'] = r['rank']['avg']
            cls_rows.sort(key=lambda r: r['cls'])
            groups.append({
                'direction': d or '全体',
                'rows': cls_rows,
                'sum_rows': _ps_sum_rows(cls_rows, sub, d, data, pdata),
            })
        if groups:
            blocks.append({'subject': sub, 'groups': groups})
    return {'exam': {'name': exam.name, 'grade': exam.grade,
                     'date': exam.exam_date.strftime('%Y-%m-%d')},
            'prev_name': prev.name if prev is not None else '',
            'blocks': blocks}


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


def _ps_sum_rows(rows, sub, direction, data, pdata=None):
    """组末汇总行：班均（各班均分的平均/上线合计）、年级（该方向全体）"""
    def _agg(label, n_total, avg_v, max_v, bands_n, prev_avg):
        return {'cls': label, 'teacher': '', 'n': n_total,
                'avg': avg_v, 'max': max_v, 'prev_avg': prev_avg, 'is_sum': True,
                'bands': {k: ({'n': (bands_n or {}).get(k), 'rank': None, 'move': None,
                               'rate': fmt_rate((bands_n or {}).get(k), n_total),
                               'lower': None, 'band_name': ''}
                              if (bands_n or {}).get(k) is not None else None)
                          for k, _k2, _l in _PS_TAGS},
                'avg_rank': None}

    # 「班均」行：均分＝各班均分的平均；参考数/上线人数＝各班求和（比率才是对的，
    # 2026-10-16 自测时误用班级数当分母 → 上线率算出 1000% 那种荒唐值）
    n_cls = len(rows)
    n_stu = sum(r['n'] or 0 for r in rows)
    cls_avg = mean([r['avg'] for r in rows if r['avg'] is not None])
    cls_n = {'211': sum((r['bands'].get('211') or {}).get('n') or 0 for r in rows),
             '特控': sum((r['bands'].get('特控') or {}).get('n') or 0 for r in rows),
             '本科': sum((r['bands'].get('本科') or {}).get('n') or 0 for r in rows)}
    avg_row = _agg('班均', n_stu, cls_avg,
                   max([r['max'] for r in rows if r['max'] is not None] or [None]),
                   cls_n, mean([r['prev_avg'] for r in rows
                                if r['prev_avg'] is not None]))
    avg_row['n_cls'] = n_cls

    all_sc = _ps_scores(data, sub, None, direction) if hasattr(data, 'classes') else []

    # 年级行的上线人数＝**各班逐生结果之和**（口径与班级行严格一致；
    # 2026-10-16 复查前这里用"方向一条线 + 全体分数"重算，会与班级行口径不一致）
    def _sum_of(key):
        vals = [(r['bands'].get(key) or {}).get('n') for r in rows]
        vals = [v for v in vals if v is not None]
        return sum(vals) if vals else None

    grd_n = {k: _sum_of(k) for k, _a, _b in _PS_TAGS}
    # 「年级」行的「上次均分」：上一场该（方向×科目）全体学生的均分
    # （同方向取不到时回落不分方向，与逐班口径一致；取不到则空着显示 —）
    _prev_all = _ps_scores(pdata, sub, None, direction)
    if not _prev_all:
        _prev_all = _ps_scores(pdata, sub, None, None)
    grade_row = _agg('年级', len(all_sc), mean(all_sc),
                     max(all_sc) if all_sc else None, grd_n,
                     mean(_prev_all) if _prev_all else None)
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
            # v1.19.49.36 审核修正：返回时拷贝外层与 means/counts 内层，
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
