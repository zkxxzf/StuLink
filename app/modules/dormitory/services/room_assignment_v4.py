"""
宿舍自动分配算法 V4.0 — 两阶段 + 7种情形逐级尝试

Phase 1（选班后）: 计算 sum(ceil/6) 得到最多所需房间数
Phase 2（选房后）: 按7种情形逐级尝试，找到第一个可行方案
  情形1: 均≤6人，无合班
  情形2: 均≤6人，有合班(合班≤6)
  情形3: 6人间6人，8人间7人，无合班
  情形4: 6人间6人，8人间7人，有合班(合班≤6)
  情形5: 6人间6人，8人间8人，无合班
  情形6: 6人间6人，8人间8人，有合班(合班≤6)
  情形7: 6人间6人，8人间8人，有合班(合班≤8) — 极限模式

确定情形后：按(楼栋,楼层,房号)顺序分配，同班级房间同楼层连续，合班居中。
"""
# StuLink v1.6.1 2026-07-09
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import math
from collections import defaultdict, OrderedDict
from app.models import Room, Student, ClassProfile
from app.extensions import db


# ============================================================================
# 统一入口（与V3保持相同接口）
# ============================================================================

def auto_assign_preview(selected_keys, selected_room_ids, mode='keep_existing',
                        combine_confirmations=None, force_full_8=False, dry_run=True):
    """
    预览/执行自动分配 V4

    参数同 V3:
        selected_keys: [{grade, class_name, gender}, ...]
        selected_room_ids: [room_id, ...]
        mode: 'keep_existing' | 'clear_all'
        combine_confirmations: [{class1, class2, room_id}, ...]
        force_full_8: True=允许情形7（极限模式）
        dry_run: True=仅预览, False=写DB

    返回: {success, logs, assignments, stats, needs_combine, combine_suggestions,
            scenario, needs_level_upgrade, has_assigned, ...}
    """
    logs = []
    all_assignments = []
    total_stats = {
        'total_students': 0,
        'total_rooms_assigned': 0,
        'combined_rooms': 0,
        'unassigned_students': 0,
    }

    try:
        # ---- 1. 按性别分组学生 ----
        male_classes, female_classes = _group_by_gender(selected_keys, logs)
        total_students = sum(c['count'] for c in male_classes) + sum(c['count'] for c in female_classes)
        logs.append(f"[INFO] 本次分配共 {total_students} 名学生")

        # ---- 2. 加载房间，按性别分组 ----
        all_rooms = _load_rooms(selected_room_ids)

        # 已分配房间检测 - mode='keep_existing'时保留原有分配
        has_assigned, assigned_info = _check_assigned(all_rooms)
        
        if has_assigned and mode == 'keep_existing':
            logs.append(f"[WARN] 有 {len(assigned_info)} 间已分配，将保留并跳过")
            # 从可用房间中移除已分配的房间（这些房间已有班级占用）
            male_rooms_raw = [r for r in all_rooms if r.gender in ('男', '不限') and not (r.class_name and r.class_name.strip())]
            female_rooms_raw = [r for r in all_rooms if r.gender in ('女', '不限') and not (r.class_name and r.class_name.strip())]
            logs.append(f"[INFO] 剩余可用房间: 男生{len(male_rooms_raw)}间, 女生{len(female_rooms_raw)}间")
        elif has_assigned and mode == 'clear_all':
            logs.append("[INFO] 覆盖模式：将清除所有已分配房间和床位")
            if not dry_run:
                for r in all_rooms:
                    r.grade = None
                    r.class_name = None
                    r.combined_class = None
                # 同时清除这些房间的床位分配
                from app.models import BedAssignment
                room_ids = [r.id for r in all_rooms]
                BedAssignment.query.filter(
                    BedAssignment.room_id.in_(room_ids),
                    BedAssignment.student_id.isnot(None)
                ).update({'student_id': None, 'assigned_by': None, 'assigned_at': None}, synchronize_session=False)
            male_rooms_raw = [r for r in all_rooms if r.gender in ('男', '不限')]
            female_rooms_raw = [r for r in all_rooms if r.gender in ('女', '不限')]
        else:
            # 无已分配或dry_run模式
            male_rooms_raw = [r for r in all_rooms if r.gender in ('男', '不限')]
            female_rooms_raw = [r for r in all_rooms if r.gender in ('女', '不限')]

        # ---- Phase 1: 选班后估算 ----
        male_max_rooms = sum(max(1, math.ceil(c['count'] / 6)) for c in male_classes if c['count'] > 0)
        female_max_rooms = sum(max(1, math.ceil(c['count'] / 6)) for c in female_classes if c['count'] > 0)
        male_total_students = sum(c['count'] for c in male_classes)
        female_total_students = sum(c['count'] for c in female_classes)
        logs.append(f"[Phase1] 男生: {male_total_students}人, 最多需{male_max_rooms}间(6人/间)")
        logs.append(f"[Phase1] 女生: {female_total_students}人, 最多需{female_max_rooms}间(6人/间)")

        # 床位校验
        male_beds = sum(r.capacity for r in male_rooms_raw)
        female_beds = sum(r.capacity for r in female_rooms_raw)
        if male_total_students > 0 and male_beds < male_total_students:
            logs.append(f"[ERROR] 男生床位不足！需{male_total_students}个，仅{male_beds}个")
        if female_total_students > 0 and female_beds < female_total_students:
            logs.append(f"[ERROR] 女生床位不足！需{female_total_students}个，仅{female_beds}个")

        no_rooms_left = has_assigned and mode == 'keep_existing' and \
                        len(male_rooms_raw) == 0 and len(female_rooms_raw) == 0

        # ---- 加载班型信息 ----
        all_classes_list = male_classes + female_classes
        profiles = _load_class_profiles(all_classes_list)

        # ---- 处理各性别（多年级支持：先入校年级分在低楼层）----
        needs_combine = False
        combine_suggestions = []
        overall_scenario = 0

        for gender_classes, gender_rooms, gender_label in [
            (male_classes, male_rooms_raw, '男'),
            (female_classes, female_rooms_raw, '女'),
        ]:
            if not gender_classes:
                continue
            if not gender_rooms:
                logs.append(f"[WARN] {gender_label}生无可用房间")
                total_stats['unassigned_students'] += sum(c['count'] for c in gender_classes)
                continue

            gender_combine_groups = _build_combine_groups(gender_classes, profiles)

            result = _allocate_gender_v4(
                gender_classes, gender_rooms, gender_label,
                gender_combine_groups, combine_confirmations, force_full_8, logs
            )

            all_assignments.extend(result['assignments'])
            total_stats['total_students'] += result['stats']['total_students']
            total_stats['total_rooms_assigned'] += result['stats']['total_rooms_assigned']
            total_stats['combined_rooms'] += result['stats']['combined_rooms']
            total_stats['unassigned_students'] += result['stats']['unassigned_students']

            if result.get('scenario', 0) > overall_scenario:
                overall_scenario = result['scenario']
            if result.get('needs_combine'):
                needs_combine = True
                combine_suggestions.extend(result.get('combine_suggestions', []))

        # ---- Phase 1 估算信息 ----
        phase1_info = {
            'male_max_rooms': male_max_rooms,
            'female_max_rooms': female_max_rooms,
            'male_total': male_total_students,
            'female_total': female_total_students,
        }

        # ---- 写DB ----
        if not dry_run and all_assignments:
            _write_to_db(all_assignments, all_rooms, logs)
            db.session.commit()

        response = {
            'success': True,
            'logs': logs,
            'assignments': _format_assignments(all_assignments),
            'stats': total_stats,
            'scenario': overall_scenario,
            'phase1': phase1_info,
            'has_assigned': has_assigned,
            'assigned_room_count': len(assigned_info),
            'no_rooms_left': no_rooms_left,
            'needs_combine': needs_combine,
            'combine_suggestions': combine_suggestions,
        }
        return response

    except Exception as e:
        db.session.rollback()
        logs.append(f"[ERROR] 分配异常: {str(e)}")
        return {'success': False, 'error': str(e), 'logs': logs}


# ============================================================================
# V4核心：单性别7种情形逐级尝试
# ============================================================================

def _allocate_gender_v4(classes, rooms, gender, combine_groups,
                         combine_confirmations, force_full_8, logs):
    """
    对单一性别执行 V4 算法：逐级尝试7种情形

    返回: {sufficient, assignments, stats, scenario, needs_combine, combine_suggestions, needs_level_upgrade}
    """
    total_students = sum(c['count'] for c in classes)
    if total_students == 0:
        return {
            'sufficient': True, 'assignments': [], 'scenario': 0,
            'stats': {'total_students': 0, 'total_rooms_assigned': 0, 'combined_rooms': 0, 'unassigned_students': 0},
            'needs_combine': False, 'combine_suggestions': [], 'needs_level_upgrade': False,
        }

    # 分类房间
    rooms_6 = [r for r in rooms if r.capacity == 6]
    rooms_8 = [r for r in rooms if r.capacity == 8]
    all_rooms_sorted = sorted(rooms, key=lambda r: (r.building or '', r.floor or 0, r.room_number or ''))

    logs.append(f"[INFO] ===== {gender}生: {total_students}人, "
                f"{len(rooms_6)}间6人间 + {len(rooms_8)}间8人间 = {len(rooms)}间 =====")

    # 7种情形定义: (limit_6, limit_8, allow_combine, combine_limit)
    scenarios = [
        # (name, limit_6, limit_8, allow_combine, combine_limit)
        ("情形1: ≤6人/间，不合班",      6, 6, False, 0),
        ("情形2: ≤6人/间，合班≤6",      6, 6, True,  6),
        ("情形3: 6人=6, 8人=7，不合班",  6, 7, False, 0),
        ("情形4: 6人=6, 8人=7，合班≤6",  6, 7, True,  6),
        ("情形5: 6人=6, 8人=8，不合班",  6, 8, False, 0),
        ("情形6: 6人=6, 8人=8，合班≤6",  6, 8, True,  6),
        ("情形7: 6人=6, 8人=8，合班≤8",  6, 8, True,  8),
    ]

    best_partial = None  # 跟踪最佳(未分配最少)的部分结果

    # [安全] 防御性清理：确保本次所选房间的合班标记在进入场景循环前为None
    # 注意：all_rooms_sorted 仅包含用户已选的房间（来自 selected_room_ids）
    for r in all_rooms_sorted:
        r.combined_class = None

    for idx, (name, limit_6, limit_8, allow_combine, combine_limit) in enumerate(scenarios, 1):
        # 快速容量检查
        effective_capacity = len(rooms_6) * min(6, limit_6) + len(rooms_8) * min(8, limit_8)
        if effective_capacity < total_students:
            logs.append(f"[INFO] {name} → 跳过（有效容量{effective_capacity} < 需{total_students}）")
            continue

        logs.append(f"[INFO] 尝试 {name}...")

        if allow_combine:
            result = _try_allocate_with_combine(
                classes, rooms_6, rooms_8, all_rooms_sorted,
                limit_6, limit_8, combine_limit,
                combine_groups, combine_confirmations, gender, logs
            )
        else:
            result = _try_allocate_no_combine(
                classes, rooms_6, rooms_8, all_rooms_sorted,
                limit_6, limit_8, gender, logs
            )

        if result['sufficient']:
            result['scenario'] = idx
            logs.append(f"[OK] {gender}生 {name} 成功！场景{idx}")
            return result
        else:
            logs.append(f"[INFO] {name} → 不足（{result['stats']['unassigned_students']}人无法分配）")
            # 跟踪最佳部分结果
            if best_partial is None or result['stats']['unassigned_students'] < best_partial['stats']['unassigned_students']:
                best_partial = result
                best_partial['scenario'] = idx

    # 所有情形都失败 → 返回最佳部分结果
    logs.append(f"[WARN] {gender}生所有情形均失败！返回最佳部分结果")
    if best_partial:
        best_partial['sufficient'] = False
        return best_partial
    return {
        'sufficient': False,
        'assignments': [],
        'scenario': 0,
        'stats': {
            'total_students': total_students,
            'total_rooms_assigned': 0,
            'combined_rooms': 0,
            'unassigned_students': total_students,
        },
        'needs_combine': False,
        'combine_suggestions': [],
    }


# ============================================================================
# 不合班分配（情形1, 3, 5）
# ============================================================================

def _try_allocate_no_combine(classes, rooms_6, rooms_8, all_rooms_sorted,
                               limit_6, limit_8, gender, logs):
    """
    不合班分配：大班优先分配8人间→均衡分配人数→同楼层连续排房

    步骤:
      1. 计算每个班级需要的房间数（大班优先分配8人间）
      2. 按楼栋/楼层/房号排序分配连续房间（大班优先）
      3. 班内均衡分配人数（8人间可多分配）
    """
    assignments = []
    total_students = sum(c['count'] for c in classes)
    total_rooms = len(rooms_6) + len(rooms_8)
    stats = {
        'total_students': total_students,
        'total_rooms_assigned': 0,
        'combined_rooms': 0,
        'unassigned_students': 0,
    }

    active_classes = [(cls, cls['count']) for cls in classes if cls['count'] > 0]
    if not active_classes:
        return {'sufficient': True, 'assignments': [], 'stats': stats,
                'needs_combine': False, 'combine_suggestions': []}

    logs.append(f"[DEBUG] {gender}生独立分配: {len(active_classes)}个班级, "
                f"{len(rooms_6)}间6人间, {len(rooms_8)}间8人间")

    # ---- 步骤1: 计算每班房间数（大班优先分配8人间）----
    total_max_need = sum(max(1, math.ceil(count / 6)) for _, count in active_classes)
    if limit_6 == 6 and limit_8 == 6 and total_max_need > total_rooms:
        return {
            'sufficient': False,
            'assignments': [],
            'stats': {'total_students': total_students, 'total_rooms_assigned': 0,
                       'combined_rooms': 0, 'unassigned_students': total_students},
            'needs_combine': False, 'combine_suggestions': [],
        }

    class_room_counts = _calc_class_room_counts(
        active_classes, total_students, total_rooms, limit_6, limit_8,
        len(rooms_6), len(rooms_8)
    )

    if sum(class_room_counts.values()) > total_rooms:
        return {
            'sufficient': False,
            'assignments': [],
            'stats': {'total_students': total_students, 'total_rooms_assigned': 0,
                       'combined_rooms': 0, 'unassigned_students': total_students},
            'needs_combine': False, 'combine_suggestions': [],
        }

    # ---- 步骤2: 按楼层连续分配具体房间（大班优先选8人间）----
    free_rooms = list(all_rooms_sorted)

    class_order = sorted(active_classes, key=lambda x: x[1], reverse=True)

    class_assignments = {}
    room_type_preference = {}

    for cls, count in class_order:
        key = _class_key(cls)
        need = class_room_counts.get(key, 0)
        if need == 0:
            continue

        logs.append(f"[DEBUG] 班级 {cls['class_name']}: {count}人, 需要{need}间")

        # 大班优先分配8人间
        if count >= limit_8 and len(rooms_8) > 0:
            room_type_preference[key] = '8'
            assigned = _pick_consecutive_rooms_by_type(free_rooms, need, prefer_8=True)
        else:
            room_type_preference[key] = '6'
            assigned = _pick_consecutive_rooms(free_rooms, need)

        class_assignments[key] = assigned

        for r in assigned:
            if r in free_rooms:
                free_rooms.remove(r)

    # ---- 步骤3: 班内均衡分配人数 ----
    for cls, count in class_order:
        key = _class_key(cls)
        taken = class_assignments.get(key, [])
        if not taken:
            stats['unassigned_students'] += count
            continue

        limits = [_room_effective_cap(r, limit_6, limit_8) for r in taken]
        total_limit = sum(limits)
        actual = min(count, total_limit)

        base = actual // len(taken)
        rem = actual % len(taken)

        for i, room in enumerate(taken):
            expected = base + (1 if i < rem else 0)
            if expected > limits[i]:
                expected = limits[i]
            assignments.append({
                'room': room,
                'grade': cls['grade'],
                'class_name': cls['class_name'],
                'expected_count': expected,
                'gender': gender,
                'is_combined': False,
                'combined_info': '',
            })

        stats['total_rooms_assigned'] += len(taken)

        remaining = count - actual
        if remaining > 0:
            stats['unassigned_students'] += remaining
            logs.append(f"[DEBUG] 班级 {cls['class_name']}: {remaining}人未分配")

    # ---- 容量平衡：如有未分配，尝试交换房间优化容量 ----
    if stats['unassigned_students'] > 0:
        logs.append(f"[DEBUG] 尝试容量平衡，{stats['unassigned_students']}人未分配")
        for cls, count in class_order:
            key = _class_key(cls)
            if key not in class_assignments:
                continue
            taken = class_assignments[key]
            limits = [_room_effective_cap(r, limit_6, limit_8) for r in taken]
            total_cap = sum(limits)
            if total_cap >= count:
                continue
            deficit = count - total_cap
            for other_cls, other_count in class_order:
                if deficit <= 0:
                    break
                other_key = _class_key(other_cls)
                if other_key == key:
                    continue
                other_taken = class_assignments.get(other_key, [])
                other_limits = [_room_effective_cap(r, limit_6, limit_8) for r in other_taken]
                other_cap = sum(other_limits)
                other_surplus = other_cap - other_count
                if other_surplus <= 0:
                    continue
                for i, (r_self, lim_self) in enumerate(zip(taken, limits)):
                    if deficit <= 0:
                        break
                    for j, (r_other, lim_other) in enumerate(zip(other_taken, other_limits)):
                        if deficit <= 0:
                            break
                        if lim_other > lim_self and other_surplus - (lim_other - lim_self) >= 0:
                            taken[i], other_taken[j] = r_other, r_self
                            limits[i], other_limits[j] = lim_other, lim_self
                            total_cap = sum(limits)
                            deficit = count - total_cap
                            other_surplus -= (lim_other - lim_self)
                            break
                class_assignments[key] = taken
                class_assignments[other_key] = other_taken

        assignments.clear()
        stats['total_rooms_assigned'] = 0
        stats['unassigned_students'] = 0
        for cls, count in class_order:
            key = _class_key(cls)
            taken = class_assignments.get(key, [])
            if not taken:
                stats['unassigned_students'] += count
                continue
            limits = [_room_effective_cap(r, limit_6, limit_8) for r in taken]
            total_limit = sum(limits)
            actual = min(count, total_limit)
            base = actual // len(taken)
            rem = actual % len(taken)
            for i, room in enumerate(taken):
                expected = base + (1 if i < rem else 0)
                if expected > limits[i]:
                    expected = limits[i]
                assignments.append({
                    'room': room,
                    'grade': cls['grade'],
                    'class_name': cls['class_name'],
                    'expected_count': expected,
                    'gender': gender,
                    'is_combined': False,
                    'combined_info': '',
                })
            stats['total_rooms_assigned'] += len(taken)
            remaining = count - actual
            if remaining > 0:
                stats['unassigned_students'] += remaining

    sufficient = stats['unassigned_students'] == 0
    return {
        'sufficient': sufficient,
        'assignments': assignments,
        'stats': stats,
        'needs_combine': False,
        'combine_suggestions': [],
    }


def _pick_consecutive_rooms_by_type(free_rooms, need, prefer_8=True):
    """
    优先选择8人间的连续房间分配
    """
    if prefer_8:
        rooms_8_filtered = [r for r in free_rooms if r.capacity == 8]
        if len(rooms_8_filtered) >= need:
            return _pick_consecutive_rooms(rooms_8_filtered, need)
        assigned = _pick_consecutive_rooms(rooms_8_filtered, len(rooms_8_filtered))
        remaining = need - len(assigned)
        if remaining > 0:
            remaining_rooms = [r for r in free_rooms if r not in assigned]
            assigned.extend(_pick_consecutive_rooms(remaining_rooms, remaining))
        return assigned
    return _pick_consecutive_rooms(free_rooms, need)


# ============================================================================
# 含合班分配（情形2, 4, 6, 7）
# ============================================================================

def _try_allocate_with_combine(classes, rooms_6, rooms_8, all_rooms_sorted,
                                 limit_6, limit_8, combine_limit,
                                 combine_groups, combine_confirmations,
                                 gender, logs):
    """
    含合班分配：
      1. 先按不合班方式分配（使用ceil(total/limit)计算最小房间数）
      2. 收集各班级剩余学生
      3. 在同合班组内配对合班
      4. 剩余房间分配给合班
      5. 尽量最后一公里吸收
    """
    assignments = []
    total_students = sum(c['count'] for c in classes)
    total_rooms = len(rooms_6) + len(rooms_8)
    stats = {
        'total_students': total_students,
        'total_rooms_assigned': 0,
        'combined_rooms': 0,
        'unassigned_students': 0,
    }
    combine_suggestions = []

    active_classes = [(cls, cls['count']) for cls in classes if cls['count'] > 0]
    if not active_classes:
        return {'sufficient': True, 'assignments': [], 'stats': stats,
                'needs_combine': False, 'combine_suggestions': []}

    # ---- 步骤1: 按最小值(ceil(total/limit))分配房间数 ----
    # 使用高效上限: 8人间用limit_8, 6人间用limit_6
    avg_limit = (len(rooms_6) * limit_6 + len(rooms_8) * limit_8) / max(total_rooms, 1)
    min_total_rooms = math.ceil(total_students / avg_limit) if avg_limit > 0 else total_rooms

    # 实际使用的房间数 = min(总房间数, 按某个合理比例)
    # 为了给合班留房间，使用 min_total_rooms 作为基础
    rooms_to_use = min(total_rooms, max(min_total_rooms, total_rooms - 2))  # 留2间给合班

    class_room_counts = _calc_class_room_counts_compact(
        active_classes, total_students, rooms_to_use, limit_6, limit_8,
        len(rooms_6), len(rooms_8)
    )

    # ---- 步骤2: 分配具体房间 + 收集剩余 ----
    free_rooms = list(all_rooms_sorted)
    class_order = sorted(active_classes, key=lambda x: x[1], reverse=True)

    class_assignments = {}
    unallocated = []  # [(cls_info, remaining_count), ...]

    for cls, count in class_order:
        key = _class_key(cls)
        need = class_room_counts.get(key, 0)
        if need == 0:
            unallocated.append((cls, count))
            continue

        assigned = _pick_consecutive_rooms(free_rooms, need)
        class_assignments[key] = assigned
        for r in assigned:
            if r in free_rooms:
                free_rooms.remove(r)

    # ---- 步骤3: 均衡分配 + 收集剩余 ----
    for cls, count in class_order:
        key = _class_key(cls)
        taken = class_assignments.get(key, [])
        if not taken:
            continue

        limits = [_room_effective_cap(r, limit_6, limit_8) for r in taken]
        total_limit = sum(limits)
        actual = min(count, total_limit)

        base = actual // len(taken)
        rem = actual % len(taken)

        for i, room in enumerate(taken):
            expected = base + (1 if i < rem else 0)
            if expected > limits[i]:
                expected = limits[i]
            assignments.append({
                'room': room,
                'grade': cls['grade'],
                'class_name': cls['class_name'],
                'expected_count': expected,
                'gender': gender,
                'is_combined': False,
                'combined_info': '',
            })

        stats['total_rooms_assigned'] += len(taken)

        remaining = count - actual
        if remaining > 0:
            unallocated.append((cls, remaining))

    # ---- 步骤4: 合班配对 ----
    if unallocated and free_rooms:
        pairs, suggestions, used_keys = _find_combine_pairs(
            unallocated, combine_groups, free_rooms,
            combine_confirmations, gender, combine_limit,
            class_assignments=class_assignments
        )

        for pair in pairs:
            room = pair['room']
            c1 = pair['class1']
            c2 = pair.get('class2')
            combined_name = pair.get('combined_name') or \
                           f"{c1['class_name']}+{c2['class_name']}" if c2 else c1['class_name']
            limit = _room_effective_cap(room, limit_6, limit_8)
            expected = min(pair['combined_count'], min(limit, combine_limit))

            assignments.append({
                'room': room,
                'grade': c1['grade'],
                'class_name': combined_name,
                'expected_count': expected,
                'gender': gender,
                'is_combined': True,
                'combined_info': combined_name,
            })
            stats['total_rooms_assigned'] += 1
            stats['combined_rooms'] += 1
            if room in free_rooms:
                free_rooms.remove(room)

        # 重新计算未分配
        stats['unassigned_students'] = 0
        for cls_info, remaining in unallocated:
            key = _class_key(cls_info)
            if key not in used_keys:
                stats['unassigned_students'] += remaining

        # ---- 最后一公里吸收 ----
        if stats['unassigned_students'] > 0 and not combine_confirmations:
            _last_mile_absorb(assignments, unallocated, used_keys,
                              combine_groups, limit_6, limit_8, stats)

        if suggestions and not combine_confirmations:
            combine_suggestions = suggestions
            return {
                'sufficient': False,
                'assignments': assignments,
                'stats': stats,
                'needs_combine': True,
                'combine_suggestions': suggestions,
            }
    else:
        stats['unassigned_students'] = sum(r for _, r in unallocated)

    sufficient = stats['unassigned_students'] == 0
    return {
        'sufficient': sufficient,
        'assignments': assignments,
        'stats': stats,
        'needs_combine': False,
        'combine_suggestions': combine_suggestions,
    }


# ============================================================================
# 房间数计算
# ============================================================================

def _calc_class_room_counts(active_classes, total_students, total_rooms,
                              limit_6, limit_8, count_6, count_8):
    """
    按比例计算每班的房间数（不合班场景）

    返回: {class_key: room_count}
    """
    counts = {}
    allocated = 0
    total_max_need = 0
    per_class_info = []

    for cls, count in active_classes:
        # 使用当前情形的最大有效容量估算（而非固定6）
        max_limit = max(limit_6, limit_8)
        max_need = max(1, math.ceil(count / max_limit))
        ideal = max(1, round(count / total_students * total_rooms)) if total_students > 0 else 0
        per_class_info.append({
            'cls': cls,
            'count': count,
            'max_need': max_need,
            'ideal': ideal,
        })
        total_max_need += max_need

    # 确定最终房间数
    if total_max_need <= total_rooms:
        # 房间充足：每班给max_need
        for info in per_class_info:
            counts[_class_key(info['cls'])] = info['max_need']
    else:
        # 房间不够：按ideal分配
        for info in per_class_info:
            counts[_class_key(info['cls'])] = info['ideal']
        allocated = sum(counts.values())

        # 调整到 total_rooms
        if allocated > total_rooms:
            excess = allocated - total_rooms
            sorted_keys = sorted(counts.keys(), key=lambda k: counts[k], reverse=True)
            for k in sorted_keys:
                if excess <= 0:
                    break
                if counts[k] > 1:
                    counts[k] -= 1
                    excess -= 1
        elif allocated < total_rooms:
            deficit = total_rooms - allocated
            sorted_info = sorted(per_class_info, key=lambda x: x['count'], reverse=True)
            for info in sorted_info:
                if deficit <= 0:
                    break
                k = _class_key(info['cls'])
                if counts.get(k, 0) < info['max_need']:
                    counts[k] = counts.get(k, 0) + 1
                    deficit -= 1

        # ---- 容量保证：确保每班房间数×平均容量 ≥ 人数 ----
        if total_rooms > 0:
            avg_eff_cap = (count_6 * limit_6 + count_8 * limit_8) / total_rooms
        else:
            avg_eff_cap = 6
        for info in sorted(per_class_info, key=lambda x: x['count'], reverse=False):
            k = _class_key(info['cls'])
            current = counts.get(k, 0)
            if current * avg_eff_cap >= info['count']:
                continue
            need = max(1, math.ceil(info['count'] / avg_eff_cap))
            deficit = need - current
            for other in sorted(per_class_info, key=lambda x: x['count'], reverse=True):
                if deficit <= 0:
                    break
                ok = _class_key(other['cls'])
                if ok == k:
                    continue
                other_rooms = counts.get(ok, 0)
                if other_rooms > 1 and (other_rooms - 1) * avg_eff_cap >= other['count']:
                    counts[ok] -= 1
                    counts[k] = counts.get(k, 0) + 1
                    deficit -= 1

    return counts


def _calc_class_room_counts_compact(active_classes, total_students, rooms_to_use,
                                      limit_6, limit_8, count_6, count_8):
    """
    紧凑模式房间数计算（含合班场景，留空间给合班）

    使用 ceil(count / max_limit) 作为每班最多房间数（max_limit = max(limit_6, limit_8)）
    确保为合班预留至少 2 间房间
    """
    max_limit = max(limit_6, limit_8)
    # 预留合班房间：rooms_to_use 已预留2间，这里再轻量预留
    reserve = max(1, min(rooms_to_use // 8, 3))
    allocatable = rooms_to_use - reserve

    counts = {}
    total_max_need = 0
    per_class_info = []

    for cls, count in active_classes:
        max_need = max(1, math.ceil(count / max_limit))  # 用max_limit而非固定6
        ideal = max(1, round(count / total_students * allocatable)) if total_students > 0 else 0
        per_class_info.append({
            'cls': cls,
            'count': count,
            'max_need': max_need,
            'ideal': min(ideal, max_need),
        })
        total_max_need += max_need

    for info in per_class_info:
        counts[_class_key(info['cls'])] = info['ideal']

    allocated = sum(counts.values())

    # 调整到 allocatable
    if allocated > allocatable:
        excess = allocated - allocatable
        sorted_keys = sorted(counts.keys(), key=lambda k: counts[k], reverse=True)
        for k in sorted_keys:
            if excess <= 0:
                break
            if counts[k] > 1:
                counts[k] -= 1
                excess -= 1
    elif allocated < allocatable:
        deficit = allocatable - allocated
        sorted_info = sorted(per_class_info, key=lambda x: x['count'], reverse=True)
        for info in sorted_info:
            if deficit <= 0:
                break
            k = _class_key(info['cls'])
            if counts.get(k, 0) < info['max_need']:
                counts[k] = counts.get(k, 0) + 1
                deficit -= 1

    # ---- 容量保证（紧凑模式）----
    if allocatable > 0:
        avg_eff_cap = (count_6 * limit_6 + count_8 * limit_8) / max(allocatable, 1)
    else:
        avg_eff_cap = 6
    for info in sorted(per_class_info, key=lambda x: x['count'], reverse=False):
        k = _class_key(info['cls'])
        current = counts.get(k, 0)
        if current * avg_eff_cap >= info['count']:
            continue
        need = max(1, math.ceil(info['count'] / avg_eff_cap))
        deficit = need - current
        for other in sorted(per_class_info, key=lambda x: x['count'], reverse=True):
            if deficit <= 0:
                break
            ok = _class_key(other['cls'])
            if ok == k:
                continue
            other_rooms = counts.get(ok, 0)
            if other_rooms > 1 and (other_rooms - 1) * avg_eff_cap >= other['count']:
                counts[ok] -= 1
                counts[k] = counts.get(k, 0) + 1
                deficit -= 1

    return counts


# ============================================================================
# 楼层连续房间分配
# ============================================================================

def _pick_consecutive_rooms(free_rooms, need):
    """
    从空闲房间中选择 need 间，尽量同楼层连续。

    策略:
      1. 按(楼栋, 楼层)分组
      2. 在组内找连续的 need 间
      3. 如果找不到，选最长的连续段
      4. 如果都不够，按房号排序贪心取
    """
    if need <= 0 or not free_rooms:
        return []

    if need >= len(free_rooms):
        return list(free_rooms)

    # 按(楼栋, 楼层, 房号)分组排序
    groups = defaultdict(list)
    for r in free_rooms:
        floor_key = (r.building or '', r.floor or 0)
        groups[floor_key].append(r)

    for key in groups:
        groups[key].sort(key=lambda r: _room_number_int(r.room_number))

    # 尝试在每个楼层组内找连续段
    best = None
    best_len = 0

    for floor_key, room_list in groups.items():
        if len(room_list) < need:
            continue
        # 滑窗找连续段
        for i in range(len(room_list) - need + 1):
            segment = room_list[i:i + need]
            if _is_consecutive_numbers(segment):
                return segment
            if len(segment) > best_len:
                best = segment
                best_len = len(segment)

    # 找不到完全连续的 → 在同楼层内尽量多取
    if best and len(best) == need:
        return best

    # 跨楼层时选最紧凑的房间集（最小化楼层跨度）
    all_sorted = sorted(free_rooms, key=lambda r: (
        r.building or '', r.floor or 0, _room_number_int(r.room_number)))

    if need >= len(all_sorted):
        return list(all_sorted)

    # 滑窗找房号跨度最小的 need 间（同楼层 > 相邻楼层 > 远距离）
    best_span = float('inf')
    best_start = 0
    for i in range(len(all_sorted) - need + 1):
        first = all_sorted[i]
        last = all_sorted[i + need - 1]
        # 跨度 = 楼栋变化*10000 + 楼层变化*1000 + 房号差
        b_diff = (1 if (last.building or '') != (first.building or '') else 0)
        f_diff = abs((last.floor or 0) - (first.floor or 0))
        span = b_diff * 10000 + f_diff * 1000
        if span < best_span:
            best_span = span
            best_start = i
            if span == 0:  # 同楼栋同楼层，最优
                break
    return all_sorted[best_start:best_start + need]


def _room_number_int(room_number):
    """房间号转整数，用于排序"""
    try:
        return int(room_number)
    except (ValueError, TypeError):
        return 0


def _is_consecutive_numbers(rooms):
    """检查房间号是否连续（整数意义上的连续）"""
    if len(rooms) <= 1:
        return True
    nums = []
    for r in rooms:
        n = _room_number_int(r.room_number)
        if n == 0:
            return False
        nums.append(n)
    nums.sort()
    for i in range(1, len(nums)):
        if nums[i] != nums[i-1] + 1:
            return False
    return True


def _room_effective_cap(room, limit_6, limit_8):
    """计算房间在指定情形下的有效容量"""
    if room.capacity == 6:
        return min(6, limit_6)
    else:
        return min(room.capacity, limit_8)


# ============================================================================
# 合班配对（支持多班合并，<=6人上限）
# ============================================================================

def _find_combine_pairs(unallocated_by_class, combine_groups, room_pool,
                         combine_confirmations, gender, combine_limit=6,
                         class_assignments=None):
    """
    在同组内寻找最优合班配对

    unallocated_by_class: [(cls_info, remaining), ...]
    combine_groups: {group_key: [class_key, ...]}
    room_pool: 剩余可用房间列表
    combine_limit: 合班人数上限 (6 or 8)
    class_assignments: 已有班级房间分配，用于选择合班房间中间位置

    支持多班合并: 同一合班组内多个班级剩余之和 ≤ combine_limit 可合并到一间
    返回: (applied_pairs, suggestions, used_class_keys)
    """
    used_keys = set()
    applied_pairs = []
    suggestions = []

    # 先处理用户确认的合班
    confirmed = combine_confirmations or []
    for conf in confirmed:
        c1 = conf.get('class1', {})
        c2 = conf.get('class2', {})
        room_id = conf.get('room_id')
        room = next((r for r in room_pool if r.id == room_id), None)

        if room and c1 and c2:
            key1 = _class_key(c1)
            key2 = _class_key(c2)
            rem1 = next((r for cls, r in unallocated_by_class if _class_key(cls) == key1), 0)
            rem2 = next((r for cls, r in unallocated_by_class if _class_key(cls) == key2), 0)

            applied_pairs.append({
                'room': room,
                'class1': c1, 'class2': c2,
                'combined_count': rem1 + rem2,
            })
            used_keys.add(key1)
            used_keys.add(key2)
            if room in room_pool:
                room_pool.remove(room)

    if confirmed:
        return applied_pairs, [], used_keys

    # 按合班分组归类剩余
    groups_remaining = defaultdict(list)
    for cls_info, remaining in unallocated_by_class:
        key = _class_key(cls_info)
        found = False
        for gkey, keys in combine_groups.items():
            if key in keys:
                groups_remaining[gkey].append((cls_info, remaining))
                found = True
                break
        if not found:
            groups_remaining['__default__'].append((cls_info, remaining))

    # 在每个组内：贪心合并
    for gkey, group_items in groups_remaining.items():
        if not group_items:
            continue
        group_items.sort(key=lambda x: x[1], reverse=True)
        group_used = set()

        i = 0
        while i < len(group_items):
            cls1, rem1 = group_items[i]
            key1 = _class_key(cls1)
            if key1 in group_used:
                i += 1
                continue

            best_combo = [i]
            best_sum = rem1

            # 贪心：尝试加入更多班级直到总和接近 combine_limit
            for j in range(i + 1, len(group_items)):
                cls_j, rem_j = group_items[j]
                key_j = _class_key(cls_j)
                if key_j in group_used:
                    continue
                if best_sum + rem_j <= combine_limit:
                    best_combo.append(j)
                    best_sum += rem_j
                    if best_sum == combine_limit:
                        break

            if len(best_combo) >= 2:
                combo_classes = [group_items[idx][0] for idx in best_combo]
                key1 = _class_key(combo_classes[0])
                key2 = _class_key(combo_classes[1]) if len(combo_classes) >= 2 else None
                
                room = _pick_combine_room(room_pool, class_assignments, key1, key2)
                if room:
                    room_pool.remove(room)
                    combined_name = '+'.join(c['class_name'] for c in combo_classes)
                    for idx in best_combo:
                        group_used.add(_class_key(group_items[idx][0]))
                        used_keys.add(_class_key(group_items[idx][0]))

                    applied_pairs.append({
                        'room': room,
                        'class1': combo_classes[0],
                        'class2': combo_classes[1] if len(combo_classes) >= 2 else combo_classes[0],
                        'combined_count': best_sum,
                        'combined_name': combined_name,
                    })
                else:
                    combo_classes = [group_items[idx][0] for idx in best_combo]
                    suggestions.append({
                        'class1': {
                            'grade': combo_classes[0]['grade'],
                            'class_name': combo_classes[0]['class_name'],
                            'count': group_items[best_combo[0]][1],
                            'gender': gender,
                        },
                        'class2': {
                            'grade': combo_classes[1]['grade'] if len(combo_classes) >= 2 else '',
                            'class_name': combo_classes[1]['class_name'] if len(combo_classes) >= 2 else '',
                            'count': group_items[best_combo[1]][1] if len(combo_classes) >= 2 else 0,
                            'gender': gender,
                        },
                        'combined_count': best_sum,
                        'candidate_rooms': [],
                    })
                i += 1
            else:
                i += 1

    return applied_pairs, suggestions, used_keys


def _pick_combine_room(room_pool, class_assignments=None, class1_key=None, class2_key=None):
    """
    从可用房间中选一个合班房间
    优先选择两个班级房间之间的中间位置
    """
    if not room_pool:
        return None

    if class_assignments and class1_key and class2_key:
        c1_rooms = class_assignments.get(class1_key, [])
        c2_rooms = class_assignments.get(class2_key, [])
        
        if c1_rooms and c2_rooms:
            c1_last = sorted(c1_rooms, key=lambda r: (r.building or '', r.floor or 0, r.room_number or ''))[-1]
            c2_first = sorted(c2_rooms, key=lambda r: (r.building or '', r.floor or 0, r.room_number or ''))[0]
            
            c1_key = (c1_last.building or '', c1_last.floor or 0)
            c2_key = (c2_first.building or '', c2_first.floor or 0)
            
            if c1_key == c2_key:
                c1_room_num = _room_number_int(c1_last.room_number)
                c2_room_num = _room_number_int(c2_first.room_number)
                
                for room in room_pool:
                    if (room.building or '', room.floor or 0) == c1_key:
                        room_num = _room_number_int(room.room_number)
                        if c1_room_num < room_num < c2_room_num:
                            return room

    mid = len(room_pool) // 2
    return room_pool[mid]


def _last_mile_absorb(assignments, unallocated, used_keys,
                       combine_groups, limit_6, limit_8, stats):
    """
    最后一公里：尝试把未分配学生塞进同合班组已有空位的房间
    """
    for cls_info, remaining in list(unallocated):
        key = _class_key(cls_info)
        if key in used_keys:
            continue
        if remaining <= 0:
            continue

        # 找该班级所在的合班组
        cls_gkey = None
        for gkey, keys in combine_groups.items():
            if key in keys:
                cls_gkey = gkey
                break
        if not cls_gkey:
            continue

        group_keys = set(combine_groups.get(cls_gkey, []))

        for a in assignments:
            if remaining <= 0:
                break
            if a.get('is_combined'):
                continue
            a_key = f"{a['grade']}:{a['class_name']}"
            if a_key not in group_keys:
                continue

            limit = _room_effective_cap(a['room'], limit_6, limit_8)
            spare = limit - a['expected_count']
            if spare > 0:
                take = min(spare, remaining)
                a['expected_count'] += take
                remaining -= take

        if remaining <= 0:
            used_keys.add(key)

    # 重新计算未分配
    unassigned = 0
    for cls_info, remaining in unallocated:
        key2 = _class_key(cls_info)
        if key2 not in used_keys:
            unassigned += remaining
    stats['unassigned_students'] = unassigned


# ============================================================================
# 辅助函数（与V3共用的部分）
# ============================================================================

def _group_by_gender(selected_keys, logs):
    """将 selected_keys 按性别分组，查询实际住校人数"""
    from app.utils.helpers import get_dict_values

    try:
        valid_grades = set(get_dict_values('grade'))
        valid_classes = set(get_dict_values('class'))
    except Exception:
        valid_grades, valid_classes = set(), set()

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

        count = Student.query.filter_by(
            grade=grade, class_name=class_name,
            gender=gender, boarding_type='住校'
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
    """加载房间列表，按楼栋+楼层+房号排序"""
    if not room_ids:
        return []
    rooms = Room.query.filter(Room.id.in_(room_ids), Room.is_active == True).all()
    rooms.sort(key=lambda r: (r.building or '', r.floor or 0, r.room_number or ''))
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


def _build_combine_groups(classes, profiles):
    """
    构建合班分组：同组才能合班
    group_key = grade|class_type
    合班限制：同性别、同年级、同班型（强基/卓越分开）
    """
    groups = defaultdict(list)
    for cls in classes:
        key = _class_key(cls)
        profile = profiles.get(f"{cls['grade']}:{cls['class_name']}")
        if profile:
            ct = profile.class_type or 'default'
            gkey = f"{cls['grade']}|{ct}"
        else:
            gkey = f"{cls['grade']}|default"
        groups[gkey].append(key)
    return dict(groups)


def _class_key(cls):
    return f"{cls['grade']}:{cls['class_name']}"


def _write_to_db(assignments, all_rooms, logs):
    """将分配结果写入 Room 表，合班标记统一为'合班'（与手动设置一致）"""
    for a in assignments:
        room = a['room']
        room.grade = a.get('grade', '') or None
        room.class_name = a.get('class_name', '') or None
        room.gender = a.get('gender', room.gender or '')
        if a.get('is_combined'):
            room.combined_class = '合班'
        else:
            room.combined_class = None
    logs.append(f"[DONE] 已写入 {len(assignments)} 个房间分配")


def _format_assignments(assignments):
    """格式化为前端可消费的字典列表"""
    return [{
        'room_id': a['room'].id,
        'room_number': a['room'].room_number,
        'building': a['room'].building,
        'floor': a['room'].floor,
        'grade': a.get('grade', ''),
        'class_name': a.get('class_name', ''),
        'gender': a.get('gender', ''),
        'expected_count': a.get('expected_count', 0),
        'is_combined': a.get('is_combined', False),
        'combined_info': a.get('combined_info', ''),
    } for a in assignments]


def _sort_grades_by_enrollment(grades):
    """
    按入学时间排序年级：先入校年级在前（数字小的年级先入校）
    如：2024级 → 2025级 → 2026级
    """
    def grade_sort_key(grade):
        try:
            return int(grade.replace('级', ''))
        except:
            return 9999
    return sorted(grades, key=grade_sort_key)


