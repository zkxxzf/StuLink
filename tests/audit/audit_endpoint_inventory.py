
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)          # 审计脚本按项目根定位 app 包与 data/*.db

# -*- coding: utf-8 -*-
"""审计 B2：列出带 id 的读写路由（越权测试目标）+ 取样数据"""
from app import create_app  # noqa: E402

a = create_app()
with a.app_context():
    rules = []
    for r in a.url_map.iter_rules():
        if r.endpoint == 'static' or '<int:' not in r.rule:
            continue
        ms = sorted(set(r.methods) & {'GET', 'POST', 'PUT', 'DELETE'})
        if not ms:
            continue
        rules.append((r.endpoint, r.rule, ','.join(ms)))
    print(f'带 int 参数的读写路由 {len(rules)} 个\n')
    for ep, rule, ms in sorted(rules):
        print(f'  {ms:<10}{ep:<44}{rule}')

with a.app_context():
    from app.models import Student
    from app.models.grades import Exam
    # 2024级 01班 / 02班 各取一个学生，用于跨班测试
    s1 = Student.query.filter_by(grade='2024级', class_name='01班').first()
    s2 = Student.query.filter_by(grade='2024级', class_name='02班').first()
    print('\n样本学生:')
    print('  01班:', s1.id if s1 else None, s1.name if s1 else '')
    print('  02班:', s2.id if s2 else None, s2.name if s2 else '')
    e = Exam.query.order_by(Exam.id.desc()).first()
    print('  考试:', e.id if e else None, e.name if e else '')