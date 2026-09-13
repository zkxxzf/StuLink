"""
宿舍自动分配算法 v7（2026-08-14）— 单年级平滑动态贪心（全局压力等级制）

================================================================================
SPEC（约束条件，全部强制）
================================================================================
S1  性别独立：男女分别分配，房间 gender 严格隔离（男/女/不限）
S2  合班约束：仅同性别 + 同年级 + 同班型；每间合班房 ≤2 个班；每班发起合班 ≤1 次
S3  合班时机：仅班级收尾（剩余 1 ~ limit-1 人）且下一班有剩余时被动触发
S4  压力等级：L 从 6 递增，取第一个满足 Σmin(L,容量) ≥ 人数的等级（6/7/8）
S5  房间上限：limit = min(L, 容量)，合班房与普通房完全一致（压力均匀）
S6  6人间优先：同层小容量房间先分配（S 型内容量升序），6 人间优先住满
S7  独享优先于合班：收尾先填满本班独享房空床（至物理容量），仍剩才合班/独占
S8  房间不足兜底：收尾填满独享仍不足 → 该 L 失败，自动升档重试（L+1...8）
S9  分散优化（后处理）：房间充足时把超过 L 的 8 人房拆到空房，
    尽量用上所有勾选房间（合班房 ≤2 班且同年级）
S10 合班调整（后处理）：链式合班重组（同年级）减少合班宿舍数
S11 方案选优：L=6/7/8 各跑一遍，评分（宽松优先 + 8人房惩罚 + 合班惩罚 + 用房率）选最优
S12 全量校验：Σ实际入住 == Σ总人数，否则报错
S13 单年级约束：一次分配仅允许一个年级（前端勾选限制 + 后端 selected_keys 校验）
S14 班级唯一性：所有班级查询/写入 (grade, class_name) 成对
S15 物理容量：Σ物理床位 < 人数 → 直接报错，提示增选宿舍
================================================================================
"""
# StuLink v1.8.0 2026-08-14（算法 v7：单年级 + 全约束重写）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import re
import json
from collections import defaultdict
from app.models import Room, Student, ClassProfile, StudentAccommodation, BedAssignment
from app.extensions import db


# ============================================================================
# 排序工具
# ============================================================================

CLASS_TYPE_ORDER = {'default': 0, '卓越班': 1, '强基班': 2}


def _room_number_int(room_number):
    try:
        return int(room_number)
    except (ValueError, TypeError):
        return 0


def _extract_class_number(class_name):
    if not class_name:
        return 9999
    m = re.search(r'(\d+)', class_name)
    return int(m.group(1)) if m else 9999


def _extract_grade_year(grade):
    if not grade:
        return 9999
    m = re.search(r'(\d+)', grade)
    return int(m.group(1)) if m else 9999


def sort_rooms_s(rooms):
    """S型序列化：楼层分组，偶数层正向、奇数层反向，楼层升序连接
    同层内容量小的房间优先（6 人间先于 8 人间）—— S6
    """
    floor_groups = defaultdict(list)
    for r in rooms:
        floor_groups[r['floor']].append(r)
    result = []
    for f in sorted(floor_groups):
        group = sorted(floor_groups[f], key=lambda r: (r['capacity'], _room_number_int(r['room_number'])))
        if f % 2 == 0:
            result.extend(group)
        else:
            result.extend(reversed(group))
    return result


def _sort_classes(classes, profiles):
    """班级排序: (年级数字升序, 班型, 班号升序)——高年级优先低楼层"""
    def _class_type(c):
        p = profiles.get(f"{c['grade']}:{c['class_name']}")
        return (p.class_type or 'default') if p else 'default'

    return sorted(classes, key=lambda c: (
        _extract_grade_year(c['grade']),
        CLASS_TYPE_ORDER.get(_class_type(c), 0),
        _extract_class_number(c['class_name'])))


# ============================================================================
# 核心
# ============================================================================

def calc_level(rooms, total_students):
    """全局压力等级 L: 返回 (L, 物理容量) 或 (None, 物理容量)—— S4/S15"""
    total_cap = sum(r['capacity'] for r in rooms)
    if total_students > total_cap:
        return None, total_cap
    for L in (6, 7, 8):
        if sum(min(L, r['capacity']) for r in rooms) >= total_students:
            return L, total_cap
    return None, total_cap


def allocate_one_gender(classes, rooms, logs, gender_label=''):
    """单性别分配：严格分级递进—— S4/S8/S9/S10

    第 1 级 L=6：6 人间住满 6 人、8 人间住 6 人（宽松优先）；
    房间/床位不足 → 清除重算升 L=7（8 人间住 7 人，合班宿舍上限同步 7）；
    仍不足 → 清除重算升 L=8（8 人间住 8 人，合班宿舍上限同步 8）。
    第一个成功的等级即为最终结果（不评分选优）。

    classes: 同年级班级 [{grade, class_name, gender, count, key, class_type}]
    rooms:   已 S 型排序房间 [{id, building, room_number, floor, capacity, gender}]
    """
    for c in classes:
        if 'key' not in c:
            c['key'] = f"{c['grade']}|{c['class_name']}"
        if 'class_type' not in c:
            c['class_type'] = 'default'
    total_students = sum(c['count'] for c in classes)
    L0, total_cap = calc_level(rooms, total_students)
    if L0 is None:
        return {'success': False,
                'error': f'物理床位不足: {total_students}人 > {total_cap}床（勾选房间不足，请增选宿舍）'}

    logs.append(f"[V7-{gender_label}] 总人数{total_students} | 房间{len(rooms)}间 | "
                f"物理容量{total_cap}床 | L={L0} -> {'宽松' if L0 == 6 else '紧张'}模式")

    last_error = None
    for L in range(L0, 9):
        result = _allocate_with_level(classes, rooms, L)
        if result['success']:
            # 后处理（合班调整/分散优化/多轮均衡/合班优化）——每次升档后重新计算
            _optimize_combined_rooms(result, rooms, classes, L, logs, gender_label)   # S10 链式重组
            _spread_to_empty_rooms(result, rooms, classes, L, logs, gender_label)     # S9 分散
            _balance_rooms(result, rooms, classes, L, logs, gender_label)             # 多轮贪心均衡
            # 均衡会移动人员可能重新制造链式/交叉合班，循环做合班优化（2 轮）
            for _ in range(2):
                _optimize_combined_rooms(result, rooms, classes, L, logs, gender_label)
                _recombine_same_set(result, rooms, classes, L, logs, gender_label)
                _balance_rooms(result, rooms, classes, L, logs, gender_label)
            result['level'] = L
            result['mode'] = '宽松' if L == 6 else '紧张'
            logs.append(f"[V7-{gender_label}] 选定 L={L}（{'宽松：6人间6人、8人间6人' if L == 6 else '紧张：8人间住%d人' % L}）")
            return result
        last_error = result.get('error')
        if L < 8:
            logs.append(f"[V7-{gender_label}] L={L} 房间不足，清除重算升 L={L + 1}"
                        f"（合班宿舍上限同步 {L + 1} 人）")
        else:
            logs.append(f"[V7-{gender_label}] L=8 仍无法分配: {last_error}")
    return {'success': False, 'error': last_error or '分配失败'}


def _evaluate_solution(result, rooms, L):
    """方案评分（分数越高越好）—— S11：
    L 越低越好；用房率越高越好；8 人间住满 8 人惩罚（物理满员，无论 L）；
    >2 班合班重罚；合班房总数轻罚
    """
    room_details = result.get('room_details') or {}
    occupied = result.get('occupied') or []
    n = len(rooms) or 1
    used = sum(1 for v in occupied if v > 0)
    full8 = sum(1 for i, v in enumerate(occupied)
                if rooms[i]['capacity'] >= 8 and v == 8)
    multi = sum(1 for sh in room_details.values() if len(sh) > 2)
    comb = sum(1 for sh in room_details.values() if len(sh) > 1)
    score = (8 - L) * 20 + used / n * 5 - full8 * 5 - multi * 50 - comb * 2
    return score


def _allocate_with_level(classes, rooms, L):
    """用指定等级 L 执行一趟分配（内部）—— S5/S6/S7/S8/S12"""
    n = len(rooms)
    total_students = sum(c['count'] for c in classes)

    room_idx = 0
    has_merged = False
    merge_prev_share = 0
    initiated = set()                        # 发起过合班的班级（被借不算），每班最多发起 1 次 —— S2
    class_rem = {c['key']: c['count'] for c in classes}
    allocations = {c['key']: [] for c in classes}
    room_details = {}
    occupied = [0] * n
    class_rooms = defaultdict(list)          # key -> [room_idx,...] 本班占用的房间（收尾填满用）

    for i, cur in enumerate(classes):
        key = cur['key']

        # ---- 处理上一班遗留的合班房（先于 rem==0 检查，确保被借完的班份额正确记录）----
        if has_merged:
            r = rooms[room_idx]
            borrowed = occupied[room_idx] - merge_prev_share
            allocations[key].append((room_idx, borrowed))
            room_details.setdefault(room_idx, []).append((key, borrowed))
            class_rooms[key].append(room_idx)
            has_merged = False
            room_idx += 1

        rem = class_rem[key]
        if rem == 0:
            continue

        # ---- 分配当前班 ----
        while rem > 0:
            while room_idx < n and occupied[room_idx] >= rooms[room_idx]['capacity']:
                room_idx += 1
            if room_idx >= n:
                # S8 兜底：房间不足时填满本班独享房空床（突破 limit 至物理容量）
                for ri in list(class_rooms[key]):
                    if rem <= 0:
                        break
                    if len(room_details.get(ri, [])) != 1:
                        continue
                    space = rooms[ri]['capacity'] - occupied[ri]
                    if space <= 0:
                        continue
                    fill = min(space, rem)
                    occupied[ri] += fill
                    room_details[ri] = [(key, occupied[ri])]
                    allocations[key] = [(r2, c) for r2, c in allocations[key] if r2 != ri] \
                        + [(ri, occupied[ri])]
                    rem -= fill
                if rem <= 0:
                    break
                return {'success': False,
                        'error': f'{cur["grade"]}{cur["class_name"]} 剩余{rem}人无法安置'
                                 f'（房间不足，请增选宿舍）',
                        'allocations': allocations, 'room_details': room_details,
                        'occupied': occupied}

            r = rooms[room_idx]
            maxc = r['capacity']
            limit = min(L, maxc)             # S5 全局等级制

            if rem >= limit:
                occ = limit
                occupied[room_idx] = occ
                allocations[key].append((room_idx, occ))
                room_details.setdefault(room_idx, []).append((key, occ))
                class_rooms[key].append(room_idx)
                rem -= occ
                room_idx += 1
            else:
                # ---- 收尾: 0 < rem < limit（1~5人）----
                # ① S7 独享优先：填满本班独享房空床至 limit（不超 L，
                #    8 人间 L=6 时仍住 6 人，宽松优先不受破坏）
                for ri in list(class_rooms[key]):
                    if rem <= 0:
                        break
                    if len(room_details.get(ri, [])) != 1:
                        continue
                    space = min(rooms[ri]['capacity'], limit) - occupied[ri]
                    if space <= 0:
                        continue
                    fill = min(space, rem)
                    occupied[ri] += fill
                    room_details[ri] = [(key, occupied[ri])]
                    allocations[key] = [(r2, c) for r2, c in allocations[key] if r2 != ri] \
                        + [(ri, occupied[ri])]
                    rem -= fill
                if rem == 0:
                    break
                # ② 仍剩：末班独占 / 同年级合班 / 独占
                if i == len(classes) - 1:
                    occupied[room_idx] = rem
                    allocations[key].append((room_idx, rem))
                    room_details.setdefault(room_idx, []).append((key, rem))
                    class_rooms[key].append(room_idx)
                    rem = 0
                    room_idx += 1
                else:
                    nxt = classes[i + 1]
                    next_rem = class_rem[nxt['key']]
                    # S2/S3 合班条件：下一班有剩余 + 同班型 + 同年级 + 本班未发起过合班
                    if next_rem <= 0 or (nxt['class_type'] or 'default') != (cur['class_type'] or 'default') \
                            or nxt['grade'] != cur['grade'] \
                            or key in initiated:
                        occupied[room_idx] = rem
                        allocations[key].append((room_idx, rem))
                        room_details.setdefault(room_idx, []).append((key, rem))
                        class_rooms[key].append(room_idx)
                        rem = 0
                        room_idx += 1
                    else:
                        max_possible = min(limit, rem + next_rem)
                        occ = max_possible
                        borrowed = occ - rem
                        occupied[room_idx] = occ
                        allocations[key].append((room_idx, rem))
                        room_details.setdefault(room_idx, []).append((key, rem))
                        class_rem[nxt['key']] -= borrowed
                        initiated.add(key)    # 每班最多发起 1 次 —— S2
                        has_merged = True
                        merge_prev_share = rem
                        rem = 0
                        break                # room_idx 不递增 → 下一班继续使用这间合班房

    # S12 全量校验
    total_alloc = sum(sum(c for _, c in alloc) for alloc in allocations.values())
    if total_alloc != total_students:
        return {'success': False,
                'error': f'内部校验失败: 已分配{total_alloc}人 != 应分配{total_students}人',
                'allocations': allocations, 'room_details': room_details,
                'occupied': occupied}
    return {'success': True, 'allocations': allocations,
            'room_details': room_details, 'occupied': occupied,
            'total_alloc': total_alloc, 'total_students': total_students}


# ============================================================================
# 后处理：合班调整（S10）与分散优化（S9）
# ============================================================================

def _optimize_combined_rooms(result, rooms, classes, L, logs=None, gender_label=''):
    """链式合班重组（同年级）——减少合班宿舍数，不影响总人数"""
    if logs is None:
        logs = []
    room_details = result.get('room_details') or {}
    allocations = result.get('allocations') or {}
    occupied = result.get('occupied') or []
    if not room_details or len(occupied) != len(rooms):
        return 0

    cls_label = {c['key']: f"{c['grade']}{c['class_name']}" for c in classes}
    cls_grade = {c['key']: c['grade'] for c in classes}

    def _room_limit(ri):
        return min(L, rooms[ri]['capacity'])

    reduced = 0
    for _ in range(30):
        comb = {ri: list(shares) for ri, shares in room_details.items()
                if len(shares) == 2}
        if not comb:
            break
        cls_rooms = defaultdict(list)
        for ri, shares in comb.items():
            for idx, (key, cnt) in enumerate(shares):
                cls_rooms[key].append((ri, cnt, idx == 0))

        changed = False
        for key, items in list(cls_rooms.items()):
            if len(items) != 2:
                continue
            borrowed = [x for x in items if not x[2]]
            initiated = [x for x in items if x[2]]
            if len(borrowed) != 1 or len(initiated) != 1:
                continue
            (ri_borrow, share_borrow, _), = borrowed
            (ri_init, share_init, _), = initiated
            shares_b = comb[ri_borrow]
            shares_i = comb[ri_init]
            prev_key, prev_share = shares_b[0]
            next_key, next_share = shares_i[1]

            # S2 同年级：链内 3 班必须同年级
            if len({cls_grade.get(k) for k in (prev_key, key, next_key)}) != 1:
                continue
            # 条件1: 中间班合班总份额可独立成 1 间房
            total = share_borrow + share_init
            if total > _room_limit(ri_init):
                continue
            # 条件2: 两端班份额可重组成 1 间合班房
            if prev_share + next_share > _room_limit(ri_borrow):
                continue

            room_details[ri_borrow] = [(prev_key, prev_share), (next_key, next_share)]
            room_details[ri_init] = [(key, total)]
            occupied[ri_borrow] = prev_share + next_share
            occupied[ri_init] = total
            allocations[key] = [(r, c) for r, c in allocations[key]
                                if r not in (ri_borrow, ri_init)] + [(ri_init, total)]
            if next_key in allocations:
                allocations[next_key] = [(r, c) for r, c in allocations[next_key]
                                         if r != ri_init] + [(ri_borrow, next_share)]
            reduced += 1
            changed = True
            pn, kn, nn = (cls_label.get(prev_key, prev_key),
                          cls_label.get(key, key),
                          cls_label.get(next_key, next_key))
            logs.append(f"[V7-{gender_label}优化] 合班调整: 拆{pn}+{kn}、{kn}+{nn} → "
                        f"{pn}+{nn}合班 + {kn}{total}人独立（合班宿舍-1）")
            break
        if not changed:
            break

    if reduced > 0:
        logs.append(f"[V7-{gender_label}] 合班宿舍调整完成: 共减少 {reduced} 间合班宿舍")
    return reduced


def _recombine_same_set(result, rooms, classes, L, logs=None, gender_label=''):
    """班集相同的合班房重排：
    如 522(9班4+10班3)、523(9班3+10班3) → 522 全 9班、523 全 10班（班级集中，合班数 -2）
    约束：同年级（班集内天然同年级）、不超 limit、性别匹配。
    """
    if logs is None:
        logs = []
    room_details = result.get('room_details') or {}
    occupied = result.get('occupied') or []
    allocations = result.get('allocations') or {}
    if not room_details or len(occupied) != len(rooms):
        return 0

    comb = []
    for ri, shares in room_details.items():
        distinct = {k for k, _ in shares}
        if len(distinct) == 2:
            comb.append((ri, distinct, list(shares)))

    moved = 0
    for i in range(len(comb)):
        ri_a, set_a, sh_a = comb[i]
        for j in range(i + 1, len(comb)):
            ri_b, set_b, sh_b = comb[j]
            if set_a != set_b:
                continue
            if rooms[ri_a]['gender'] != rooms[ri_b]['gender']:
                continue
            key1, key2 = sorted(set_a)
            total1 = sum(c for k, c in sh_a + sh_b if k == key1)
            total2 = sum(c for k, c in sh_a + sh_b if k == key2)
            lim_a = min(L, rooms[ri_a]['capacity'])
            lim_b = min(L, rooms[ri_b]['capacity'])
            if total1 <= lim_a and total2 <= lim_b:
                room_details[ri_a] = [(key1, total1)]
                room_details[ri_b] = [(key2, total2)]
                occupied[ri_a] = total1
                occupied[ri_b] = total2
                moved += total1 + total2
            elif total2 <= lim_a and total1 <= lim_b:
                room_details[ri_a] = [(key2, total2)]
                room_details[ri_b] = [(key1, total1)]
                occupied[ri_a] = total2
                occupied[ri_b] = total1
                moved += total1 + total2
            if moved > 0:
                break
        if moved > 0:
            break

    if moved > 0:
        new_alloc = {k: [] for k in allocations}
        for ri, shares in room_details.items():
            for key, cnt in shares:
                new_alloc.setdefault(key, []).append((ri, cnt))
        result['allocations'] = new_alloc
        logs.append(f"[V7-{gender_label}合班优化] 同班集合班房重排: 调整 {moved} 人"
                    f"（如 {key1.split('|')[-1]} 与 {key2.split('|')[-1]} 各自集中）")
    return moved


def _spread_to_empty_rooms(result, rooms, classes, L, logs=None, gender_label=''):
    """分散优化：把超过 L 的独享 8 人房拆到空房，尽量用上所有勾选房间—— S9
    约束：只拆独享房；目标空房同性别；合班房 ≤2 班且同年级
    """
    if logs is None:
        logs = []
    room_details = result.get('room_details') or {}
    occupied = result.get('occupied') or []
    allocations = result.get('allocations') or {}
    if not room_details or len(occupied) != len(rooms):
        return 0

    cls_map = {c['key']: c for c in classes}

    empty_by_gender = {}
    for ri in range(len(rooms)):
        if occupied[ri] == 0:
            empty_by_gender.setdefault(rooms[ri]['gender'], []).append(ri)
    unisex_empty = empty_by_gender.pop('不限', [])
    if not empty_by_gender and not unisex_empty:
        return 0

    over_by_gender = {}
    for ri in sorted(room_details.keys()):
        if occupied[ri] <= L:
            continue
        shares = room_details[ri]
        if len(shares) != 1:
            continue
        key, cnt = shares[0]
        cls = cls_map.get(key)
        if not cls:
            continue
        over_by_gender.setdefault(cls['gender'], []).append((ri, key, occupied[ri] - L))

    moved = 0
    for gender, overs in over_by_gender.items():
        targets = list(empty_by_gender.get(gender, [])) + list(unisex_empty)
        if not targets:
            continue
        ti = 0
        for ri, key, ov in overs:
            while ov > 0 and ti < len(targets):
                ei = targets[ti]
                cur = occupied[ei]
                space = L - cur
                if space <= 0:
                    ti += 1
                    continue
                existing_classes = {k for k, _ in room_details.get(ei, [])}
                if existing_classes:
                    if len(existing_classes) >= 2 and key not in existing_classes:
                        ti += 1
                        continue
                    grades = {cls_map.get(k, {}).get('grade') for k in existing_classes}
                    if len(grades) != 1 or cls.get('grade') not in grades:
                        ti += 1
                        continue
                fill = min(space, ov)
                if cur == 0:
                    room_details[ei] = [(key, fill)]
                else:
                    room_details[ei] = room_details.get(ei, []) + [(key, fill)]
                occupied[ei] = cur + fill
                occupied[ri] -= fill
                ov -= fill
                moved += fill
                if occupied[ei] >= L:
                    ti += 1
            room_details[ri] = [(key, occupied[ri])]

    # 第二阶段：从同一班的多间满员房（==L）各移 1 人到空房凑房
    # （同班凑满一间空房，避免"1 人住 1 间"；所有勾选房间尽量用上）
    remaining_empty = [ri for ri in range(len(rooms)) if occupied[ri] == 0]
    if remaining_empty:
        full_by_class = defaultdict(list)    # key -> [ri, ...] 该班满员房（仅 8 人间，6 人间保持满员）
        for ri, shares in room_details.items():
            if len(shares) == 1 and occupied[ri] == L and rooms[ri]['capacity'] >= 8:
                full_by_class[shares[0][0]].append(ri)
        for ei in remaining_empty:
            placed = False
            for key, ris in list(full_by_class.items()):
                if len(ris) < 2:
                    continue              # 该班只有 1 间满员房 → 凑不起，跳过
                cls = cls_map.get(key)
                if not cls:
                    continue
                if rooms[ei]['gender'] not in (cls['gender'], '不限'):
                    continue
                n = min(len(ris), L)      # 空房最多收 L 人（同班）
                for ri in ris[:n]:
                    occupied[ri] -= 1
                    room_details[ri] = [(key, occupied[ri])]
                room_details[ei] = [(key, n)]
                occupied[ei] = n
                moved += n
                del full_by_class[key][:n]
                placed = True
                break
            if not placed:
                break                  # 无班可凑 → 保留空房（不造 1 人房）

    if moved > 0:
        new_alloc = {k: [] for k in allocations}
        for ei, shares in room_details.items():
            for key, cnt in shares:
                new_alloc.setdefault(key, []).append((ei, cnt))
        result['allocations'] = new_alloc
        logs.append(f"[V7-{gender_label}分散] 房间充足优化: {moved} 人分散到空房间")
    return moved


def _balance_rooms(result, rooms, classes, L, logs=None, gender_label=''):
    """多轮贪心均衡化（参考 V6 多轮调整思路）

    反复把"高占用独享房"的 1 人移到"低占用可接收房"（空房/同班房/1 班房），
    直到每间房人数趋于平均——消除"3 人房 vs 8 人房"的极端并存。

    约束：源房=独享房（合班房不拆）；目标房≤2 个班且同年级；不超 limit；
    6 人间保持满员（limit=6 自然限制）。
    """
    import math
    if logs is None:
        logs = []
    room_details = result.get('room_details') or {}
    occupied = result.get('occupied') or []
    allocations = result.get('allocations') or {}
    if not room_details or len(occupied) != len(rooms):
        return 0

    cls_map = {c['key']: c for c in classes}
    moved = 0

    for _ in range(120):
        used = [ri for ri in range(len(rooms)) if occupied[ri] > 0]
        if not used:
            break
        total = sum(occupied[ri] for ri in used)
        # 目标人均：按全部勾选房间计算（含空房），房间充足时趋于 3-4 人/间
        target = math.ceil(total / max(1, len(rooms)))
        # 候选源房：8 人间独享房且人数 > min(target, 6)
        # （6 人间保持满员不移出；8 人间 7 人在 target=7 时也可移 1 人到小房/空房，
        #   实现"填充小房"而非"小房并入清空"，避免勾选房间空置）
        donors = [ri for ri in used
                  if len(room_details.get(ri, [])) == 1
                  and rooms[ri]['capacity'] >= 8
                  and occupied[ri] > min(target, 6)]
        if not donors:
            break
        donor = max(donors, key=lambda ri: occupied[ri])
        key, cnt = room_details[donor][0]
        cls = cls_map.get(key)
        if not cls:
            break

        # 找目标房：人数最少且可接收（空房优先，其次同班/1 班房）
        best_ei, best_occ = None, 10 ** 9
        empty_ei = None
        for ei in range(len(rooms)):
            if ei == donor:
                continue
            if occupied[ei] == 0:
                if rooms[ei]['gender'] in (cls['gender'], '不限') and empty_ei is None:
                    empty_ei = ei
                continue
            if occupied[ei] >= min(L, rooms[ei]['capacity']):
                continue
            if occupied[ei] >= target:
                continue
            if rooms[ei]['gender'] not in (cls['gender'], '不限'):
                continue
            shares = room_details.get(ei, [])
            distinct = {k for k, _ in shares}
            ok = False
            if len(distinct) == 1:
                ok = True          # 同班 或 1 班房（移入后变 2 班合班，同年级天然满足）
            elif len(distinct) == 2:
                ok = key in distinct   # 已有本班份额可继续累积（仍是 ≤2 班）
            if ok and occupied[ei] < best_occ:
                best_occ = occupied[ei]
                best_ei = ei

        # 目标：已用最少房优先（先升小房）；无可用已用房时才开空房
        # （空房收 1 人后成为小房，下一轮继续被优先填满，不会出现 1 人房泛滥）
        if best_ei is None and empty_ei is not None:
            best_ei, best_occ = empty_ei, 0
        if best_ei is None:
            break

        # 移动 1 人：源房 -1
        occupied[donor] -= 1
        room_details[donor] = [(key, occupied[donor])]
        # 目标房 +1（同班份额合并，保持 ≤2 个班）
        if occupied[best_ei] == 0:
            room_details[best_ei] = [(key, 1)]
        else:
            shares = room_details[best_ei]
            if key in {k for k, _ in shares}:
                room_details[best_ei] = [(k, c + (1 if k == key else 0))
                                         for k, c in shares]
            else:
                room_details[best_ei] = shares + [(key, 1)]
        occupied[best_ei] += 1
        moved += 1

    # 小房并入：≤2 人的独享房学生并入可接收房（同班/1 班房，≤2 班且不超 limit），
    # 小房清空变空房——避免"1-2 人住 1 间"（房间充足时合并，紧张时保留）
    for ei in list(room_details.keys()):
        shares = room_details[ei]
        if len({k for k, _ in shares}) != 1:
            continue
        if occupied[ei] > 2:
            continue
        key, cnt = shares[0]
        cls = cls_map.get(key)
        if not cls:
            continue
        best = None
        for tj in sorted(room_details.keys()):
            if tj == ei:
                continue
            if rooms[tj]['gender'] not in (cls['gender'], '不限'):
                continue
            if occupied[tj] + cnt > min(L, rooms[tj]['capacity']):
                continue
            tshares = room_details[tj]
            tdistinct = {k for k, _ in tshares}
            if len(tdistinct) == 1 or (len(tdistinct) == 2 and key in tdistinct):
                best = tj
                break
        if best is not None:
            occupied[ei] -= cnt
            occupied[best] += cnt
            del room_details[ei]
            tshares = room_details[best]
            if key in {k for k, _ in tshares}:
                room_details[best] = [(k, c + (cnt if k == key else 0))
                                      for k, c in tshares]
            else:
                room_details[best] = tshares + [(key, cnt)]
            moved += cnt

    if moved > 0:
        new_alloc = {k: [] for k in allocations}
        for ei, shares in room_details.items():
            for key, cnt in shares:
                new_alloc.setdefault(key, []).append((ei, cnt))
        result['allocations'] = new_alloc
        logs.append(f"[V7-{gender_label}均衡] 多轮贪心均衡: 调整 {moved} 人"
                    f"（每间尽量趋于 {max(6, math.ceil(sum(v for v in occupied) / max(1, sum(1 for v in occupied if v > 0))))} 人）")
    return moved


# ============================================================================
# 输出构建
# ============================================================================

def _build_assignments(result, rooms, classes, gender):
    """将分配结果转换为 assignment 字典列表（合班房精确份额，class_counts 带年级）"""
    cls_map = {c['key']: c for c in classes}
    assignments = []
    for ri in sorted(result['room_details']):
        shares = result['room_details'][ri]
        room = rooms[ri]
        total = sum(c for _, c in shares)
        if len(shares) == 1:
            cls = cls_map[shares[0][0]]
            assignments.append({
                'room': room,
                'grade': cls['grade'],
                'class_name': cls['class_name'],
                'gender': gender,
                'expected_count': total,
                'is_combined': False,
                'combined_info': '',
                'class_counts': [],
            })
        else:
            names = [cls_map[k]['class_name'] for k, _ in shares]
            combined_name = '+'.join(names)
            assignments.append({
                'room': room,
                'grade': cls_map[shares[0][0]]['grade'],
                'class_name': combined_name,
                'gender': gender,
                'expected_count': total,
                'is_combined': True,
                'combined_info': f"{cls_map[shares[0][0]]['grade']} {combined_name}",
                'class_counts': [{'grade': cls_map[k]['grade'],
                                  'class_name': cls_map[k]['class_name'], 'count': c}
                                 for k, c in shares],
            })
    assignments.sort(key=lambda a: (
        a['room'].get('building') or '', a['room'].get('floor') or 0,
        _room_number_int(a['room'].get('room_number'))))
    return assignments


# ============================================================================
# 数据加载
# ============================================================================

def _ensure_room_beds(room_ids, logs=None):
    """确保指定房间的 BedAssignment 记录完整（与 capacity 匹配）"""
    if not room_ids:
        return 0
    rooms = Room.query.filter(Room.id.in_(room_ids), Room.is_active == True).all()
    fixed_count = 0
    for room in rooms:
        bed_count = BedAssignment.query.filter_by(room_id=room.id).count()
        expected_beds = room.capacity
        if bed_count < expected_beds:
            existing_bed_nums = set(
                b.bed_number for b in BedAssignment.query.filter_by(room_id=room.id).all())
            beds_to_create = [
                BedAssignment(room_id=room.id, bed_number=bed_num)
                for bed_num in range(1, expected_beds + 1)
                if bed_num not in existing_bed_nums
            ]
            if beds_to_create:
                db.session.add_all(beds_to_create)
                fixed_count += len(beds_to_create)
                if logs is not None:
                    logs.append(f"[FIX] 房间 {room.building} {room.room_number} "
                                f"({expected_beds}人间) 补充 {len(beds_to_create)} 个床位记录")
        elif bed_count > expected_beds:
            extra_beds = BedAssignment.query.filter(
                BedAssignment.room_id == room.id,
                BedAssignment.bed_number > expected_beds,
                BedAssignment.student_id.is_(None)
            ).all()
            if extra_beds:
                for bed in extra_beds:
                    db.session.delete(bed)
                if logs is not None:
                    logs.append(f"[FIX] 房间 {room.building} {room.room_number} "
                                f"删除 {len(extra_beds)} 个多余的空床位")
    if fixed_count > 0 and logs is not None:
        logs.append(f"[FIX] 共修复 {fixed_count} 个床位记录")
    return fixed_count


def _group_by_gender(selected_keys, logs):
    """将 selected_keys 按性别分组，查询实际住校人数—— S13/S14（grade+class_name 成对）"""
    from app.utils.helpers import get_dict_values, get_active_grades

    try:
        valid_grades = set(get_active_grades())
        valid_classes = set(get_dict_values('class'))
    except Exception:
        valid_grades, valid_classes = set(), set()

    # S13 单年级约束：所有 key 必须同一年级
    grades = {sk.get('grade', '') for sk in selected_keys}
    grades.discard('')
    if len(grades) > 1:
        raise ValueError('不允许同时分配两个年级的宿舍，请只勾选一个年级')

    boarding_ids = [sa.student_id for sa in StudentAccommodation.query.filter(
        StudentAccommodation.boarding_type == '住校'
    ).all()]

    male_list, female_list = [], []
    male_map, female_map = {}, {}

    for sk in selected_keys:
        grade = sk.get('grade', '')
        class_name = sk.get('class_name', '')
        gender = sk.get('gender', '')

        if valid_grades and grade not in valid_grades:
            continue
        if valid_classes and class_name not in valid_classes:
            continue
        if gender not in ('男', '女'):
            continue
        if class_name == '已转出':
            continue

        count = Student.query.filter(
            Student.grade == grade,          # S14 年级+班级成对
            Student.class_name == class_name,
            Student.gender == gender,
            Student.id.in_(boarding_ids) if boarding_ids else False,
            db.or_(
                Student.enrollment_status.is_(None),
                ~Student.enrollment_status.in_(['学籍已转出', '借读后离校'])
            )
        ).count()

        if count == 0:
            continue

        item = {'grade': grade, 'class_name': class_name, 'count': count, 'gender': gender}
        key = f"{grade}:{class_name}:{gender}"

        if gender == '男':
            if key not in male_map:
                male_map[key] = item
                male_list.append(item)
        else:
            if key not in female_map:
                female_map[key] = item
                female_list.append(item)

    logs.append(f"[INFO] 男生: {sum(c['count'] for c in male_list)}人 / {len(male_list)}个班级组合")
    logs.append(f"[INFO] 女生: {sum(c['count'] for c in female_list)}人 / {len(female_list)}个班级组合")
    return male_list, female_list


def _load_rooms(room_ids):
    """加载房间列表"""
    if not room_ids:
        return []
    rooms = Room.query.filter(Room.id.in_(room_ids), Room.is_active == True).all()
    rooms.sort(key=lambda r: (r.building or '', r.floor or 0, _room_number_int(r.room_number)))
    return rooms


def _check_assigned(all_rooms):
    """检测已分配过班级的房间"""
    assigned = []
    for r in all_rooms:
        if r.class_name and r.class_name.strip():
            assigned.append(f"{r.building} {r.room_number}({r.grade or ''} {r.class_name})")
    return len(assigned) > 0, assigned


def _load_class_profiles(classes):
    """批量加载班型信息"""
    profiles = {}
    grades = set(c['grade'] for c in classes)
    class_names = set(c['class_name'] for c in classes)
    if not grades or not class_names:
        return profiles
    try:
        results = ClassProfile.query.filter(
            ClassProfile.grade.in_(grades),
            ClassProfile.class_name.in_(class_names)
        ).all()
        for p in results:
            profiles[f"{p.grade}:{p.class_name}"] = p
    except Exception:
        pass
    return profiles


# ============================================================================
# 统一入口
# ============================================================================

def auto_assign_preview(selected_keys, selected_room_ids, mode='keep_existing',
                        occ_ranges=None, dry_run=True,
                        combine_confirmations=None, force_full_8=False,
                        adjusted_assignments=None):
    """
    预览/执行自动分配 V7（单年级）

    参数:
        selected_keys: [{grade, class_name, gender}, ...]（必须同一 grade —— S13）
        selected_room_ids: [room_id, ...]
        mode: 'keep_existing' | 'clear_all'
        dry_run: True=仅预览, False=写DB
    """
    logs = []
    all_assignments = []
    total_stats = {
        'total_students': 0,
        'total_rooms_assigned': 0,
        'combined_rooms': 0,
        'unassigned_students': 0,
        'below_min_rooms': 0,
    }
    levels = {}

    try:
        logs.append("[V7] 算法版本: v7 — 单年级平滑动态贪心（全局压力等级制）")

        # ---- S13 单年级校验（前端 + 后端双保险）----
        grades = {sk.get('grade', '') for sk in selected_keys}
        grades.discard('')
        if len(grades) > 1:
            return {'success': False,
                    'error': '不允许同时分配两个年级的宿舍，请只勾选一个年级再执行',
                    'logs': logs, 'stats': total_stats}

        # ---- 1. 按性别分组学生 ----
        male_classes, female_classes = _group_by_gender(selected_keys, logs)
        total_students = sum(c['count'] for c in male_classes) + sum(c['count'] for c in female_classes)
        logs.append(f"[INFO] 本次分配共 {total_students} 名学生")

        # ---- 2. 加载房间 ----
        all_rooms = _load_rooms(selected_room_ids)
        _ensure_room_beds(selected_room_ids, logs)
        db.session.commit()

        # ---- 3. 已分配房间检测 ----
        has_assigned, assigned_info = _check_assigned(all_rooms)

        if has_assigned and mode == 'keep_existing':
            logs.append(f"[WARN] 有 {len(assigned_info)} 间已分配，将保留并跳过")
            male_rooms_raw = [r for r in all_rooms if r.gender in ('男', '不限')
                              and not (r.class_name and r.class_name.strip())]
            female_rooms_raw = [r for r in all_rooms if r.gender in ('女', '不限')
                                and not (r.class_name and r.class_name.strip())]
        elif has_assigned and mode == 'clear_all':
            logs.append("[INFO] 覆盖模式：将清除所有已分配房间和床位")
            if not dry_run:
                for r in all_rooms:
                    r.grade = None
                    r.class_name = None
                    r.combined_class = None
                    r.combined_details = None
                room_ids = [r.id for r in all_rooms]
                BedAssignment.query.filter(
                    BedAssignment.room_id.in_(room_ids),
                    BedAssignment.student_id.isnot(None)
                ).update({'student_id': None, 'assigned_by': None, 'assigned_at': None},
                         synchronize_session=False)
            male_rooms_raw = [r for r in all_rooms if r.gender in ('男', '不限')]
            female_rooms_raw = [r for r in all_rooms if r.gender in ('女', '不限')]
        else:
            male_rooms_raw = [r for r in all_rooms if r.gender in ('男', '不限')]
            female_rooms_raw = [r for r in all_rooms if r.gender in ('女', '不限')]

        no_rooms_left = has_assigned and mode == 'keep_existing' \
            and len(male_rooms_raw) == 0 and len(female_rooms_raw) == 0

        # ---- 4. 加载班型信息 ----
        profiles = _load_class_profiles(male_classes + female_classes)

        # ---- 5. 分性别独立分配 ----
        for gender_classes, gender_rooms, gender_label in [
            (male_classes, male_rooms_raw, '男'),
            (female_classes, female_rooms_raw, '女'),
        ]:
            if not gender_classes:
                continue
            if not gender_rooms:
                logs.append(f"[ERROR] {gender_label}生无可用房间，"
                            f"{sum(c['count'] for c in gender_classes)}人无法分配")
                total_stats['unassigned_students'] += sum(c['count'] for c in gender_classes)
                continue

            sorted_classes = _sort_classes(gender_classes, profiles)
            room_dicts = [{'id': r.id, 'building': r.building, 'room_number': r.room_number,
                           'floor': r.floor, 'capacity': r.capacity, 'gender': r.gender}
                          for r in gender_rooms]
            sorted_rooms = sort_rooms_s(room_dicts)
            for c in sorted_classes:
                c['class_type'] = (profiles.get(f"{c['grade']}:{c['class_name']}").class_type
                                   if profiles.get(f"{c['grade']}:{c['class_name']}") else 'default')

            result = allocate_one_gender(sorted_classes, sorted_rooms, logs, gender_label)

            if not result['success']:
                return {
                    'success': False,
                    'error': f"{gender_label}生分配失败：{result.get('error', '')}",
                    'logs': logs,
                    'stats': total_stats,
                }

            levels[gender_label] = result['level']
            assignments = _build_assignments(result, sorted_rooms, sorted_classes, gender_label)
            all_assignments.extend(assignments)

            combined = sum(1 for a in assignments if a.get('is_combined'))
            used_rooms = sum(1 for v in result['occupied'] if v > 0)
            logs.append(f"[V7-{gender_label}] 完成: {len(assignments)}间房"
                        f"（使用{used_rooms}/{len(sorted_rooms)}间）, 合班{combined}间, "
                        f"未分配{max(0, result['total_students'] - result['total_alloc'])}人")

            total_stats['total_students'] += result['total_students']
            total_stats['total_rooms_assigned'] += len(assignments)
            total_stats['combined_rooms'] += combined
            total_stats['unassigned_students'] += max(0, result['total_students'] - result['total_alloc'])

        # ---- 最终校验 S12 ----
        if total_stats['unassigned_students'] > 0:
            failure_msg = f"有{total_stats['unassigned_students']}人无法分配宿舍，请增选宿舍后重试"
            logs.append(f"[ERROR] {failure_msg}")
            return {'success': False, 'error': failure_msg, 'logs': logs, 'stats': total_stats}

        # ---- 6. 应用用户手动调整 ----
        if adjusted_assignments and dry_run is False:
            logs.append("[INFO] 应用用户手动调整的分配方案...")
            room_map = {r.id: r for r in all_rooms}
            adjusted = []
            for aa in adjusted_assignments:
                room = room_map.get(aa.get('room_id'))
                if not room:
                    continue
                adjusted.append({
                    'room': room,
                    'grade': aa.get('grade', ''),
                    'class_name': aa.get('class_name', ''),
                    'gender': aa.get('gender', ''),
                    'expected_count': aa.get('expected_count', 0),
                    'is_combined': aa.get('is_combined', False),
                    'combined_info': aa.get('combined_info', ''),
                    'class_counts': aa.get('class_counts', []),
                    'below_min': aa.get('below_min', False),
                })
            if adjusted:
                all_assignments = adjusted
                logs.append(f"[INFO] 已应用手动调整方案，共 {len(adjusted)} 间房间")

        # ---- 7. 写DB ----
        if not dry_run and all_assignments:
            _write_to_db(all_assignments, all_rooms, logs)
            db.session.commit()

        return {
            'success': True,
            'logs': logs,
            'assignments': _format_assignments(all_assignments),
            'stats': total_stats,
            'levels': levels,
            'mode': '宽松' if all(v == 6 for v in levels.values()) else '紧张',
            'has_assigned': has_assigned,
            'assigned_room_count': len(assigned_info),
            'no_rooms_left': no_rooms_left,
            'needs_combine': False,
            'combine_suggestions': [],
            'scenarios': levels,
        }

    except ValueError as ve:
        db.session.rollback()
        logs.append(f"[ERROR] {str(ve)}")
        return {'success': False, 'error': str(ve), 'logs': logs, 'stats': total_stats}
    except Exception as e:
        db.session.rollback()
        logs.append(f"[ERROR] 分配异常: {str(e)}")
        import traceback
        logs.append(f"[TRACE] {traceback.format_exc()}")
        return {'success': False, 'error': str(e), 'logs': logs}


# ============================================================================
# 前端实时拥挤度评估（勾选房间时毫秒级计算，只读不写库）
# ============================================================================

def calc_pressure(selected_keys, room_ids):
    """返回每性别: {ok, total, beds, level, mode, combined_rooms, avg_per_room, levels_info, error}

    levels_info: 各级别（L=6/7/8）的床位可行性，供前端勾选时预览：
        {6: {cap, ok, need_beds, need_rooms}, 7: {...}, 8: {...}}
        need_rooms 为粗略估算（按该级别 L 人/间），提示还需勾选多少间
    """
    import math
    logs = []
    try:
        male_classes, female_classes = _group_by_gender(selected_keys, logs)
    except ValueError as ve:
        return {'error': str(ve)}
    all_rooms = _load_rooms(room_ids)
    profiles = _load_class_profiles(male_classes + female_classes)

    result = {}
    for label, classes in (('male', male_classes), ('female', female_classes)):
        gender_cn = '男' if label == 'male' else '女'
        info = {'ok': True, 'total': sum(c['count'] for c in classes),
                'beds': 0, 'level': None, 'mode': '', 'combined_rooms': 0,
                'avg_per_room': 0, 'levels_info': {}, 'error': ''}
        if not classes:
            result[label] = info
            continue
        g_rooms = [r for r in all_rooms if r.gender in (gender_cn, '不限')]
        info['beds'] = sum(r.capacity for r in g_rooms)
        room_dicts = [{'id': r.id, 'building': r.building, 'room_number': r.room_number,
                       'floor': r.floor, 'capacity': r.capacity, 'gender': r.gender}
                      for r in g_rooms]
        sorted_rooms = sort_rooms_s(room_dicts)

        # 各级别可行性（S4：6 人间满 6，8 人间按 L）
        for L in (6, 7, 8):
            cap = sum(min(L, r['capacity']) for r in sorted_rooms)
            need = max(0, info['total'] - cap)
            info['levels_info'][L] = {
                'cap': cap,
                'ok': need == 0,
                'need_beds': need,
                'need_rooms': math.ceil(need / L) if need > 0 else 0,
            }

        L, total_cap = calc_level(sorted_rooms, info['total'])
        if L is None:
            info['ok'] = False
            info['error'] = f'物理床位不足: {info["total"]}人 > {total_cap}床，需增选宿舍'
            result[label] = info
            continue
        info['level'] = L
        info['mode'] = '宽松' if L == 6 else '紧张'
        info['avg_per_room'] = round(info['total'] / len(sorted_rooms), 1) if sorted_rooms else 0
        sorted_classes = _sort_classes(classes, profiles)
        for c in sorted_classes:
            c['class_type'] = (profiles.get(f"{c['grade']}:{c['class_name']}").class_type
                               if profiles.get(f"{c['grade']}:{c['class_name']}") else 'default')
        alloc = allocate_one_gender(sorted_classes, sorted_rooms, logs, gender_cn)
        if alloc['success']:
            info['level'] = alloc.get('level', L)
            info['mode'] = alloc.get('mode', '宽松' if L == 6 else '紧张')
            info['combined_rooms'] = sum(
                1 for shares in alloc['room_details'].values() if len(shares) > 1)
            used = sum(1 for v in alloc['occupied'] if v > 0)
            info['used_rooms'] = used
            info['avg_per_room'] = round(info['total'] / used, 1) if used else 0
        else:
            info['ok'] = False
            info['error'] = alloc.get('error', '分配失败')
        result[label] = info
    return result


# ============================================================================
# 写库与输出
# ============================================================================

def _write_to_db(assignments, all_rooms, logs):
    """将分配结果写入 Room 表（room 支持 dict 或 ORM 对象）"""
    orm_map = {r.id: r for r in all_rooms}
    for a in assignments:
        room = a['room']
        if isinstance(room, dict):
            room = orm_map.get(room.get('id'))
            if room is None:
                continue
        room.grade = a.get('grade', '') or None
        room.gender = a.get('gender', room.gender or '')

        if a.get('is_combined'):
            combined_name = a.get('class_name', '')
            room.class_name = combined_name or None
            room.combined_class = combined_name or '合班'
            class_counts = a.get('class_counts', [])
            if class_counts:
                room.combined_details = json.dumps(class_counts, ensure_ascii=False)
            else:
                parts = combined_name.split('+')
                count_per = a.get('expected_count', 0) // len(parts) if parts else 0
                details = [{'class_name': p.strip(), 'count': count_per} for p in parts]
                room.combined_details = json.dumps(details, ensure_ascii=False)
        else:
            room.class_name = a.get('class_name', '') or None
            room.combined_class = None
            room.combined_details = None

    logs.append(f"[DONE] 已写入 {len(assignments)} 个房间分配")


def _format_assignments(assignments):
    """格式化为前端可消费的字典列表（room 支持 dict 或 ORM 对象）"""
    result = []
    for a in assignments:
        room = a['room']
        if isinstance(room, dict):
            room_id, rn, bd, fl, cap = (room.get('id'), room.get('room_number'),
                                        room.get('building'), room.get('floor'),
                                        room.get('capacity'))
        else:
            room_id, rn, bd, fl, cap = (room.id, room.room_number, room.building,
                                        room.floor, room.capacity)
        result.append({
            'room_id': room_id,
            'room_number': rn,
            'building': bd,
            'floor': fl,
            'capacity': cap,
            'grade': a.get('grade', ''),
            'class_name': a.get('class_name', ''),
            'gender': a.get('gender', ''),
            'expected_count': a.get('expected_count', 0),
            'is_combined': a.get('is_combined', False),
            'combined_info': a.get('combined_info', ''),
            'class_counts': a.get('class_counts', []),
        })
    return result
