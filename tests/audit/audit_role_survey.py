
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)          # 审计脚本按项目根定位 app 包与 data/*.db

# -*- coding: utf-8 -*-
"""审计 B1：角色体系与样本账号摸底（为越权矩阵做准备）"""
import sys
from collections import Counter

sys.path.insert(0, '.')

from app import create_app  # noqa: E402
from app.models import User  # noqa: E402

a = create_app()
with a.app_context():
    users = User.query.all()
    print(f'用户总数 {len(users)}')
    print('角色分布:', dict(Counter(u.role for u in users)))
    print('\n=== 各组样本（每组最多 3 个） ===')
    seen = {}
    for u in users:
        seen.setdefault(u.role, []).append(u)
    for role, lst in seen.items():
        print(f'\n[{role}] {len(lst)} 人')
        for u in lst[:3]:
            print(f'   id={u.id} {u.username} {u.real_name} '
                  f'grade={getattr(u, "grade", None)} '
                  f'class={(getattr(u, "duty_class", None) or getattr(u, "class_name", None))}')

    # 教师-班级-科目关系
    from app.models.academic import Teacher  # noqa: F401
    try:
        from app.models.grades import ExamTeacherLink
        print(f'\nExamTeacherLink 行数 {ExamTeacherLink.query.count()}')
    except Exception as e:  # noqa: BLE001
        print('ExamTeacherLink 不可用', e)
    try:
        from app.models import TeacherClass
        print(f'TeacherClass 行数 {TeacherClass.query.count()}')
    except Exception:  # noqa: BLE001
        pass