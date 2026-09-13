"""
宿舍自动分配算法 V20260805 — 平滑动态贪心（全局压力等级制）

================================================================================
核心规则 (v20260805)
================================================================================
1. 全局压力等级 L: 从 6 递增，取第一个满足 Σ min(L, 房间容量) >= 总人数 的等级(6/7/8)
   - L=6 宽松: 所有房间(含合班房) <= 6 人
   - L=7 紧张: 8人间住 7 人(合班房同步 <= 7)
   - L=8 极限: 8人间住满 8 人(合班房同步 <= 8)
2. 房间入住上限 limit = min(L, 房间容量)，普通房与合班房完全一致
3. 合班被动触发: 班级收尾剩余 1~5 人且非末班、下一班有剩余、同班型、
   本班未发起过合班(被借不算发起) → 借人填满上限
4. S型序列化: 楼层分组，偶数层正向、奇数层反向，楼层升序连接
5. 班级排序: (年级数字升序[高年级优先], 班型 default<卓越班<强基班, 班号升序)
6. 合班房每班份额精确记录 → 与床位分配天然一致
7. 合班宿舍调整(后处理优化): 分解链式合班(如 01+02、02+03)，中间班份额
   可独立成房且两端班可重组合班时，把 2 间合班房重组为 1 间合班房 +
   1 间独立房，减少合班宿舍数量，不影响主算法结果
================================================================================
"""
# StuLink v1.7.2 2026-08-13（算法：小容量房间优先住满 + 独享宿舍优先于合班）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import re
import json
from collections import defaultdict
from app.models import Room, Student, ClassProfile, StudentAccommodation, BedAssignment
from app.extensions import db


# ============================================================================
# 常量与排序工具
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
    同层内容量小的房间优先（如 6 人间先于 8 人间），保证小容量房间尽量住满

    rooms: [{floor, room_number, capacity, ...}, ...]（dict 或 Room 对象均可）
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
    """班级排序: (年级数字升序, 班型, 班号升序)"""

    def _class_type(c):
        p = profiles.get(f"{c['grade']}:{c['class_name']}")
        return (p.class_type or 'default') if p else 'default'

    return sorted(classes, key=lambda c: (
        _extract_grade_year(c['grade']),
        CLASS_TYPE_ORDER.get(_class_type(c), 0),
        _extract_class_number(c['class_name'])))


# ============================================================================
# v20260805 核心
# ============================================================================

def calc_level(rooms, total_students):
    """全局压力等级: 返回 (L, 物理容量) 或 (None, 物理容量)"""
    total_cap = sum(r['capacity'] for r in rooms)
    if total_students > total_cap:
        return None, total_cap
    for L in (6, 7, 8):
        if sum(min(L, r['capacity']) for r in rooms) >= total_students:
            return L, total_cap
    return None, total_cap


def allocate_one_gender(classes, rooms, logs, gender_label=''):
    """对单一性别执行 v20260805 平滑动态贪心分配

    L 从最低可行等级(L0)开始尝试；若因收尾独占导致"房间不足"，
    自动升档重试（每间住更多人 → 消耗房间更少），直到成功或 L=8 仍失败。

    classes: 已排序班级 [{grade, class_name, gender, count, key}]（缺 key/class_type 自动补齐）
    rooms:   已 S 型排序房间 [{id, building, room_number, floor, capacity, gender}]
    返回: {success, level, mode, allocations, room_details, occupied,
          total_alloc, total_students, error?}
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

    logs.append(f"[V20260805-{gender_label}] 总人数{total_students} | 房间{len(rooms)}间 | "
                f"物理容量{total_cap}床 | L={L0} -> {'宽松' if L0 == 6 else '紧张'}模式")

    last_error = None
    # 多轮迭代：尝试所有可行 L（6/7/8），每档生成候选方案并按约束满足度评估，选最优
    candidates = []
    for L in range(L0, 9):
        result = _allocate_with_level(classes, rooms, L)
        if result['success']:
            # 合班宿舍调整（后处理优化步骤）：减少链式合班产生的合班宿舍
            _optimize_combined_rooms(result, rooms, classes, L, logs, gender_label)
            # 房间充足分散优化（后处理）：把超过等级上限的高占用独享房拆到空房间，
            # 尽量用上所有勾选房间，降低每间宿舍人数（合班房最多 2 个班）
            _spread_to_empty_rooms(result, rooms, classes, L, logs, gender_label)
            score = _evaluate_solution(result, rooms, L)
            candidates.append((score, L, result))
            logs.append(f"[V20260805-{gender_label}] L={L} 候选评分 {score:.1f}")
        else:
            last_error = result.get('error')
            if L < 8:
                logs.append(f"[V20260805-{gender_label}] L={L} 房间不足（收尾班级占房过多），"
                            f"尝试 L={L + 1}")
            else:
                logs.append(f"[V20260805-{gender_label}] L=8 仍无法分配: {last_error}")

    if candidates:
        # 选约束满足度最高的方案
        candidates.sort(key=lambda x: -x[0])
        best_score, best_L, best_result = candidates[0]
        best_result['level'] = best_L
        best_result['mode'] = '宽松' if best_L == 6 else '紧张'
        logs.append(f"[V20260805-{gender_label}] 多轮迭代选定 L={best_L}（评分 {best_score:.1f}）")
        return best_result
    return {'success': False, 'error': last_error or '分配失败'}


def _evaluate_solution(result, rooms, L):
    """方案评估（约束满足度，分数越高越好）

    - 等级 L 越低越好（宽松优先）
    - 房间使用率越高越好（尽量用上所有勾选房间）
    - 8 人间住满 8 人惩罚（无论 L，物理住满都不优先；L=8 时同样惩罚）
    - 超过 2 个班的合班房重罚（不合理合班）
    - 合班房总数轻度惩罚
    """
    room_details = result.get('room_details') or {}
    occupied = result.get('occupied') or []
    n = len(rooms) or 1
    used = sum(1 for v in occupied if v > 0)
    # 8 人间住满 8 人：无论 L 统一按物理满员惩罚（L=7 的 over 不再额外重罚，
    # 否则禁止跨年级合班后 L=7 的少量 8 人房方案会被 L=8 的大量 8 人房方案打败）
    full8 = sum(1 for i, v in enumerate(occupied)
                if rooms[i]['capacity'] >= 8 and v == 8)
    multi = sum(1 for sh in room_details.values() if len(sh) > 2)
    comb = sum(1 for sh in room_details.values() if len(sh) > 1)
    score = (8 - L) * 20 + used / n * 5 - full8 * 5 - multi * 50 - comb * 2
    return score


def _spread_to_empty_rooms(result, rooms, classes, L, logs=None, gender_label=''):
    """房间充足分散优化（后处理，不影响主算法结果）

    当勾选房间数多于实际需要时，把超过等级上限(L)的高占用独享房学生
    拆到空房间（每空房最多收 L 人，可累积多个班级变合班房），
    使每个房间人数不超过 L，并尽量用上所有勾选房间
    （如 8 人间 8 人 -> 7 人，空房分到 1 人）。

    约束：只拆独享房（合班房不动）；目标空房性别匹配（含不限）；
    班级份额/分配记录同步重建，保证一致。

    返回: 分散的人数
    """
    if logs is None:
        logs = []
    room_details = result.get('room_details') or {}
    occupied = result.get('occupied') or []
    allocations = result.get('allocations') or {}
    if not room_details or len(occupied) != len(rooms):
        return 0

    cls_map = {c['key']: c for c in classes}

    # 1. 空房按性别分组（'不限' 单独一组，可被任意性别使用）
    empty_by_gender = {}
    for ri in range(len(rooms)):
        if occupied[ri] == 0:
            empty_by_gender.setdefault(rooms[ri]['gender'], []).append(ri)
    unisex_empty = empty_by_gender.pop('不限', [])
    if not empty_by_gender and not unisex_empty:
        return 0

    # 2. 超员池：独享房且人数超过 L
    over_by_gender = {}
    for ri in sorted(room_details.keys()):
        if occupied[ri] <= L:
            continue
        shares = room_details[ri]
        if len(shares) != 1:
            continue  # 合班房不拆
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
                # 约束：合班宿舍最多 2 个班且必须同年级
                # （跨年级合班在宿舍页面显示异常，必须禁止）
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
                # 空房累积份额（同班可继续累积，跨班最多 2 个班）
                if cur == 0:
                    room_details[ei] = [(key, fill)]
                else:
                    room_details[ei] = room_details.get(ei, []) + [(key, fill)]
                occupied[ei] = cur + fill
                occupied[ri] -= fill
                ov -= fill
                moved += fill
                if occupied[ei] >= L:
                    ti += 1  # 该空房已满
            room_details[ri] = [(key, occupied[ri])]

    # 第二轮：仍有空房时，从满员房(==L)移 1 人到空房，最大化分散人数
    # （已用空房不再收人，保持分散；源房降到 L-1）
    remaining_empty = [ri for ri in range(len(rooms)) if occupied[ri] == 0]
    if remaining_empty:
        full_rooms = [ri for ri, shares in room_details.items()
                      if len(shares) == 1 and occupied[ri] == L]
        for ri in full_rooms:
            if not remaining_empty:
                break
            key, cnt = room_details[ri][0]
            cls = cls_map.get(key)
            if not cls:
                continue
            gender = cls['gender']
            # 目标：同性别空房，其次不限空房
            ei = None
            for g in (gender, '不限'):
                pool = [e for e in remaining_empty if rooms[e]['gender'] == g]
                if pool:
                    ei = pool[0]
                    break
            if ei is None:
                continue
            fill = 1
            room_details[ei] = [(key, fill)]
            occupied[ei] = fill
            occupied[ri] -= fill
            room_details[ri] = [(key, occupied[ri])]
            moved += fill
            remaining_empty.remove(ei)

    if moved > 0:
        # 3. 重建分配记录（与 room_details 严格一致）
        new_alloc = {k: [] for k in allocations}
        for ei, shares in room_details.items():
            for key, cnt in shares:
                new_alloc.setdefault(key, []).append((ei, cnt))
        result['allocations'] = new_alloc
        logs.append(f"[V20260805-{gender_label}分散] 房间充足优化: {moved} 人分散到空房间"
                    f"（所有房间不超过 {L} 人）")
    return moved


def _allocate_with_level(classes, rooms, L):
    """用指定等级 L 执行一趟贪心分配（内部函数，不负责升档重试）"""
    n = len(rooms)
    total_students = sum(c['count'] for c in classes)

    room_idx = 0
    has_merged = False
    merge_prev_share = 0
    initiated = defaultdict(int)        # 发起过合班的班级计数（被借不算），每班最多发起 2 次
    # （链式合班更充分，减少收尾独占造成的房间碎片；合班房仍 ≤2 个班/间）
    class_rem = {c['key']: c['count'] for c in classes}
    allocations = {c['key']: [] for c in classes}
    room_details = {}
    occupied = [0] * n
    class_rooms = defaultdict(list)    # key -> [room_idx,...] 本班占用的房间（用于收尾时填满独享空床）

    for i, cur in enumerate(classes):
        key = cur['key']

        # ---- 处理上一班遗留的合班房（必须先于 rem==0 检查，确保被借完的班份额正确记录）----
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
                # 房间不足兜底：填满本班独享宿舍空床（突破 limit 至物理容量）
                # （收尾阶段 has_spare 只判断指针后是否有空房，无法预测后续班级的
                #   房间需求；此处兜底保证收尾班级优先填满自己的独享房而非无床可睡）
                for ri in list(class_rooms[key]):
                    if rem <= 0:
                        break
                    if len(room_details.get(ri, [])) != 1:
                        continue  # 合班房不参与填满
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
            limit = min(L, maxc)       # 全局等级制：普通房与合班房上限一致

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
                # ① 先填满本班独享宿舍空床（突破 limit 至物理容量）
                #    提高房间利用率、减少收尾占房（禁止跨年级合班后收尾只能独占，
                #    必须靠填满独享来省房；产生的 8 人房由评分机制优先 L=7 方案）
                for ri in list(class_rooms[key]):
                    if rem <= 0:
                        break
                    if len(room_details.get(ri, [])) != 1:
                        continue  # 合班房不参与填满
                    space = rooms[ri]['capacity'] - occupied[ri]
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
                # ② 仍有人剩余，才走原有收尾逻辑（末班独占/合班）
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
                    # 合班条件：下一班有剩余 + 同班型 + 同年级 + 本班未发起过合班
                    # （跨年级合班（如 2024级10班+2025级01班）在宿舍页面显示异常，
                    #   且实际管理中不同年级不应混住，必须禁止）
                    if next_rem <= 0 or (nxt['class_type'] or 'default') != (cur['class_type'] or 'default') \
                            or nxt['grade'] != cur['grade'] \
                            or initiated[key] >= 2:
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
                        initiated[key] += 1
                        has_merged = True
                        merge_prev_share = rem
                        rem = 0
                        # room_idx 不递增 → 下一班继续使用这间合班房
                        break

    total_alloc = sum(sum(c for _, c in alloc) for alloc in allocations.values())
    return {'success': True, 'allocations': allocations,
            'room_details': room_details, 'occupied': occupied,
            'total_alloc': total_alloc, 'total_students': total_students}


def _optimize_combined_rooms(result, rooms, classes, L, logs=None, gender_label=''):
    """合班宿舍调整（后处理优化步骤，不影响主算法分配结果）

    问题：链式合班产生多余合班宿舍。例如班级 01班-02班-03班 连续合班时：
        合班房A(01班5人, 02班1人)、合班房B(02班5人, 03班1人)，
        02班在合班房只占 1 人却要管理 2 间合班宿舍。
    优化：若中间班(02班)的合班总份额可独立成 1 间房（≤ 房间上限），
        且两端班(01班、03班)的份额可合并成 1 间合班房（≤ 房间上限），
        则把 2 间合班房重组为 1 间合班房 + 1 间独立房，合班宿舍数 -1。
    约束保持：同年级同班型（链内天然一致）、每班发起合班次数 ≤ 1、
        房间人数 ≤ 该房上限 min(L, capacity)、班级总人数不变、房间位置不变。
    迭代执行直到无中间班可调整。

    返回: 减少的合班宿舍数
    """
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
        # 重新收集合班房（每次调整后结构可能变化）
        comb = {ri: list(shares) for ri, shares in room_details.items()
                if len(shares) == 2}
        if not comb:
            break
        # 班级 -> [(房间索引, 份额, 是否发起方)]，发起方为份额列表第 1 个班
        cls_rooms = defaultdict(list)
        for ri, shares in comb.items():
            for idx, (key, cnt) in enumerate(shares):
                cls_rooms[key].append((ri, cnt, idx == 0))

        changed = False
        for key, items in list(cls_rooms.items()):
            if len(items) != 2:
                continue  # 防御：只处理 1 被借 + 1 发起的中间班
            borrowed = [x for x in items if not x[2]]
            initiated = [x for x in items if x[2]]
            if len(borrowed) != 1 or len(initiated) != 1:
                continue
            (ri_borrow, share_borrow, _), = borrowed
            (ri_init, share_init, _), = initiated
            shares_b = comb[ri_borrow]       # [前班, 中间班]
            shares_i = comb[ri_init]         # [中间班, 后班]
            prev_key, prev_share = shares_b[0]
            next_key, next_share = shares_i[1]

            # 条件0: 链内 3 个班必须同年级（跨年级合班在宿舍页面显示异常，必须禁止）
            if len({cls_grade.get(k) for k in (prev_key, key, next_key)}) != 1:
                continue

            # 条件1: 中间班合班总份额可独立成 1 间房
            total = share_borrow + share_init
            if total > _room_limit(ri_init):
                continue
            # 条件2: 两端班份额可重组合成 1 间合班房
            if prev_share + next_share > _room_limit(ri_borrow):
                continue

            # 执行调整：ri_borrow -> (前班+后班) 合班房；ri_init -> 中间班独立房
            room_details[ri_borrow] = [(prev_key, prev_share), (next_key, next_share)]
            room_details[ri_init] = [(key, total)]
            occupied[ri_borrow] = prev_share + next_share
            occupied[ri_init] = total
            allocations[key] = [(r, c) for r, c in allocations[key]
                                if r not in (ri_borrow, ri_init)] + [(ri_init, total)]
            if next_key in allocations:
                allocations[next_key] = [(r, c) for r, c in allocations[next_key]
                                         if r != ri_init] + [(ri_borrow, next_share)]
            # 前班份额仍在 ri_borrow，无需改动
            reduced += 1
            changed = True
            pn, kn, nn = (cls_label.get(prev_key, prev_key),
                          cls_label.get(key, key),
                          cls_label.get(next_key, next_key))
            logs.append(f"[V20260805-{gender_label}优化] 合班调整: 拆"
                        f"{pn}+{kn}、{kn}+{nn} → {pn}+{nn}合班 + {kn}{total}人独立"
                        f"（合班宿舍-1）")
            break
        if not changed:
            break

    if reduced > 0:
        logs.append(f"[V20260805-{gender_label}] 合班宿舍调整完成: 共减少 {reduced} 间合班宿舍")
    return reduced


def _build_assignments(result, rooms, classes, gender):
    """将分配结果转换为 assignment 字典列表（合班房精确份额）"""
    cls_map = {c['key']: c for c in classes}
    assignments = []
    for ri in sorted(result['room_details']):
        shares = result['room_details'][ri]          # [(key, count), ...]
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
                # class_counts 带 grade：跨年级合班（如 2024级10班+2025级01班）时，
                # 每个份额必须记录自身年级，否则分床位按 Room.grade 过滤会丢失份额
                'class_counts': [{'grade': cls_map[k]['grade'],
                                  'class_name': cls_map[k]['class_name'], 'count': c}
                                 for k, c in shares],
            })
    assignments.sort(key=lambda a: (
        a['room'].get('building') or '', a['room'].get('floor') or 0,
        _room_number_int(a['room'].get('room_number'))))
    return assignments


# ============================================================================
# 防御性工具函数
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
                b.bed_number for b in
                BedAssignment.query.filter_by(room_id=room.id).all()
            )
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


# ============================================================================
# 统一入口
# ============================================================================

def auto_assign_preview(selected_keys, selected_room_ids, mode='keep_existing',
                        occ_ranges=None, dry_run=True,
                        combine_confirmations=None, force_full_8=False,
                        adjusted_assignments=None):
    """
    预览/执行自动分配 V20260805

    参数:
        selected_keys: [{grade, class_name, gender}, ...]
        selected_room_ids: [room_id, ...]
        mode: 'keep_existing' | 'clear_all'
        occ_ranges: 保留参数兼容性（V20260805 不使用，上限由全局压力等级决定）
        dry_run: True=仅预览, False=写DB
        combine_confirmations/force_full_8: 保留参数兼容性
        adjusted_assignments: 用户手动调整后的分配方案

    返回: {success, logs, assignments, stats, levels, scenarios, ...}
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
        # ---- 0. 参数校验 ----
        logs.append("[V20260805] 算法版本: v20260805 — 平滑动态贪心（全局压力等级制）")

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
            male_rooms_raw = [r for r in all_rooms if r.gender in ('男', '不限') and not (r.class_name and r.class_name.strip())]
            female_rooms_raw = [r for r in all_rooms if r.gender in ('女', '不限') and not (r.class_name and r.class_name.strip())]
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
                ).update({'student_id': None, 'assigned_by': None, 'assigned_at': None}, synchronize_session=False)
            male_rooms_raw = [r for r in all_rooms if r.gender in ('男', '不限')]
            female_rooms_raw = [r for r in all_rooms if r.gender in ('女', '不限')]
        else:
            male_rooms_raw = [r for r in all_rooms if r.gender in ('男', '不限')]
            female_rooms_raw = [r for r in all_rooms if r.gender in ('女', '不限')]

        no_rooms_left = has_assigned and mode == 'keep_existing' and len(male_rooms_raw) == 0 and len(female_rooms_raw) == 0

        # ---- 4. 加载班型信息 ----
        profiles = _load_class_profiles(male_classes + female_classes)

        # ---- 5. 分性别独立分配 ----
        needs_combine = False
        combine_suggestions = []

        for gender_classes, gender_rooms, gender_label in [
            (male_classes, male_rooms_raw, '男'),
            (female_classes, female_rooms_raw, '女'),
        ]:
            if not gender_classes:
                continue
            if not gender_rooms:
                logs.append(f"[ERROR] {gender_label}生无可用房间，{sum(c['count'] for c in gender_classes)}人无法分配")
                total_stats['unassigned_students'] += sum(c['count'] for c in gender_classes)
                continue

            # 班级排序: (年级, 班型, 班号)
            sorted_classes = _sort_classes(gender_classes, profiles)
            # 房间 S 型序列化
            room_dicts = [{'id': r.id, 'building': r.building, 'room_number': r.room_number,
                           'floor': r.floor, 'capacity': r.capacity, 'gender': r.gender}
                          for r in gender_rooms]
            sorted_rooms = sort_rooms_s(room_dicts)
            # 班型注入
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
            logs.append(f"[V20260805-{gender_label}] 完成: {len(assignments)}间房"
                        f"（使用{used_rooms}/{len(sorted_rooms)}间）, 合班{combined}间, "
                        f"未分配{max(0, result['total_students'] - result['total_alloc'])}人")

            total_stats['total_students'] += result['total_students']
            total_stats['total_rooms_assigned'] += len(assignments)
            total_stats['combined_rooms'] += combined
            total_stats['unassigned_students'] += max(0, result['total_students'] - result['total_alloc'])

        # 最终校验
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
            'needs_combine': needs_combine,
            'combine_suggestions': combine_suggestions,
            'scenarios': levels,
        }

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
    """返回每性别: {ok, total, beds, level, mode, combined_rooms, error}"""
    logs = []
    male_classes, female_classes = _group_by_gender(selected_keys, logs)
    all_rooms = _load_rooms(room_ids)
    profiles = _load_class_profiles(male_classes + female_classes)

    result = {}
    for label, classes in (('male', male_classes), ('female', female_classes)):
        gender_cn = '男' if label == 'male' else '女'
        info = {'ok': True, 'total': sum(c['count'] for c in classes),
                'beds': 0, 'level': None, 'mode': '', 'combined_rooms': 0, 'error': ''}
        if not classes:
            result[label] = info
            continue
        g_rooms = [r for r in all_rooms if r.gender in (gender_cn, '不限')]
        info['beds'] = sum(r.capacity for r in g_rooms)
        room_dicts = [{'id': r.id, 'building': r.building, 'room_number': r.room_number,
                       'floor': r.floor, 'capacity': r.capacity, 'gender': r.gender}
                      for r in g_rooms]
        sorted_rooms = sort_rooms_s(room_dicts)
        L, total_cap = calc_level(sorted_rooms, info['total'])
        if L is None:
            info['ok'] = False
            info['error'] = f'物理床位不足: {info["total"]}人 > {total_cap}床，需增选宿舍'
            result[label] = info
            continue
        info['level'] = L
        info['mode'] = '宽松' if L == 6 else '紧张'
        sorted_classes = _sort_classes(classes, profiles)
        for c in sorted_classes:
            c['class_type'] = (profiles.get(f"{c['grade']}:{c['class_name']}").class_type
                               if profiles.get(f"{c['grade']}:{c['class_name']}") else 'default')
        alloc = allocate_one_gender(sorted_classes, sorted_rooms, logs, gender_cn)
        if alloc['success']:
            # 注意：可能发生升档重试，以实际成功等级为准（不一定等于 calc_level 的初始 L）
            info['level'] = alloc.get('level', L)
            info['mode'] = alloc.get('mode', '宽松' if L == 6 else '紧张')
            info['combined_rooms'] = sum(
                1 for shares in alloc['room_details'].values() if len(shares) > 1)
        else:
            info['ok'] = False
            info['error'] = alloc.get('error', '分配失败')
        result[label] = info
    return result


# ============================================================================
# 数据加载工具函数
# ============================================================================

def _group_by_gender(selected_keys, logs):
    """将 selected_keys 按性别分组，查询实际住校人数"""
    from app.utils.helpers import get_dict_values, get_active_grades

    try:
        valid_grades = set(get_active_grades())
        valid_classes = set(get_dict_values('class'))
    except Exception:
        valid_grades, valid_classes = set(), set()

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
            Student.grade == grade,
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
# 输出函数
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
            # 合班宿舍：class_name 存完整合班名（多个班+拼接，不分主次），combined_class 同步兼容
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
