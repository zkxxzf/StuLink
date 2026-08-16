"""
宿舍自动分配算法 v8（2026-08-15）— 初稿 + 迭代爬山 + 评分选优

================================================================================
核心思路（用户确认）
================================================================================
1. 初稿生成：严格分级（L=6→7→8）贪心，取第一个成功等级（同 v7 主分配）
2. 迭代爬山：每轮枚举所有可行"份额移动/交换"，执行评分下降最多的操作，
   直到无改善或达到迭代轮数上限（默认 300）
3. 评分函数：权重可配（前端可见可调），分数越低越好：
   score = L权重×(L-6) + 超6权重×8人间超6人数 + 合班权重×合班宿舍数
         + 分散权重×(各班占房数-1 之和) + 小房权重×≤3人房数 + 空房权重×空房数
4. 约束全程保持：同年级合班、≤2班/间、6人间满6不移出、不超limit、单年级操作
================================================================================
"""
# StuLink v1.8.0 2026-08-15（算法 v8：迭代爬山 + 评分选优）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import json
from collections import defaultdict
from app.models import Room, Student, ClassProfile, StudentAccommodation, BedAssignment
from app.extensions import db

# 复用 v7 工具（排序/分组/加载/写库等）
from app.modules.dormitory.services.room_assignment_v7 import (
    sort_rooms_s, _sort_classes, calc_level, _group_by_gender, _load_rooms,
    _check_assigned, _load_class_profiles, _ensure_room_beds, _allocate_with_level,
    _build_assignments, _write_to_db, _format_assignments, _room_number_int,
    _extract_class_number, _extract_grade_year, CLASS_TYPE_ORDER,
)

# 默认权重与迭代轮数（前端可调）
DEFAULT_WEIGHTS = {'L': 200, 'over6': 80, 'comb': 50, 'spread': 80, 'small': 100, 'empty': 20,
                   'full8': 100}
DEFAULT_ITERATIONS = 1000


# ============================================================================
# 评分函数（增量友好）
# ============================================================================

def _room_score(occ_n, room, L, W):
    """单间房评分项（不含班级分散）"""
    s = 0
    if 0 < occ_n <= 3:
        s += W['small']
    if occ_n == 0:
        s += W['empty']
    elif room['capacity'] >= 8 and occ_n > 6:
        s += W['over6'] * (occ_n - 6)
        if occ_n >= 8 and room['capacity'] == 8:
            s += W.get('full8', 100)   # 8 人间住满 8：物理极限重罚（离散）
    return s


def score_result(occ, room_details, rooms, L, W):
    """完整评分（用于初稿评估与最终输出）"""
    s = W['L'] * (L - 6)
    cls_rooms = defaultdict(list)
    for ri, shares in room_details.items():
        distinct = {k for k, _ in shares}
        occ_n = occ[ri]
        if len(distinct) > 1:
            s += W['comb']
        s += _room_score(occ_n, rooms[ri], L, W)
        for key, cnt in shares:
            cls_rooms[key].append(ri)
    for ris in cls_rooms.values():
        s += W['spread'] * (len(ris) - 1)
    return s


def _delta_move(occ, rd, rooms, L, W, ri_a, key, n, ri_b):
    """增量评分：key 的 n 人从 ri_a 移到 ri_b 的评分差（负=改善）"""
    delta = 0
    # ---- A 房间项 ----
    delta -= _room_score(occ[ri_a], rooms[ri_a], L, W)
    delta += _room_score(occ[ri_a] - n, rooms[ri_a], L, W)
    # ---- B 房间项 ----
    delta -= _room_score(occ[ri_b], rooms[ri_b], L, W)
    delta += _room_score(occ[ri_b] + n, rooms[ri_b], L, W)
    # ---- 合班项：A/B 班数变化 ----
    def _comb_term(ri, key_removed, key_added):
        """ri 移除 key_removed、加入 key_added 后是否为合班"""
        keys = {k for k, _ in rd.get(ri, [])}
        if key_removed:
            keys.discard(key_removed)
        if key_added:
            keys.add(key_added)
        return 1 if len(keys) > 1 else 0

    old_a_comb = 1 if len({k for k, _ in rd.get(ri_a, [])}) > 1 else 0
    new_a_comb = _comb_term(ri_a, key, None)
    delta += W['comb'] * (new_a_comb - old_a_comb)
    old_b_comb = 1 if len({k for k, _ in rd.get(ri_b, [])}) > 1 else 0
    new_b_comb = _comb_term(ri_b, None, key)
    delta += W['comb'] * (new_b_comb - old_b_comb)
    # ---- 分散项：key 占房数变化 ----
    key_rooms = sum(1 for sh in rd.values() if any(k == key for k, _ in sh))
    a_keeps = any(k == key and c > n for k, c in rd.get(ri_a, []))
    b_has = any(k == key for k, _ in rd.get(ri_b, []))
    after = key_rooms - (0 if a_keeps else 1) + (0 if b_has else 1)
    delta += W['spread'] * (after - key_rooms)
    return delta


def _apply_move(occ, rd, ri_a, key, n, ri_b):
    """执行移动：key 的 n 人从 ri_a 到 ri_b"""
    rem = []
    for k, c in rd.get(ri_a, []):
        if k == key:
            c -= n
        if c > 0:
            rem.append((k, c))
    if rem:
        rd[ri_a] = rem
    else:
        del rd[ri_a]
    occ[ri_a] -= n
    if ri_b in rd and key in {k for k, _ in rd[ri_b]}:
        rd[ri_b] = [(k, c + (n if k == key else 0)) for k, c in rd[ri_b]]
    elif ri_b in rd:
        rd[ri_b] = rd[ri_b] + [(key, n)]
    else:
        rd[ri_b] = [(key, n)]
    occ[ri_b] += n


# ============================================================================
# 迭代爬山
# ============================================================================

def _hillclimb(result, rooms, classes, L, W, max_rounds, logs, gender_label,
               stagnation_limit=20):
    """迭代爬山：枚举移动/交换，执行评分下降最多的操作，直到收敛或达上限

    收敛检测：连续 stagnation_limit（默认 20）轮未刷新历史最优（实质改善 < 评分 0.5%）
    即停止；局部最优时自动抖动（接受最不差操作）尝试跳出，结束时回滚到历史最优。
    """
    occ = list(result['occupied'])
    rd = {k: list(v) for k, v in result['room_details'].items()}
    cls_map = {c['key']: c for c in classes}
    base = score_result(occ, rd, rooms, L, W)
    min_improve = max(1, int(abs(base) * 0.005))   # 实质改善阈值（评分 0.5%）
    no_improve = 0
    improved = 0
    # 历史最优状态（抖动可能临时变差，结束时回滚到最低分状态）
    best_score = base
    best_state = (list(occ), {k: list(v) for k, v in rd.items()})

    for _ in range(max_rounds):
        best_delta, best_op = 10 ** 9, None   # 记录最小 delta 操作（含变差，供抖动）
        # ---- 操作1：份额拆分移动（移 1 人 / 移 min(2, 份额) 人）----
        for ri_a, shares in list(rd.items()):
            for key, cnt in list(shares):
                if len({k for k, _ in shares}) == 1 and occ[ri_a] <= 6:
                    continue  # 独享且≤6 人房不拆
                cls = cls_map.get(key)
                if not cls:
                    continue
                for ri_b in range(len(rooms)):
                    if ri_b == ri_a or rooms[ri_b]['gender'] not in (cls['gender'], '不限'):
                        continue
                    lim = min(L, rooms[ri_b]['capacity'])
                    if occ[ri_b] + 1 > lim:
                        continue
                    if occ[ri_b] > 0:
                        bd = {k for k, _ in rd.get(ri_b, [])}
                        if len(bd) >= 2 and key not in bd:
                            continue
                    max_n = min(cnt, lim - occ[ri_b])
                    for n in (1, min(2, max_n)):
                        d = _delta_move(occ, rd, rooms, L, W, ri_a, key, n, ri_b)
                        if d < best_delta:
                            best_delta, best_op = d, ('move', ri_a, key, n, ri_b)
        # ---- 操作2：交换（2 间合班房互换份额）----
        if best_op is None:
            comb_rooms = [ri for ri, sh in rd.items() if len({k for k, _ in sh}) == 2]
            for i in range(len(comb_rooms)):
                ri_a = comb_rooms[i]
                for j in range(i + 1, len(comb_rooms)):
                    ri_b = comb_rooms[j]
                    if rooms[ri_a]['gender'] != rooms[ri_b]['gender']:
                        continue
                    for key_a, cnt_a in rd[ri_a]:
                        for key_b, cnt_b in rd[ri_b]:
                            if key_a == key_b:
                                continue
                            n = min(cnt_a, cnt_b)
                            lim_a = min(L, rooms[ri_a]['capacity'])
                            lim_b = min(L, rooms[ri_b]['capacity'])
                            if occ[ri_a] - n + n > lim_a or occ[ri_b] - n + n > lim_b:
                                # 等量交换容量不变，直接检查最终班数是否可接受
                                pass
                            # 等量交换后：ri_a 的 key_a→key_b（同量），ri_b 反之
                            # 模拟评分差：用两次移动近似（key_a n 人 A→B + key_b n 人 B→A）
                            d1 = _delta_move(occ, rd, rooms, L, W, ri_a, key_a, n, ri_b)
                            # 第二次移动基于假设状态，近似用对称 delta
                            d2 = _delta_move(occ, rd, rooms, L, W, ri_b, key_b, n, ri_a)
                            d = d1 + d2
                            if d < best_delta:
                                best_delta, best_op = d, ('swap', ri_a, key_a, ri_b, key_b, n)
        # ---- 收敛判断（连续 20 轮未刷新历史最优即停止）----
        if best_op is None:
            break  # 连可行操作都没有 → 真最优，停止
        # 执行操作（改善或抖动统一执行；抖动接受 delta 最小——可能临时变差）
        if best_op[0] == 'move':
            _, ri_a, key, n, ri_b = best_op
            _apply_move(occ, rd, ri_a, key, n, ri_b)
        else:
            _, ri_a, key_a, ri_b, key_b, n = best_op
            _apply_move(occ, rd, ri_a, key_a, n, ri_b)
            _apply_move(occ, rd, ri_b, key_b, n, ri_a)
        improved += 1

        # 准确重算当前评分；只有"刷新历史最优（实质改善）"才清零计数，
        # 抖动变差与抖动后的"恢复"都不会刷新最优 → 计入无改善（避免震荡死循环）
        cur = score_result(occ, rd, rooms, L, W)
        min_improve = max(1, int(abs(best_score) * 0.005))   # 阈值随最优分动态
        if cur < best_score - min_improve:
            best_score = cur
            best_state = (list(occ), {k: list(v) for k, v in rd.items()})
            no_improve = 0
        else:
            no_improve += 1
            if no_improve >= stagnation_limit:
                logs.append(f"[V8-{gender_label}收敛] 连续 {stagnation_limit} 轮未刷新最优"
                            f"（含抖动跳出尝试），收敛停止（最优评分 {best_score:.0f}）")
                break

    # 回滚到历史最优状态（抖动后未找到更优时，避免方案退化）
    occ, rd = best_state

    if improved > 0:
        # 重建 allocations（与 room_details 严格一致）
        new_alloc = {k: [] for k in result['allocations']}
        for ri, shares in rd.items():
            for key, cnt in shares:
                new_alloc.setdefault(key, []).append((ri, cnt))
        result['allocations'] = new_alloc
        result['room_details'] = rd
        result['occupied'] = occ
        # 最终评分重算（不依赖增量累计，避免漂移）
        final_score = score_result(occ, rd, rooms, L, W)
        logs.append(f"[V8-{gender_label}迭代] 爬山优化 {improved} 轮（评分 {final_score:.0f}）")
    return improved


# ============================================================================
# 单性别分配（初稿 + 迭代爬山）
# ============================================================================

def allocate_one_gender(classes, rooms, logs, gender_label='',
                        weights=None, iterations=DEFAULT_ITERATIONS):
    """严格分级初稿 → 迭代爬山选优

    weights: {'L','over6','comb','spread','small','empty'}（缺省用默认）
    iterations: 迭代轮数上限（无改善提前收敛）
    """
    W = {**DEFAULT_WEIGHTS, **(weights or {})}
    if not iterations:
        iterations = DEFAULT_ITERATIONS
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

    logs.append(f"[V8-{gender_label}] 总人数{total_students} | 房间{len(rooms)}间 | "
                f"物理容量{total_cap}床 | L={L0} -> {'宽松' if L0 == 6 else '紧张'}模式")

    last_error = None
    for L in range(L0, 9):
        result = _allocate_with_level(classes, rooms, L)
        if result['success']:
            result['level'] = L
            result['mode'] = '宽松' if L == 6 else '紧张'
            # 迭代爬山优化（权重/轮数可调）
            improved = _hillclimb(result, rooms, classes, L, W, iterations, logs, gender_label)
            logs.append(f"[V8-{gender_label}] 选定 L={L}"
                        f"（{'宽松：6人间6人、8人间6人' if L == 6 else '紧张：8人间住%d人' % L}）"
                        f" | 迭代{improved}轮")
            return result
        last_error = result.get('error')
        if L < 8:
            logs.append(f"[V8-{gender_label}] L={L} 房间不足，清除重算升 L={L + 1}"
                        f"（合班宿舍上限同步 {L + 1} 人）")
        else:
            logs.append(f"[V8-{gender_label}] L=8 仍无法分配: {last_error}")
    return {'success': False, 'error': last_error or '分配失败'}


# ============================================================================
# 统一入口
# ============================================================================

def auto_assign_preview(selected_keys, selected_room_ids, mode='keep_existing',
                        occ_ranges=None, dry_run=True,
                        combine_confirmations=None, force_full_8=False,
                        adjusted_assignments=None,
                        weights=None, iterations=DEFAULT_ITERATIONS):
    """预览/执行自动分配 V8（单年级 + 迭代爬山）

    weights: 评分权重 {L, over6, comb, spread, small, empty}（前端可调）
    iterations: 迭代轮数上限（默认 300）
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
        logs.append(f"[V8] 算法版本: v8 — 初稿 + 迭代爬山 + 评分选优"
                    f"（权重{weights or '默认'}，迭代{iterations}轮上限）")

        # 单年级校验（S13）
        grades = {sk.get('grade', '') for sk in selected_keys}
        grades.discard('')
        if len(grades) > 1:
            return {'success': False,
                    'error': '不允许同时分配两个年级的宿舍，请只勾选一个年级再执行',
                    'logs': logs, 'stats': total_stats}

        # 1. 按性别分组
        male_classes, female_classes = _group_by_gender(selected_keys, logs)
        total_students = sum(c['count'] for c in male_classes) + sum(c['count'] for c in female_classes)
        logs.append(f"[INFO] 本次分配共 {total_students} 名学生")

        # 2. 加载房间
        all_rooms = _load_rooms(selected_room_ids)
        _ensure_room_beds(selected_room_ids, logs)
        db.session.commit()

        # 3. 已分配房间检测
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

        # 4. 班型
        profiles = _load_class_profiles(male_classes + female_classes)

        # 5. 分性别分配（v8：初稿 + 迭代爬山）
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

            result = allocate_one_gender(sorted_classes, sorted_rooms, logs, gender_label,
                                         weights=weights, iterations=iterations)

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
            logs.append(f"[V8-{gender_label}] 完成: {len(assignments)}间房"
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

        # 6. 应用手动调整
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

        # 7. 写库
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
# 前端实时拥挤度评估（勾选房间时毫秒级，权重可配）
# ============================================================================

def calc_pressure(selected_keys, room_ids, weights=None, iterations=DEFAULT_ITERATIONS):
    """返回每性别: {ok, total, beds, level, mode, combined_rooms, avg_per_room, levels_info, error}"""
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
        sorted_classes = _sort_classes(classes, profiles)
        for c in sorted_classes:
            c['class_type'] = (profiles.get(f"{c['grade']}:{c['class_name']}").class_type
                               if profiles.get(f"{c['grade']}:{c['class_name']}") else 'default')
        alloc = allocate_one_gender(sorted_classes, sorted_rooms, logs, gender_cn,
                                    weights=weights, iterations=min(iterations, 50))
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
