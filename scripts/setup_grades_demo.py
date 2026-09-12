# -*- coding: utf-8 -*-
"""成绩模块演示环境初始化（幂等，可重复执行）

做四件事：
1. 年级归一：把演示学生 / 班型统一到考试所在年级
   （否则任课映射、年级长与领导的“可见年级”都和考试对不上，谁都看不到成绩）
2. 字典补年级：确保该年级在年级字典中且未毕业（visible_grades 依赖字典）
3. 权限组与账号：领导(全校) / 年级长(本年级) / 班主任(本班) / 任课教师×5
4. 任课映射：为各班各科绑定任课教师

用法：
    python scripts/setup_grades_demo.py                 # 默认按最新考试的年级归一
    python scripts/setup_grades_demo.py --grade 2026级

演示账号密码统一 123456（首登不强制改密，方便演示）。
"""
import argparse
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from app import create_app                                    # noqa: E402
from app.extensions import db                                 # noqa: E402
from app.models import (User, Student, ClassProfile, PermissionGroup,  # noqa: E402
                        UserClassLink, DictCategory, DictItem)
from app.models.grades import TeacherSubjectLink, Exam        # noqa: E402

app = create_app()

PWD = '123456'
GRADE_PERMS = ['grades.view']

# 演示账号：用户名 / 姓名 / 角色
LEADER = ('leader', '李校长', 'school_viewer')
G_LEADER = ('gleader', '王年级长', 'grade_leader')
H_TEACHER = ('hteacher', '赵班主任', 'homeroom_teacher')
TEACHERS = [
    ('t01', '张老师', 'teacher'),   # 语文 01/05
    ('t02', '李老师', 'teacher'),   # 数学 01/05
    ('t03', '王老师', 'teacher'),   # 外语 01/05
    ('t04', '陈老师', 'teacher'),   # 历史/政治/地理 01班
    ('t05', '刘老师', 'teacher'),   # 物理/化学/生物 05班
]
# 任课映射：用户名 -> [(班级, [科目...])]
ASSIGN = {
    't01': [('01班', ['语文']), ('05班', ['语文'])],
    't02': [('01班', ['数学']), ('05班', ['数学'])],
    't03': [('01班', ['外语']), ('05班', ['外语'])],
    't04': [('01班', ['历史', '政治', '地理'])],
    't05': [('05班', ['物理', '化学', '生物'])],
}
HOMEROOM_CLASS = '01班'


def ensure_group(name, scope, role):
    g = PermissionGroup.query.filter_by(name=name).first()
    if not g:
        g = PermissionGroup(name=name, scope_type=scope, role=role)
        db.session.add(g)
        db.session.flush()
    g.scope_type, g.role = scope, role
    keys = set(g.get_menu_keys()) | set(GRADE_PERMS)
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
    u.real_name, u.role = real_name, role
    u.is_active = True
    u.permission_group_id = group.id if group else None
    if grade:
        u.grade = grade
    if class_name:
        u.class_name = class_name
    return u


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--grade', default=None, help='目标年级，默认取最新考试所在年级')
    args = ap.parse_args()

    with app.app_context():
        exam = Exam.query.order_by(Exam.id.desc()).first()
        grade = args.grade or (exam.grade if exam else '2026级')
        print(f'目标年级：{grade}（考试：{exam.name if exam else "无"}）')

        # ---- 1. 年级归一 ----
        moved = 0
        for s in Student.query.filter_by(grade='2025级').all():
            s.grade = grade
            moved += 1
        for cp in ClassProfile.query.filter_by(grade='2025级').all():
            exist = ClassProfile.query.filter_by(grade=grade,
                                                class_name=cp.class_name).first()
            if exist:                      # 目标年级已有同名班型：合并方向后删旧
                exist.subject_direction = exist.subject_direction or cp.subject_direction
                db.session.delete(cp)
            else:
                cp.grade = grade
        db.session.commit()
        print(f'年级归一：学生 {moved} 人 → {grade}')

        # ---- 2. 字典补年级 ----
        cat = DictCategory.query.filter_by(code='grade').first()
        if cat and not DictItem.query.filter_by(category_id=cat.id, value=grade).first():
            mx = db.session.query(db.func.max(DictItem.sort_order)) \
                .filter_by(category_id=cat.id).scalar() or 0
            db.session.add(DictItem(category_id=cat.id, value=grade,
                                    sort_order=mx + 1, is_active=True))
            db.session.commit()
            print(f'年级字典已补充：{grade}')

        # ---- 3. 权限组与账号 ----
        g_leader = ensure_group('领导组', 'school', 'school_viewer')
        g_grade = ensure_group('年级长组', 'grade', 'grade_leader')
        g_home = ensure_group('班主任组', 'class', 'homeroom_teacher')
        g_teach = ensure_group('任课教师组', 'class', 'teacher')

        ensure_user(*LEADER, group=g_leader)
        ensure_user(*G_LEADER, group=g_grade, grade=grade)
        h = ensure_user(*H_TEACHER, group=g_home, grade=grade, class_name=HOMEROOM_CLASS)
        for uname, name, role in TEACHERS:
            ensure_user(uname, name, role, group=g_teach, grade=grade)

        # 班主任 → 班级关联
        if not UserClassLink.query.filter_by(user_id=h.id, grade=grade,
                                             class_name=HOMEROOM_CLASS).first():
            db.session.add(UserClassLink(user_id=h.id, grade=grade,
                                         class_name=HOMEROOM_CLASS))
        db.session.commit()

        # ---- 4. 任课映射 ----
        n_link = 0
        for uname, items in ASSIGN.items():
            u = User.query.filter_by(username=uname).first()
            if not u:
                continue
            for cls, subs in items:
                for sub in subs:
                    link = TeacherSubjectLink.query.filter_by(
                        grade=grade, class_name=cls, subject=sub).first()
                    if not link:
                        db.session.add(TeacherSubjectLink(
                            grade=grade, class_name=cls, subject=sub,
                            user_id=u.id, active=True))
                    else:
                        link.user_id, link.active = u.id, True
                    n_link += 1
        db.session.commit()

        # ---- 结果 ----
        print(f'\n任课映射：{n_link} 条（{grade}）')
        print('\n演示账号（密码均为 %s）：' % PWD)
        rows = [(LEADER[0], LEADER[1], '领导(全校)'), (G_LEADER[0], G_LEADER[1], '年级长(本年级)'),
                (H_TEACHER[0], H_TEACHER[1], f'班主任({HOMEROOM_CLASS})')] + \
               [(u[0], u[1], '任课教师') for u in TEACHERS]
        for uname, name, desc in rows:
            u = User.query.filter_by(username=uname).first()
            gname = u.permission_group.name if u.permission_group else '-'
            scope = u.permission_group.scope_type if u.permission_group else '-'
            print(f'  {uname:10s} {name:6s} {desc:14s} 组={gname} 范围={scope}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
