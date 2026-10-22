
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)          # 审计脚本按项目根定位 app 包与 data/*.db

# -*- coding: utf-8 -*-
"""审计 B4：任课教师范围数据（班级映射 vs 年级兜底）"""
from app import create_app  # noqa: E402
from app.models import User  # noqa: E402

a = create_app()
with a.app_context():
    from app.models import UserClassLink
    from app.models.grades import TeacherSubjectLink
    from app.utils.scope import (class_pairs, teaching_pairs, teaching_grades,
                                 teaching_links)
    for uname in ('licancan', 'renrunmeng', 'penghuilan'):
        u = User.query.filter_by(username=uname).first()
        if not u:
            continue
        links = teaching_links(u)
        print(f'\n[{uname}] role={u.role}')
        print(f'  UserClassLink: {class_pairs(u) or "空"}')
        print(f'  TeacherSubjectLink {len(links)} 条（active）:')
        for l in links[:6]:
            print(f'    {l.grade} {l.class_name or "(空=整年级)"} {l.subject}')
        print(f'  teaching_pairs: {teaching_pairs(u) or "空"}')
        print(f'  teaching_grades: {teaching_grades(u) or "空"}')
    print('\n=== 全局统计 ===')
    print('  UserClassLink 行数:', UserClassLink.query.count())
    print('  TeacherSubjectLink 行数:', TeacherSubjectLink.query.count())
    empty = TeacherSubjectLink.query.filter(
        (TeacherSubjectLink.class_name.is_(None))
        | (TeacherSubjectLink.class_name == '')).count()
    print('  class_name 为空的任课映射:', empty)