"""
宿舍自动分配算法 V5 — 均衡分配 + 最少合班

================================================================================
核心规则
================================================================================
R1: 男女生完全独立计算和分配
R2: 同班各房间人数均衡（差异不超过1人）
R3: 任何房间最终人数 >= 6
R4: 合班最少化 — 每班最多1个合班宿舍，每间合班最多2个班
R5: 合班条件 — 同性别 + 同年级 + 同班型(class_type)
R6: 高年级(年份数字小=学长)住低楼层
R7: 小序号班级分配小房号
R8: 6人间=6人固定，8人间=6~8人灵活
R9: 兜底 — 余量无法合班时：重分配/加房/吸收
R10: 保留 keep_existing / clear_all 两种模式

================================================================================
算法流程
================================================================================
[1] 数据加载 → 按性别分组
[2] 对每个性别独立执行:
    [2.1] 年级排序(高年级先→低楼层) + 班级排序(班号升序)
    [2.2] 房间排序(楼栋,楼层,房号)
    [2.3] 计算每班房间需求(均衡数学)
    [2.4] 顺序分配房间
    [2.5] 余量处理(合班配对 → 重分配 → 吸收)
[3] 合并结果，格式化输出
"""
# StuLink v1.7.0 2026-08-02
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import math
import re
import json
from collections import defaultdict
from app.models import Room, Student, ClassProfile, StudentAccommodation, BedAssignment
from app.extensions import db


# ============================================================================
# 防御性工具函数
# ============================================================================

def _ensure_room_beds(room_ids, logs=None):
    """
    确保指定房间的 BedAssignment 记录完整（与 capacity 匹配）
    """
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

    if fixed_count > 0:
        if logs is not None:
            logs.append(f"[FIX] 共修复 {fixed_count} 个床位记录")

    return fixed_count


# ============================================================================
# 统一入口
# ============================================================================

def auto_assign_preview(selected_keys, selected_room_ids, mode='keep_existing',
                        combine_confirmations=None, force_full_8=False, dry_run=True,
                        adjusted_assignments=None):
    """
    预览/执行自动分配 V5

    参数:
        selected_keys: [{grade, class_name, gender}, ...]
        selected_room_ids: [room_id, ...]
        mode: 'keep_existing' | 'clear_all'
        combine_confirmations: 保留参数兼容性（V5不使用）
        force_full_8: 保留参数兼容性
        dry_run: True=仅预览, False=写DB
        adjusted_assignments: 用户手动调整后的分配方案

    返回: {success, logs, assignments, stats, needs_combine, combine_suggestions,
            has_assigned, ...}
    """
    logs = []
    all_assignments = []
    total_stats = {
        'total_students': 0,
        'rooms_needed_6': 0,
        'rooms_needed_8': 0,
        'total_rooms_assigned': 0,
        'combined_rooms': 0,
        'unassigned_students': 0,
    }

    try:
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
            logs.append(f"[INFO] 剩余可用房间: 男生{len(male_rooms_raw)}间, 女生{len(female_rooms_raw)}间")
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

        # ---- 4. 容量校验 ----
        male_total = sum(c['count'] for c in male_classes)
        female_total = sum(c['count'] for c in female_classes)
        male_beds = sum(r.capacity for r in male_rooms_raw)
        female_beds = sum(r.capacity for r in female_rooms_raw)

        total_stats['rooms_needed_6'] = sum(math.ceil(c['count'] / 6) for c in male_classes + female_classes if c['count'] > 0)
        total_stats['rooms_needed_8'] = sum(math.ceil(c['count'] / 8) for c in male_classes + female_classes if c['count'] > 0)

        insufficient_details = []
        if male_total > 0 and male_beds < male_total:
            shortage = male_total - male_beds
            logs.append(f"[ERROR] 男生床位不足！需{male_total}个，仅{male_beds}个，差{shortage}个")
            insufficient_details.append({'gender': '男', 'needed': male_total, 'available': male_beds, 'shortage': shortage})
        if female_total > 0 and female_beds < female_total:
            shortage = female_total - female_beds
            logs.append(f"[ERROR] 女生床位不足！需{female_total}个，仅{female_beds}个，差{shortage}个")
            insufficient_details.append({'gender': '女', 'needed': female_total, 'available': female_beds, 'shortage': shortage})

        if insufficient_details:
            detail_msgs = [f"{d['gender']}生：需{d['needed']}个床位，仅{d['available']}个，差{d['shortage']}个" for d in insufficient_details]
            return {
                'success': False,
                'error': '床位不足，无法分配。\n' + '\n'.join(detail_msgs) + '\n请增选宿舍后重试。',
                'logs': logs,
                'insufficient_details': insufficient_details,
            }

        no_rooms_left = has_assigned and mode == 'keep_existing' and len(male_rooms_raw) == 0 and len(female_rooms_raw) == 0

        # ---- 5. 加载班型信息 ----
        profiles = _load_class_profiles(male_classes + female_classes)

        # ---- 6. 分性别独立分配 ----
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

            result = _allocate_gender_v5(gender_classes, gender_rooms, gender_label, profiles, logs)

            if not result['success']:
                return {
                    'success': False,
                    'error': result.get('error', f'{gender_label}生分配失败'),
                    'logs': logs,
                    'stats': total_stats,
                }

            all_assignments.extend(result['assignments'])
            total_stats['total_students'] += result['stats']['total_students']
            total_stats['total_rooms_assigned'] += result['stats']['total_rooms_assigned']
            total_stats['combined_rooms'] += result['stats']['combined_rooms']
            total_stats['unassigned_students'] += result['stats']['unassigned_students']

            if result.get('needs_combine'):
                needs_combine = True
                combine_suggestions.extend(result.get('combine_suggestions', []))

        # 最终校验
        if total_stats['unassigned_students'] > 0:
            failure_msg = f"有{total_stats['unassigned_students']}人无法分配宿舍，请增选宿舍后重试"
            logs.append(f"[ERROR] {failure_msg}")
            return {'success': False, 'error': failure_msg, 'logs': logs, 'stats': total_stats}

        # ---- 7. 应用用户手动调整 ----
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
                })
            if adjusted:
                all_assignments = adjusted
                logs.append(f"[INFO] 已应用手动调整方案，共 {len(adjusted)} 间房间")

        # ---- 8. 写DB ----
        if not dry_run and all_assignments:
            _write_to_db(all_assignments, all_rooms, logs)
            db.session.commit()

        phase1_info = {
            'male_max_rooms_6': sum(math.ceil(c['count'] / 6) for c in male_classes if c['count'] > 0),
            'female_max_rooms_6': sum(math.ceil(c['count'] / 6) for c in female_classes if c['count'] > 0),
            'male_max_rooms_8': sum(math.ceil(c['count'] / 8) for c in male_classes if c['count'] > 0),
            'female_max_rooms_8': sum(math.ceil(c['count'] / 8) for c in female_classes if c['count'] > 0),
            'male_total': male_total,
            'female_total': female_total,
        }

        return {
            'success': True,
            'logs': logs,
            'assignments': _format_assignments(all_assignments),
            'stats': total_stats,
            'scenario': 0,
            'phase1': phase1_info,
            'has_assigned': has_assigned,
            'assigned_room_count': len(assigned_info),
            'no_rooms_left': no_rooms_left,
            'needs_combine': needs_combine,
            'combine_suggestions': combine_suggestions,
        }

    except Exception as e:
        db.session.rollback()
        logs.append(f"[ERROR] 分配异常: {str(e)}")
        import traceback
        logs.append(f"[TRACE] {traceback.format_exc()}")
        return {'success': False, 'error': str(e), 'logs': logs}


# ============================================================================
# V5 核心：单性别均衡分配
# ============================================================================

def _allocate_gender_v5(classes, rooms, gender, profiles, logs):
    """
    对单一性别执行均衡分配

    参数:
        classes: [{'grade','class_name','count','gender'}, ...]
        rooms: [Room, ...] 已按(楼栋,楼层,房号)排序
        gender: '男' | '女'
        profiles: 班型信息字典
        logs: 日志列表

    返回: {success, assignments, stats, needs_combine, combine_suggestions, error}
    """
    assignments = []
    combine_suggestions = []

    # ---- 排序班级: 高年级(年份小)在前 + 班号升序 ----
    sorted_classes = sorted(classes, key=lambda c: (_extract_grade_year(c['grade']), _extract_class_number(c['class_name'])))

    # ---- 排序房间: 按(楼栋,楼层,房号) ----
    sorted_rooms = sorted(rooms, key=lambda r: (r.building or '', r.floor or 0, _room_number_int(r.room_number)))

    total_students = sum(c['count'] for c in sorted_classes)
    total_rooms = len(sorted_rooms)
    total_beds = sum(r.capacity for r in sorted_rooms)

    logs.append(f"[V5-{gender}] {total_students}人, {len(sorted_classes)}个班, {total_rooms}间房/{total_beds}床")

    # ---- 计算每班房间需求 ----
    class_plans = []  # [{'cls', 'count', 'k', 'room_counts', 'remainder'}]
    total_rooms_ideal = 0
    total_rooms_absolute_min = 0

    for cls in sorted_classes:
        n = cls['count']
        plan = _calc_room_plan(n)
        plan['cls'] = cls
        # 绝对最少房间: 保证余量<=8（可通过合班解决）
        plan['abs_min_k'] = max(0, math.ceil(n / 8) - 1) if n > 8 else 0
        class_plans.append(plan)
        total_rooms_ideal += plan['k']
        total_rooms_absolute_min += plan['abs_min_k']

    logs.append(f"[V5-{gender}] 理想需{total_rooms_ideal}间, 绝对最少{total_rooms_absolute_min}间, 可用{total_rooms}间")

    # 硬性判断: 只有绝对最少房间数超过可用数才失败
    if total_rooms_absolute_min > total_rooms:
        return {
            'success': False,
            'error': f'{gender}生房间严重不足：至少需要{total_rooms_absolute_min}间（不含合班房），仅有{total_rooms}间。请增选宿舍。',
            'assignments': [], 'stats': {'total_students': 0, 'total_rooms_assigned': 0, 'combined_rooms': 0, 'unassigned_students': total_students},
        }

    # ---- 两阶段分配房间 ----
    # Phase A: 先给每班分配绝对最少数房间（保证余量<=8）
    # Phase B: 剩余房间按需求差额分配给需要更多房间的班级（均衡优化）
    cursor = 0

    # Phase A: 绝对最少
    for plan in class_plans:
        k = plan['abs_min_k']
        plan['k'] = k
        if k > 0:
            plan['rooms'] = sorted_rooms[cursor: cursor + k]
            cursor += k
            plan['room_counts'] = [8] * k  # 先按满员8计算
            plan['remainder'] = plan['count'] - 8 * k
        else:
            plan['rooms'] = []
            plan['room_counts'] = []
            plan['remainder'] = plan['count']

    # Phase B: 用剩余房间均衡优化（给有余量且能消化的班级加房）
    remaining_rooms = total_rooms - cursor
    # 按"加一间房能减少多少余量"的优先级分配
    upgradeable = [p for p in class_plans if p['remainder'] > 0 and p['k'] < p.get('count', 0) // 6]
    # 优先给余量大的班级加房
    upgradeable.sort(key=lambda p: p['remainder'], reverse=True)

    for plan in upgradeable:
        if remaining_rooms <= 0:
            break
        # 加一间房后能否让所有房间>=6?
        new_k = plan['k'] + 1
        n = plan['count']
        if n >= 6 * new_k:  # 加房后每间至少6人
            plan['k'] = new_k
            plan['rooms'] = sorted_rooms[cursor: cursor + 1] + plan.get('rooms', [])
            # 重新排序房间(保持楼层顺序)
            plan['rooms'].sort(key=lambda r: (r.building or '', r.floor or 0, _room_number_int(r.room_number)))
            cursor += 1
            remaining_rooms -= 1
            # 重新均衡计算
            plan['room_counts'] = _distribute_evenly(n, new_k)
            plan['remainder'] = n - sum(plan['room_counts'])

    # 对所有班级重新校验room_counts（确保每间房6~8人）
    for plan in class_plans:
        if plan['k'] > 0 and plan.get('rooms'):
            n = plan['count']
            k = plan['k']
            if 6 * k <= n <= 8 * k:
                # 可以完美均衡，无余量
                plan['room_counts'] = _distribute_evenly(n, k)
                plan['remainder'] = 0
            else:
                # 无法均衡（n>8k 或 n<6k），按满员8分配，产生余量
                plan['room_counts'] = [8] * k
                plan['remainder'] = n - 8 * k

    # 剩余房间作为合班/兜底备用
    spare_rooms = sorted_rooms[cursor:]
    logs.append(f"[V5-{gender}] 常规分配{cursor}间, 备用{len(spare_rooms)}间(用于合班)")

    # ---- 处理余量(合班) ----
    remainder_plans = [p for p in class_plans if p.get('remainder', 0) > 0]

    if remainder_plans:
        logs.append(f"[V5-{gender}] {len(remainder_plans)}个班有余量需处理: " +
                    ", ".join(f"{p['cls']['grade']}{p['cls']['class_name']}(余{p['remainder']}人)" for p in remainder_plans))

        combine_result = _handle_remainders(remainder_plans, class_plans, spare_rooms, gender, profiles, logs)
        assignments.extend(combine_result['assignments'])
        combine_suggestions.extend(combine_result.get('combine_suggestions', []))
        spare_rooms = combine_result['remaining_spare']

    # ---- 生成常规分配结果 ----
    for plan in class_plans:
        cls = plan['cls']
        room_counts = plan.get('room_counts', [])
        plan_rooms = plan.get('rooms', [])

        for i, room in enumerate(plan_rooms):
            count = room_counts[i] if i < len(room_counts) else 0
            if count <= 0:
                continue
            assignments.append({
                'room': room,
                'grade': cls['grade'],
                'class_name': cls['class_name'],
                'gender': gender,
                'expected_count': count,
                'is_combined': False,
                'combined_info': '',
            })

    # ---- 统计 ----
    combined_count = sum(1 for a in assignments if a.get('is_combined'))
    assigned_students = sum(a['expected_count'] for a in assignments)
    unassigned = total_students - assigned_students

    needs_combine = len(combine_suggestions) > 0

    logs.append(f"[V5-{gender}] 完成: {len(assignments)}间房, 合班{combined_count}间, 未分配{unassigned}人")

    return {
        'success': True,
        'assignments': assignments,
        'stats': {
            'total_students': total_students,
            'total_rooms_assigned': len(assignments),
            'combined_rooms': combined_count,
            'unassigned_students': max(0, unassigned),
        },
        'needs_combine': needs_combine,
        'combine_suggestions': combine_suggestions,
    }


# ============================================================================
# 房间需求计算（核心数学）
# ============================================================================

def _calc_room_plan(n):
    """
    计算一个班级(n人)的房间分配计划

    规则:
    - 6人间固定住6人, 8人间住6~8人
    - 所有房间人数 >= 6
    - 均衡: 各房间人数差异不超过1

    返回: {'count', 'k', 'room_counts', 'remainder'}
    """
    if n <= 0:
        return {'count': 0, 'k': 0, 'room_counts': [], 'remainder': 0}

    k_min = math.ceil(n / 8)   # 最少房间数(每间最多8人)
    k_max = n // 6             # 最多房间数(每间最少6人)

    if k_min <= k_max:
        # 正常: 使用 k_min 间房, 均衡分配
        k = k_min
        room_counts = _distribute_evenly(n, k)
        remainder = 0
    else:
        # 冲突: 人数无法让所有房间>=6
        # 用 k_max 间房, 每间尽量装满(8人), 产生余量
        k = max(k_max, 1) if n > 8 else 1
        if k == k_max and k_max > 0:
            room_counts = [8] * k_max
            remainder = n - 8 * k_max
        else:
            # n <= 8 的特殊情况
            room_counts = [n] if n >= 6 else []
            remainder = 0 if n >= 6 else n

    return {'count': n, 'k': k, 'room_counts': room_counts, 'remainder': remainder}


def _distribute_evenly(n, k):
    """将n人均衡分配到k个房间，差异不超过1"""
    if k <= 0:
        return []
    base = n // k
    extra = n % k
    # extra间房住(base+1)人, 其余住base人
    return [base + 1] * extra + [base] * (k - extra)


# ============================================================================
# 余量处理（合班逻辑）
# ============================================================================

def _handle_remainders(remainder_plans, all_plans, spare_rooms, gender, profiles, logs):
    """
    处理有余量的班级: 合班配对 → 重分配 → 吸收

    返回: {'assignments', 'combine_suggestions', 'remaining_spare'}
    """
    assignments = []
    combine_suggestions = []
    used_spare = set()

    # 按合班条件分组: (grade, class_type)
    groups = defaultdict(list)
    for plan in remainder_plans:
        cls = plan['cls']
        profile = profiles.get(f"{cls['grade']}:{cls['class_name']}")
        class_type = (profile.class_type or 'default') if profile else 'default'
        gkey = f"{cls['grade']}|{class_type}"
        groups[gkey].append(plan)

    unpaired = []  # 配对后仍有余量的班级

    for gkey, group in groups.items():
        # 按余量降序排列
        group.sort(key=lambda p: p['remainder'], reverse=True)
        paired = set()

        # 贪心配对: 大余量配小余量, 使和>=6且<=8
        for i in range(len(group)):
            if i in paired:
                continue
            pi = group[i]
            best_j = None
            best_diff = 999

            for j in range(i + 1, len(group)):
                if j in paired:
                    continue
                pj = group[j]
                total = pi['remainder'] + pj['remainder']
                if 6 <= total <= 8:
                    diff = abs(total - 7)  # 越接近7越好(均衡)
                    if diff < best_diff:
                        best_diff = diff
                        best_j = j

            if best_j is not None:
                pj = group[best_j]
                paired.add(i)
                paired.add(best_j)

                # 创建合班宿舍
                combined_count = pi['remainder'] + pj['remainder']
                cls_i = pi['cls']
                cls_j = pj['cls']
                combined_name = f"{cls_i['class_name']}+{cls_j['class_name']}"

                # 找一间备用房(优先8人间)
                room = _pick_spare_room(spare_rooms, used_spare, combined_count)
                if room:
                    used_spare.add(room.id)
                    assignments.append({
                        'room': room,
                        'grade': cls_i['grade'],
                        'class_name': combined_name,
                        'gender': gender,
                        'expected_count': combined_count,
                        'is_combined': True,
                        'combined_info': f"{cls_i['grade']} {combined_name}",
                        'class_counts': [
                            {'class_name': cls_i['class_name'], 'count': pi['remainder']},
                            {'class_name': cls_j['class_name'], 'count': pj['remainder']},
                        ],
                    })
                    logs.append(f"[V5-{gender}] 合班: {cls_i['grade']} {combined_name} "
                                f"({pi['remainder']}+{pj['remainder']}={combined_count}人) → {room.building} {room.room_number}")
                else:
                    # 无备用房: 尝试吸收
                    unpaired.append(pi)
                    unpaired.append(pj)
                    logs.append(f"[WARN-{gender}] 合班 {combined_name} 无可用房间")

                combine_suggestions.append({
                    'class1': f"{cls_i['grade']} {cls_i['class_name']}",
                    'class2': f"{cls_j['grade']} {cls_j['class_name']}",
                    'count': combined_count,
                })

        # 未配对的班级
        for idx in range(len(group)):
            if idx not in paired:
                unpaired.append(group[idx])

    # ---- 处理未配对的余量 ----
    for plan in unpaired:
        if plan['remainder'] <= 0:
            continue
        cls = plan['cls']
        rem = plan['remainder']

        # 策略1: 从本班已有房间匀人 + 备用房 → 凑>=6
        resolved = _try_redistribute(plan, spare_rooms, used_spare, gender, assignments, logs)
        if resolved:
            continue

        # 策略2: 吸收到本班已有房间(不超容量8)
        absorbed = _try_absorb(plan, gender, logs)
        if absorbed:
            continue

        # 策略3: 实在无法处理，用备用房(即使<6)
        room = _pick_spare_room(spare_rooms, used_spare, rem)
        if room:
            used_spare.add(room.id)
            assignments.append({
                'room': room,
                'grade': cls['grade'],
                'class_name': cls['class_name'],
                'gender': gender,
                'expected_count': rem,
                'is_combined': False,
                'combined_info': '',
            })
            logs.append(f"[WARN-{gender}] {cls['grade']} {cls['class_name']} 余{rem}人单独安排(不足6人)")
        else:
            logs.append(f"[ERROR-{gender}] {cls['grade']} {cls['class_name']} 余{rem}人无法安排")

    remaining_spare = [r for r in spare_rooms if r.id not in used_spare]
    return {
        'assignments': assignments,
        'combine_suggestions': combine_suggestions,
        'remaining_spare': remaining_spare,
    }


def _try_redistribute(plan, spare_rooms, used_spare, gender, assignments, logs):
    """
    重分配策略: 从本班已有房间匀人 + 一间备用房, 凑成>=6人的新房间

    条件: 本班已有房间中有"可抽出"的人(即该房间人数>6)
    """
    cls = plan['cls']
    rem = plan['remainder']
    need_from_others = 6 - rem  # 需要从其他房间抽多少人

    if need_from_others <= 0:
        # 余量本身>=6, 直接给一间备用房
        room = _pick_spare_room(spare_rooms, used_spare, rem)
        if room:
            used_spare.add(room.id)
            assignments.append({
                'room': room,
                'grade': cls['grade'],
                'class_name': cls['class_name'],
                'gender': gender,
                'expected_count': rem,
                'is_combined': False,
                'combined_info': '',
            })
            logs.append(f"[V5-{gender}] {cls['grade']} {cls['class_name']} 余{rem}人独立安排")
            plan['remainder'] = 0
            return True
        return False

    # 从本班room_counts中抽人
    room_counts = plan.get('room_counts', [])
    available_to_pull = sum(max(0, c - 6) for c in room_counts)

    if available_to_pull < need_from_others:
        return False  # 抽不出足够的人

    # 执行抽人
    pulled = 0
    for i in range(len(room_counts)):
        if pulled >= need_from_others:
            break
        can_pull = room_counts[i] - 6
        if can_pull > 0:
            take = min(can_pull, need_from_others - pulled)
            room_counts[i] -= take
            pulled += take

    # 分配备用房
    new_count = rem + pulled  # = 6
    room = _pick_spare_room(spare_rooms, used_spare, new_count)
    if room:
        used_spare.add(room.id)
        assignments.append({
            'room': room,
            'grade': cls['grade'],
            'class_name': cls['class_name'],
            'gender': gender,
            'expected_count': new_count,
            'is_combined': False,
            'combined_info': '',
        })
        plan['remainder'] = 0
        logs.append(f"[V5-{gender}] {cls['grade']} {cls['class_name']} 重分配: "
                    f"余{rem}人+抽出{pulled}人={new_count}人 → {room.building} {room.room_number}")
        return True

    return False


def _try_absorb(plan, gender, logs):
    """
    吸收策略: 将余量塞入本班已有房间(不超容量8)
    """
    cls = plan['cls']
    rem = plan['remainder']
    room_counts = plan.get('room_counts', [])
    rooms = plan.get('rooms', [])

    if not room_counts or not rooms:
        return False

    # 计算本班已有房间还能容纳多少人(上限8)
    total_absorbable = sum(max(0, 8 - c) for c in room_counts)
    if total_absorbable < rem:
        return False

    # 均匀吸收
    remaining = rem
    for i in range(len(room_counts)):
        if remaining <= 0:
            break
        space = 8 - room_counts[i]
        if space > 0:
            take = min(space, remaining)
            room_counts[i] += take
            remaining -= take

    if remaining == 0:
        plan['remainder'] = 0
        logs.append(f"[V5-{gender}] {cls['grade']} {cls['class_name']} 余{rem}人已吸收到本班房间")
        return True

    return False


def _pick_spare_room(spare_rooms, used_spare, need_count):
    """从备用房间中选一间合适的(优先容量匹配的)"""
    best = None
    for r in spare_rooms:
        if r.id in used_spare:
            continue
        if r.capacity >= need_count:
            if best is None or r.capacity < best.capacity:
                best = r
    # 如果没有容量完全匹配的，返回任何可用房间
    if best is None:
        for r in spare_rooms:
            if r.id not in used_spare:
                return r
    return best


# ============================================================================
# 数据加载工具函数
# ============================================================================

def _group_by_gender(selected_keys, logs):
    """将 selected_keys 按性别分组，查询实际住校人数"""
    from app.utils.helpers import get_dict_values

    try:
        valid_grades = set(get_dict_values('grade'))
        valid_classes = set(get_dict_values('class'))
    except Exception:
        valid_grades, valid_classes = set(), set()

    # 一次性查询所有住校学生ID
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
    """加载房间列表，按楼栋+楼层+房号排序"""
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
# 排序工具函数
# ============================================================================

def _room_number_int(room_number):
    """房间号转整数，用于排序"""
    try:
        return int(room_number)
    except (ValueError, TypeError):
        return 0


def _extract_class_number(class_name):
    """从班名提取数字用于排序，如 '01班' -> 1, '10班' -> 10"""
    if not class_name:
        return 9999
    m = re.search(r'(\d+)', class_name)
    return int(m.group(1)) if m else 9999


def _extract_grade_year(grade):
    """从年级字符串提取年份，如 '2024级' -> 2024"""
    if not grade:
        return 9999
    m = re.search(r'(\d+)', grade)
    return int(m.group(1)) if m else 9999


def _class_key(cls):
    return f"{cls['grade']}:{cls['class_name']}"


# ============================================================================
# 输出函数
# ============================================================================

def _write_to_db(assignments, all_rooms, logs):
    """将分配结果写入 Room 表"""
    for a in assignments:
        room = a['room']
        room.grade = a.get('grade', '') or None
        room.gender = a.get('gender', room.gender or '')

        if a.get('is_combined'):
            combined_name = a.get('class_name', '')
            primary_class = combined_name.split('+')[0].strip() if '+' in combined_name else combined_name
            room.class_name = primary_class or None
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
    """格式化为前端可消费的字典列表"""
    return [{
        'room_id': a['room'].id,
        'room_number': a['room'].room_number,
        'building': a['room'].building,
        'floor': a['room'].floor,
        'capacity': a['room'].capacity,
        'grade': a.get('grade', ''),
        'class_name': a.get('class_name', ''),
        'gender': a.get('gender', ''),
        'expected_count': a.get('expected_count', 0),
        'is_combined': a.get('is_combined', False),
        'combined_info': a.get('combined_info', ''),
    } for a in assignments]
