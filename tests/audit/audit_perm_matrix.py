# -*- coding: utf-8 -*-
"""诊断：哪些账号能编辑学生/教师，是否同时有查看手机号权限"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

from app import create_app  # noqa: E402
from app.models import User  # noqa: E402

a = create_app()
with a.app_context():
    combos = [
        ('students.edit', 'students.view_phone'),
        ('academic.edit', 'academic.view_teacher_phone'),
    ]
    for edit_perm, view_perm in combos:
        can_edit = [u.username for u in User.query.all() if u.has_perm(edit_perm)]
        both = [u.username for u in User.query.all()
                if u.has_perm(edit_perm) and u.has_perm(view_perm)]
        only_edit = [x for x in can_edit if x not in both]
        print(f'\n{edit_perm}: {len(can_edit)} 人可编辑')
        print(f'  同时有 {view_perm}: {len(both)} 人')
        print(f'  **可编辑但看不到号码**: {len(only_edit)} 人 {only_edit[:8]}')

    # 班主任 liuwen 的前端实际渲染情况
    from app.models.student import Student
    u = User.query.filter_by(username='liuwen').first()
    s = Student.query.filter_by(grade='2024级', class_name='01班').first()
    print(f'\n样本：liuwen 可编辑={u.has_perm("students.edit")} '
          f'可看号码={u.has_perm("students.view_phone")} '
          f'学生#{s.id} {s.name} 号码={s.phone1}')
