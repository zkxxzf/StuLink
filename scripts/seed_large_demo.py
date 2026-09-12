# -*- coding: utf-8 -*-
"""大规模演示数据生成器（成绩模块压力数据）

一次生成：
  - N 名学生（默认 1500），编入若干教学班（默认每班 50 人）
  - 班型 / 选科：物理类与历史类各半，再选科覆盖「四选二」的多种组合
    （同班内也有不同组合，用于验证划线取「方向内选科并集」）
  - 教师账号 + 任课映射（每科一位教师，覆盖全部相关班级）
  - M 场考试（默认 30），每场全体学生 6 科成绩 + 总分，自动排名
  - 每场按四层模板（人数比例）划线

用法：
  python scripts/seed_large_demo.py                          # 1500 人 / 30 场
  python scripts/seed_large_demo.py --students 200 --exams 3 # 快速试跑
  python scripts/seed_large_demo.py --clean                  # 仅清空演示数据

说明：幂等——重复执行会先清空本年级演示数据再重建；其他年级数据不受影响。
"""
import argparse
import os
import random
import sys
from datetime import date, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from app import create_app                                     # noqa: E402
from app.extensions import db                                  # noqa: E402
from app.models import (User, Student, ClassProfile, PermissionGroup,  # noqa: E402
                        UserClassLink)
from app.models.grades import (Exam, ExamScore, ExamBand, TeacherSubjectLink,  # noqa: E402
                               SUBJECTS, TOTAL_SUBJECT, subjects_of_selection)

app = create_app()

GRADE = '2026级'
CLASS_SIZE = 50
PWD = '123456'

# 再选科组合（四选二），物理类 / 历史类各 6 种
PHYS_COMBOS = ['物化生', '物化政', '物化地', '物生政', '物生地', '物政地']
HIST_COMBOS = ['史政地', '史生政', '史生地', '史化政', '史化生', '史化地']

# 每位教师负责一个科目
TEACHERS = [
    ('t01', '张老师', '语文'), ('t02', '李老师', '数学'), ('t03', '王老师', '外语'),
    ('t04', '刘老师', '物理'), ('t05', '陈老师', '历史'), ('t06', '杨老师', '化学'),
    ('t07', '黄老师', '生物'), ('t08', '周老师', '政治'), ('t09', '吴老师', '地理'),
]
HOMEROOMS = [('hteacher', '赵班主任', '01班'), ('h02', '钱班主任', '02班'),
             ('h03', '孙班主任', '03班')]

# 各科满分与基线（语数外 150，其余 100）
BASE_SCORE = {'语文': 105, '数学': 92, '外语': 98, '物理': 70, '历史': 72,
              '化学': 68, '生物': 68, '政治': 70, '地理': 69}
SD_SCORE = {'语文': 11, '数学': 22, '外语': 18, '物理': 14, '历史': 12,
            '化学': 14, '生物': 14, '政治': 12, '地理': 13}

SURNAMES = ('赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜'
            '戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳')
GIVEN = ('伟芳娜秀英敏静丽强磊洋艳勇军杰娟涛明超霞平刚桂英建华文博子轩浩然'
         '梓涵一诺思远雨欣嘉宇航晨曦若曦嘉怡俊杰欣怡家豪雅静志强晓明')

EXAM_TYPES = ['月考', '单元测', '期中', '期末', '模拟']
BAND_TEMPLATE = [('优秀', 20), ('良好', 60), ('及格', 95), ('待提升', 0)]


def _full(sub):
    return 150 if sub in ('语文', '数学', '外语') else 100


def _clamp(v, sub):
    return max(0.0, min(float(_full(sub)), v))


def ensure_group(name, scope, role):
    g = PermissionGroup.query.filter_by(name=name).first()
    if not g:
        g = PermissionGroup(name=name, scope_type=scope, role=role)
        db.session.add(g)
        db.session.flush()
    keys = set(g.get_menu_keys()) | {'grades.view'}
    g.set_menu_keys(sorted(keys))
    return g


def ensure_user(username, real_name, role, group, grade=None, class_name=None):
    u = User.query.filter_by(username=username).first()
    if not u:
        u = User(username=username, real_name=real_name, role=role)
        u.set_password(PWD)
        u.must_change_pwd = False
        db.session.add(u)
        db.session.flush()
    u.real_name, u.role, u.is_active = real_name, role, True
    u.permission_group_id = group.id if group else None
    if grade:
        u.grade = grade
    if class_name:
        u.class_name = class_name
    return u


def clean_demo_data():
    """清空本年级演示数据（学生、考试及成绩、划线、任课映射）"""
    nos = [r[0] for r in db.session.query(Student.student_number)
           .filter_by(grade=GRADE).all()]
    exams = Exam.query.filter_by(grade=GRADE).all()
    eids = [e.id for e in exams]
    n = 0
    if eids:
        n += ExamScore.query.filter(ExamScore.exam_id.in_(eids)).delete()
        n += ExamBand.query.filter(ExamBand.exam_id.in_(eids)).delete()
    for e in exams:
        db.session.delete(e)
    if nos:
        Student.query.filter_by(grade=GRADE).delete()
    ClassProfile.query.filter_by(grade=GRADE).delete()
    TeacherSubjectLink.query.filter_by(grade=GRADE).delete()
    db.session.query(UserClassLink).filter_by(grade=GRADE).delete()
    db.session.commit()
    return len(nos), len(eids), n


def build_students(n_stu, rnd):
    """生成学生与班型"""
    n_class = (n_stu + CLASS_SIZE - 1) // CLASS_SIZE
    rows = []
    for i in range(n_stu):
        cls_idx = i // CLASS_SIZE
        cls = f'{cls_idx + 1:02d}班'
        # 每 7 人换一种选科组合：同班内也有多种组合（验证选科并集）
        if cls_idx % 2 == 0:
            sel = PHYS_COMBOS[(i // 7) % len(PHYS_COMBOS)]
        else:
            sel = HIST_COMBOS[(i // 7) % len(HIST_COMBOS)]
        name = rnd.choice(SURNAMES) + rnd.choice(GIVEN) + \
            (rnd.choice(GIVEN) if rnd.random() < 0.45 else '')
        rows.append({
            'no': f'2026{i + 1:04d}', 'name': name, 'class_name': cls, 'sel': sel,
            'gender': '男' if i % 2 == 0 else '女',
        })
    # 班型
    for cls_idx in range(n_class):
        cls = f'{cls_idx + 1:02d}班'
        direction = '物理' if cls_idx % 2 == 0 else '历史'
        cp = ClassProfile(grade=GRADE, class_name=cls, class_type='普通班',
                          subject_direction=direction)
        db.session.add(cp)
    db.session.flush()

    students = []
    for r in rows:
        s = Student(student_number=r['no'], name=r['name'], grade=GRADE,
                    class_name=r['class_name'], gender=r['gender'],
                    subject_selection=r['sel'], enrollment_status='在读')
        students.append(s)
        db.session.add(s)
    db.session.commit()
    return students, n_class


def build_teachers(students):
    """教师账号 + 任课映射（每科一位教师，覆盖出现该科的班级）"""
    g_teach = ensure_group('任课教师组', 'class', 'teacher')
    g_home = ensure_group('班主任组', 'class', 'homeroom_teacher')
    users = {}
    for uname, name, sub in TEACHERS:
        users[sub] = ensure_user(uname, name, 'teacher', g_teach, grade=GRADE)
    for uname, name, cls in HOMEROOMS:
        u = ensure_user(uname, name, 'homeroom_teacher', g_home, grade=GRADE,
                        class_name=cls)
        if not UserClassLink.query.filter_by(user_id=u.id, grade=GRADE,
                                             class_name=cls).first():
            db.session.add(UserClassLink(user_id=u.id, grade=GRADE, class_name=cls))
    # 班级 → 该班实际出现的科目（选科并集）
    cls_subs = {}
    for s in students:
        cls_subs.setdefault(s.class_name, set())
        for sub in (subjects_of_selection(s.subject_selection) or []):
            cls_subs[s.class_name].add(sub)
    n = 0
    for cls, subs in cls_subs.items():
        for sub in [x for x in SUBJECTS if x in subs]:
            u = users.get(sub)
            if not u:
                continue
            link = TeacherSubjectLink.query.filter_by(
                grade=GRADE, class_name=cls, subject=sub).first()
            if not link:
                db.session.add(TeacherSubjectLink(grade=GRADE, class_name=cls,
                                                  subject=sub, user_id=u.id,
                                                  active=True))
            else:
                link.user_id, link.active = u.id, True
            n += 1
    db.session.commit()
    return n


def build_exams(students, n_exam, rnd, start=date(2026, 9, 10)):
    """生成多场考试及成绩（含趋势与少量缺考），逐场排名与划线"""
    # 学生能力与科目倾向
    ability = {s.student_number: rnd.gauss(0, 1) for s in students}
    bias = {s.student_number: {sub: rnd.gauss(0, 0.6) for sub in SUBJECTS}
            for s in students}
    plan = {s.student_number: (subjects_of_selection(s.subject_selection) or [])
            for s in students}

    created = []
    for k in range(n_exam):
        d = start + timedelta(days=8 * k)
        etype = EXAM_TYPES[k % len(EXAM_TYPES)]
        exam = Exam(name=f'{GRADE}{etype}{d.isoformat()}', grade=GRADE,
                    exam_date=d, exam_type=etype,
                    term=f'{d.year}-{d.year + 1}' if d.month >= 9
                    else f'{d.year - 1}-{d.year}')
        db.session.add(exam)
        db.session.flush()
        # 新考试可能复用已删除考试的 id：先清掉该 id 上的历史残留行，避免唯一键冲突
        ExamScore.query.filter_by(exam_id=exam.id).delete()
        ExamBand.query.filter_by(exam_id=exam.id).delete()

        rows = []
        for s in students:
            no = s.student_number
            subs = plan[no]
            total = 0.0
            for sub in subs:
                if rnd.random() < 0.008:     # 少量缺考
                    continue
                sd = SD_SCORE[sub]
                val = (BASE_SCORE[sub] + ability[no] * sd * 0.8
                       + bias[no][sub] * sd * 0.4 + 0.25 * k
                       + rnd.gauss(0, sd * 0.45))
                val = round(_clamp(val, sub), 1)
                total += val
                rows.append(ExamScore(
                    exam_id=exam.id, student_no=no, student_name=s.name,
                    grade=GRADE, class_name=s.class_name,
                    direction='物理' if s.subject_selection.startswith('物') else '历史',
                    subject_selection=s.subject_selection, subject=sub, score=val))
            if total > 0:
                rows.append(ExamScore(
                    exam_id=exam.id, student_no=no, student_name=s.name,
                    grade=GRADE, class_name=s.class_name,
                    direction='物理' if s.subject_selection.startswith('物') else '历史',
                    subject_selection=s.subject_selection,
                    subject=TOTAL_SUBJECT, score=round(total, 1)))
        db.session.bulk_save_objects(rows)
        db.session.flush()
        # 排名
        from app.modules.grades.services import ranking
        ranking.recalc_exam(exam.id)
        exam.status = 'imported'
        db.session.commit()
        created.append((exam.id, exam.name, len(rows)))
        print(f'  考试 {k + 1}/{n_exam}: {exam.name} — {len(rows)} 行')
    return created


def build_bands(exam_ids):
    """为每场考试按四层模板（人数比例）划线：方向 × 学科（取选科并集）"""
    from app.modules.grades.services import stats_service as st
    total_rows = 0
    for eid in exam_ids:
        ExamBand.query.filter_by(exam_id=eid).delete()   # 幂等：重跑不冲突
        db.session.commit()
        data = st.ExamData(eid)
        directions = data.directions or ['物理', '历史']
        for d in directions:
            subs = [TOTAL_SUBJECT] + [s for s in SUBJECTS
                                      if data.scores_of_subject(s, direction=d)]
            for sub in subs:
                if sub == TOTAL_SUBJECT:
                    scores = sorted((t['score'] for t in data.totals_of(direction=d)),
                                    reverse=True)
                else:
                    scores = sorted(data.scores_of_subject(sub, direction=d),
                                    reverse=True)
                if not scores:
                    continue
                n = len(scores)
                for seq, (name, ratio) in enumerate(BAND_TEMPLATE, start=1):
                    if seq == len(BAND_TEMPLATE):
                        value = 0.0
                    else:
                        idx = max(0, min(n - 1, int(-(-n * ratio // 100)) - 1))
                        value = scores[idx]
                    db.session.add(ExamBand(exam_id=eid, direction=d, subject=sub,
                                            seq=seq, name=name, lower_mode='score',
                                            lower_value=value))
                    total_rows += 1
        db.session.commit()
    return total_rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--students', type=int, default=1500)
    ap.add_argument('--exams', type=int, default=30)
    ap.add_argument('--clean', action='store_true', help='仅清空演示数据')
    ap.add_argument('--seed', type=int, default=20260913)
    args = ap.parse_args()

    rnd = random.Random(args.seed)
    with app.app_context():
        if args.clean:
            n_s, n_e, n_r = clean_demo_data()
            print(f'已清空：学生 {n_s}、考试 {n_e}、成绩/划线行 {n_r}')
            return 0
        print(f'目标：{GRADE} {args.students} 名学生 / {args.exams} 场考试')
        n_s, n_e, n_r = clean_demo_data()
        print(f'清理旧演示数据：学生 {n_s}、考试 {n_e}、行 {n_r}')

        students, n_class = build_students(args.students, rnd)
        print(f'学生 {len(students)} 人，{n_class} 个班')

        n_link = build_teachers(students)
        print(f'任课映射 {n_link} 条')

        created = build_exams(students, args.exams, rnd)
        n_band = build_bands([c[0] for c in created])
        print(f'划线 {n_band} 行')

        n_score = ExamScore.query.filter(
            ExamScore.exam_id.in_([c[0] for c in created])).count()
        print(f'\n完成：{len(created)} 场考试 / 成绩 {n_score} 行 / 划线 {n_band} 行')
        print(f'浏览器：http://localhost:5000/grades/')
    return 0


if __name__ == '__main__':
    sys.exit(main())
