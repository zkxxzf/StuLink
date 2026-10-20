# -*- coding: utf-8 -*-
# StuLink 2026-10-19
# 监考自动编排的均衡分配（纯函数，不依赖数据库 / Flask）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""监考自动编排的均衡分配算法。

口径（2026-10-19 用户要求「不能让次数多、时间也多」）：
  · 均衡的**主指标是累计监考时长（分钟）**，不是监考格数；
  · 格数多的老师必须拿到更短的场次，最终每人总时长尽量接近；
  · 硬约束：同科回避 / 排除名单（进算法前已剔除，见 cands）、
    一人一场次一格（同一 sid 只能占一个 cell）、时间不重叠（overlap 回调）。

算法四步：
  1. 贪心 —— 长场次优先（LPT），逐格分给「当前累计时长最轻」的老师；
  1.5 缺口补位 —— 排不下的格，把挡路的安排挪给第三人再填（1 层）；
  2. 交换降差 —— 把最重的人的一场让给更轻、接得了的人（移动 / 互换）。
     只在「要转移的时长 < 两人当前差距」时接受，故 Σload² 严格下降、必然收敛；
  3. 次数收尾 —— 总时长已经摆平后，用**等长场次互换**收窄格数极差
     （等长互换不改变任何人的总时长，所以不会破坏第 2 步的结果）。

注意：每场监考格数固定时，"每人服务哪几场"几乎就决定了总时长，
单一起点的贪心容易把 5 场的人全排在长场次、4 场的人全排在短场次
（正是用户截图里 495 分 vs 345 分）。因此 `best_assign` 用固定种子
多起点重排、取最优 —— 结果可复现，不会两次排得不一样。

对外暴露 `Cell` / `assign()` / `best_assign()`。
"""

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class Cell:
    """一个待填的监考格（场次 × 考场 × 监考位）。

    key      : 调用方自己的标识（如 (schedule_id, room_no, slot)）
    sid      : 场次 id（同一场次一人只能占一格）
    duration : 本场监考时长（分钟）
    cands    : 静态可排的教师姓名（已剔除同科回避 / 排除名单）
    pref     : 本格**优先**排的人（如"每天首场优先排班主任"）。

               只是"候选里有优先人就先挑优先人"，不是硬约束：优先人全都
               时间冲突 / 已被本场占住时，退回全部 cands，不会排不出来。
               均衡仍是主目标 —— 后续交换若能把差距拉平，允许把优先人换走。
    """

    key: object
    sid: object
    duration: int
    cands: frozenset
    pref: frozenset = frozenset()


class _State:
    """分配过程中的可变状态：时长 / 格数 / 已占场次 / 时间冲突封锁。"""

    def __init__(self, names, ovl):
        self.ovl = ovl                     # sid → 与之时间重叠的 sid 集合
        self.load = {n: 0 for n in names}
        self.cnt = {n: 0 for n in names}
        self.sids = {n: set() for n in names}
        self.blocked = {n: set() for n in names}   # 因时间重叠不能再接的场次
        self.used = {}                     # sid → 已占格的教师

    def can_take(self, cell, name):
        """name 现在接得了 cell 吗：本场次没占格 + 与已排场次不重叠。"""
        return (cell.sid not in self.sids[name]
                and cell.sid not in self.blocked[name])

    def put(self, cell, name, pick):
        """登记一格：累加时长 / 格数，并占用该场次。"""
        self.load[name] += cell.duration
        self.cnt[name] += 1
        self.sids[name].add(cell.sid)
        self.blocked[name] |= self.ovl.get(cell.sid, set())
        self.used[cell.sid].add(name)
        pick[cell] = name

    def erase(self, cell, pick):
        """撤销一格，返回原教师姓名（blocked 按剩余场次重算）。"""
        name = pick.pop(cell)
        self.load[name] -= cell.duration
        self.cnt[name] -= 1
        self.sids[name].discard(cell.sid)
        blocked = set()
        for sid in self.sids[name]:
            blocked |= self.ovl.get(sid, set())
        self.blocked[name] = blocked
        self.used[cell.sid].discard(name)
        return name

    def cells_of(self, pick, name):
        """某人当前占的格（长场次在前，便于先让出长场次）。"""
        return sorted((c for c in pick if pick[c] == name),
                      key=lambda c: (-c.duration, str(c.key)))


def _best_move(st, pick, heavy):
    """尝试把 heavy 的一格直接让给更轻的人；成功返回 True。

    只接受「转移时长 < 两人差距」—— 这等价于 Σload² 严格下降，
    既保证越来越均衡，也保证循环一定结束。
    """
    top = st.load[heavy]
    for cell in st.cells_of(pick, heavy):
        for recv in sorted(st.load, key=lambda n: (st.load[n], st.cnt[n], n)):
            if recv == heavy or top - st.load[recv] <= cell.duration:
                break                      # 更轻的都排前面，后面的只会更重
            if recv not in cell.cands or not st.can_take(cell, recv):
                continue
            st.erase(cell, pick)
            st.put(cell, recv, pick)
            return True
    return False


def _best_swap(st, pick, heavy):
    """heavy 与更轻的人**互换**一格；成功返回 True。

    直接移动经常被"该场次只能占一格 / 同科回避"卡住，互换能让双方都换到
    自己接得了的场次。净转移时长 = da - db，仍要求小于两人当前差距。
    """
    top = st.load[heavy]
    for cell_a in st.cells_of(pick, heavy):
        for recv in sorted(st.load, key=lambda n: (st.load[n], st.cnt[n], n)):
            gap = top - st.load[recv]
            if recv == heavy or gap <= 0:
                break
            if recv not in cell_a.cands or not st.can_take(cell_a, recv):
                continue
            for cell_b in st.cells_of(pick, recv):
                gain = cell_a.duration - cell_b.duration
                if gain <= 0 or gain >= gap:
                    continue               # 只换更短的，且换完确实更均衡
                if heavy not in cell_b.cands or not st.can_take(cell_b, heavy):
                    continue
                st.erase(cell_a, pick)
                st.erase(cell_b, pick)
                st.put(cell_b, heavy, pick)
                st.put(cell_a, recv, pick)
                return True
    return False


def _rebalance(st, pick, rounds):
    """第 2 步：反复给"最重的人"减负，直到没有任何一步能缩小差距。"""
    for _ in range(rounds):
        heavy = max(st.load, key=lambda n: (st.load[n], st.cnt[n], n))
        light = min(st.load, key=lambda n: (st.load[n], st.cnt[n], n))
        if st.load[heavy] <= st.load[light]:
            break
        if not _best_move(st, pick, heavy) and not _best_swap(st, pick, heavy):
            break


def _balance_count(st, pick, rounds):
    """第 3 步：时长已摆平后，用**等长场次互换**收窄格数极差。

    等长互换不改变任何人的总时长，所以不会破坏时长均衡；双方都必须接得了
    对方的场次（`can_take` 保证互换后仍是一场次只占一格）。
    """
    names = list(st.load)
    for _ in range(rounds):
        hi = max(names, key=lambda n: (st.cnt[n], -st.load[n], n))
        lo = min(names, key=lambda n: (st.cnt[n], -st.load[n], n))
        if st.cnt[hi] - st.cnt[lo] <= 1:
            break
        done = False
        for cell_a in st.cells_of(pick, hi):
            for cell_b in st.cells_of(pick, lo):
                if cell_a.duration != cell_b.duration:
                    continue
                if hi not in cell_b.cands or lo not in cell_a.cands:
                    continue
                if not st.can_take(cell_a, lo) or not st.can_take(cell_b, hi):
                    continue
                st.erase(cell_a, pick)
                st.erase(cell_b, pick)
                st.put(cell_b, hi, pick)
                st.put(cell_a, lo, pick)
                done = True
                break
            if done:
                break
        if not done:
            break


def _greedy(st, pick, cells, rank, rng=None):
    """第 1 步：长场次优先（LPT）+ 场内取累计时长最轻的人。

    长场次先落地、短场次后补，是把"每人总时长"拉平的关键；时序上互相压制
    的场次（如语文 08:00-10:30 与数学 08:00-10:00 平行、外语又与语文压 10
    分钟）留给 `_fill_gaps` 兜底补位。

    返回没排下的格（场次内已无人可用）。
    """
    gaps = []
    # 同长度的场次按种子打散（sorted 的 key 每项只算一次，结果仍是稳定的）
    tie = (lambda c: rng.random()) if rng else (lambda c: str(c.key))
    for cell in sorted(cells, key=lambda c: (-c.duration, tie(c))):
        cands = [n for n in cell.cands
                 if n in st.load and n not in st.used[cell.sid]
                 and st.can_take(cell, n)]
        if not cands:
            gaps.append(cell)
            continue
        # 2026-10-19：本格有"优先人"（如每天首场优先班主任）时先从优先人里挑；
        # 优先人一个都接不了就退回全部候选，绝不会因此排不出来。
        picks = [n for n in cands if n in cell.pref] if cell.pref else []
        name = min(picks or cands,
                   key=lambda n: (st.load[n], st.cnt[n], rank[n]))
        st.put(cell, name, pick)
    return gaps


def _fill_gaps(st, pick, gaps):
    """第 1.5 步：缺口补位 —— 把"挡路"的那一格挪给别的老师。

    缺口往往不是真没人，而是唯一接得了几个人都被同一场次占住 / 时间压着。
    这里做 1 层挪位：先把挡路的安排换给第三人，再把缺口填上。
    """
    left = []
    for cell in gaps:
        done = False
        order = sorted(cell.cands,
                       key=lambda n: (0 if n in cell.pref else 1, n))
        for name in order:
            if st.can_take(cell, name):
                st.put(cell, name, pick)
                done = True
                break
            if name in st.used[cell.sid]:
                blocker = next((c for c, n in pick.items()
                                if n == name and c.sid == cell.sid), None)
            else:
                overlap = st.ovl.get(cell.sid, set())
                blocker = next((c for c, n in pick.items()
                                if n == name and c.sid in overlap), None)
            if blocker is None:
                continue
            for alt in blocker.cands:
                if alt == name or not st.can_take(blocker, alt):
                    continue
                st.erase(blocker, pick)
                st.put(blocker, alt, pick)
                st.put(cell, name, pick)
                done = True
                break
            if done:
                break
        if not done:
            left.append(cell)
    return left


def assign(cells, names, overlap, max_rounds=400, seed=None):
    """把 cells 分给 names，返回 (plan, gaps, stats)。

    plan  : [(cell, name)]，长度 = 已排下的格数
    gaps  : 没排下的 cell 列表（无可用教师）
    stats : {'load'(分钟), 'count'(格), 'max_load', 'min_load',
             'mean_load', 'spread'}，极差/人均只统计**有安排**的老师
    overlap: callable(sid_a, sid_b) -> bool，两个场次时间是否重叠
    seed  : 贪心阶段的平手顺序（不同种子 → 不同候选解，供 `best_assign` 择优）
    """
    names = list(names)
    rng = random.Random(seed) if seed is not None else None
    rank = ({n: rng.random() for n in names} if rng is not None
            else {n: 0 for n in names})
    sids = sorted({c.sid for c in cells})
    ovl = {a: {b for b in sids if b != a and overlap(a, b)} for a in sids}
    st = _State(names, ovl)
    st.used = {sid: set() for sid in sids}
    pick = {}

    gaps = _greedy(st, pick, cells, rank, rng)
    _rebalance(st, pick, max_rounds)
    gaps = _fill_gaps(st, pick, gaps)
    _rebalance(st, pick, max_rounds)      # 补位可能让某个人变重，再拉平一次
    _balance_count(st, pick, max_rounds)

    vals = sorted(st.load[n] for n in names if st.cnt[n]) or [0]
    return list(pick.items()), gaps, {
        'load': st.load, 'count': st.cnt,
        'max_load': vals[-1], 'min_load': vals[0],
        'mean_load': int(round(sum(vals) / len(vals))) if st.cnt else 0,
        'spread': vals[-1] - vals[0],
    }


def best_assign(cells, names, overlap, tries=24):
    """多起点择优：换若干固定种子各排一遍，取最好的一份。

    排序键依次为：**缺口数**（先保证排满，排不满的方案再均衡也没意义）、
    时长极差、max_load（别让某个人特别累）、Σload²（总体均衡）、格数极差。
    种子固定，结果可复现，同一份数据不会两次排得不一样。
    规模越大重跑越贵，故按格数自动减次数（用户点一次"自动编排"等待 < 2s）。
    """
    if len(cells) > 1200:
        tries = min(tries, 4)
    elif len(cells) > 600:
        tries = min(tries, 12)
    elif len(cells) > 400:
        tries = min(tries, 60)
    elif len(cells) > 200:
        tries = max(tries, 240)
    else:
        tries = max(tries, 600)             # 小批量多试几次：排满、排匀比省 1 秒重要
    best = None
    for seed in range(tries):
        plan, gaps, st = assign(cells, names, overlap, seed=seed)
        key = (len(gaps), st['spread'], st['max_load'],
               sum(v * v for v in st['load'].values()),
               max(st['count'].values()) - min(st['count'].values()))
        if best is None or key < best[0]:
            best = (key, plan, gaps, st)
    return best[1], best[2], best[3]
