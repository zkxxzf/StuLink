# -*- coding: utf-8 -*-
"""监考编排负载均衡 —— 参数矩阵扫描（不连生产库、不改任何业务数据）。

目标：验证算法在**各种规模**下都稳健 ——
  - 教师池：8 / 15 / 30 / 60 人
  - 每考场监考员：1 / 2 / 3 人
  - 场次：1 ~ 10 场（时长各不相同）
  - 考场：5 / 20 个
  - 约束强度：无人受限 / 30% 的人只能排部分场次

对比四种口径：
  A 现状          —— 按格数均衡（现在生产代码的做法）
  B 仅改排序键     —— 只把排序键换成累计时长
  C 时长键+交换降差
  D 格数键+交换降差

判据：改进方案必须在**所有**配置下都不劣于现状（极差不能变大），
      否则宁可不改。
"""
import itertools
import random
import statistics

SUBJECTS = ['语文', '数学', '英语', '物理', '化学', '生物']


# ---------------- 构造场景 ----------------

def build_case(n_pool, durations, n_room, slots, restricted_ratio, seed=7):
    rnd = random.Random(seed)
    schedules = []
    for i, dur in enumerate(durations):
        schedules.append({
            'id': i + 1,
            'day': i // 2,
            'start': 8 * 60 + (i % 3) * 180,
            'dur': dur,
            'subjects': {SUBJECTS[i % len(SUBJECTS)]},
        })
    all_ids = {s['id'] for s in schedules}
    pool = []
    for i in range(n_pool):
        subj = {SUBJECTS[i % len(SUBJECTS)]} if i % 3 == 0 else set()
        avail = set(all_ids)
        pool.append({'name': f'T{i:03d}', 'subjects': subj, 'avail': avail})
    # 受限：只能排前一半场次（模拟时间冲突 / 排除名单）
    if restricted_ratio > 0:
        n_rest = int(n_pool * restricted_ratio)
        half = {s['id'] for s in schedules[:max(1, len(schedules) // 2)]}
        for p in pool[:n_rest]:
            p['avail'] = set(half)
    rooms = [f'{r:02d}' for r in range(1, n_room + 1)]
    return pool, schedules, rooms, slots


def overlap(s1, s2):
    if s1['day'] != s2['day']:
        return False
    return not (s1['start'] + s1['dur'] <= s2['start']
                or s2['start'] + s2['dur'] <= s1['start'])


def can_take(p, sch, used, busy):
    return (p['name'] not in used
            and sch['id'] in p['avail']
            and not (p['subjects'] & sch['subjects'])
            and not any(overlap(sch, s2) for s2 in busy))


# ---------------- 编排 ----------------

def arrange(pool, schedules, rooms, slots, mode):
    dur_of = {s['id']: s['dur'] for s in schedules}
    by_name = {p['name']: p for p in pool}
    load_min = {p['name']: 0 for p in pool}     # 累计分钟
    duty_cnt = {p['name']: 0 for p in pool}     # 格数
    busy = {p['name']: [] for p in pool}
    assign = {}
    filled = gaps = 0

    def cands_of(sch):
        return [p for p in pool if sch['id'] in p['avail']
                and not (p['subjects'] & sch['subjects'])]

    order = sorted(schedules, key=lambda s: (len(cands_of(s)), s['id']))
    for sch in order:
        used = set()
        for room in rooms:
            for slot in range(1, slots + 1):
                cands = [p for p in pool if can_take(p, sch, used, busy[p['name']])]
                if not cands:
                    gaps += 1
                    continue
                if mode in ('count', 'count_swap'):
                    cands.sort(key=lambda p: (duty_cnt[p['name']], p['name']))
                else:
                    cands.sort(key=lambda p: (load_min[p['name']],
                                              duty_cnt[p['name']], p['name']))
                p = cands[0]
                used.add(p['name'])
                duty_cnt[p['name']] += 1
                load_min[p['name']] += dur_of[sch['id']]
                busy[p['name']].append(sch)
                assign[(sch['id'], room, slot)] = p['name']
                filled += 1

    if mode in ('swap', 'count_swap'):
        _swap_down(assign, load_min, duty_cnt, busy, dur_of, by_name)
    return duty_cnt, load_min, filled, gaps


def _swap_down(assign, load_min, duty_cnt, busy, dur_of, by_name, rounds=300):
    """交换降差：把最重的人的**最长一场**让给更轻且接得了的人。"""
    if not assign:
        return
    shortest = min(dur_of.values())
    for _ in range(rounds):
        heavy = max(load_min, key=lambda n: load_min[n])
        light = min(load_min, key=lambda n: load_min[n])
        if load_min[heavy] - load_min[light] <= shortest:
            break
        keys = sorted([k for k, w in assign.items() if w == heavy],
                      key=lambda k: -dur_of[k[0]])
        targets = sorted([n for n in load_min if load_min[n] < load_min[heavy]],
                         key=lambda n: load_min[n])
        moved = False
        for key in keys:
            sch_id, room, slot = key
            sch = next(s for s in _SCHEDULES if s['id'] == sch_id)
            dur = dur_of[sch_id]
            for tname in targets:
                cand = by_name[tname]
                others = [s for s in busy[tname] if s['id'] != sch_id]
                if not can_take(cand, sch, set(), others):
                    continue
                if any(w == tname and k[0] == sch_id for k, w in assign.items()):
                    continue                    # 同场一人一格
                new_h = load_min[heavy] - dur
                new_l = load_min[tname] + dur
                if new_h < new_l:
                    continue                    # 换了反而反转
                busy[heavy].remove(sch)
                load_min[heavy] = new_h
                duty_cnt[heavy] -= 1
                busy[tname].append(sch)
                load_min[tname] = new_l
                duty_cnt[tname] += 1
                assign[key] = tname
                moved = True
                break
            if moved:
                break
        if not moved:
            break


_SCHEDULES = []      # 供 _swap_down 反查场次对象


def rng_of(load_min):
    v = list(load_min.values())
    return max(v) - min(v)


# ---------------- 参数矩阵 ----------------

DUR_SETS = {
    1: [120],
    2: [150, 90],
    4: [150, 120, 90, 150],
    6: [150, 150, 120, 120, 90, 90],
    10: [150, 150, 120, 120, 90, 90, 150, 120, 90, 120],
}


def sweep():
    global _SCHEDULES
    rows = []
    for n_sch, n_pool, n_room, slots, rest in itertools.product(
            (1, 2, 4, 6, 10), (8, 15, 30, 60), (5, 20), (1, 2, 3), (0, 0.3)):
        # 可行性：同一场次内一人一格，池子至少要够一个考场的需求
        if n_pool < n_room * slots:
            continue
        pool, schedules, rooms, slots_n = build_case(
            n_pool, DUR_SETS[n_sch], n_room, slots, rest)
        _SCHEDULES = schedules
        durs = set(s['dur'] for s in schedules)
        one = {}
        for mode in ('count', 'minutes', 'count_swap', 'swap'):
            c, m, f, g = arrange(pool, schedules, rooms, slots_n, mode)
            one[mode] = rng_of(m)
        rows.append((n_sch, n_pool, n_room, slots, rest, one, len(durs)))
    return rows


def main():
    rows = sweep()
    print(f'共 {len(rows)} 种配置（池人数 / 考场数 / 每考场人数 / 场次 / 受限比例）\n')

    base = 'count'
    stat = {m: {'better': 0, 'same': 0, 'worse': 0, 'sum': 0} for m in
            ('minutes', 'count_swap', 'swap')}
    worse_cases = {m: [] for m in stat}

    for n_sch, n_pool, n_room, slots, rest, one, ndur in rows:
        for m in ('minutes', 'count_swap', 'swap'):
            if one[m] < one[base]:
                stat[m]['better'] += 1
            elif one[m] == one[base]:
                stat[m]['same'] += 1
            else:
                stat[m]['worse'] += 1
                worse_cases[m].append((n_sch, n_pool, n_room, slots, rest,
                                       one[base], one[m]))
            stat[m]['sum'] += one[m]

    base_sum = sum(r[5][base] for r in rows)
    print(f'现状(A) 极差总和：{base_sum} 分钟\n')
    print(f'{"方案":<26}{"更优":>6}{"持平":>6}{"变劣":>6}{"极差总和":>12}{"改善":>10}')
    names = {'minutes': 'B 仅改排序键(时长)',
             'count_swap': 'D 格数键+交换降差',
             'swap': 'C 时长键+交换降差'}
    for m in ('minutes', 'count_swap', 'swap'):
        s = stat[m]
        imp = (base_sum - s['sum']) / base_sum * 100 if base_sum else 0
        print(f'{names[m]:<26}{s["better"]:>6}{s["same"]:>6}{s["worse"]:>6}'
              f'{s["sum"]:>12}{imp:>9.0f}%')

    print('\n===== 变劣明细（若非空则该方案不能用）=====')
    for m in stat:
        if worse_cases[m]:
            print(f'\n{names[m]}：{len(worse_cases[m])} 例')
            for c in worse_cases[m][:8]:
                print(f'  {c[0]}场/{c[1]}人/{c[2]}考场×{c[3]}人/受限{c[4]}: '
                      f'{c[5]} → {c[6]}')
        else:
            print(f'{names[m]}：无变劣')

    print('\n===== 结论 =====')
    for m in ('count_swap', 'swap'):
        if not worse_cases[m]:
            imp = (base_sum - stat[m]['sum']) / base_sum * 100 if base_sum else 0
            print(f'  {names[m]}：可并入（{len(rows)} 种配置全部不劣于现状，'
                  f'总体缩小 {imp:.0f}%）')
        else:
            print(f'  {names[m]}：**不能并入**（有 {len(worse_cases[m])} 例变劣）')


if __name__ == '__main__':
    main()
