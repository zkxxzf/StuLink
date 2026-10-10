# -*- coding: utf-8 -*-
"""教务 · 统一班级与教师任课数据源（2026-10-09）。

教务（课表 / 查课 / 调课 / 晚自习）此前各存各的：班级从课表条目里反推、教师只有一份
名单（没有"教哪些班哪科"），导致课表没排到的班就"查无此班"。实际上班级、教师、任课
跟学生管理 / 成绩管理是同一套基础数据，应当共用：

- **班级** → 班级档案 `class_profiles`（`is_active=1` 为在用班级），课表里出现过的班兜底；
- **教师任课** → 成绩管理 `teacher_subject_links`（谁教哪班哪科，按 user_id），
  通过 `academic.teachers.user_id` 翻译成教务的 teacher_uid；查不到再回退课表条目；
- **教师档案** → `academic.teachers`（已全部绑定登录账号，可跨库对齐）。

所有结果都按"启用班级"过滤，避免历史 / 批量导入的班级污染下拉。
"""
from app.extensions import db


# ── 班级 ────────────────────────────────────────────────────────────────────

def active_class_pairs(grade=None):
    """启用班级 [(grade, class_name)]（按年级、班名排序）。"""
    from app.models import ClassProfile
    q = ClassProfile.query.filter(ClassProfile.is_active.is_(True))
    if grade:
        q = q.filter(ClassProfile.grade == grade)
    rows = (q.with_entities(ClassProfile.grade, ClassProfile.class_name)
            .distinct().all())
    return sorted({(g, c) for g, c in rows if g and c})


def active_class_map(grade=None):
    """{grade: [class_name, ...]}（启用班级）。"""
    out = {}
    for g, c in active_class_pairs(grade):
        out.setdefault(g, []).append(c)
    for g in out:
        out[g] = sorted(out[g])
    return out


def scheduled_class_pairs(schedule_id=None):
    """课表里出现过的班级 [(grade, class_name)]（未删除条目）。"""
    from app.models.timetable import ScheduleEntry
    q = ScheduleEntry.query.filter(ScheduleEntry.is_deleted.is_(False))
    if schedule_id:
        q = q.filter(ScheduleEntry.term_schedule_id == schedule_id)
    rows = q.with_entities(ScheduleEntry.grade, ScheduleEntry.class_name).distinct().all()
    return sorted({(g, c) for g, c in rows if g and c})


def class_candidates(schedule_id=None):
    """教务各页统一的班级候选：启用班级 ∪ 课表班级（课表里有课的一定不能漏）。

    返回 {'grades': [...], 'grade_classes': {grade: [class_name, ...]}}。
    """
    pairs = set(active_class_pairs()) | set(scheduled_class_pairs(schedule_id))
    grade_classes = {}
    for g, c in sorted(pairs):
        grade_classes.setdefault(g, []).append(c)
    return {'grades': sorted(grade_classes), 'grade_classes': grade_classes}


# ── 教师 / 任课 ─────────────────────────────────────────────────────────────

def teacher_of_user(user):
    """登录账号 → 教务教师档案（复用 teacher_service 的 user_id / 唯一同名绑定）。"""
    if not user:
        return None
    try:
        from app.modules.academic.services import teacher_service
        return teacher_service.teacher_of_user(user)
    except Exception:  # noqa: BLE001  绑定失败不阻断，调用方按"无教师"处理
        return None


def teacher_teaching_scope(teacher_uid, schedule_id=None):
    """教师任课范围 [(grade, class_name, subject)]。

    优先成绩管理的任课映射（权威），没有再回退课表条目；结果只保留启用班级。
    """
    if not teacher_uid:
        return []
    active = set(active_class_pairs())
    out = []

    from app.models.academic import Teacher
    teacher = Teacher.query.filter_by(teacher_uid=teacher_uid).first()
    if teacher and teacher.user_id:
        try:
            from app.models.grades import TeacherSubjectLink
            rows = (TeacherSubjectLink.query
                    .filter_by(user_id=teacher.user_id, active=True)
                    .with_entities(TeacherSubjectLink.grade,
                                   TeacherSubjectLink.class_name,
                                   TeacherSubjectLink.subject).all())
            for g, c, s in rows:
                if g and c and (not active or (g, c) in active):
                    out.append((g, c, s or ''))
        except Exception:  # noqa: BLE001  grades 库不可用时回退课表
            pass
    if out:
        return sorted(set(out))

    from app.models.timetable import ScheduleEntry
    q = ScheduleEntry.query.filter(ScheduleEntry.teacher_uid == teacher_uid,
                                   ScheduleEntry.is_deleted.is_(False))
    if schedule_id:
        q = q.filter(ScheduleEntry.term_schedule_id == schedule_id)
    for g, c, s in (q.with_entities(ScheduleEntry.grade, ScheduleEntry.class_name,
                                    ScheduleEntry.subject).distinct().all()):
        if g and c and (not active or (g, c) in active):
            out.append((g, c, s or ''))
    return sorted(set(out))


def classes_of_teacher(teacher_uid, schedule_id=None):
    """该教师任教的班级 [(grade, class_name)]（去重排序）。"""
    return sorted({(g, c) for g, c, _s in teacher_teaching_scope(teacher_uid, schedule_id)})


def teachers_of_class(grade, class_name, schedule_id=None):
    """该班的任课教师 [(teacher_uid, teacher_name, subject)]。

    先取成绩管理任课映射（user_id → teachers.user_id → teacher_uid），
    再补上课表里出现的教师（临时代课 / 映射没维护的情况）。
    """
    if not (grade and class_name):
        return []
    result = {}
    try:
        from app.models.grades import TeacherSubjectLink
        from app.models.academic import Teacher
        rows = (TeacherSubjectLink.query
                .filter_by(grade=grade, class_name=class_name, active=True)
                .with_entities(TeacherSubjectLink.user_id,
                               TeacherSubjectLink.subject).all())
        uids = [u for u, _s in rows if u]
        tmap = {}
        if uids:
            for t in Teacher.query.filter(Teacher.user_id.in_(uids)).all():
                tmap[t.user_id] = t
        for u, s in rows:
            t = tmap.get(u)
            if t:
                result[t.teacher_uid] = (t.teacher_uid, t.name, s or t.subject or '')
    except Exception:  # noqa: BLE001
        pass

    from app.models.timetable import ScheduleEntry
    q = ScheduleEntry.query.filter(ScheduleEntry.grade == grade,
                                   ScheduleEntry.class_name == class_name,
                                   ScheduleEntry.is_deleted.is_(False))
    if schedule_id:
        q = q.filter(ScheduleEntry.term_schedule_id == schedule_id)
    for uid, name, subj in (q.with_entities(ScheduleEntry.teacher_uid,
                                            ScheduleEntry.teacher_name,
                                            ScheduleEntry.subject).distinct().all()):
        if uid and uid not in result:
            result[uid] = (uid, name or '', subj or '')
    return sorted(result.values())
