# -*- coding: utf-8 -*-
"""教务演示数据生成器（教师名单之外的全部教务数据）。

生成内容（围绕既有 students 数据，默认取每个年级前 N 个班）：
  1. 一个启用中的学期 + 13 节默认作息
  2. 班级周课表（避开同班/同教师/同教室三重冲突；含少量单双周交替课与调课条目）
  3. 备课组长登记（学科 × 年级，含职责与备课组范围）
  4. 查课记录（近 30 个工作日）与教师业绩（含待审/已驳回样本）

教师名单不在这里生成 —— 请先运行：
    python -m scripts.sync_users_to_teachers --commit

用法：
    python scripts/seed_academic_demo.py                       # dry-run，只报告
    python scripts/seed_academic_demo.py --commit              # 实际写入
    python scripts/seed_academic_demo.py --commit --classes 8  # 每年级 8 个班
    python scripts/seed_academic_demo.py --purge --commit      # 删除本脚本生成的数据

幂等/可回滚：
  - 学期以 description 打标记「演示数据」，--purge 只删带标记的学期及其节次/条目/调课/版本；
  - 备课组长/查课/业绩同样带演示标记或可按 (学年+学期) 范围识别；
  - 重复 --commit 会跳过已存在的学期，不会叠加数据。
"""
from __future__ import annotations

import argparse
import os
import random
import sys
from datetime import date, datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app import create_app  # noqa: E402
from app.extensions import db
from app.models import Student, User
from app.models.academic import (DUTY_TEMPLATES, InspectionRecord, SubjectLeader,
                                 Teacher, TeacherAchievement)
from app.models.timetable import (NightDuty, PeriodDef, ScheduleEntry,
                                  ScheduleSwap, ScheduleVersion, TermSchedule,
                                  get_default_periods)
from app.modules.academic.services.schedule_common import week_ranges_overlap

SEED_TAG = '演示数据'
TERM_NAME = '2026-2027学年第一学期'
SCHOOL_YEAR = '2026-2027'
TERM = '第一学期'
TERM_START = date(2026, 9, 1)
TERM_END = date(2027, 1, 22)
TOTAL_WEEKS = 21

# 学科周课时（高中口径；合计 37 节/周）
# ── 高中分层课时方案（周正课节数）─────────────────────────────────────
# 真实高中不是"全员学九科"：
#   · 高一尚未选科：九科全开，但理化生政史地课时低于语数英；
#   · 高二已选科（3+1+2）：选考三科课时拉高，合格考科目降到 1 节（学期末考完）；
#   · 高三备考：只留高考科目 + 体育/班会，取消音乐、美术、信息技术。
PLAN_G1 = [('语文', 5), ('数学', 5), ('英语', 5),
           ('物理', 3), ('化学', 2), ('生物', 2), ('政治', 2), ('历史', 2), ('地理', 2),
           ('体育', 2), ('信息技术', 1), ('音乐', 1), ('美术', 1), ('班会', 1)]
PLAN_G2_PHY = [('语文', 5), ('数学', 5), ('英语', 5),
               ('物理', 5), ('化学', 4), ('生物', 4),
               ('政治', 1), ('地理', 1),                    # 合格考科目
               ('体育', 2), ('信息技术', 1), ('音乐', 1), ('美术', 1), ('班会', 1)]
PLAN_G2_HIS = [('语文', 5), ('数学', 5), ('英语', 5),
               ('历史', 5), ('政治', 4), ('地理', 4),
               ('物理', 1), ('化学', 1), ('生物', 1),       # 合格考科目
               ('体育', 2), ('信息技术', 1), ('音乐', 1), ('美术', 1), ('班会', 1)]
PLAN_G3_PHY = [('语文', 5), ('数学', 5), ('英语', 5),
               ('物理', 5), ('化学', 4), ('生物', 4), ('体育', 2), ('班会', 1)]
PLAN_G3_HIS = [('语文', 5), ('数学', 5), ('英语', 5),
               ('历史', 5), ('政治', 4), ('地理', 4), ('体育', 2), ('班会', 1)]

ALL_PLANS = (PLAN_G1, PLAN_G2_PHY, PLAN_G2_HIS, PLAN_G3_PHY, PLAN_G3_HIS)


def _subject_max_hours():
    """各学科在所有方案里的最高周课时 → 用于估算教师池规模"""
    out = {}
    for plan in ALL_PLANS:
        for subj, hours in plan:
            out[subj] = max(out.get(subj, 0), hours)
    return out


def _stage_of(grade, all_grades):
    """年级阶段：1=高一 2=高二 3=高三（按入学年份，最新入学＝高一）。"""
    from app.modules.academic.services.grade_utils import grade_entry_year
    years = [y for y in (grade_entry_year(g) for g in all_grades) if y]
    y = grade_entry_year(grade)
    if not y or not years:
        return 1
    return min(max(max(years) - y + 1, 1), 3)


def _direction_of(index):
    """班级序号 → 选科方向。必须与 fill_class_profiles() 同一规则，
    否则会出现"课表按物理类排、档案写历史类"的自相矛盾数据。"""
    return '历史' if index % 3 == 0 else '物理'


def _plan_for(stage, direction):
    if stage <= 1:
        return PLAN_G1
    if stage == 2:
        return PLAN_G2_PHY if direction == '物理' else PLAN_G2_HIS
    return PLAN_G3_PHY if direction == '物理' else PLAN_G3_HIS

# 成绩模块的任课映射用的是「外语」，课表/任课表统一用「英语」（配色与排序按它走）
SUBJECT_ALIAS = {'外语': '英语', '思想政治': '政治', '通用技术': '信息技术'}

# 功能教室（普通课用班级固定教室）。间数要够整校轮转，否则实验课会排不下。
ROOM_RULES = {
    '物理': ['物理实验室1', '物理实验室2', '物理实验室3'],
    '化学': ['化学实验室1', '化学实验室2'],
    '生物': ['生物实验室1', '生物实验室2'],
    '体育': ['操场', '体育馆'],
    '信息技术': ['机房1', '机房2', '机房3'],
    '音乐': ['音乐教室1', '音乐教室2'],
    '美术': ['美术教室1', '美术教室2'],
}

INSPECT_RESULTS = ['normal'] * 17 + ['late', 'late', 'absent', 'swap', 'other']

ACHIEVE_SAMPLES = [
    ('certificate', '省级优质课一等奖', '省级'),
    ('certificate', '市级教学能手', '市级'),
    ('course', '市级课题《高效课堂的实践研究》', '市级'),
    ('course', '校本课程开发优秀成果', '校级'),
    ('paper', '省级论文评比二等奖', '省级'),
    ('paper', '《中学教学参考》发表论文一篇', '国家级'),
    ('honor', '校级优秀班主任', '校级'),
    ('honor', '区级优秀教师', '区县级'),
    ('training', '省级骨干教师培训合格', '省级'),
    ('training', '新高考改革专题研修', '市级'),
    ('other', '指导学生竞赛获市级优秀指导教师', '市级'),
]


def _weekday_dates(days_back=30):
    """近 N 天里的工作日列表"""
    today = date.today()
    out = []
    for i in range(days_back):
        d = today - timedelta(days=i)
        if d.weekday() < 5:
            out.append(d)
    return out


def purge(commit: bool):
    app = create_app()
    with app.app_context():
        terms = TermSchedule.query.filter(
            TermSchedule.description.like(f'%{SEED_TAG}%')).all()
        leaders = SubjectLeader.query.filter_by(school_year=SCHOOL_YEAR,
                                                term=TERM).all()
        insp = InspectionRecord.query.filter(
            InspectionRecord.note.like(f'%{SEED_TAG}%')).all()
        ach = TeacherAchievement.query.filter(
            TeacherAchievement.note.like(f'%{SEED_TAG}%')).all()
        n_entries = 0
        for ts in terms:
            n_entries += ScheduleEntry.query.filter_by(term_schedule_id=ts.id).count()
        print(f'[PURGE] 学期 {len(terms)} 个（含课表条目 {n_entries} 条）'
              f'、备课组长 {len(leaders)} 条、查课 {len(insp)} 条、业绩 {len(ach)} 条')
        if not commit:
            print('[DRY-RUN] 加 --commit 才会真正删除')
            return
        for ts in terms:
            ScheduleVersion.query.filter_by(term_schedule_id=ts.id).delete()
            ScheduleSwap.query.filter_by(term_schedule_id=ts.id).delete()
            NightDuty.query.filter_by(term_schedule_id=ts.id).delete()
            ScheduleEntry.query.filter_by(term_schedule_id=ts.id).delete()
            PeriodDef.query.filter_by(term_schedule_id=ts.id).delete()
            db.session.delete(ts)
        for ld in leaders:
            db.session.delete(ld)
        for r in insp:
            db.session.delete(r)
        for a in ach:
            db.session.delete(a)
        db.session.commit()
        print('[PURGE] 已删除')


# 各科「每班每周」课时（用于按班级规模推算教师编制；高中新课标常规值）
_PER_CLASS_HOURS = {'语文': 5, '数学': 5, '英语': 5, '物理': 3.2, '历史': 1.8,
                    '化学': 2.4, '生物': 2.4, '政治': 1.8, '地理': 1.8,
                    '体育': 2, '信息技术': 0.7, '音乐': 0.7, '美术': 0.7}
TEACHER_WEEKLY_CAP = 15     # 高中教师一周满课时约 14~16 节


def _staff_quota(total_classes):
    """按班级规模推算各科教师人数（每人周课时 ≤ TEACHER_WEEKLY_CAP）。

    向上取整：宁可多配一位教师（每人 2~4 个班），也不要让人超课时。
    """
    import math
    return {subj: max(1, math.ceil(total_classes * hours / TEACHER_WEEKLY_CAP))
            for subj, hours in _PER_CLASS_HOURS.items()}


def _assign_subjects(teachers, total_classes):
    """按高中编制给学科为空的教师分配学科（就地修改，不 commit）。

    真实高中每位教师都有明确学科（语文教师只教语文），排课表才能"一人带 2~4
    个班"。演示库的教师是从用户同步来的、subject 为空，不补就只能随机配师，
    会出现"一个老师带 21 个班 38 节课"这种明显不像高中的结果。
    """
    from collections import Counter
    have = Counter(t.subject for t in teachers if t.subject)
    blanks = [t for t in teachers if not t.subject]
    plan = []
    for subj, quota in _staff_quota(total_classes).items():
        plan.extend([subj] * max(quota - have.get(subj, 0), 0))
    plan = plan[:len(blanks)]
    for t, subj in zip(blanks, plan):
        t.subject = subj
    return plan, len(blanks) - len(plan)


def assign_teacher_subjects(commit: bool, classes_per_grade: int):
    """独立命令：给演示教师补学科（不改课表）。"""
    app = create_app()
    with app.app_context():
        teachers = Teacher.query.filter_by(status='active').order_by(Teacher.id).all()
        if not teachers:
            print('[ERR] 教师表为空，请先运行 scripts/sync_users_to_teachers.py --commit')
            return 1
        total_classes = classes_per_grade * 3      # 演示库固定 3 个年级
        plan, rest = _assign_subjects(teachers, total_classes)
        from collections import Counter
        if commit:
            db.session.commit()
            print(f'[OK] 已给 {len(plan)} 位教师分配学科：{dict(Counter(plan).most_common())}')
        else:
            print(f'[DRY-RUN] 将分配 {len(plan)} 位：{dict(Counter(plan).most_common())}')
        if rest > 0:
            print(f'     另有 {rest} 位留作班主任/行政（不排学科课）')
        return 0


def _build_subject_pools(teachers_by_id, links, total_classes):
    """全局（跨年级）按学科切分教师池。

    - 优先放入成绩模块任课映射里该学科的教师；
    - 再从未被其它学科占用的教师里补足（每人尽量只带一个学科，贴近真实排课）；
    - 所需人数按「每人每周 ≤ 15 节」估算，教师不够时才允许跨学科复用。
    """
    names = list(teachers_by_id.values())
    used = set()
    pools = {}
    for subj, hours in _subject_max_hours().items():
        grp = []
        for (g, cn, s), uid in links.items():
            if SUBJECT_ALIAS.get(s, s) != subj:
                continue
            t = teachers_by_id.get(uid)
            if t and t not in grp:
                grp.append(t)
        need = max(2, (total_classes * hours + TEACHER_WEEKLY_CAP - 1)
                   // TEACHER_WEEKLY_CAP)
        for allow_reuse in (False, True):
            i = 0
            while len(grp) < need and names and i < len(names) * 2:
                cand = names[i % len(names)]
                i += 1
                if cand in grp:
                    continue
                if not allow_reuse and cand.teacher_uid in used:
                    continue
                grp.append(cand)
        for t in grp:
            used.add(t.teacher_uid)
        pools[subj] = grp
    return pools


def seed(commit: bool, classes_per_grade: int, seed_no: int):
    rnd = random.Random(seed_no)
    app = create_app()
    with app.app_context():
        teachers = Teacher.query.filter_by(status='active').all()
        if not teachers:
            print('[ERR] academic.teachers 为空，请先运行：'
                  'python -m scripts.sync_users_to_teachers --commit')
            return 1
        teachers_by_id = {}
        for t in teachers:
            if t.user_id:
                teachers_by_id[t.user_id] = t
        # 学科映射（grades.db：教学班 → 教师）
        links = {}
        try:
            from app.models.grades import TeacherSubjectLink
            for row in TeacherSubjectLink.query.filter_by(active=True).all():
                links[(row.grade, row.class_name, row.subject)] = row.user_id
        except Exception as e:  # noqa: BLE001
            print(f'[WARN] 读取任课映射失败（不影响排课）：{e}')

        # 班级：每年级前 N 个班（按班级名排序）
        rows = db.session.query(Student.grade, Student.class_name).distinct().all()
        by_grade = {}
        for g, cn in rows:
            if g and cn and cn.endswith('班') and cn[:2].isdigit() and int(cn[:2]) <= 30:
                by_grade.setdefault(g, set()).add(cn)
        grades = sorted(by_grade)
        classes = {g: sorted(by_grade[g])[:classes_per_grade] for g in grades}
        total_classes = sum(len(v) for v in classes.values())

        # 教师没学科就排不出"像高中"的任课表（会一人带 20 多个班）：先按编制补学科
        blanks = [t for t in teachers if not t.subject]
        if len(blanks) > len(teachers) // 2:
            from collections import Counter
            plan, rest = _assign_subjects(teachers, total_classes)
            if commit:
                db.session.commit()
            print(f'[{"OK" if commit else "DRY-RUN"}] 教师学科分配 {len(plan)} 人：'
                  f'{dict(Counter(plan).most_common())}'
                  + (f'；{rest} 人做班主任/行政' if rest else ''))

        existing = TermSchedule.query.filter_by(name=TERM_NAME).first()
        if existing and SEED_TAG not in (existing.description or ''):
            print(f'[ERR] 已存在同名学期「{TERM_NAME}」但不是本脚本生成的，'
                  f'请手工处理后再跑')
            return 1
        planned_entries = 0
        for _g in grades:
            _stage = _stage_of(_g, grades)
            for _ci, _cn in enumerate(classes[_g]):
                planned_entries += sum(h for _, h in
                                       _plan_for(_stage, _direction_of(_ci)))
        print(f'[PLAN] 年级 {grades}；班级共 {total_classes} 个；'
              f'预计课表条目 ≈ {planned_entries} 条')
        if existing:
            print(f'[SKIP] 学期「{TERM_NAME}」已存在（id={existing.id}），'
                  f'如需重建请先 --purge --commit')
            return 0
        if not commit:
            print('[DRY-RUN] 以上为将生成的数据量；加 --commit 实际写入')
            return 0

        # ── 学期 + 节次 ──
        ts = TermSchedule(name=TERM_NAME, school_year=SCHOOL_YEAR, term=TERM,
                          status='active', is_current=True, start_date=TERM_START,
                          end_date=TERM_END, total_weeks=TOTAL_WEEKS,
                          week_start_offset=0, created_by=1,
                          description=f'{SEED_TAG}（scripts/seed_academic_demo.py 生成，'
                                      f'可 --purge 删除）')
        db.session.add(ts)
        db.session.flush()
        for p in get_default_periods():
            db.session.add(PeriodDef(term_schedule_id=ts.id, **p))
        db.session.commit()
        # 第1节留给早读（后面单独排），第6节午休；其余时段给正课
        periods = [2, 3, 4, 5, 7, 8, 9, 10, 11, 12, 13]
        slots = [(wd, pn) for wd in range(1, 6) for pn in periods]

        busy_class = set()
        busy_teacher = set()
        busy_room = set()
        entries = []
        missed = 0

        # 教师负载表：同一教师的总课时要摊平，否则换格时必然撞车
        teacher_load = {}
        class_count = {}          # 教师 → 已带班级数（高中一位教师一般带 2~4 个班）
        pools = _build_subject_pools(teachers_by_id, links, total_classes)
        for grade in grades:
            stage = _stage_of(grade, grades)
            for ci, cn in enumerate(classes[grade]):
                direction = _direction_of(ci)
                home_room = f'{grade}{cn}教室'
                plan = []
                for subj, hours in _plan_for(stage, direction):
                    lst = pools.get(subj) or teachers
                    # 同班同学科固定一位教师（贴近真实任课表）：课时最少、带班最少者优先
                    t = min(lst, key=lambda x: (teacher_load.get(x.teacher_uid, 0),
                                                class_count.get(x.teacher_uid, 0)))
                    teacher_load[t.teacher_uid] = teacher_load.get(t.teacher_uid, 0) + hours
                    class_count[t.teacher_uid] = class_count.get(t.teacher_uid, 0) + 1
                    for _ in range(hours):
                        plan.append((subj, t))
                rnd.shuffle(plan)
                for subj, t in plan:
                    rooms = ROOM_RULES.get(subj) or [home_room]
                    placed = False
                    for off in range(len(slots)):
                        wd, pn = slots[(ci * 3 + off) % len(slots)]
                        room = rooms[(ci + pn) % len(rooms)]
                        if (cn, grade, wd, pn) in busy_class:
                            continue
                        if (t.teacher_uid, wd, pn) in busy_teacher:
                            continue
                        if (room, wd, pn) in busy_room:
                            continue
                        entries.append(ScheduleEntry(
                            term_schedule_id=ts.id, grade=grade, class_name=cn,
                            weekday=wd, period_number=pn, subject=subj,
                            teacher_uid=t.teacher_uid, teacher_name=t.name,
                            room=room, week_range='1-21', entry_type='normal'))
                        busy_class.add((cn, grade, wd, pn))
                        busy_teacher.add((t.teacher_uid, wd, pn))
                        busy_room.add((room, wd, pn))
                        placed = True
                        break
                    if not placed:
                        missed += 1
                db.session.add_all(entries)
                db.session.commit()
                entries = []

        # ── 早读（第1节）：语文/英语交替跟班（高中典型安排）──
        # 学科记为「早读」而非语文/英语，避免虚增学科周课时；教师挂该班语文/英语
        # 任课教师，若同一时段已在别的班跟早读则留空（现实中由班主任看班）。
        read_subjects = ['语文', '英语', '语文', '英语', '语文']
        read_added = 0
        for grade in grades:
            for cn in classes[grade]:
                home_room = f'{grade}{cn}教室'
                for wd in range(1, 6):
                    if (cn, grade, wd, 1) in busy_class:
                        continue
                    owner = ScheduleEntry.query.filter_by(
                        term_schedule_id=ts.id, grade=grade, class_name=cn,
                        subject=read_subjects[wd - 1], is_deleted=False).first()
                    t_uid = owner.teacher_uid if owner else None
                    t_name = owner.teacher_name if owner else None
                    if t_uid and (t_uid, wd, 1) in busy_teacher:
                        t_uid, t_name = None, None      # 该教师同时在别班 → 留空
                    elif t_uid:
                        busy_teacher.add((t_uid, wd, 1))
                    db.session.add(ScheduleEntry(
                        term_schedule_id=ts.id, grade=grade, class_name=cn,
                        weekday=wd, period_number=1, subject='早读',
                        teacher_uid=t_uid, teacher_name=t_name,
                        room=home_room, week_range='1-21', entry_type='normal'))
                    busy_class.add((cn, grade, wd, 1))
                    read_added += 1
        db.session.commit()
        print(f'[OK] 早读排课 {read_added} 条（第1节，语文/英语交替）')

        # ── 单双周交替课：每班把「生物」与「化学」各一节并到同一格 ──
        # 注意：合并后化学变成"双周"，必须校验"双周"口径下该教师/该教室在目标
        # 时段是否空闲（原时段合法不代表新时段合法），否则会造出真实冲突。
        merged = 0
        sample_classes = [(g, cn) for g in grades for cn in classes[g][:6]]
        by_teacher_slot = {}
        by_room_slot = {}
        for e in ScheduleEntry.query.filter_by(term_schedule_id=ts.id,
                                               is_deleted=False).all():
            by_teacher_slot.setdefault((e.teacher_uid, e.weekday, e.period_number),
                                       []).append(e)
            by_room_slot.setdefault((e.room, e.weekday, e.period_number), []).append(e)

        def _slot_free(store, key, self_ids):
            for other in store.get(key) or []:
                if other.id in self_ids:
                    continue
                if week_ranges_overlap('双周', other.week_range):
                    return False
            return True

        for grade, cn in sample_classes:
            bio = ScheduleEntry.query.filter_by(
                term_schedule_id=ts.id, grade=grade, class_name=cn,
                subject='生物', is_deleted=False).first()
            chem = ScheduleEntry.query.filter_by(
                term_schedule_id=ts.id, grade=grade, class_name=cn,
                subject='化学', is_deleted=False).first()
            if not bio or not chem:
                continue
            wd, pn = bio.weekday, bio.period_number
            self_ids = {bio.id, chem.id}
            if not _slot_free(by_teacher_slot,
                              (chem.teacher_uid, wd, pn), self_ids):
                continue
            if not _slot_free(by_room_slot, (chem.room, wd, pn), self_ids):
                continue
            by_teacher_slot.setdefault((chem.teacher_uid, wd, pn), []).append(chem)
            by_room_slot.setdefault((chem.room, wd, pn), []).append(chem)
            chem.weekday, chem.period_number = wd, pn
            bio.week_range = '单周'
            chem.week_range = '双周'
            merged += 1
        db.session.commit()

        # ── 调课演示：3 个班各 1 条 swap 条目 + 调课记录 ──
        swaps = 0
        for grade, cn in sample_classes[:3]:
            e = ScheduleEntry.query.filter_by(
                term_schedule_id=ts.id, grade=grade, class_name=cn,
                subject='体育', is_deleted=False).first()
            if not e:
                continue
            e.entry_type = 'swap'
            e.note = f'{SEED_TAG}：教研活动调课'
            db.session.add(ScheduleSwap(
                term_schedule_id=ts.id, swap_type='personal',
                original_entry_id=e.id, applicant_uid=e.teacher_uid or '',
                applicant_name=e.teacher_name or '', new_weekday=e.weekday,
                new_period=e.period_number, new_room=e.room,
                is_permanent=True, reason=f'{SEED_TAG}：教研活动',
                status='executed', reviewed_by=1, reviewed_at=datetime.now(),
                review_note='同意'))
            swaps += 1
        db.session.commit()

        # ── 备课组长：学科 × 年级 ──
        leader_count = 0
        subjects = ['语文', '数学', '外语', '物理', '化学', '生物', '政治', '历史',
                    '地理', '体育']
        for grade in grades:
            for subj in subjects:
                lst = pools.get(subj) or teachers
                lead = lst[0]
                members = '、'.join(t.name for t in lst[:4])
                key = '毕业年级' if grade.startswith('2024') else 'default'
                db.session.add(SubjectLeader(
                    school_year=SCHOOL_YEAR, term=TERM, grade=grade, subject=subj,
                    leader_uid=lead.teacher_uid, leader_name=lead.name,
                    members=f'{grade}{subj}备课组（{members}）',
                    duty=DUTY_TEMPLATES[key],
                    updated_by=1))
                leader_count += 1
        db.session.commit()

        # ── 查课记录（近 30 个工作日） ──
        all_entries = ScheduleEntry.query.filter_by(term_schedule_id=ts.id).all()
        insp_count = 0
        for d in _weekday_dates(30):
            for _ in range(rnd.randint(2, 4)):
                e = rnd.choice(all_entries)
                db.session.add(InspectionRecord(
                    inspect_date=d, grade=e.grade, period=e.period_number,
                    teacher_uid=e.teacher_uid, teacher_name=e.teacher_name,
                    class_name=e.class_name, subject=e.subject,
                    result=rnd.choice(INSPECT_RESULTS),
                    inspector_id=1, note=f'{SEED_TAG}'))
                insp_count += 1
        db.session.commit()

        # ── 教师业绩 ──
        ach_count = 0
        for i in range(60):
            t = rnd.choice(teachers)
            cat, title, level = rnd.choice(ACHIEVE_SAMPLES)
            status = 'approved' if i % 10 < 7 else ('pending' if i % 10 < 9 else 'rejected')
            db.session.add(TeacherAchievement(
                teacher_uid=t.teacher_uid, teacher_name=t.name, category=cat,
                title=title, level=level,
                tags=','.join(ACHIEVE_TAGS.get(cat, ['其他'])),
                obtain_date=date(2026, rnd.randint(1, 9), rnd.randint(1, 28)),
                issuer='市教研室' if level in ('市级', '省级') else '学校',
                note=f'{SEED_TAG}', status=status,
                reviewed_by=1 if status != 'pending' else None,
                reviewed_at=datetime.now() if status != 'pending' else None,
                review_note='材料齐全' if status == 'approved'
                else ('材料不全，请补扫描件' if status == 'rejected' else None)))
            ach_count += 1
        db.session.commit()

        print(f'[OK] 学期「{TERM_NAME}」id={ts.id}')
        print(f'     课表条目 {ScheduleEntry.query.filter_by(term_schedule_id=ts.id).count()} 条'
              f'（未排上 {missed} 条）')
        print(f'     单双周交替 {merged} 组 · 调课演示 {swaps} 条')
        print(f'     备课组长 {leader_count} 条 · 查课 {insp_count} 条 · 业绩 {ach_count} 条')

        # ── 晚自习值班表（高中刚需：晚自习排值班教师，不排学科课）──
        try:
            from app.modules.academic.services import night_duty_service as nd_svc
            ok_n, msg_n = nd_svc.auto_assign(ts.id, max_per_week=2, operator=None)
            print(f'     晚自习值班 {"：" + msg_n if ok_n else "未生成（" + msg_n + "）"}')
        except Exception as exc:  # noqa: BLE001  值班生成失败不影响课表
            print(f'     [WARN] 晚自习值班未生成：{exc}')
        return 0


def fill_class_profiles(commit: bool, classes_per_grade: int):
    """为演示班级补高中选科信息（方向 + 组合）。

    班级档案（ClassProfile）是既有数据，但演示库里没填选科，课表上就看不出
    "这是物理类班还是历史类班"。这里给本脚本涉及的班级补上：
    - subject_direction：物理 / 历史（新高考 3+1+2 的"1"）
    - ClassSubject：物化生 / 史政地 等组合
    已填过的班级不覆盖（幂等）。
    """
    app = create_app()
    with app.app_context():
        from app.models import ClassProfile, ClassSubject, Student
        rows = db.session.query(Student.grade, Student.class_name).distinct().all()
        by_grade = {}
        for g, cn in rows:
            if g and cn and cn.endswith('班') and cn[:2].isdigit() and int(cn[:2]) <= 30:
                by_grade.setdefault(g, set()).add(cn)
        combos = {
            '物理': ('物理', '化学', '生物'),
            '历史': ('历史', '政治', '地理'),
        }
        touched = 0
        all_grades = sorted(by_grade)
        for grade in all_grades:
            # 高一尚未选科（新高考一般是高一下/高二上才定选考），不填方向与组合，
            # 否则会出现"历史类班却按高一全科课表上课"的自相矛盾数据。
            # 同时**清掉历史遗留**的选科（早先版本给高一也填过）。
            if _stage_of(grade, all_grades) < 2:
                # 清空该年级**全部班级档案**（不只本脚本用的那几个）：高一尚未选科，
                # 留任何选科都会变成"高一却有物理类/史政地"的矛盾数据。
                for cp in ClassProfile.query.filter_by(grade=grade).all():
                    if cp.subjects.count():
                        for s in cp.subjects.all():
                            db.session.delete(s)
                        touched += 1
                    if cp.subject_direction:
                        cp.subject_direction = None
                continue     # 高一处理完（清空）直接下一轮，不要掉进下面的填充逻辑
            for i, cn in enumerate(sorted(by_grade[grade])[:classes_per_grade]):
                direction = '物理' if i % 3 else '历史'      # 大致 2:1，像真实分班
                cp = ClassProfile.query.filter_by(grade=grade, class_name=cn).first()
                if cp is None:
                    cp = ClassProfile(grade=grade, class_name=cn)
                    db.session.add(cp)
                    db.session.flush()
                subs = [s.subject_value for s in
                        ClassSubject.query.filter_by(class_profile_id=cp.id).all()]
                if subs:
                    # 已有组合：**方向必须由组合反推**，否则会出现"标物理却配史政地"
                    inferred = ('物理' if any('物理' in s for s in subs)
                                else '历史' if any('历史' in s for s in subs) else None)
                    if inferred and cp.subject_direction != inferred:
                        cp.subject_direction = inferred
                else:
                    if not cp.subject_direction:
                        cp.subject_direction = direction
                    for s in combos.get(cp.subject_direction, combos['物理']):
                        db.session.add(ClassSubject(class_profile_id=cp.id,
                                                    subject_value=s))
                touched += 1
        if commit:
            db.session.commit()
            print(f'[OK] 已为 {touched} 个班补写选科方向/组合（物理类:历史类 ≈ 2:1）')
        else:
            print(f'[DRY-RUN] 将为 {touched} 个班补写选科方向/组合（加 --commit 生效）')
        return 0


WALK_SLOT = 8          # 第8节作为「走班时段」（真实高中常把下午最后一节留给选考走班）
WALK_PLAN = [          # (星期, 教学班, 学科)：同组合不同时段，避免学生重复上课
    (2, '物化生1', '物理'), (2, '史政地1', '历史'),
    (3, '物化生1', '化学'), (3, '史政地1', '政治'),
    (4, '物化生1', '生物'), (4, '史政地1', '地理'),
    (5, '物化生1', '物理'), (5, '史政地1', '历史'),
]


def build_walking(commit: bool):
    """造一批**走班课**演示数据（新高考选考走班）。

    做法：把每年级第8节清空为「走班时段」（行政班课软删），再按教学班排课：
    - 物化生1：物理/化学/生物/物理（周二~周五第8节）
    - 史政地1：历史/政治/地理/历史
    这样学生课表＝行政班课 + 本人组合对应的走班课，「走班教学班」页也有内容。
    """
    from app.models.academic import Teacher
    from app.models.timetable import ScheduleEntry
    from app.modules.academic.services import schedule_service as svc

    app = create_app()
    with app.app_context():
        ts = TermSchedule.query.filter(
            TermSchedule.description.like('%演示数据%')).first()
        if not ts:
            print('[!] 未找到演示学期，请先跑一次 seed（不加 --walking）')
            return 1
        grades = sorted({g for (g,) in db.session.query(
            ScheduleEntry.grade).filter_by(
            term_schedule_id=ts.id, is_deleted=False).all()})
        cleared = added = 0
        for grade in grades:
            # 1) 清空走班时段：该年级第8节的行政班课软删（走班时段行政班不排课）
            #    注意：svc.add_entry 内部会 commit，所以 dry-run 必须**不调用**它，
            #    否则软删也会被连带提交 —— 演示时先用 --walking 空跑看数量。
            for e in ScheduleEntry.query.filter_by(
                    term_schedule_id=ts.id, grade=grade,
                    period_number=WALK_SLOT, is_deleted=False).all():
                if (e.teaching_class or '').strip():
                    continue
                if commit:
                    e.is_deleted = True
                cleared += 1
            classes = sorted({c for (c,) in db.session.query(
                ScheduleEntry.class_name).filter_by(
                term_schedule_id=ts.id, grade=grade, is_deleted=False).all()})
            anchor = classes[0] if classes else '01班'
            # 2) 按教学班排课
            for wd, tc, subject in WALK_PLAN:
                t = Teacher.query.filter_by(subject=subject, status='在职').first()
                if not commit:          # dry-run：只统计，不落库
                    added += 1
                    continue
                ok, res = svc.add_entry(
                    ts.id, grade, anchor, wd, WALK_SLOT, subject,
                    teacher_uid=(t.teacher_uid if t else ''),
                    teacher_name=(t.name if t else ''),
                    room=f'{subject}教室', teaching_class=tc, operator=None)
                if ok:
                    added += 1
                else:
                    print(f'   [skip] {grade} 周{wd} 第{WALK_SLOT}节 {tc} {subject}：{res}')
        if commit:
            db.session.commit()
            print(f'[OK] 走班演示数据：清空走班时段行政班课 {cleared} 条，'
                  f'新增走班课 {added} 条（教学班：物化生1 / 史政地1）')
        else:
            print(f'[DRY-RUN] 将清空 {cleared} 条、新增走班课 {added} 条'
                  f'（加 --commit 生效）')
        return 0


# 业绩类别 → 默认标签（演示数据用；页面标签云按这些聚合）
ACHIEVE_TAGS = {
    'certificate': ['证书', '资质'],
    'course': ['课题立项', '教研'],
    'paper': ['论文发表'],
    'honor': ['荣誉表彰'],
    'training': ['继续教育'],
    'other': ['其他'],
}


def backfill_achievement_tags(commit: bool):
    """给已有业绩补默认标签（幂等：只补 tags 为空的），让标签云立刻可用。"""
    app = create_app()
    with app.app_context():
        rows = TeacherAchievement.query.filter(
            db.or_(TeacherAchievement.tags.is_(None), TeacherAchievement.tags == '')).all()
        for r in rows:
            r.tags = ','.join(ACHIEVE_TAGS.get(r.category, ['其他']))
        if commit:
            db.session.commit()
            print(f'[OK] 已为 {len(rows)} 条业绩补默认标签')
        else:
            print(f'[DRY-RUN] 将为 {len(rows)} 条业绩补默认标签（加 --commit 生效）')
        return 0


def main():
    ap = argparse.ArgumentParser(description='教务演示数据生成器')
    ap.add_argument('--commit', action='store_true', help='实际写入（默认 dry-run）')
    ap.add_argument('--purge', action='store_true', help='删除本脚本生成的数据')
    ap.add_argument('--classes', type=int, default=6,
                    help='每个年级生成的班级数（默认 6；全校 18 个班约需 48 位教师，'
                         '与演示教师规模匹配，人均周课时 ≈ 13 节）')
    ap.add_argument('--seed', type=int, default=20260926, help='随机种子')
    ap.add_argument('--profiles', action='store_true',
                    help='只给演示班级补写选科方向/组合（不重建课表）')
    ap.add_argument('--walking', action='store_true',
                    help='造走班（教学班）演示课：清空第8节行政班课后按教学班排课')
    ap.add_argument('--assign-subjects', action='store_true',
                    help='只给演示教师补学科（不改课表）')
    ap.add_argument('--tag-achievements', action='store_true',
                    help='给还没标签的教师业绩补默认标签（幂等）')
    args = ap.parse_args()
    if args.tag_achievements:
        return backfill_achievement_tags(args.commit)
    if args.assign_subjects:
        return assign_teacher_subjects(args.commit, args.classes)
    if args.profiles:
        return fill_class_profiles(args.commit, args.classes)
    if args.walking:
        return build_walking(args.commit)
    if args.purge:
        purge(args.commit)
        return 0
    return seed(args.commit, args.classes, args.seed)


if __name__ == '__main__':
    sys.exit(main())
